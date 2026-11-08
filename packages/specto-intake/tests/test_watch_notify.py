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
    assert pump_until(engine, jobs, lambda: settled(engine.links))
    return engine, jobs


def settled(links, link_id: str = "l2") -> bool:
    """The watcher's start is behind it: the baseline, and in notify mode the listing
    after notifications armed (SPEC-83), so a count of listings taken now stays put."""
    status = links.watch_status(link_id) or {}
    return status.get("polls", 0) >= (2 if status.get("mode") == "notify" else 1)


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
    steps = len(engine.live.history_summaries())

    drip(project.root / "drops", 3, 2)

    assert pump_until(engine, jobs, lambda: items_of(engine) == 5, timeout=10)
    after = links.watch_status("l2")
    assert after["polls"] == polls  # no listing: the notices named the files
    assert len(engine.live.history_summaries()) == steps  # an arrival is no undo step
    assert after["notices"] >= 1
    assert sum(a["added"] for a in arrivals) >= 4 and arrivals[0]["link"] == "l2"
    # the notices name what arrived, as a listing would (SPEC-59)
    assert sum(len(a["arrived"]) for a in arrivals) == sum(a["added"] for a in arrivals)
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
    monkeypatch.setattr(fswatch._FolderWatch, "dispatch", lambda self, event: None)  # all lost
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
        for n in list(self.subscribers):
            if n.on_lost is not None:
                n.on_lost()

    monkeypatch.setattr(fswatch._FolderWatch, "dispatch", every_read_overflows)
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
            # the emitter's thread opens the folder on its own time: write until it sees
            deadline = time.monotonic() + 20
            i = 0
            while not lost and time.monotonic() < deadline:
                (tmp_path / f"a{i}.txt").write_text("a", encoding="utf-8")
                i += 1
                time.sleep(0.05)
            while not paths and time.monotonic() < deadline:
                (tmp_path / f"b{i}.txt").write_text("b", encoding="utf-8")
                i += 1
                time.sleep(0.05)
        finally:
            n.stop()
    assert lost == ["lost"]  # the first read overflowed, and only that one
    assert paths  # the later reads name their files


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

    assert counts == (1, 1, 1, ["0:a/4.txt"])  # the arrival by name (SPEC-59)
    assert table == fresh(root, link)


def test_the_listing_walks_a_folder_moved_in_and_drops_one_moved_away(tmp_path: Path):
    root = make_tree(tmp_path, {"data/a/1.txt": "1", "data/b/x/2.txt": "2", "data/b/3.txt": "3"})
    make_tree(tmp_path, {"outside/new/5.txt": "5", "outside/new/deep/6.txt": "6"})
    listing, link, table = listing_for(root, {"sources": ["data"]})
    shutil.move(str(root / "outside/new"), str(root / "data/new"))
    shutil.move(str(root / "data/b"), str(root / "outside/b"))

    counts = listing.apply(table, [str(root / "data/new"), str(root / "data/b")])

    assert counts[:3] == (2, 2, 0)
    assert sorted(counts[3]) == ["0:new/5.txt", "0:new/deep/6.txt"]
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

    # only data/3.jpg: a txt, too deep, the project's own folder
    assert counts == (1, 0, 0, ["0:data/3.jpg"])
    assert table == fresh(root, link)


def test_a_single_file_source_follows_its_file(tmp_path: Path):
    root = make_tree(tmp_path, {"one.txt": "1", "other.txt": "2"})
    listing, link, table = listing_for(root, {"sources": ["one.txt"]})
    bump(root / "one.txt", "rewritten")
    (root / "two.txt").write_text("x", encoding="utf-8")

    counts = listing.apply(table, [str(root / "one.txt"), str(root / "two.txt")])

    assert counts == (0, 0, 1, [])
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
    assert e.value.reason is fswatch.Unavailable.MISSING


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
    assert counts == (1, 0, 0, ["0:2.txt"])
    time.sleep(0.01)
    assert listing.apply(table, [str(p)]) == (0, 0, 0, [])


# -- review follow-ups (PR 59) --------------------------------------------------------------


def test_the_windows_observer_falls_back_when_watchdogs_internals_moved(monkeypatch):
    if os.name != "nt":
        pytest.skip("ReadDirectoryChangesW only")
    from watchdog.observers import winapi

    monkeypatch.delattr(winapi, "_parse_event_buffer")
    assert fswatch._windows_observer() is None
    cls, why = fswatch._observer_cls.__wrapped__()
    assert why is None and cls.__name__ == "WindowsApiObserver"  # watchdog's own


def test_the_listing_says_when_a_source_is_missing_and_what_only_the_relist_covers(
    tmp_path: Path,
):
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    listing, _, table = listing_for(root, {"sources": ["data", "later"]})
    assert listing.source_missing(table)
    listing2, _, table2 = listing_for(root, {"sources": ["data"]})
    assert not listing2.source_missing(table2) and listing2.note() == ""


def test_writers_temporaries_are_not_arrivals(tmp_path: Path):
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    listing, link, table = listing_for(root, {"sources": ["data"]})
    tmp = root / "data" / ".mesh.glb.4242.tmp"
    tmp.write_text("half", encoding="utf-8")
    part = root / "data" / ".frame.jpg.part"
    part.write_text("half", encoding="utf-8")

    assert listing.apply(table, [str(tmp), str(part)]) == (0, 0, 0, [])
    assert table == fresh(root, link)  # the full listing leaves them out too
    tmp.replace(root / "data" / "mesh.glb")  # the rename into place is the arrival
    renamed = [str(tmp), str(root / "data" / "mesh.glb")]
    assert listing.apply(table, renamed) == (1, 0, 0, ["0:mesh.glb"])


def test_a_rename_that_only_changes_case_is_a_change_not_an_arrival(tmp_path: Path):
    root = make_tree(tmp_path, {"data/a.txt": "1", "data/b.txt": "2"})
    if not fswatch.case_insensitive(str(root / "data")):
        pytest.skip("a case-sensitive file system: two names, two files")
    listing, link, table = listing_for(root, {"sources": ["data"]})
    os.rename(root / "data" / "a.txt", root / "data" / "A.txt")

    counts = listing.apply(table, [str(root / "data" / "a.txt"), str(root / "data" / "A.txt")])

    assert counts == (0, 0, 1, [])  # a change, never an arrival
    assert table == fresh(root, link)


def test_a_folder_reached_through_a_junction_is_named_in_the_note(tmp_path: Path):
    import subprocess

    root = make_tree(tmp_path, {"data/1.txt": "1", "elsewhere/2.txt": "2"})
    link_dir = root / "data" / "linked"
    if os.name == "nt":
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(root / "elsewhere")],
            capture_output=True,
        )
        if made.returncode != 0:
            pytest.skip("no junction")
    else:
        os.symlink(root / "elsewhere", link_dir, target_is_directory=True)
    params = {"sources": [{"root": "data", "follow_links": True}]}
    listing = IntakerListing(
        {"kind": "intaker", "params": params}, FilesValue(root=str(root)), root=str(root), env={}
    )
    table = listing.full()
    assert "0:linked/2.txt" in table
    assert listing.note() == "1 folder reached through links covered by the periodic listing only"
    if os.name == "nt":
        os.rmdir(link_dir)  # the junction alone, never its target


def test_links_watching_one_folder_share_one_watch(tmp_path: Path):
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    seen: list[list[str]] = [[], []]
    before = fswatch.watched_folders()
    a = fswatch.Notifier([(str(root / "data"), True)], seen[0].extend).start()
    b = fswatch.Notifier([(str(root / "data"), True)], seen[1].extend).start()
    try:
        assert fswatch.watched_folders() == before + 1
        deadline = time.monotonic() + 10
        i = 0
        while not (seen[0] and seen[1]) and time.monotonic() < deadline:
            (root / "data" / f"n{i}.txt").write_text("n", encoding="utf-8")
            i += 1
            time.sleep(0.05)
        assert seen[0] and seen[1]  # one notice, both subscribers
    finally:
        a.stop()
        b.stop()
    assert fswatch.watched_folders() == before


def test_a_remote_drive_is_relisted_at_most_every_30_s():
    w = links_mod.LinkWatch(
        "lx", lambda *a, **k: {}, {}, None, root="", env={}, changes=None, interval_s=1.0
    )
    w._remote, w._list_s = True, 5.0  # a 5 s listing: 50 of them would be 250 s
    assert w._relist_gap() == links_mod.RELIST_REMOTE_MAX_S
    w._remote = False
    assert w._relist_gap() == 250.0


@needs_testdata
def test_a_write_storm_is_listed_once_it_settles_not_per_batch(project: Project, monkeypatch):
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    monkeypatch.setattr(links_mod, "BURST_PATHS", 3)
    monkeypatch.setattr(links_mod, "SETTLE_S", 0.6)
    monkeypatch.setattr(links_mod, "STORM_MIN_S", 30.0)
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")
    polls = links.watch_status("l2")["polls"]
    drops = project.root / "drops"

    end = time.monotonic() + 2.5  # batches of 4 files every 0.1 s: each one a burst
    n = 0
    while time.monotonic() < end:
        for _ in range(4):
            (drops / f"storm_{n:04d}.txt").write_text("s", encoding="utf-8")
            n += 1
        jobs.poll()
        engine.links.poll()
        time.sleep(0.1)
    during = links.watch_status("l2")["polls"] - polls

    assert during <= 1  # not a listing per batch (it was one each 0.1-0.5 s)
    assert pump_until(
        engine, jobs, lambda: links.watch_status("l2")["polls"] > polls + during, timeout=10
    )  # once it settled
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_a_watched_folder_renamed_away_is_armed_again_on_its_path(project: Project, monkeypatch):
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")
    drops = project.root / "drops"
    os.rename(drops, project.root / "drops-old")  # the handle follows this one
    copy_frames(drops, 2, "cam-a")  # a new folder under the old name

    assert pump_until(engine, jobs, lambda: items_of(engine) == 2, timeout=10)
    assert links.watch_status("l2")["mode"] == "notify"
    polls = links.watch_status("l2")["polls"]
    drip(drops, 5, 1)  # notices from the new folder now
    assert pump_until(engine, jobs, lambda: items_of(engine) == 3, timeout=10)
    assert links.watch_status("l2")["polls"] == polls
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_a_drive_that_sends_no_notices_is_polled(project: Project, monkeypatch):
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    monkeypatch.setattr(links_mod, "RELIST_S", 0.4)
    monkeypatch.setattr(links_mod, "RELIST_FACTOR", 0.0)
    monkeypatch.setattr(fswatch._FolderWatch, "dispatch", lambda self, event: None)  # silent
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")

    for k in range(4):  # changes the relists find and no notice reported
        drip(project.root / "drops", 3 + k, 1)
        pump_until(engine, jobs, lambda: False, timeout=0.6)
        if links.watch_status("l2")["mode"] == "poll":
            break

    status = links.watch_status("l2")
    assert status["mode"] == "poll"
    assert status["reason"] == "polling: this drive sends no change notices"
    engine.close()
    jobs.shutdown()


@needs_testdata
def test_notifications_that_come_back_are_followed_again(project: Project, monkeypatch):
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    monkeypatch.setattr(links_mod, "RETRY_MIN_S", 0.5)
    engine, jobs = start_watching(project)
    links = engine.links
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify")
    with pytest.MonkeyPatch.context() as blip:
        blip.setattr(fswatch.Notifier, "alive", property(lambda self: False))
        assert pump_until(
            engine, jobs, lambda: links.watch_status("l2")["mode"] == "poll", timeout=5
        )
    # the share is back: after the retry gap the watcher follows notices again
    assert pump_until(
        engine, jobs, lambda: links.watch_status("l2")["mode"] == "notify", timeout=10
    )
    engine.close()
    jobs.shutdown()


# -- round-2 review ---------------------------------------------------------------------------


def _write_until(folder: Path, done, deadline: float, tag: str) -> None:
    i = 0
    while not done() and time.monotonic() < deadline:
        (folder / f"{tag}{i}.txt").write_text("n", encoding="utf-8")
        i += 1
        time.sleep(0.05)


def test_a_shared_watch_is_armed_again_for_the_folder_now_at_its_path(tmp_path: Path):
    """Two links follow one folder; it is renamed away and a new one made in its place.
    The first to arm its path again tears the stale watch down and arms a new one; the
    other link sees its notices stop (or the move) and arms again too, onto the same
    new watch."""
    root = make_tree(tmp_path, {"data/1.txt": "1"})
    folder = root / "data"
    seen_a: list[str] = []
    seen_b: list[str] = []
    before = fswatch.watched_folders()
    a = fswatch.Notifier([(str(folder), True)], seen_a.extend).start()
    b = fswatch.Notifier([(str(folder), True)], seen_b.extend).start()
    try:
        os.rename(folder, root / "data-old")
        folder.mkdir()
        assert a.moved() and b.moved()
        a.stop()  # what LinkWatch does on moved(): arm the path again
        a = fswatch.Notifier([(str(folder), True)], seen_a.extend).start()
        assert not b.alive  # the stale watch is gone: b's link polls or re-arms
        b.stop()
        b = fswatch.Notifier([(str(folder), True)], seen_b.extend).start()
        assert fswatch.watched_folders() == before + 1  # one watch, both links on it
        _write_until(folder, lambda: bool(seen_a and seen_b), time.monotonic() + 10, "new")
        new = str(folder).replace("\\", "/")
        for seen in (seen_a, seen_b):
            assert any(p.replace("\\", "/").startswith(new) for p in seen)
        assert not a.moved() and not b.moved()
    finally:
        a.stop()
        b.stop()


def _bounded(fn, timeout: float = 10.0) -> bool:
    """Run ``fn`` on a daemon thread; False when it did not end in time (a regression
    then fails an assertion instead of hanging the suite)."""
    import threading

    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(timeout)
    return not t.is_alive()


def test_a_folder_subscribed_while_its_watch_is_reaped_keeps_a_live_watch(
    tmp_path: Path, monkeypatch
):
    """The reaper unschedules a lingering watch outside the hub's lock; a link
    subscribing to that folder meanwhile must get a live watch of its own, never the
    emitter being torn down (watchdog hands an equal watch the old emitter). The two
    overlap every run: the reaper's timer is stubbed out and the reaper run by hand,
    its unschedule takes 0.3 s, and the subscriber starts once that unschedule is under
    way. Red on 42f4de7 (the subscriber's watch died with the old emitter), green now."""
    import threading

    root = make_tree(tmp_path, {"data/1.txt": "1"})
    folder = str(root / "data")
    monkeypatch.setattr(fswatch, "LINGER_S", 0.0)
    monkeypatch.setattr(fswatch._HUB, "_arm_reaper", lambda delay: None, raising=False)
    first = fswatch.Notifier([(folder, True)], lambda p: None).start()
    obs = fswatch._HUB.observer
    unscheduling, unscheduled = threading.Event(), threading.Event()
    real_unschedule = obs.unschedule

    def slow_unschedule(watch) -> None:
        unscheduling.set()
        time.sleep(0.3)
        real_unschedule(watch)
        unscheduled.set()

    monkeypatch.setattr(obs, "unschedule", slow_unschedule)
    time.sleep(0.05)  # in its read: a stop now never wedges watchdog's close
    first.stop()  # idle, and with no timer only _reap below takes it
    got: list = []
    reap = threading.Thread(target=fswatch._HUB._reap, daemon=True)
    reap.start()
    assert unscheduling.wait(5), "the reaper did not unschedule the idle watch"
    sub = threading.Thread(
        target=lambda: got.append(fswatch.Notifier([(folder, True)], lambda p: None).start()),
        daemon=True,
    )
    sub.start()
    reap.join(10)
    sub.join(10)
    assert not reap.is_alive() and not sub.is_alive()
    assert unscheduled.wait(5), "the old watch was never unscheduled"
    n = got[0]
    assert n.alive, "the subscriber got a reaped watch"
    assert _bounded(n.stop)


def test_an_unschedule_that_never_returns_costs_its_observer_and_refuses_the_subscriber(
    tmp_path: Path, monkeypatch
):
    """watchdog's unschedule joins the emitter with no timeout, holding its observer's
    lock; on Windows an emitter can hang in its close. A subscriber tearing a stale
    watch down waits ``WATCHDOG_CALL_TIMEOUT_S`` for it, then gives that observer up and is
    refused (its link polls and tries again on the backoff); the observer's other
    watches are gone with it, and the next subscriber starts a fresh observer."""
    import threading

    root = make_tree(tmp_path, {"data/1.txt": "1", "other/1.txt": "1"})
    folder = str(root / "data")
    monkeypatch.setattr(fswatch, "WATCHDOG_CALL_TIMEOUT_S", 0.2)
    other = fswatch.Notifier([(str(root / "other"), True)], lambda p: None).start()
    first = fswatch.Notifier([(folder, True)], lambda p: None).start()
    obs = fswatch._HUB.observer
    release = threading.Event()
    monkeypatch.setattr(obs, "unschedule", lambda watch: release.wait(30))
    stale = [True]  # the folder's emitter reported dead: the next subscriber tears it down
    real_alive = fswatch._HUB._emitter_alive
    monkeypatch.setattr(fswatch._HUB, "_emitter_alive", lambda s: not stale[0] and real_alive(s))
    try:
        t0 = time.monotonic()
        with pytest.raises(fswatch.NotifyUnavailable) as e:
            fswatch.Notifier([(folder, True)], lambda p: None).start()
        assert time.monotonic() - t0 < 5.0
        assert e.value.reason is fswatch.Unavailable.REFUSED
        assert "did not stop" in str(e.value)
        assert fswatch._HUB.observer is None, "the wedged observer was kept"
        stale[0] = False
        assert not other.alive, "a watch on the given-up observer still counts as alive"
        again = fswatch.Notifier([(folder, True)], lambda p: None).start()
        assert again.alive and fswatch._HUB.observer is not obs
        assert _bounded(again.stop)
    finally:
        release.set()
        _bounded(first.stop)
        _bounded(other.stop)


def test_a_reaped_watch_whose_unschedule_never_returns_costs_its_observer(
    tmp_path: Path, monkeypatch
):
    """The reaper's unschedule is bounded the same way: past ``WATCHDOG_CALL_TIMEOUT_S``
    the reaper returns, the observer is given up, and the folder can be watched afresh."""
    import threading

    root = make_tree(tmp_path, {"data/1.txt": "1", "other/1.txt": "1"})
    folder = str(root / "data")
    monkeypatch.setattr(fswatch, "WATCHDOG_CALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(fswatch, "LINGER_S", 0.0)
    monkeypatch.setattr(fswatch._HUB, "_arm_reaper", lambda delay: None)
    keep = fswatch.Notifier([(str(root / "other"), True)], lambda p: None).start()
    idle = fswatch.Notifier([(folder, True)], lambda p: None).start()
    obs = fswatch._HUB.observer
    release = threading.Event()
    monkeypatch.setattr(obs, "unschedule", lambda watch: release.wait(30))
    try:
        time.sleep(0.05)
        idle.stop()
        assert _bounded(fswatch._HUB._reap, 5.0), "the reaper hung on the unschedule"
        assert fswatch._HUB.observer is None
        assert not keep.alive
        again = fswatch.Notifier([(folder, True)], lambda p: None).start()
        assert again.alive and fswatch._HUB.observer is not obs
        assert _bounded(again.stop)
    finally:
        release.set()
        _bounded(keep.stop)


def test_a_schedule_blocked_by_a_wedged_unschedule_is_refused_and_the_folder_stays_watchable(
    tmp_path: Path, monkeypatch
):
    """A wedged unschedule holds watchdog's observer lock, which schedule takes too: a
    subscriber for another folder would block in schedule for good, its folder stuck
    ``ARMING`` and every later subscriber for it refused for the life of the process.
    The schedule is bounded like the unschedule: the subscriber is refused in time, the
    observer given up, and a later subscriber for that folder gets a live watch on a
    fresh observer."""
    import threading

    root = make_tree(tmp_path, {"a/1.txt": "1", "b/1.txt": "1"})
    monkeypatch.setattr(fswatch, "WATCHDOG_CALL_TIMEOUT_S", 0.3, raising=False)
    monkeypatch.setattr(fswatch, "LINGER_S", 0.0)
    monkeypatch.setattr(fswatch._HUB, "_arm_reaper", lambda delay: None)
    idle = fswatch.Notifier([(str(root / "a"), True)], lambda p: None).start()
    obs = fswatch._HUB.observer
    release = threading.Event()

    def wedged_unschedule(watch) -> None:
        with obs._lock:  # as watchdog's own does, while it joins the emitter
            release.wait(30)

    monkeypatch.setattr(obs, "unschedule", wedged_unschedule)
    time.sleep(0.05)
    idle.stop()
    reap = threading.Thread(target=fswatch._HUB._reap, daemon=True)
    reap.start()
    time.sleep(0.05)  # the unschedule is wedged, the observer's lock held
    outcome: dict[str, object] = {}

    def subscribe_b() -> None:
        try:
            outcome["b"] = fswatch.Notifier([(str(root / "b"), True)], lambda p: None).start()
        except fswatch.NotifyUnavailable as e:
            outcome["b"] = e

    sub = threading.Thread(target=subscribe_b, daemon=True)
    sub.start()
    sub.join(5)
    try:
        assert not sub.is_alive(), "the subscriber hangs in watchdog's schedule"
        e = outcome["b"]
        assert isinstance(e, fswatch.NotifyUnavailable), f"served: {e!r}"
        assert e.reason is fswatch.Unavailable.REFUSED
        reap.join(5)
        assert not reap.is_alive()
        assert fswatch._HUB.observer is None
        third = fswatch.Notifier([(str(root / "b"), True)], lambda p: None).start()
        assert third.alive and fswatch._HUB.observer is not obs
        assert _bounded(third.stop)
    finally:
        release.set()


def test_a_shutdown_during_an_arm_leaves_no_watch_and_refuses_the_waiters(
    tmp_path: Path, monkeypatch
):
    """A subscriber inside watchdog's schedule when the hub shuts down: its watch is
    unscheduled again and it is refused; a second subscriber waiting on that arming
    is woken and refused too, and neither starts a new observer. The sweep's shutdown
    is not final: the next subscriber after it is served."""
    import threading

    root = make_tree(tmp_path, {"data/1.txt": "1", "other/1.txt": "1"})
    folder = str(root / "data")
    warm = fswatch.Notifier([(str(root / "other"), True)], lambda p: None).start()
    obs = fswatch._HUB.observer
    in_schedule, go = threading.Event(), threading.Event()
    real_schedule = obs.schedule

    def slow_schedule(handler, path, *, recursive=False, **kw):
        in_schedule.set()
        go.wait(10)
        return real_schedule(handler, path, recursive=recursive, **kw)

    monkeypatch.setattr(obs, "schedule", slow_schedule)
    outcomes: dict[str, object] = {}

    def subscribe(name: str) -> None:
        try:
            outcomes[name] = fswatch.Notifier([(folder, True)], lambda p: None).start()
        except fswatch.NotifyUnavailable as e:
            outcomes[name] = e

    arming = threading.Thread(target=subscribe, args=("arming",), daemon=True)
    arming.start()
    assert in_schedule.wait(5)
    waiting = threading.Thread(target=subscribe, args=("waiting",), daemon=True)
    waiting.start()
    time.sleep(0.1)  # the second waits on the first's arming
    try:
        fswatch.shutdown()
        go.set()
        arming.join(5)
        waiting.join(5)
        assert not arming.is_alive() and not waiting.is_alive()
        for name in ("arming", "waiting"):
            e = outcomes[name]
            assert isinstance(e, fswatch.NotifyUnavailable), f"{name} was served: {e!r}"
            assert e.reason is fswatch.Unavailable.REFUSED
        assert fswatch._HUB.observer is None, "a refused subscriber started an observer"
        assert fswatch.watched_folders() == 0
        assert not warm.alive
        after = fswatch.Notifier([(folder, True)], lambda p: None).start()
        assert after.alive
        assert _bounded(after.stop)
    finally:
        go.set()
        _bounded(warm.stop)


def test_shutdown_takes_about_a_second_however_wedged_the_hub_is(tmp_path: Path, monkeypatch):
    """The exit's shutdown is bounded at about ``STOP_TIMEOUT_S`` in all, lock acquire
    plus join: with the observer's teardown hanging, and with the hub's lock held
    elsewhere."""
    import threading

    root = make_tree(tmp_path, {"data/1.txt": "1"})
    n = fswatch.Notifier([(str(root / "data"), True)], lambda p: None).start()
    obs = fswatch._HUB.observer
    hang = threading.Event()
    monkeypatch.setattr(obs, "unschedule_all", lambda: hang.wait(30))
    try:
        t0 = time.monotonic()
        fswatch.shutdown()
        assert time.monotonic() - t0 < fswatch.STOP_TIMEOUT_S + 0.5
    finally:
        hang.set()
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with fswatch._HUB.lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5)
    try:
        t0 = time.monotonic()
        fswatch.shutdown()
        assert time.monotonic() - t0 < fswatch.STOP_TIMEOUT_S + 0.5
    finally:
        release.set()
        holder.join(5)
        _bounded(n.stop)


def test_subscribing_while_notices_dispatch_never_deadlocks(tmp_path: Path):
    """watchdog dispatches holding its observer's lock; subscribing and unsubscribing
    schedule and unschedule, which take that lock too. Dispatch never waits for the
    hub's lock, so the two cannot block each other."""
    import sys
    import threading

    root = make_tree(tmp_path, {"data/1.txt": "1"})
    seen: list[str] = []
    keep = fswatch.Notifier([(str(root / "data"), True)], seen.extend).start()
    stop = threading.Event()

    def churn() -> None:
        # a new folder each time: each one is scheduled while notices dispatch
        k = 0
        while not stop.is_set() and k < 40:
            other = root / f"other{k}"
            other.mkdir()
            n = fswatch.Notifier([(str(other), True)], lambda p: None).start()
            n.stop()
            k += 1
            time.sleep(0.02)

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    t = threading.Thread(target=churn, daemon=True)
    try:
        t.start()
        end = time.monotonic() + 2.0
        i = 0
        while time.monotonic() < end:
            (root / "data" / f"d{i}.txt").write_text("d", encoding="utf-8")
            i += 1
        stop.set()
        t.join(10)
        assert not t.is_alive(), "subscribe/unsubscribe deadlocked against dispatch"
    finally:
        sys.setswitchinterval(old)
        stop.set()
        cleaned = _bounded(keep.stop)
    assert cleaned, "stopping a notifier hung: the hub's lock is wedged"


@needs_testdata
def test_two_watched_links_on_a_folder_renamed_away_both_follow_the_new_one(
    project: Project, monkeypatch
):
    """The moved branch with two LinkWatches on one folder: both arm the path again
    (one tears the stale shared watch down, the other re-arms onto the new one), both
    collections show the new folder, and later arrivals reach both by notices."""
    import copy

    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.05)
    doc = watched_gallery(engine.live.document())
    doc["views"]["lami"] = copy.deepcopy(doc["views"]["kobi"])
    doc["views"]["lami"]["layout"]["position"] = [1000, 0]
    second = copy.deepcopy(doc["links"]["l2"])
    second["target"] = {"view": "lami", "port": "data"}
    second["label"] = "dropped frames again"
    doc["links"]["l3"] = second
    engine.live.submit(doc, origin="agent:s1", label="w2")
    links = engine.links
    lids = sorted(
        k
        for k, v in engine.live.data["links"].items()
        if v["kind"] == "intaker" and v["target"]["view"] in ("kobi", "lami")
    )
    views = [engine.live.data["links"][k]["target"]["view"] for k in lids]

    def counts() -> list[int]:
        out = []
        for v in views:
            col = links.input_of(v, "data")
            out.append(len(col.items) if isinstance(col, Collection) else -1)
        return out

    assert pump_until(engine, jobs, lambda: counts() == [3, 3])
    assert pump_until(
        engine, jobs, lambda: all(links.watch_status(k)["mode"] == "notify" for k in lids)
    )
    drops = project.root / "drops"
    os.rename(drops, project.root / "drops-old")
    copy_frames(drops, 2, "cam-a")

    assert pump_until(engine, jobs, lambda: counts() == [2, 2], timeout=15)
    assert pump_until(
        engine,
        jobs,
        lambda: all(links.watch_status(k)["mode"] == "notify" for k in lids),
        timeout=10,
    )
    drip(drops, 5, 1)
    assert pump_until(engine, jobs, lambda: counts() == [3, 3], timeout=10)
    engine.close()
    jobs.shutdown()


# -- SPEC-83: what lands while notifications arm is an arrival; the safety net catches up --


@needs_testdata
def test_files_landing_while_notifications_arm_are_arrivals(project: Project, monkeypatch):
    """The baseline is taken at once, before notifications arm (which can take a while:
    the observer starts on first use), and listed again once they are: two frames that
    land in between are an arrival, never part of a baseline the resolve never saw."""
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    real_start = fswatch.Notifier.start
    landed: list[str] = []

    def slow_start(self):
        if not landed:  # the first arming only: frames land meanwhile
            landed.extend(drip(project.root / "drops", 3, 2))
        return real_start(self)

    monkeypatch.setattr(fswatch.Notifier, "start", slow_start)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.05)
    links = engine.links
    arrivals: list = []
    links.events.subscribe(
        lambda ev: arrivals.append(ev.payload) if ev.kind == "link.arrivals" else None
    )
    engine.live.submit(watched_gallery(engine.live.document()), origin="agent:s1", label="w")
    try:
        assert pump_until(engine, jobs, lambda: items_of(engine) == 5), arrivals
        assert landed and sum(a["added"] for a in arrivals) >= 1
        assert links.watch_status("l2")["mode"] == "notify"
    finally:
        engine.close()
        jobs.shutdown()


@needs_testdata
def test_a_notify_watch_that_gets_no_notices_still_lands_files_by_the_safety_net(
    project: Project, monkeypatch
):
    """A drive that sends no notices for a while: the safety-net listing finds the new
    files (here every half second instead of 30 s)."""
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    monkeypatch.setattr(links_mod, "RELIST_S", 0.5)
    monkeypatch.setattr(links_mod.LinkWatch, "_on_paths", lambda self, paths: None)
    engine, jobs = start_watching(project)
    links = engine.links
    try:
        assert pump_until(
            engine, jobs, lambda: (links.watch_status("l2") or {}).get("polls", 0) >= 1
        )
        assert links.watch_status("l2")["mode"] == "notify"
        drip(project.root / "drops", 3, 2)
        assert pump_until(engine, jobs, lambda: items_of(engine) == 5)
        assert links.watch_status("l2")["notices"] == 0  # the listing found them, no notice
    finally:
        engine.close()
        jobs.shutdown()


@needs_testdata
def test_the_mode_is_said_before_the_baseline_while_notifications_arm(
    project: Project, monkeypatch
):
    """SPEC-83 review: the baseline now comes before arming, but the mode is decided
    first, so a listed watcher never reports (or says through ``link.watching``) that it
    polls while its notifications are only arming."""
    monkeypatch.delenv("SPECTO_WATCH", raising=False)
    real_start = fswatch.Notifier.start
    armed = []

    def slow_start(self):
        time.sleep(0.5)
        armed.append(1)
        return real_start(self)

    monkeypatch.setattr(fswatch.Notifier, "start", slow_start)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.05)
    links = engine.links
    said: list = []
    links.events.subscribe(
        lambda ev: said.append(ev.payload) if ev.kind == "link.watching" else None
    )
    engine.live.submit(watched_gallery(engine.live.document()), origin="agent:s1", label="w")
    try:
        assert pump_until(
            engine, jobs, lambda: (links.watch_status("l2") or {}).get("polls", 0) >= 1
        )
        assert not armed and links.watch_status("l2")["mode"] == "notify"
        assert pump_until(engine, jobs, lambda: bool(armed) and bool(said))
        assert [s["mode"] for s in said] == ["notify"], said
    finally:
        engine.close()
        jobs.shutdown()
