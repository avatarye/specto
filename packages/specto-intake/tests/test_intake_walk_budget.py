"""SPEC-94: the run-time intake walk and the watcher's listing are bounded. Past
``WALK_MAX_FILES`` files or ``WALK_SECONDS`` seconds the walk stops and says so: the
collection carries a ``truncated`` note with a fix-it, which lands in the link's status;
the watcher's table marks the source truncated (stable, so the watch never sees a
truncated listing as files arriving or leaving) and ignores notices under it. An
agent-written ``C:/**`` would otherwise walk the whole drive on every rescan."""

from __future__ import annotations

from pathlib import Path

import pytest

import specto_intake  # noqa: F401  (registers the resolver and watcher)
from specto_core.links import FilesValue
from specto_intake import sources
from specto_intake.resolve import resolve_intaker
from specto_intake.spec import intake_spec
from specto_intake.watch_listing import IntakerListing, watch_table


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    for d in range(4):
        sub = root / "runs" / f"r{d}"
        sub.mkdir(parents=True)
        for f in range(5):
            (sub / f"{d:02d}_{f:02d}.txt").write_bytes(b"x")
    return root


def link(sources_: list[str]) -> dict:
    return {"kind": "intaker", "params": {"sources": sources_}, "target_type": "gallery"}


def resolve(folder: Path, sources_: list[str]):
    return resolve_intaker(
        link(sources_), FilesValue(root=str(folder)), root=str(folder), env={"aliases": {}}
    )


def test_a_walk_within_the_budget_is_not_truncated(folder: Path):
    col = resolve(folder, ["runs/**/*.txt"])
    assert len(col.items) == 20
    assert col.truncated == ""


def test_a_walk_past_the_file_cap_stops_and_says_so(folder, monkeypatch):
    monkeypatch.setattr(sources, "WALK_MAX_FILES", 7)
    col = resolve(folder, ["runs/**/*.txt"])
    assert len(col.items) == 7
    assert "source 0 truncated" in col.truncated
    assert "7 files" in col.truncated
    assert "narrow" in col.truncated


def test_a_walk_past_the_time_budget_stops_and_says_so(folder, monkeypatch):
    monkeypatch.setattr(sources, "WALK_SECONDS", 0.0)
    col = resolve(folder, ["runs/**/*.txt"])
    assert len(col.items) < 20
    assert "source 0 truncated" in col.truncated and " s" in col.truncated


def test_the_time_budget_also_stops_a_walk_that_matches_nothing(folder, monkeypatch):
    """``C:/**/*.zz`` yields no file at all: the clock is read per folder, not per file."""
    monkeypatch.setattr(sources, "WALK_SECONDS", 0.0)
    seen: list[str] = []
    real = sources.os.scandir

    def counting(path):
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr(sources.os, "scandir", counting)
    col = resolve(folder, ["runs/**/*.zz"])
    assert col.items == [] and "truncated" in col.truncated
    assert len(seen) <= 1


def test_the_link_status_carries_the_truncation(folder, monkeypatch):
    from specto_core import Engine, Project

    monkeypatch.setattr(sources, "WALK_MAX_FILES", 3)
    engine = Engine(project=Project.init(folder, "demo"))
    try:
        doc = engine.live.document()
        doc["views"]["kobi"] = {
            "type": "gallery",
            "label": "kobi",
            "layout": {"position": [400, 0], "size": [600, 400]},
        }
        doc["links"]["l7"] = {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "kobi", "port": "data"},
            "params": {"sources": ["runs/**/*.txt"]},
            "summary": {"sentence": "The gallery shows the runs."},
        }
        engine.live.submit(doc, origin="agent:prompt")
        engine.links.poll()
        st = engine.links.status("l7") or {}
        assert st.get("state") == "resolved"
        assert st.get("truncated") is True
        assert "source 0 truncated" in st.get("message", "")
    finally:
        engine.close()


def test_the_watcher_marks_a_truncated_source_and_keeps_its_table_stable(folder, monkeypatch):
    monkeypatch.setattr(sources, "WALK_MAX_FILES", 7)
    spec = intake_spec({"sources": ["runs/**/*.txt"]})
    one = watch_table(spec, folder)
    assert one == {"0:": "truncated"}
    assert watch_table(spec, folder) == one


def test_an_untruncated_watch_table_is_unchanged(folder):
    spec = intake_spec({"sources": ["runs/**/*.txt"]})
    table = watch_table(spec, folder)
    assert len(table) == 20 and "0:" not in table


def test_the_listing_notes_the_truncation_and_ignores_notices_under_it(folder, monkeypatch):
    monkeypatch.setattr(sources, "WALK_MAX_FILES", 7)
    listing = IntakerListing(
        link(["runs/**/*.txt"]), FilesValue(root=str(folder)), root=str(folder), env={}
    )
    table = listing.full()
    listing.reset(table)
    assert "truncated" in listing.note()
    (folder / "runs" / "r0" / "new.txt").write_bytes(b"x")
    before = dict(table)
    out = listing.apply(table, [str(folder / "runs" / "r0" / "new.txt")])
    assert out is not None and out[:3] == (0, 0, 0)
    assert table == before
