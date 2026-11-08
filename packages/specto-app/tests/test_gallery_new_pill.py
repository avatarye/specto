"""SPEC-59: the gallery's "+N new" pill is fed by its intaker's arrivals. A
``link.arrivals`` carries the entries that arrived (``arrived``); the host keeps them
pending and marks an item new when a delivered collection holds one of its parts, in
whatever generation. Pending entries that never show up expire after two deliveries;
the pill's own retirement (``ackArrivals``) clears what is new."""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

import pytest
import test_shell
from gallery_helpers import TESTDATA, FakeGallery, copy_frames, needs_testdata
from test_ports_hosts import HeldJobs, _gallery_from_folder
from test_shell import click_item, gallery_doc, submit

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.app import find_item
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.gallery import PENDING_CAP, GalleryFactory, GalleryHost, NewPill
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection

frames_project = test_shell.frames_project
app = test_shell.app


# -- the host alone: what arrives, what is delivered ---------------------------------------
def _host(tmp_path: Path) -> tuple[GalleryHost, FakeGallery, Engine]:
    engine = Engine(project=Project.init(tmp_path / "proj"), jobs=InlineJobManager())
    ctx = HostContext(engine, None)
    fake = FakeGallery(window=0)
    return GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake), fake, engine


def _col(root: Path, names: list[str], generation: int) -> Collection:
    """Single-part items as the intaker makes them: ``meta.source`` and ``meta.rel`` name
    the file an arrival entry names (``<source index>:<rel>``)."""
    items = [
        Item(
            key=Path(n).stem,
            parts={Path(n).suffix: str(root / n)},
            meta={"source": 0, "rel": {Path(n).suffix: n}, "stat": {Path(n).suffix: [1, 1]}},
        )
        for n in names
    ]
    return Collection(items=items, root=str(root), generation=generation, link="l2")


def _arrivals(*names: str) -> dict:
    return {
        "link": "l2",
        "target": {"view": "kobi", "port": "data"},
        "added": len(names),
        "removed": 0,
        "changed": 0,
        "arrived": [f"0:{n}" for n in names],
    }


def test_two_bursts_before_one_swap_are_both_new(tmp_path: Path):
    host, fake, engine = _host(tmp_path)
    host.receive("data", _col(tmp_path, ["a.jpg", "b.jpg"], 1))
    assert fake.new == NewPill(0, -1)  # the first load is no arrival
    host.on_arrivals(_arrivals("c.jpg"))
    host.on_arrivals(_arrivals("d.jpg"))  # the second burst, before the first swapped in
    host.receive("data", _col(tmp_path, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"], 2))
    assert fake.new == NewPill(2, 2)
    assert host.snapshot()["new"] == {"count": 2, "first": 2}
    engine.close()


def test_a_filter_edit_in_flight_when_a_notice_arrives_adds_nothing_new(tmp_path: Path):
    host, fake, engine = _host(tmp_path)
    host.receive("data", _col(tmp_path, ["a.jpg", "b.jpg"], 1))
    host.on_arrivals(_arrivals("c.jpg"))
    # the filter edit's resolve lands first: a higher generation, nothing of the arrival
    host.receive("data", _col(tmp_path, ["a.jpg"], 2))
    assert fake.new == NewPill(0, -1)
    # then the arrival's own collection: the arrived file, and it alone, is new
    host.receive("data", _col(tmp_path, ["a.jpg", "c.jpg"], 3))
    assert fake.new == NewPill(1, 1)
    engine.close()


def test_an_unchanged_or_failed_re_resolve_then_a_filter_edit_adds_nothing_new(tmp_path: Path):
    host, fake, engine = _host(tmp_path)
    host.receive("data", _col(tmp_path, ["a.jpg", "b.jpg"], 1))
    # a file the screener drops arrived: the re-resolve delivers nothing (unchanged), or
    # fails; either way no collection comes for it
    host.on_arrivals(_arrivals("notes.txt"))
    host.receive("data", _col(tmp_path, ["a.jpg"], 2))  # a filter edit
    assert fake.new == NewPill(0, -1)
    host.receive("data", _col(tmp_path, ["a.jpg", "b.jpg"], 3))  # the filter widened again
    assert fake.new == NewPill(0, -1)  # b was never announced
    assert host.pending_arrivals() == 0  # the entry expired after two deliveries
    engine.close()


def test_an_arriving_archive_marks_every_member_new(tmp_path: Path):
    """The watcher lists an archive unexpanded (``0:drive.zip``); the items it yields
    carry member paths (``drive.zip#/front/a.jpg``): they match by the archive."""
    host, fake, engine = _host(tmp_path)
    host.receive("data", _col(tmp_path, ["c.jpg"], 1))
    host.on_arrivals(_arrivals("drive.zip"))
    host.receive(
        "data", _col(tmp_path, ["c.jpg", "drive.zip#/front/a.jpg", "drive.zip#/front/b.jpg"], 2)
    )
    assert fake.new == NewPill(2, 1)  # the collection's order: c, then the archive's a and b
    assert host.pending_arrivals() == 0
    engine.close()


def test_pending_entries_are_capped(tmp_path: Path):
    host, _fake, engine = _host(tmp_path)
    host.on_arrivals(_arrivals(*(f"{i}.jpg" for i in range(PENDING_CAP + 500))))
    assert host.pending_arrivals() == PENDING_CAP
    engine.close()


# -- through the runtime: a watcher's arrivals reach the host --------------------------------
def _watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frames: int):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "drops")
    copy_frames(proj.root / "drops", frames)
    jobs = HeldJobs()
    engine = Engine(project=proj, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.0)
    clear_pending()
    fake = FakeGallery(window=0)

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            return GalleryHost(ctx, view_id, manifest, gallery=fake)

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(Factory())
    ctx.links  # noqa: B018  (the runtime starts)
    doc = _gallery_from_folder(engine.live.document(), ["drops"], watch=True)
    engine.live.submit(doc, origin="agent:s1", label="w")
    while jobs.held:
        jobs.run_next()
    return proj, engine, jobs, ctx, reg, fake


def _arrive(proj: Project, ctx: HostContext, jobs: HeldJobs, fake: FakeGallery, n: int) -> None:
    before = fake.count()
    copy_frames(proj.root / "drops", n)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "l2" not in ctx.links.poll():
        time.sleep(0.02)
    while fake.count() == before and jobs.held:
        jobs.run_next()


@needs_testdata
def test_arrivals_show_the_new_pill_and_its_retirement_clears_it(tmp_path, monkeypatch):
    proj, engine, jobs, ctx, reg, fake = _watched(tmp_path, monkeypatch, 3)
    kobi = reg.host("kobi")
    assert fake.count() == 3 and fake.new == NewPill(0, -1)  # the first load is no arrival

    _arrive(proj, ctx, jobs, fake, 5)  # two more frames
    assert fake.count() == 5 and fake.new == NewPill(2, 3)
    assert kobi.snapshot()["new"] == {"count": 2, "first": 3}

    _arrive(proj, ctx, jobs, fake, 6)  # the burst grows: one pill, its count with it
    assert fake.new == NewPill(3, 3)

    kobi.bridge.ackArrivals()  # the pill retires (new items in view, burst quiet)
    assert fake.new == NewPill(0, -1)
    assert kobi.snapshot()["new"] == {"count": 0, "first": -1}
    engine.close()


@needs_testdata
def test_an_archive_arriving_in_the_folder_marks_its_frames_new(tmp_path, monkeypatch):
    proj, engine, jobs, ctx, reg, fake = _watched(tmp_path, monkeypatch, 3)
    kobi = reg.host("kobi")  # hosts are built on first use; this one hears the arrivals
    assert fake.count() == 3
    frames = sorted((TESTDATA / "runs" / "run-001" / "cam-a").glob("*.jpg"))[3:5]
    staged = tmp_path / "drive.zip"  # written aside, then moved in whole
    with zipfile.ZipFile(staged, "w") as z:
        for f in frames:
            z.write(f, f"front/{f.name}")
    os.replace(staged, proj.root / "drops" / "drive.zip")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "l2" not in ctx.links.poll():
        time.sleep(0.02)
    while fake.count() == 3 and jobs.held:
        jobs.run_next()
    assert fake.count() == 5
    assert [it.meta["rel"] for it in kobi.items][3:] == [
        {".jpg": f"drive.zip#/front/{f.name}"} for f in frames
    ]
    assert fake.new == NewPill(2, 3)  # the archive arrived as one entry; both members are new
    assert kobi.pending_arrivals() == 0
    engine.close()


@needs_testdata
def test_a_host_that_fails_on_arrivals_lands_in_its_health(tmp_path, monkeypatch):
    proj, engine, jobs, ctx, reg, fake = _watched(tmp_path, monkeypatch, 3)
    kobi = reg.host("kobi")

    def boom(payload):
        raise RuntimeError("boom")

    kobi.on_arrivals = boom  # type: ignore[method-assign]
    seen: list[tuple[str, str]] = []
    ctx.health_listeners.append(
        lambda vid: seen.append((ctx.health_of(vid)["status"], ctx.health_of(vid)["message"]))
    )
    _arrive(proj, ctx, jobs, fake, 4)
    # the arrival note posted, then the error landed in the view's health (contracts
    # section 11); the re-resolve the same poll started then reported its own progress,
    # and the collection swapped in
    assert any(s == "loading" and m.startswith("2 arrived") for s, m in seen)
    assert ("error", "on_arrivals: RuntimeError: boom") in seen
    assert fake.count() == 4 and ctx.health_of("kobi")["status"] != "error"
    engine.close()


# -- the pill itself, in the app's window ------------------------------------------------
@needs_testdata
def test_the_pill_shows_the_arrivals_and_a_click_reveals_them_then_it_retires(
    app, spin, frames_project
):
    copy_frames(frames_project.root / "runs" / "cam-a", 30)  # rows below the view's fold
    doc = gallery_doc(app, "all_files")
    doc["links"]["l2"]["params"]["watch"] = True
    submit(app, doc, "watched gallery")
    spin(until=lambda: len(getattr(app.hosts.hosts.get("kobi"), "items", [])) == 30)
    kobi = app.hosts.hosts["kobi"]
    pill = find_item(app.window.contentItem(), "newPill")
    assert pill is not None and not pill.isVisible()

    copy_frames(frames_project.root / "runs" / "cam-a", 32)  # two more frames arrive
    spin(until=lambda: pill.isVisible(), timeout=10)
    label = next(c for c in pill.childItems() if c.property("text"))
    assert label.property("text") == "+2 new"
    assert kobi.snapshot()["new"] == {"count": 2, "first": 30}
    assert kobi.gallery.visible()[1] < 30  # the new frames are below the fold

    click_item(app, "newPill")  # scrolls to the first new frame; retires 3 s later
    spin(until=lambda: kobi.gallery.visible()[0] <= 30 <= kobi.gallery.visible()[1], timeout=5)
    spin(until=lambda: not pill.isVisible(), timeout=8)
    assert kobi.snapshot()["new"] == {"count": 0, "first": -1}
