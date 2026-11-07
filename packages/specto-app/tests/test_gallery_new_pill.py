"""SPEC-59: the gallery's "+N new" pill is fed by its intaker's arrivals. Files arriving
in a watched folder set how many are new and where the first of them is, once their
collection swaps in; the pill's own retirement (``ackArrivals``) clears it."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import test_shell
from gallery_helpers import FakeGallery, copy_frames, needs_testdata
from test_ports_hosts import HeldJobs, _gallery_from_folder
from test_shell import click_item, gallery_doc, submit

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.app import find_item
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_core import Engine, Project

frames_project = test_shell.frames_project
app = test_shell.app


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
    assert fake.count() == 3 and fake.new == (0, -1)  # the first load is no arrival

    _arrive(proj, ctx, jobs, fake, 5)  # two more frames
    assert fake.count() == 5 and fake.new == (2, 3)
    assert kobi.snapshot()["new"] == {"count": 2, "first": 3}

    _arrive(proj, ctx, jobs, fake, 6)  # the burst grows: one pill, its count with it
    assert fake.new == (3, 3)

    kobi.bridge.ackArrivals()  # the pill retires (new items in view, burst quiet)
    assert fake.new == (0, -1)
    assert kobi.snapshot()["new"] == {"count": 0, "first": -1}
    engine.close()


# -- the pill itself, in the app's window ------------------------------------------------
@needs_testdata
def test_the_pill_shows_the_arrivals_and_a_click_reveals_them_then_it_retires(
    app, spin, frames_project
):
    doc = gallery_doc(app, "all_files")
    doc["links"]["l2"]["params"]["watch"] = True
    submit(app, doc, "watched gallery")
    spin(until=lambda: getattr(app.hosts.hosts.get("kobi"), "collection", None) is not None)
    kobi = app.hosts.hosts["kobi"]
    pill = find_item(app.window.contentItem(), "newPill")
    assert pill is not None and not pill.isVisible()

    copy_frames(frames_project.root / "runs" / "cam-a", 8)  # two more frames arrive
    spin(until=lambda: pill.isVisible(), timeout=10)
    label = next(c for c in pill.childItems() if c.property("text"))
    assert label.property("text") == "+2 new"
    assert kobi.snapshot()["new"] == {"count": 2, "first": 6}

    click_item(app, "newPill")  # scrolls to the first new frame; retires 3 s later
    spin(until=lambda: not pill.isVisible(), timeout=8)
    assert kobi.snapshot()["new"] == {"count": 0, "first": -1}
