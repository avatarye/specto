"""SPEC-8: a watched intaker follows change notifications instead of listing its sources
every interval. An arrival lands without another listing; where notifications are not
available the watcher polls as before and says why; ``IntakerListing`` keeps the same
table a full listing would give, from the changed paths alone."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest
from intake_helpers import copy_frames, make_tree, needs_testdata
from test_watch_resolve import drip, pump_until, watched_gallery

import specto_intake  # noqa: F401  (registers the intaker resolver and watcher)
from specto_core import Engine, Project, fswatch
from specto_core import links as links_mod
from specto_core.jobs import ThreadJobManager
from specto_core.links import Collection, FilesValue
from specto_intake.resolve import watch_intaker
from specto_intake.watch_listing import IntakerListing


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "drops")
    copy_frames(proj.root / "drops", 3, "cam-a")
    return proj


def items_of(engine: Engine) -> int:
    col = engine.links.input_of("kobi", "data")
    return len(col.items) if isinstance(col, Collection) else -1


def start_watching(project: Project, monkeypatch=None) -> tuple[Engine, ThreadJobManager]:
    jobs = ThreadJobManager()
    # a short interval: a polling watcher would list many times during the test
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.05)
    engine.live.submit(watched_gallery(engine.live.document()), origin="agent:s1", label="w")
    assert pump_until(engine, jobs, lambda: items_of(engine) == 3)
    return engine, jobs


@needs_testdata
def test_an_arrival_lands_from_a_notice_without_listing_the_sources_again(
    project: Project, monkeypatch
):
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    engine, jobs = start_watching(project)
    links = engine.links
    arrivals: list = []
    links.events.subscribe(
        lambda ev: arrivals.append(ev.payload) if ev.kind == "link.arrivals" else None
    )
    assert pump_until(engine, jobs, lambda: (links.watch_status("l2") or {}).get("polls", 0) >= 1)
    status = links.watch_status("l2")
    assert status["mode"] == "notify" and status["reason"] == ""
    polls = status["polls"]

    drip(project.root / "drops", 3, 2)

    assert pump_until(engine, jobs, lambda: items_of(engine) == 5, timeout=10)
    after = links.watch_status("l2")
    assert after["polls"] == polls  # no listing: the notices named the files
    assert after["notices"] >= 1
    assert sum(a["added"] for a in arrivals) >= 4 and arrivals[0]["link"] == "l2"
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_without_notifications_the_watcher_polls_and_says_why(project: Project, monkeypatch):
    monkeypatch.setattr(
        fswatch, "_observer_cls", lambda: (None, "no change notifications on this system")
    )
    engine, jobs = start_watching(project)
    links = engine.links

    def said() -> list[dict]:
        return [e.payload for e in links.events.since(0) if e.kind == "link.watching"]

    assert pump_until(engine, jobs, lambda: (links.watch_status("l2") or {}).get("polls", 0) >= 2)
    status = links.watch_status("l2")
    assert status["mode"] == "poll"
    assert status["reason"] == "polling: no change notifications on this system"
    # the status bar's words: said once as an event, and for the view it feeds
    assert pump_until(engine, jobs, lambda: bool(said()), timeout=2)
    assert said() == [
        {
            "link": "l2",
            "target": {"view": "kobi", "port": "data"},
            "mode": "poll",
            "reason": "polling: no change notifications on this system",
        }
    ]
    assert links.watch_note("kobi") == "polling: no change notifications on this system"

    drip(project.root / "drops", 3, 1)

    assert pump_until(engine, jobs, lambda: items_of(engine) == 4, timeout=10)
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_polling_can_be_asked_for(project: Project, monkeypatch):
    monkeypatch.setenv("SPECTO_WATCH", "poll")
    engine, jobs = start_watching(project)
    assert pump_until(engine, jobs, lambda: engine.links.watch_status("l2")["polls"] >= 1)
    status = engine.links.watch_status("l2")
    assert status["mode"] == "poll" and status["reason"] == "polling: SPECTO_WATCH=poll"
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_notifications_that_stop_fall_back_to_polling(project: Project, monkeypatch):
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")
    monkeypatch.setattr(fswatch.Notifier, "alive", property(lambda self: False))

    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "poll", timeout=5)
    assert links.watch_status("l2")["reason"] == "polling: change notifications stopped"
    drip(project.root / "drops", 3, 1)
    assert pump_until(engine, jobs, lambda: items_of(engine) == 4, timeout=10)
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_a_notice_the_system_dropped_is_caught_by_the_next_full_listing(
    project: Project, monkeypatch
):
    """A ReadDirectoryChangesW overflow says nothing: the watcher lists everything
    again every RELIST_S anyway, so a lost notice costs latency, never the file."""
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    monkeypatch.setattr(links_mod, "RELIST_S", 0.5)
    monkeypatch.setattr(links_mod, "RELIST_FACTOR", 0.0)
    monkeypatch.setattr(fswatch._Handler, "dispatch", lambda self, event: None)  # all lost
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")

    drip(project.root / "drops", 3, 1)

    assert pump_until(engine, jobs, lambda: items_of(engine) == 4, timeout=10)
    assert links.watch_status("l2")["notices"] == 0
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_lost_notices_make_the_watcher_list_everything(project: Project, monkeypatch):
    """Windows says nothing but "no bytes" when its notice buffer overflowed; the
    observer turns that into a loss notice, and the watcher lists its sources."""
    monkeypatch.delenv("SPECTO_WATCH", raising=False)

    def every_read_overflows(self, event):
        if self.on_lost is not None:
            self.on_lost()

    monkeypatch.setattr(fswatch._Handler, "dispatch", every_read_overflows)
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")
    polls = links.watch_status("l2")["polls"]

    drip(project.root / "drops", 3, 1)

    assert pump_until(engine, jobs, lambda: items_of(engine) == 4, timeout=10)
    assert links.watch_status("l2")["polls"] > polls


def test_the_windows_observer_reports_an_overflow_as_lost(tmp_path: Path):
    if os.name != "nt":
        pytest.skip("ReadDirectoryChangesW only")
    cls, why = fswatch._observer_cls()
    assert why is None and cls.__name__ == "LossAwareWindowsObserver"
    from watchdog.observers import winapi

    lost: list[str] = []
    paths: list[list[str]] = []
    n = fswatch.Notifier([(str(tmp_path), True)], paths.append, lambda: lost.append("lost"))
    real = winapi.read_directory_changes
    calls = {"n": 0}

    def overflow_once(handle, path, *, recursive):
        buf, nbytes = real(handle, path, recursive=recursive)
        calls["n"] += 1
        return (buf, 0) if calls["n"] == 1 else (buf, nbytes)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(winapi, "read_directory_changes", overflow_once)
        n.start()
        try:
            (tmp_path / "a.txt").write_text("a", encoding="utf-8")
            deadline = time.monotonic() + 5
            while not lost and time.monotonic() < deadline:
                time.sleep(0.02)
            (tmp_path / "b.txt").write_text("b", encoding="utf-8")
            while not paths and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            n.stop()
    assert lost == ["lost"]
    assert any(p.endswith("b.txt") for batch in paths for p in batch)


# -- IntakerListing: the table from changed paths alone -----------------------------------


def listing_for(root: Path, params: dict) -> tuple[IntakerListing, dict, dict[str, str]]:
    link = {"kind": "intaker", "params": params}
    value = FilesValue(root=str(root))
    listing = IntakerListing(link, value, root=str(root), env={})
    table = watch_intaker(link, value, root=str(root), env={})
    listing.reset(table)
    return listing, link, table


def fresh(root: Path, link: dict) -> dict[str, str]:
    return watch_intaker(link, FilesValue(root=str(root)), root=str(root), env={})


def bump(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def test_the_listing_follows_files_arriving_changing_and_leaving(tmp_path: Path):
    root = make_tree(tmp_path, {"data/a/1.txt": "1", "data/a/2.txt": "2", "data/b/3.txt": "3"})
    listing, link, table = listing_for(root, {"sources": ["data"]})
    new = root / "data/a/4.txt"
    new.write_text("4", encoding="utf-8")
    bump(root / "data/a/1.txt", "one")
    (root / "data/b/3.txt").unlink()

    counts = listing.apply(
        table, [str(new), str(root / "data/a/1.txt"), str(root / "data/b/3.txt")]
    )

    assert counts == (1, 1, 1)
    assert table == fresh(root, link)


def test_the_listing_walks_a_folder_moved_in_and_drops_one_moved_away(tmp_path: Path):
    root = make_tree(tmp_path, {"data/a/1.txt": "1", "data/b/x/2.txt": "2", "data/b/3.txt": "3"})
    make_tree(tmp_path, {"outside/new/5.txt": "5", "outside/new/deep/6.txt": "6"})
    listing, link, table = listing_for(root, {"sources": ["data"]})
    shutil.move(str(root / "outside/new"), str(root / "data/new"))
    shutil.move(str(root / "data/b"), str(root / "outside/b"))

    counts = listing.apply(table, [str(root / "data/new"), str(root / "data/b")])

    assert counts == (2, 2, 0)
    assert table == fresh(root, link)


def test_a_folder_replaced_in_place_loses_what_it_no_longer_holds(tmp_path: Path):
    root = make_tree(tmp_path, {"data/a/1.txt": "1", "data/a/2.txt": "2"})
    listing, link, table = listing_for(root, {"sources": ["data"]})
    shutil.rmtree(root / "data/a")
    make_tree(root, {"data/a/2.txt": "2", "data/a/9.txt": "9"})

    listing.apply(table, [str(root / "data/a")])

    assert table == fresh(root, link)


def test_the_listing_keeps_the_walks_rules(tmp_path: Path):
    files = {"proj/data/1.jpg": "1", "proj/data/deep/er/2.jpg": "2", "proj/data/x.txt": "x"}
    root = make_tree(tmp_path, files) / "proj"
    params = {"sources": [{"root": ".", "pattern": "**/*.jpg", "depth": 2}]}
    listing, link, table = listing_for(root, params)
    paths = []
    for rel in ("data/3.jpg", "data/y.txt", "data/deep/er/est/4.jpg", ".specto/z.jpg"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("n", encoding="utf-8")
        paths.append(str(root / rel))

    counts = listing.apply(table, paths)

    assert counts == (1, 0, 0)  # only data/3.jpg: a txt, too deep, the project's own folder
    assert table == fresh(root, link)


def test_a_single_file_source_follows_its_file(tmp_path: Path):
    root = make_tree(tmp_path, {"one.txt": "1", "other.txt": "2"})
    listing, link, table = listing_for(root, {"sources": ["one.txt"]})
    bump(root / "one.txt", "rewritten")
    (root / "two.txt").write_text("x", encoding="utf-8")

    counts = listing.apply(table, [str(root / "one.txt"), str(root / "two.txt")])

    assert counts == (0, 0, 1)
    assert table == fresh(root, link)
    (root / "one.txt").unlink()
    assert listing.apply(table, [str(root / "one.txt")]) is None  # the full listing says


def test_a_notice_for_a_source_folder_itself_asks_for_a_full_listing(tmp_path: Path):
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    listing, _, table = listing_for(root, {"sources": ["data"]})
    assert listing.apply(table, [str(root / "data")]) is None


def test_a_missing_source_cannot_be_observed_yet(tmp_path: Path):
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    listing, _, _ = listing_for(root, {"sources": ["data", "later"]})
    with pytest.raises(fswatch.NotifyUnavailable) as e:
        listing.roots()
    assert e.value.retry


def test_the_listing_observes_each_source_folder(tmp_path: Path):
    root = make_tree(tmp_path, {"data/1.txt": "1", "one.txt": "x"})
    listing, _, _ = listing_for(root, {"sources": ["data", "one.txt"]})
    roots = listing.roots()
    assert (str((root / "data").resolve()), True) in roots
    assert (str(root.resolve()), False) in roots


def test_nested_folders_are_observed_once(tmp_path: Path):
    a, b = str(tmp_path), str(tmp_path / "sub")
    assert fswatch._outermost([(b, True), (a, True)]) == [(a, True)]
    assert fswatch._outermost([(a, False), (b, True)]) == [(a, False), (b, True)]
    assert fswatch._outermost([(a, False), (a, True)]) == [(a, True)]


def test_a_burst_of_notices_is_counted_once_per_file(tmp_path: Path):
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    listing, _, table = listing_for(root, {"sources": ["data"]})
    p = root / "data/2.txt"
    p.write_text("2", encoding="utf-8")
    counts = listing.apply(table, [str(p), str(p)])
    assert counts == (1, 0, 0)
    time.sleep(0.01)
    assert listing.apply(table, [str(p)]) == (0, 0, 0)
