"""Seam 1 (#102): a link whose result did not change delivers nothing. A rescan that
finds the same items with the same stat per part leaves the target's collection (and its
generation) as it was, so nothing downstream runs again: a Python link off that view
keeps its table, and a pinned intaker's view is not handed the same collection twice. A
change inside a link's files still re-runs everything downstream of it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from intake_helpers import copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Collection, FilesValue

pytestmark = needs_testdata

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"
LINK_FILE = ".specto/links/frame_to_time.py"

#: frame i -> i * 100 ms; records how many items each run of the code saw
FRAME_TO_TIME = """\
import json, pathlib

def resolve(items, params, root):
    seen = pathlib.Path(root) / ".specto" / "links" / "frame_to_time.seen"
    counts = json.loads(seen.read_text()) if seen.exists() else []
    counts.append(len(items))
    seen.write_text(json.dumps(counts))
    return {it["key"]: i * 100 for i, it in enumerate(items)}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 4, "cam-a")
    (proj.root / "notes").mkdir()
    (proj.root / "notes" / "todo.txt").write_text("one", encoding="utf-8")
    (proj.root / LINK_FILE).write_text(FRAME_TO_TIME, encoding="utf-8")
    return proj


def seen(project: Project) -> list[int]:
    p = project.root / ".specto" / "links" / "frame_to_time.seen"
    return json.loads(p.read_text()) if p.exists() else []


def build(project: Project) -> tuple[Engine, dict[str, list[Collection]]]:
    """The file tree picks the cam-a run; ``kobi`` follows the picks, ``pina`` always
    shows the run (pinned) and a Python link turns ``kobi``'s selection into a time.
    Returns the engine and every collection each gallery was handed."""
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    picks: list[str] = []

    def tree_receives(port: str, value: object) -> None:
        # what the file tree host does with a new collection: emit its picks again
        if isinstance(value, Collection):
            links.emit("fege", "selected_files", FilesValue(root=value.root, paths=picks))

    handed: dict[str, list[Collection]] = {"kobi": [], "pina": []}
    for vid in handed:
        links.attach(vid, lambda port, v, vid=vid: handed[vid].append(v))
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Picks", "layout": {"position": [300, 0]}}
    doc["views"]["pina"] = {"type": "gallery", "label": "Run", "layout": {"position": [300, 500]}}
    doc["views"]["rena"] = {"type": "video", "label": "Video", "layout": {"position": [960, 0]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery shows the frames you pick."},
    }
    doc["links"]["l3"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "pina", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery always shows the cam-a run."},
    }
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "rena", "port": "time"},
        "python": {"path": LINK_FILE, "resolves_to": "table", "output": "time"},
        "summary": {"sentence": "Selecting a frame seeks the video to that frame's moment."},
    }
    engine.live.submit(doc, origin="agent:s1", label="frames")
    engine.links.approve("l9")  # TEST ONLY, as the user's click (conftest approve_link, #203)
    picks.extend(p for p in tree_files(project.root).paths if p.endswith(".jpg"))
    links.attach("fege", tree_receives)  # replays the tree's collection: the picks go out
    assert len(links.collection_of("kobi").items) == 4
    return engine, handed


def rescan(engine: Engine) -> None:
    """Every intaker off the project source scans again (the project intaker into the
    file tree, the pinned one)."""
    for lid in ("l1", "l3"):
        engine.links.resolve(lid)


def test_a_change_outside_a_links_files_runs_nothing_downstream(project: Project):
    engine, handed = build(project)
    links = engine.links
    kobi, pina = links.collection_of("kobi"), links.collection_of("pina")
    runs, given = seen(project), {k: len(v) for k, v in handed.items()}

    (project.root / "notes" / "todo.txt").write_text("one, two", encoding="utf-8")
    rescan(engine)

    # the file tree was handed its root again (it lists its loaded folders afresh) ...
    assert "notes/todo.txt" in tree_files(project.root).paths
    # ... but neither gallery was handed anything, and the Python code did not run
    assert {k: len(v) for k, v in handed.items()} == given
    assert links.collection_of("kobi").generation == kobi.generation
    assert links.collection_of("pina").generation == pina.generation
    assert seen(project) == runs
    assert links.status("l2")["state"] == links.status("l3")["state"] == "resolved"


def test_a_change_inside_a_links_files_runs_everything_downstream_again(project: Project):
    engine, handed = build(project)
    links = engine.links
    kobi, pina = links.collection_of("kobi"), links.collection_of("pina")
    runs = seen(project)

    frame = next((project.root / "runs" / "cam-a").glob("*.jpg"))
    st = frame.stat()
    os.utime(frame, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    rescan(engine)

    assert links.collection_of("kobi").generation > kobi.generation
    assert links.collection_of("pina").generation > pina.generation
    assert handed["pina"][-1] is links.collection_of("pina")
    assert seen(project) == [*runs, 4]  # the table was rebuilt once, from the new items


def test_picks_carry_the_trees_stat_and_the_same_picks_again_resolve_nothing(
    project: Project,
):
    """#76: the file tree hands on the stat it already holds for each picked file, so
    the intaker does not stat them again; the same picks (same stat) resolve nothing."""
    engine, _ = build(project)
    links = engine.links
    jpgs = [p for p in tree_files(project.root).paths if p.endswith(".jpg")]
    stats = {p: [1234, 5678 + i] for i, p in enumerate(jpgs)}  # not what the disk says
    picks = FilesValue(root=str(project.root), paths=jpgs, stats=stats)

    assert links.emit("fege", "selected_files", picks) == ["l2"]
    items = links.collection_of("kobi").items
    assert [next(iter(it.meta["stat"].values())) for it in items] == [
        [1234, 5678 + i] for i in range(4)
    ]
    generation = links.collection_of("kobi").generation

    assert links.emit("fege", "selected_files", picks.model_copy(deep=True)) == []
    assert links.collection_of("kobi").generation == generation
