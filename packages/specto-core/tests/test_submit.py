"""Submit, reconcile and undo for the canvas document (#38). Seam 1: a document goes
in through ``submit``; what comes out is the resolved document state and the undo step
(ADR 0013: one submit is one undo step)."""

from __future__ import annotations

from pathlib import Path

import pytest

from specto_core import Project
from specto_core.canvas_document import CanvasInvalid, validate_canvas
from specto_core.canvas_example import worked_example
from specto_core.errors import TxnError
from specto_core.live_canvas import LiveCanvas


def test_submitting_a_valid_document_changes_state_and_one_undo_restores_it():
    live = LiveCanvas.headless("demo")
    before = live.document()
    assert [v["type"] for v in before["views"].values()] == ["file_tree"]

    res = live.submit(worked_example(), origin="agent:s1", label="compose the run-001 review")

    # the running canvas is now the submitted document (in its normalised JSON form),
    # except the window frame, which stays the user's (an agent cannot move the window)
    expected = validate_canvas(worked_example()).to_data()
    expected["window_frame"] = before["window_frame"]
    assert live.document() == expected
    assert not res.empty and res.label == "compose the run-001 review"
    assert set(res.views["added"]) == {"kobi", "tamu", "rena", "pola", "mihu", "vesa"}
    assert set(res.links["added"]) == {f"l{n}" for n in range(2, 14)}
    assert res.touched[:2] == ["fege", "kobi"] or "kobi" in res.touched

    undo = live.undo()

    assert live.document() == before
    assert undo.txn == res.txn and undo.cursor == 0 and undo.depth == 1


def test_an_invalid_submit_leaves_the_canvas_unchanged_and_returns_every_fixit():
    live = LiveCanvas.headless("demo")
    before = live.document()
    bad = worked_example()
    del bad["links"]["l9"]["summary"]  # a field error ...
    bad["links"]["l8"]["source"] = {"view": "rena", "port": "time"}  # ... and a port-graph error
    bad["root_view"] = "kobi"

    with pytest.raises(CanvasInvalid) as e:
        live.submit(bad, origin="agent:s1", label="broken")

    paths = {p["path"] for p in e.value.problems}
    assert paths == {"/links/l9/summary", "/links/l8", "/root_view"}
    assert all(p["fix"] for p in e.value.problems)
    assert e.value.data["problems"] == e.value.problems  # the RPC error carries them too
    assert live.document() == before
    assert live.undo_info()["depth"] == 0 and live.journal.read() == []


def test_submitting_the_same_document_is_no_undo_step():
    live = LiveCanvas.headless("demo")
    res = live.submit(live.document(), origin="agent:s1", label="nothing")
    assert res.empty and res.txn is None and res.depth == 0
    with pytest.raises(TxnError):
        live.undo()


def seed_example_files(root: Path) -> None:
    """The files the worked example names (a submit refuses unknown ones, story 24)."""
    for folder in ("runs/run-001/cam-a", "runs/run-001/cam-b", "runs/run-001/video", "geo"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "runs/run-001/video/cam-a.mp4").write_bytes(b"")
    (root / "geo/run-001-path.geojson").write_text("{}")
    (root / ".specto/links/image_to_video.py").write_text("def resolve(i, p, r):\n    return {}\n")


def test_undo_history_survives_closing_and_reopening_the_project(tmp_path: Path):
    """User story 5: revert an agent change from yesterday."""
    proj = Project.init(tmp_path / "data", "demo")
    seed_example_files(proj.root)
    live = LiveCanvas.open(proj)
    first = live.submit(worked_example(), origin="agent:s1", label="compose")
    doc = live.document()
    doc["views"]["kobi"]["layout"]["position"] = [320, 0]
    second = live.submit(doc, origin="user", label="move kobi")
    assert second.views == {"added": [], "removed": [], "changed": ["kobi"]}
    after_second = live.document()
    del live

    reopened = LiveCanvas.open(proj)
    assert reopened.document() == after_second
    assert reopened.history_note is None
    assert [h["txn"] for h in reopened.history_summaries()] == [first.txn, second.txn]
    assert reopened.undo().txn == second.txn
    assert reopened.undo().txn == first.txn
    assert [v["type"] for v in reopened.document()["views"].values()] == ["file_tree"]
    assert reopened.redo().txn == first.txn
    # the journal has every step, in order, with the submit's patch and its inverse
    kinds = [(e.kind, e.txn) for e in reopened.journal.read()]
    assert kinds == [
        ("txn", first.txn),
        ("txn", second.txn),
        ("undo", second.txn),
        ("undo", first.txn),
        ("redo", first.txn),
    ]
    assert proj.journal_file.name == "canvas.journal.jsonl"
