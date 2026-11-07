"""SPEC-48: a submit that keeps a view's id but changes its type gets the new type's
host. The registry handed the patch to the old host (a MissingHost for a type with no
host here, or the previous type's host) and never built the new one. The new host's
links resolve again for the new type (an intaker feeding a 3d view prepares scenes), and
a paused view comes back paused with the reason it had."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import test_shell
from app_doc_helpers import pin
from gallery_helpers import FakeGallery, needs_testdata
from scene3d_helpers import write_cloud_ply
from test_shell import submit

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.hosts.base import QuickViewHost, SimpleFactory
from specto_app.hosts.registry import MissingHost
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Collection

frames_project = test_shell.frames_project
app = test_shell.app


class ScenesHost(QuickViewHost):
    """A stand-in 3d host: records what it receives and whether it was paused."""

    def __init__(self, ctx, view_id, manifest):
        super().__init__(ctx, view_id, manifest)
        self.received: list[tuple[str, Any]] = []
        self.paused = False

    def receive(self, port: str, value: Any) -> None:
        self.received.append((port, value))

    def on_patch(self, patch, meta) -> None:
        pass

    def pause(self) -> None:
        self.paused = True

    def snapshot(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "scans")
    (proj.root / "scans").mkdir(parents=True, exist_ok=True)
    write_cloud_ply(proj.root / "scans" / "cloud.ply")
    engine = Engine(project=proj, jobs=InlineJobManager())
    clear_pending()
    fakes: list[FakeGallery] = []

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes.append(FakeGallery())
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[-1])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)  # no host for "3d" unless a test adds one
    reg.add_factory(Factory())
    yield engine, ctx, reg
    reg.dispose_all()
    engine.close()


def _submit(engine: Engine, vtype: str, label: str = "kobi") -> None:
    doc = engine.live.document()
    view = doc["views"].get("kobi")
    doc["views"]["kobi"] = {**(view or {"layout": {"size": [600, 400]}}), "type": vtype}
    doc["views"]["kobi"]["label"] = label
    if view is None:
        pin(doc, "kobi", sources=["scans"])  # the project's scans feed it, once
    engine.live.submit(doc, origin="agent:s1", label=f"kobi as {vtype}")


def test_a_type_change_builds_the_new_types_host(setup):
    engine, ctx, reg = setup
    _submit(engine, "3d")
    assert isinstance(reg.host("kobi"), MissingHost)
    assert ctx.health_of("kobi")["status"] == "error"

    _submit(engine, "gallery")
    host = reg.host("kobi")
    assert isinstance(host, GalleryHost) and not host.disposed
    assert ctx.health_of("kobi")["status"] != "error"

    _submit(engine, "3d")  # and back: the gallery host goes
    assert host.disposed and isinstance(reg.host("kobi"), MissingHost)


def test_a_change_that_keeps_the_type_keeps_the_host(setup):
    engine, _ctx, reg = setup
    _submit(engine, "gallery")
    host = reg.host("kobi")
    _submit(engine, "gallery", label="renamed")
    assert reg.host("kobi") is host and not host.disposed


def test_a_type_change_resolves_the_views_links_again_for_the_new_type(setup):
    """Resolution depends on the target type: the intaker prepares scene buffers for a
    3d view and not for a gallery. No link changed, so without the re-resolve the new
    host would be handed the gallery's collection."""
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    gallery = reg.host("kobi")
    assert [it.key for it in gallery.collection.items] == ["cloud"]
    assert "scene" not in gallery.collection.items[0].meta

    _submit(engine, "3d")
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost) and gallery.disposed
    cols = [v for p, v in scenes.received if p == "data" and isinstance(v, Collection)]
    assert len(cols) == 1, scenes.received  # the fresh resolve alone, nothing stale replayed
    assert cols[0].items[0].meta["scene"]["kind"] == "pointcloud"
    assert ctx.links.input_of("kobi", "data") is cols[0]


def test_a_paused_view_comes_back_paused(setup):
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    assert isinstance(reg.host("kobi"), GalleryHost)  # hosts are built on first use
    assert reg.pause("kobi") is True
    assert ctx.health_of("kobi") == {"status": "paused", "message": "off-screen", "hint": ""}

    _submit(engine, "3d")
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost) and scenes.paused and reg.is_paused("kobi")
    assert ctx.health_of("kobi")["message"] == "off-screen"
    assert reg.resume("kobi") is True and not reg.is_paused("kobi")


def test_a_collapsed_view_comes_back_collapsed(setup):
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    assert isinstance(reg.host("kobi"), GalleryHost)
    reg.pause("kobi", "collapsed")
    assert ctx.health_of("kobi") == {"status": "paused", "message": "collapsed", "hint": ""}

    _submit(engine, "3d")
    assert reg.is_paused("kobi")
    assert ctx.health_of("kobi") == {"status": "paused", "message": "collapsed", "hint": ""}


def test_undo_of_a_type_change_brings_the_old_types_host_back(setup):
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    first = reg.host("kobi")
    _submit(engine, "3d")
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost) and first.disposed

    engine.live.undo()
    again = reg.host("kobi")
    assert isinstance(again, GalleryHost) and again is not first and not again.disposed
    assert scenes.disposed
    assert [it.key for it in again.collection.items] == ["cloud"]
    assert "scene" not in again.collection.items[0].meta  # resolved for a gallery again
    assert ctx.health_of("kobi")["status"] != "error"


# -- in the app's window: the new host takes the view's slot ----------------------------
@needs_testdata
def test_in_the_app_the_new_host_takes_the_views_slot(app, spin):
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "3d",
        "label": "Cam A",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    pin(doc, "kobi")
    submit(app, doc, "3d")
    spin(until=lambda: app.hosts.slot_of("kobi") is not None, timeout=5)
    old = app.hosts.hosts["kobi"]

    doc = app.live.document()
    doc["views"]["kobi"]["type"] = "gallery"
    submit(app, doc, "kobi as gallery")
    spin(until=lambda: isinstance(app.hosts.hosts.get("kobi"), GalleryHost))
    new = app.hosts.hosts["kobi"]
    assert new is not old and old.disposed
    slot = app.hosts.slot_of("kobi")
    assert slot is not None and new.item.parentItem() is slot and new.item.isVisible()
