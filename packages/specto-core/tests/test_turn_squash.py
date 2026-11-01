"""One prompt turn = one undo step (#44, spec #35 story 13): the submits an agent made
during one turn are squashed into one record at the turn's end, so the reply strip's
Undo reverts the whole turn in one step, and the journal restores it that way."""

from __future__ import annotations

from pathlib import Path

import pytest
from doc_helpers import pin

from specto_core import Project
from specto_core.canvas_example import worked_example
from specto_core.errors import TxnError
from specto_core.live_canvas import LiveCanvas


def add_view(live: LiveCanvas, vid: str, label: str, origin: str = "agent:prompt") -> None:
    doc = live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": label,
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    pin(doc, vid)
    live.submit(doc, origin=origin, label=f"added {label}")


def test_the_submits_of_one_turn_squash_into_one_undo_step(tmp_path: Path):
    proj = Project.init(tmp_path / "proj", "demo")
    live = LiveCanvas.open(proj)
    before = live.document()
    start = live.cursor

    add_view(live, "kobi", "Front")
    add_view(live, "tamu", "Rear")
    assert len(live.history) == 2
    after = live.document()

    rec = live.squash(start, label="added front and rear galleries", origin="agent:prompt")

    # one record for the turn, the document untouched by the squash itself
    assert live.document() == after
    assert len(live.history) == 1 and live.cursor == 1
    assert live.history[0] is rec and [r.id for r in live.log] == [rec.id]
    assert rec.label == "added front and rear galleries" and rec.origin == "agent:prompt"
    assert set(rec.touched) >= {"kobi", "tamu"}
    # the strip's Undo: the whole turn in one step, and redo brings it all back
    assert live.undo().txn == rec.id
    assert live.document() == before
    assert live.redo().txn == rec.id
    assert live.document() == after

    # the journal restores the squashed history on reopen
    reopened = LiveCanvas.open(proj)
    assert reopened.history_note is None
    assert [(r.id, r.label) for r in reopened.history] == [(rec.id, rec.label)]
    assert reopened.cursor == 1 and reopened.document() == after
    assert reopened.undo().txn == rec.id and reopened.document() == before


def test_a_turn_with_one_submit_or_none_needs_no_squash():
    live = LiveCanvas.headless("demo")
    assert live.squash(0, label="nothing") is None
    add_view(live, "kobi", "Front")
    only = live.history[0]
    assert live.squash(0, label="one") is only
    assert only.label == "added Front"  # the submit's own label stands


def test_squash_refuses_a_range_that_is_not_the_top_of_the_stack():
    live = LiveCanvas.headless("demo")
    add_view(live, "kobi", "Front")
    add_view(live, "tamu", "Rear")
    live.undo()
    with pytest.raises(TxnError):
        live.squash(0, label="turn")
    live.redo()
    with pytest.raises(TxnError):
        live.squash(5, label="turn")


def test_squash_keeps_the_example_document_round_trip():
    live = LiveCanvas.headless("demo")
    before = live.document()
    live.submit(worked_example(), origin="agent:prompt", label="compose")
    doc = live.document()
    doc["views"]["kobi"]["label"] = "Front frames"
    live.submit(doc, origin="agent:prompt", label="rename")
    rec = live.squash(0, label="composed the review", origin="agent:prompt")
    assert rec is not None and len(rec.patch) > 1
    live.undo()
    assert live.document() == before
