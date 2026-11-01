"""Seam 1 (#43): Python links. A submitted document references code under
``.specto/links/``; the link runtime runs it only once the user approved that exact
file content (ADR 0006, by hash), out of process, and from then on only looks the
resolved table up (ADR 0016). Worked values come from tiny link files the tests write."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from specto_core import Engine, Project
from specto_core import python_links as pl
from specto_core.errors import SpectoError
from specto_core.jobs import InlineJobManager
from specto_core.links import Selection
from specto_core.timebase import OWN_TIMELINE, TimeValue

LINK_FILE = ".specto/links/moment.py"

#: frame -> video moment, the recorded worked example; every call leaves a trace file
#: next to the code so a test can tell whether (and in which process) it ran
MOMENTS = """\
import json, os, pathlib

def resolve(items, params, root):
    trace = pathlib.Path(root) / ".specto" / "links" / "moment.ran"
    runs = json.loads(trace.read_text()) if trace.exists() else []
    runs.append(os.getpid())
    trace.write_text(json.dumps(runs))
    return {"00002": 1500.0, "00004": 3250.0}
"""


def python_doc() -> dict:
    """A front gallery whose selection seeks a video through Python (spec story 52)."""
    return {
        "schema_version": 2,
        "project": {"label": "python"},
        "views": {
            "kobi": {"type": "gallery", "label": "Front", "layout": {"position": [0, 0]}},
            "rena": {"type": "video", "label": "Video", "layout": {"position": [700, 0]}},
        },
        "links": {
            "l2": {
                "kind": "intaker",
                "scope": "pinned",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "kobi", "port": "data"},
                "params": {"sources": ["."]},
                "summary": {"sentence": "The front gallery shows every image."},
            },
            "l9": {
                "kind": "python",
                "source": {"view": "kobi", "port": "selection"},
                "target": {"view": "rena", "port": "time"},
                "python": {
                    "path": LINK_FILE,
                    "entry": "resolve",
                    "resolves_to": "table",
                    "output": "time",
                },
                "summary": {"sentence": "Selecting a front image seeks the video to its moment."},
            },
        },
    }


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    proj = Project.init(tmp_path / "proj", "python")
    (proj.root / LINK_FILE).write_text(MOMENTS, encoding="utf-8")
    return proj


def runs(project: Project) -> list[int]:
    trace = project.root / ".specto" / "links" / "moment.ran"
    return json.loads(trace.read_text()) if trace.exists() else []


def test_an_unapproved_python_link_never_runs_and_says_it_needs_approval(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    seen: list = []
    engine.live.events.subscribe(lambda ev: seen.append(ev) if ev.kind.startswith("link.") else 0)

    engine.live.submit(python_doc(), origin="agent:s1", label="python link")
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))

    assert runs(project) == []
    assert engine.links.input_of("rena", "time") is None
    status = engine.links.status("l9")
    assert status["state"] == "needs_approval"
    assert LINK_FILE in status["message"]
    ev = next(e for e in seen if e.kind == "link.needs_approval")
    assert ev.payload["link"] == "l9" and ev.payload["path"] == LINK_FILE
    assert ev.payload["target"] == {"view": "rena", "port": "time"}
    # the agent sees it in the canvas state too
    assert engine.call("canvas.state")["links"]["l9"]["state"] == "needs_approval"


def test_an_approved_python_link_runs_once_out_of_process_and_lookups_feed_the_target(
    project: Project, approve_link
):
    engine = Engine(project=project, jobs=InlineJobManager())
    engine.live.submit(python_doc(), origin="agent:s1", label="python link")
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))

    res = approve_link(engine, "l9")

    assert res["path"] == LINK_FILE and len(res["hash"]) == 64
    assert engine.links.status("l9")["state"] == "resolved"
    # the recorded moment of frame 2
    assert engine.links.input_of("rena", "time") == TimeValue(t=1500.0, timeline=OWN_TIMELINE)
    assert len(runs(project)) == 1 and runs(project)[0] != os.getpid()  # ran, elsewhere

    # a second pick only looks the table up: the code does not run again
    engine.links.emit("kobi", "selection", Selection(keys=["00004"], last="00004"))
    assert engine.links.input_of("rena", "time") == TimeValue(t=3250.0, timeline=OWN_TIMELINE)
    # a frame the table has no entry for seeks nowhere and leaves the last moment
    engine.links.emit("kobi", "selection", Selection(keys=["00009"], last="00009"))
    assert engine.links.input_of("rena", "time") == TimeValue(t=3250.0, timeline=OWN_TIMELINE)
    assert len(runs(project)) == 1


def test_changing_the_file_invalidates_its_approval_and_the_old_table(
    project: Project, approve_link
):
    engine = Engine(project=project, jobs=InlineJobManager())
    engine.live.submit(python_doc(), origin="agent:s1", label="python link")
    approve_link(engine, "l9")
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))
    assert engine.links.input_of("rena", "time") == TimeValue(t=1500.0, timeline=OWN_TIMELINE)

    # the agent (or anyone) edits the code: the next resolve sees new content
    (project.root / LINK_FILE).write_text(MOMENTS.replace("1500.0", "999.0"), encoding="utf-8")
    engine.links.emit("kobi", "selection", Selection(keys=["00004"], last="00004"))

    assert engine.links.status("l9")["state"] == "needs_approval"
    assert engine.links.input_of("rena", "time") == TimeValue(
        t=1500.0, timeline=OWN_TIMELINE
    )  # nothing new ran or landed
    assert len(runs(project)) == 1

    approve_link(engine, "l9")
    assert engine.links.input_of("rena", "time") == TimeValue(t=3250.0, timeline=OWN_TIMELINE)
    assert len(runs(project)) == 2
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))
    assert engine.links.input_of("rena", "time") == TimeValue(t=999.0, timeline=OWN_TIMELINE)


def test_a_failing_python_link_reports_a_fixit_and_leaves_other_links_alone(
    project: Project, approve_link
):
    (project.root / LINK_FILE).write_text(
        "def resolve(items, params, root):\n    raise KeyError('timeline')\n", encoding="utf-8"
    )
    doc = python_doc()
    doc["views"]["tamu"] = {"type": "gallery", "label": "Rear", "layout": {"position": [0, 500]}}
    doc["links"]["l7"] = {
        "kind": "follow",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "summary": {"sentence": "The rear gallery selects what the front gallery selects."},
    }
    engine = Engine(project=project, jobs=InlineJobManager())
    engine.live.submit(doc, origin="agent:s1", label="python link")
    approve_link(engine, "l9")
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))

    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert f"{LINK_FILE}:resolve" in status["message"]
    assert "KeyError: 'timeline'" in status["message"]
    assert engine.links.input_of("rena", "time") is None
    assert engine.links.status("l7")["state"] == "resolved"
    assert engine.links.input_of("tamu", "selection") == Selection(keys=["00002"], last="00002")


def test_link_approve_headless_asks_no_one_and_approves_nothing(project: Project):
    """#203: the command never approves. With no window to show the card it fails with
    a fix-it; the link keeps waiting and the trust list is untouched."""
    engine = Engine(project=project, jobs=InlineJobManager())
    engine.live.submit(python_doc(), origin="agent:s1", label="python link")
    assert engine.links.status("l9")["state"] == "needs_approval"

    with pytest.raises(SpectoError) as err:
        engine.call("link.approve", {"link": "l9", "from": "FLEET/SQUAD/SHIP"})

    assert "no window" in err.value.message and "running specto-app" in (err.value.fix or "")
    assert engine.links.status("l9")["state"] == "needs_approval"
    assert not engine.links.trust.approved(pl.code_hash(str(project.root), LINK_FILE))
    assert len(runs(project)) == 0
    # the same content approved by the user already: nothing to ask, the link runs
    engine.links.approve("l9")  # TEST ONLY: the user's click (conftest approve_link)
    res = engine.call("link.approve", {"link": "l9"})
    assert res["status"] == "approved" and res["request"] is None
