"""Seam 1 (#43): a Python link builds its table from the source view's collection and
rebuilds it when that collection changes (ADR 0016: resolve when inputs change), while
a pick in the source only looks the table up. Frames come from the run-001 test data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from intake_helpers import copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import FilesValue, Selection
from specto_core.timebase import OWN_TIMELINE, TimeValue

pytestmark = needs_testdata

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"
LINK_FILE = ".specto/links/frame_to_time.py"

#: frame i -> i * 100 ms, from the items the gallery holds; records how many it saw
FRAME_TO_TIME = """\
import json, pathlib

def resolve(items, params, root):
    seen = pathlib.Path(root) / ".specto" / "links" / "frame_to_time.seen"
    counts = json.loads(seen.read_text()) if seen.exists() else []
    counts.append(len(items))
    seen.write_text(json.dumps(counts))
    return {it["key"]: i * params["step_ms"] for i, it in enumerate(items)}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 6, "cam-a")
    (proj.root / LINK_FILE).write_text(FRAME_TO_TIME, encoding="utf-8")
    return proj


def seen(project: Project) -> list[int]:
    p = project.root / ".specto" / "links" / "frame_to_time.seen"
    return json.loads(p.read_text()) if p.exists() else []


def test_the_table_is_built_from_the_source_collection_and_rebuilt_when_it_changes(
    project: Project, approve_link
):
    engine = Engine(project=project, jobs=InlineJobManager())
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {"position": [300, 0]}}
    doc["views"]["rena"] = {"type": "video", "label": "Video", "layout": {"position": [960, 0]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery shows the cam-a frames."},
    }
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "rena", "port": "time"},
        "python": {"path": LINK_FILE, "resolves_to": "table", "output": "time"},
        "params": {"step_ms": 100},
        "summary": {"sentence": "Selecting a frame seeks the video to that frame's moment."},
    }
    engine.live.submit(doc, origin="agent:s1", label="frames to video")
    approve_link(engine, "l9")
    assert seen(project) == [0]  # approved before any frames arrived: an empty table

    # the user picks the run: the intaker lands a collection on the gallery and the
    # python link rebuilds its table from those items, without a pick in between
    jpgs = [p for p in tree_files(project.root).paths if p.endswith(".jpg")]
    engine.links.emit("fege", "selected_files", FilesValue(root=str(project.root), paths=jpgs))
    items = engine.links.collection_of("kobi").items
    assert len(items) == 6 and seen(project) == [0, 6]
    assert engine.links.status("l9")["state"] == "waiting"

    engine.links.emit("kobi", "selection", Selection(keys=[items[3].key], last=items[3].key))
    assert engine.links.input_of("rena", "time") == TimeValue(t=300, timeline=OWN_TIMELINE)
    engine.links.emit("kobi", "selection", Selection(keys=[items[5].key], last=items[5].key))
    assert engine.links.input_of("rena", "time") == TimeValue(t=500, timeline=OWN_TIMELINE)
    assert seen(project) == [0, 6]  # picks never run the code

    # changing the link's params is a change of logic: the table is rebuilt once
    doc = engine.live.document()
    doc["links"]["l9"]["params"] = {"step_ms": 1000}
    engine.live.submit(doc, origin="agent:s1", label="seconds")
    assert seen(project) == [0, 6, 6]
    assert engine.links.input_of("rena", "time") == TimeValue(t=5000, timeline=OWN_TIMELINE)
