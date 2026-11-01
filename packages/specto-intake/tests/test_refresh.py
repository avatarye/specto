"""Seam 1 (#102): refresh, the manual rescan for views whose auto-update is off. On the
project it rescans every intaker off the project source (the file tree's, the pinned
ones); on a view it rescans the intakers feeding that view, stat'ing the files afresh;
the command ``links.refresh`` does either. A refresh is one rescan and no undo step, and
like any rescan it delivers only what changed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from intake_helpers import copy_frames, needs_testdata

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.errors import NotFound
from specto_core.jobs import InlineJobManager
from specto_core.links import FilesValue

pytestmark = needs_testdata

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 3, "cam-a")
    engine = Engine(project=proj, jobs=InlineJobManager())
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Picks", "layout": {"position": [300, 0]}}
    doc["views"]["pina"] = {"type": "gallery", "label": "Run", "layout": {"position": [300, 500]}}
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
    engine.live.submit(doc, origin="agent:s1", label="frames")
    return engine


def tree_listing(engine: Engine) -> int:
    """The generation of the root the file tree was handed (#231: it lists its loaded
    folders again whenever that lands)."""
    return engine.links.input_of("fege", "data").generation


def frames(root: Path) -> list[Path]:
    return sorted((root / "runs" / "cam-a").glob("*.jpg"))


def test_refreshing_the_project_rescans_every_intaker_off_it_without_an_undo_step(
    engine: Engine,
):
    root = engine.project.root
    steps = len(engine.live.history_summaries())
    listing = tree_listing(engine)
    copy_frames(root / "runs" / "cam-a", 5, "cam-a")  # two more frames arrive

    assert engine.links.refresh() == ["l1", "l3"]

    assert tree_listing(engine) > listing  # the tree lists its folders again
    assert len(engine.links.collection_of("pina").items) == 5
    assert len(engine.live.history_summaries()) == steps


def test_refreshing_a_view_stats_its_files_afresh(engine: Engine):
    root = engine.project.root
    jpgs = [p.relative_to(root).as_posix() for p in frames(root)]
    held = {p: [1, 1] for p in jpgs}  # the tree's stat, out of date
    engine.links.emit("fege", "selected_files", FilesValue(root=str(root), paths=jpgs, stats=held))
    before = engine.links.collection_of("kobi")

    frame = frames(root)[0]
    st = frame.stat()
    os.utime(frame, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert engine.links.refresh("kobi") == ["l2"]

    after = engine.links.collection_of("kobi")
    assert after.generation > before.generation
    first = next(iter(after.items[0].meta["stat"].values()))
    assert first == [frame.stat().st_size, frame.stat().st_mtime_ns]


def test_the_refresh_command_names_what_it_rescanned(engine: Engine):
    assert engine.call("links.refresh", {}) == {"links": ["l1", "l3"]}
    assert engine.call("links.refresh", {"view": "pina"}) == {"links": ["l3"]}
    with pytest.raises(NotFound):
        engine.call("links.refresh", {"view": "nope"})
