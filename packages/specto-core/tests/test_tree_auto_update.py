"""Seam 1 (#115): the file tree auto-updates by default. A new canvas watches the
project folder for the file tree; a canvas saved before #115 (every intaker saved with
``watch: false``, the old default) turns it on once when it opens, journaled like the
other load rewrites and not an undo step; after that the user's switch is the canvas's
own say and a reopen keeps it. Other views stay off."""

from __future__ import annotations

import json
from pathlib import Path

from specto_core import Project, gestures
from specto_core.canvas_document import new_canvas_document
from specto_core.canvas_example import worked_example
from specto_core.live_canvas import LiveCanvas

GALLERY_LINK = {
    "kind": "intaker",
    "scope": "pinned",
    "source": {"view": "project", "port": "all_files"},
    "target": {"view": "kobi", "port": "data"},
    "params": {"sources": ["runs"], "watch": False},
    "summary": {"sentence": "The gallery shows the runs."},
}


def before_115(doc: dict) -> dict:
    """As a canvas saved before #115: a gallery beside the tree, every intaker's
    ``watch`` spelled out false."""
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Runs",
        "layout": {"position": [400, 0], "size": [600, 400]},
    }
    doc["links"]["l2"] = json.loads(json.dumps(GALLERY_LINK))
    doc["links"]["l1"]["params"]["watch"] = False
    return doc


def test_a_new_canvas_watches_the_project_folder_for_the_file_tree():
    live = LiveCanvas(new_canvas_document("demo"), journal=False, autosave=False)
    tree = next(iter(live.document()["views"]))
    assert gestures.auto_update_of(live.document(), tree) is True
    assert gestures.auto_update_of(worked_example(), "fege") is True


def test_a_tree_intaker_that_does_not_say_turns_on_and_one_that_says_off_stays_off():
    doc = worked_example()
    del doc["links"]["l1"]["params"]["watch"]
    assert gestures.auto_update_of(LiveCanvas(doc, journal=False).document(), "fege") is True
    doc["links"]["l1"]["params"]["watch"] = False
    assert gestures.auto_update_of(LiveCanvas(doc, journal=False).document(), "fege") is False


def test_a_submitted_document_whose_tree_intaker_does_not_say_keeps_it_on():
    """A hand-written or agent-written document that leaves ``watch`` out of the file
    tree's intaker means the default (on); one that says false turns it off."""
    live = LiveCanvas(worked_example(), journal=False, autosave=False)
    doc = live.document()
    del doc["links"]["l1"]["params"]["watch"]
    live.submit(doc, origin="agent:s1", label="no say")
    assert gestures.auto_update_of(live.document(), "fege") is True
    doc["links"]["l1"]["params"]["watch"] = False
    live.submit(doc, origin="agent:s1", label="off")
    assert gestures.auto_update_of(live.document(), "fege") is False


def test_a_canvas_saved_before_115_turns_the_tree_on_once_and_then_keeps_the_users_say(
    tmp_path: Path,
):
    project = Project.init(tmp_path / "proj", "demo")
    (project.root / "runs").mkdir()
    (project.root / "runs" / "a.txt").write_text("a", encoding="utf-8")
    tree = next(iter(json.loads(project.canvas_file.read_text(encoding="utf-8"))["views"]))
    project.canvas_file.write_text(
        json.dumps(before_115(new_canvas_document("demo"))), encoding="utf-8"
    )
    del project.meta["rewrites"]  # as written before #115
    project._write_meta()
    project = Project.open(project.root)

    live = LiveCanvas.open(project)

    doc = live.document()
    assert gestures.auto_update_of(doc, tree) is True
    assert gestures.auto_update_of(doc, "kobi") is False  # other views stay off
    assert live.undo_info()["depth"] == 0  # not an undo step
    (entry,) = live.journal.read()
    assert entry.kind == "rewire" and "auto-update on for the file tree" in entry.label

    # the user turns it off: the canvas says so now, and a reopen keeps it
    gestures.set_auto_update(live, tree, False)
    live.save()
    again = LiveCanvas.open(Project.open(project.root))
    assert gestures.auto_update_of(again.document(), tree) is False
    assert [e.kind for e in again.journal.read()].count("rewire") == 1
