"""Seam 1: persistence never aborts a submit. On Windows a sync client (Dropbox) or a
scanner holds ``canvas.json`` open without FILE_SHARE_DELETE, so the atomic replace of
the temp file fails with WinError 5. The canvas keeps the change in memory, warns once,
and the next save lands it; the journal does the same with its append."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from specto_core import Engine, Project, fsio

pytestmark = pytest.mark.usefixtures("fast_retries")


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the backoff real but short: the tests hold the lock for whole saves."""
    monkeypatch.setattr(fsio, "FIRST_DELAY_S", 0.002)
    monkeypatch.setattr(fsio, "MAX_DELAY_S", 0.004)


def failures(live) -> list:
    out: list = []
    live.events.subscribe(lambda ev: out.append(ev) if ev.kind == "canvas.save_failed" else None)
    return out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing locks")
def test_a_submit_survives_canvas_json_held_open_and_the_next_save_lands_it(
    project: Project, capsys: pytest.CaptureFixture
):
    live = Engine(project=project).live
    failed = failures(live)
    saved: list = []
    live.events.subscribe(lambda ev: saved.append(ev) if ev.kind == "canvas.saved" else None)
    doc = live.document()
    doc["views"]["fege"]["label"] = "Moved"
    # Python's open() shares read and write but not delete: os.replace onto it is denied
    with open(project.canvas_file, encoding="utf-8"):
        live.submit(doc, origin="user", label="move")  # the gesture is not lost
        assert live.data["views"]["fege"]["label"] == "Moved"
        assert live.dirty is True and saved == []
        assert len(failed) == 1 and failed[0].payload["fix"]
        assert "canvas.json" in failed[0].payload["path"]
        again = live.document()
        again["views"]["fege"]["label"] = "Twice"
        live.submit(again, origin="user", label="again")
        assert len(failed) == 1  # one warning per streak, not one per gesture
        assert project.canvas_document().views["fege"].label != "Twice"
    live.save()
    assert live.dirty is False and len(saved) == 1
    assert project.canvas_document().views["fege"].label == "Twice"
    out = capsys.readouterr().out
    assert out.count("[WARN]") == 1 and "canvas.json" in out


def test_a_transient_lock_is_retried_without_a_warning(
    project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    live = Engine(project=project).live
    failed = failures(live)
    real = os.replace
    denied = {"n": 2}

    def flaky(src, dst):
        if denied["n"] > 0:
            denied["n"] -= 1
            raise PermissionError(5, "Access is denied", str(src), 5, str(dst))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    doc = live.document()
    doc["views"]["fege"]["label"] = "Moved"
    live.submit(doc, origin="user", label="move")
    assert denied["n"] == 0 and live.dirty is False and failed == []
    assert project.canvas_document().views["fege"].label == "Moved"
    assert "[WARN]" not in capsys.readouterr().out


def test_a_journal_that_cannot_be_appended_keeps_its_entries_for_the_next_write(
    project: Project, capsys: pytest.CaptureFixture
):
    live = Engine(project=project).live
    failed = failures(live)
    doc = live.document()
    doc["views"]["fege"]["label"] = "One"
    live.submit(doc, origin="user", label="one")
    jpath: Path = project.journal_file
    assert len(live.journal.read()) == 1
    jpath.chmod(stat.S_IREAD)  # a scanner that denies writers
    if os.access(jpath, os.W_OK):
        pytest.skip("read-only files are writable here")
    try:
        doc["views"]["fege"]["label"] = "Two"
        live.submit(doc, origin="user", label="two")  # not lost, not raised
        assert live.data["views"]["fege"]["label"] == "Two"
        assert len(failed) == 1 and "journal" in failed[0].payload["path"]
        assert len(live.journal.read()) == 1
    finally:
        jpath.chmod(stat.S_IREAD | stat.S_IWRITE)
    doc["views"]["fege"]["label"] = "Three"
    live.submit(doc, origin="user", label="three")
    assert [e.label for e in live.journal.read()] == ["one", "two", "three"]
    assert len(failed) == 1
    assert capsys.readouterr().out.count("[WARN]") == 1
