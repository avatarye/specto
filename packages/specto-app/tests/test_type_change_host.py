"""SPEC-48: a submit that keeps a view's id but changes its type gets the new type's
host. The registry handed the patch to the old host (a MissingHost for a type with no
host here, or the previous type's host) and never built the new one. The new host's
links resolve again for the new type (an intaker feeding a 3d view prepares scenes, a
Python link's table is rebuilt, a resolve in flight for the old type never lands), and
a paused view comes back paused with the reason it had."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import test_shell
from app_doc_helpers import pin
from gallery_helpers import FakeGallery, needs_testdata
from jobs_helpers import HeldJobs
from scene3d_helpers import write_cloud_ply
from test_shell import submit

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.hosts.base import QuickViewHost, SimpleFactory
from specto_app.hosts.registry import MissingHost
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import LINK_JOB_KIND, Collection, Selection
from specto_core.python_links import TrustStore, sha256_of

frames_project = test_shell.frames_project
app = test_shell.app

LINK_FILE = ".specto/links/scene.py"
#: a per-pick link that hands its target the cloud, whatever was picked
SCENE_LINK = (
    "def resolve(value, items, params, root):\n"
    '    return [{"parts": {".ply": "scans/cloud.ply"}}]\n'
)


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

    def collections(self) -> list[Collection]:
        return [v for p, v in self.received if p == "data" and isinstance(v, Collection)]


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs, *, runtime_first=False):
    """The world; ``runtime_first`` starts the link runtime before the registry (the
    app's order, app.py: its patch listener then hears a submit first)."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))  # the trust store
    proj = Project.init(tmp_path / "proj", "scans")
    (proj.root / "scans").mkdir(parents=True, exist_ok=True)
    write_cloud_ply(proj.root / "scans" / "cloud.ply")
    engine = Engine(project=proj, jobs=jobs)
    clear_pending()
    fakes: list[FakeGallery] = []

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes.append(FakeGallery())
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[-1])

    ctx = HostContext(engine, None)
    if runtime_first:
        ctx.links  # noqa: B018
    reg = HostRegistry(ctx, load_entry_points=False)  # no host for "3d" unless a test adds one
    reg.add_factory(Factory())
    ctx.links  # noqa: B018  (the runtime starts now, not at the first host)
    return engine, ctx, reg


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine, ctx, reg = _world(tmp_path, monkeypatch, InlineJobManager())
    yield engine, ctx, reg
    reg.dispose_all()
    engine.close()


@pytest.fixture
def held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The same world, every resolve held until the test runs it."""
    jobs = HeldJobs()
    engine, ctx, reg = _world(tmp_path, monkeypatch, jobs)
    yield engine, ctx, reg, jobs
    reg.dispose_all()
    engine.close()


def _submit(
    engine: Engine,
    vtype: str,
    label: str = "kobi",
    *,
    linked: bool = True,
    origin: str = "agent:s1",
) -> None:
    doc = engine.live.document()
    view = doc["views"].get("kobi")
    doc["views"]["kobi"] = {**(view or {"layout": {"size": [600, 400]}}), "type": vtype}
    doc["views"]["kobi"]["label"] = label
    if view is None and linked:
        pin(doc, "kobi", sources=["scans"])  # the project's scans feed it, once
    engine.live.submit(doc, origin=origin, label=f"kobi as {vtype}")


def _python_link(engine: Engine) -> None:
    """kobi (a gallery) fed by an approved per-pick Python link from lami (a gallery of
    the scans): a pick hands kobi the cloud."""
    root = engine.project.root
    (root / LINK_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / LINK_FILE).write_text(SCENE_LINK, encoding="utf-8")
    TrustStore().approve(sha256_of((root / LINK_FILE).read_bytes()), path=LINK_FILE)
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "kobi", "layout": {"size": [600, 400]}}
    doc["views"]["lami"] = {"type": "gallery", "label": "lami", "layout": {"size": [600, 400]}}
    pin(doc, "lami", sources=["scans"])
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "lami", "port": "selection"},
        "target": {"view": "kobi", "port": "data"},
        "python": {"path": LINK_FILE, "resolves_to": "value", "output": "collection"},
        "summary": {"sentence": "A pick in lami hands kobi the cloud."},
    }
    engine.live.submit(doc, origin="agent:s1", label="python")


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
    cols = scenes.collections()
    assert len(cols) == 1, scenes.received  # the fresh resolve alone, nothing stale replayed
    assert cols[0].items[0].meta["scene"]["kind"] == "pointcloud"
    assert ctx.links.input_of("kobi", "data") is cols[0]


def test_a_python_links_table_is_rebuilt_for_the_new_type(setup):
    """A Python link caches its table under a key without the target type, so without
    the fresh mark the new host would get the table built (and prepared) for the old
    type."""
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _python_link(engine)
    gallery = reg.host("kobi")
    ctx.links.emit("lami", "selection", Selection(keys=["cloud"], last="cloud"))
    assert ctx.links.status("l9")["state"] == "resolved", ctx.links.status("l9")
    assert [it.key for it in gallery.collection.items] == ["scans/cloud"]
    assert "scene" not in gallery.collection.items[0].meta

    _submit(engine, "3d")  # the same pick, run again for a 3d view: scenes prepared
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost)
    cols = scenes.collections()
    assert len(cols) == 1, scenes.received
    assert cols[0].items[0].meta["scene"]["kind"] == "pointcloud"


def test_a_resolve_in_flight_across_the_type_change_never_lands(held):
    """A resolve running for the old type when the type changes: its result is dropped
    (it was prepared for the old type) and the link resolves again for the new one."""
    engine, ctx, reg, jobs = held
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    while jobs.count(LINK_JOB_KIND):
        jobs.run_next(LINK_JOB_KIND)
    gallery = reg.host("kobi")
    assert [it.key for it in gallery.collection.items] == ["cloud"]

    (lid,) = ctx.links.refresh("kobi")  # a rescan for the gallery, held open
    assert jobs.count(LINK_JOB_KIND) == 1 and ctx.links.resolving(lid)
    _submit(engine, "3d")  # the type changes under it
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost) and scenes.collections() == []

    jobs.run_next(LINK_JOB_KIND)  # the gallery's rescan lands: dropped, resolved again
    assert scenes.collections() == [] and ctx.links.input_of("kobi", "data") is None
    assert jobs.count(LINK_JOB_KIND) == 1 and ctx.links.resolving(lid)
    jobs.run_next(LINK_JOB_KIND)
    cols = scenes.collections()
    assert len(cols) == 1 and cols[0].items[0].meta["scene"]["kind"] == "pointcloud"
    assert ctx.links.input_of("kobi", "data") is cols[0]


def test_a_view_retyped_before_its_host_is_built_gets_the_new_types_input(setup):
    """No host yet (in the app: the tick before the slots attach), so the registry has
    nothing to rebuild; the link runtime sees the type op itself and resolves again, and
    the host built afterwards is handed scenes, not the gallery's collection."""
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    assert "scene" not in ctx.links.input_of("kobi", "data").items[0].meta

    _submit(engine, "3d")
    assert ctx.links.input_of("kobi", "data").items[0].meta["scene"]["kind"] == "pointcloud"
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost)
    cols = scenes.collections()
    assert len(cols) == 1 and cols[0].items[0].meta["scene"]["kind"] == "pointcloud"


@pytest.mark.parametrize("runtime_first", [False, True], ids=["registry", "runtime"])
def test_a_type_change_resolves_once_whoever_hears_it_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_first: bool
):
    """The registry (rebuilding a host) and the link runtime (its own patch listener)
    both see the type change, in either order (the app starts the runtime first, the
    other tests the registry); the links resolve again once."""
    jobs = HeldJobs()
    engine, ctx, reg = _world(tmp_path, monkeypatch, jobs, runtime_first=runtime_first)
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery")
    while jobs.count(LINK_JOB_KIND):
        jobs.run_next(LINK_JOB_KIND)
    reg.host("kobi")

    try:
        _submit(engine, "3d")
        assert jobs.count(LINK_JOB_KIND) == 1
        assert ctx.links.retarget("kobi") == []  # already retargeted for this type
        assert jobs.count(LINK_JOB_KIND) == 1
        scenes = reg.host("kobi")
        assert isinstance(scenes, ScenesHost) and scenes.collections() == []
        jobs.run_next(LINK_JOB_KIND)
        cols = scenes.collections()
        assert len(cols) == 1 and cols[0].items[0].meta["scene"]["kind"] == "pointcloud"
    finally:
        reg.dispose_all()
        engine.close()


def test_a_python_resolve_in_flight_across_the_type_change_never_lands(held):
    """The Python link's run for the gallery is held open across the type change: its
    table (built and prepared for the gallery) is dropped and the link runs again for
    the 3d view."""
    engine, ctx, reg, jobs = held
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _python_link(engine)
    while jobs.count(LINK_JOB_KIND):
        jobs.run_next(LINK_JOB_KIND)
    reg.host("kobi")
    ctx.links.emit("lami", "selection", Selection(keys=["cloud"], last="cloud"))
    assert jobs.count() >= 1 and ctx.links.resolving("l9")
    python_kind = next(job.kind for job, *_r in jobs.held if job.canvas == "l9")

    _submit(engine, "3d")
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost) and scenes.collections() == []
    jobs.run_next(python_kind)  # the gallery's run lands: dropped, and run again
    assert scenes.collections() == [] and ctx.links.input_of("kobi", "data") is None
    assert ctx.links.resolving("l9")
    jobs.run_next(python_kind)
    cols = scenes.collections()
    assert len(cols) == 1 and cols[0].items[0].meta["scene"]["kind"] == "pointcloud"


def test_a_view_with_no_incoming_links_changes_type_too(setup):
    engine, ctx, reg = setup
    reg.add_factory(SimpleFactory("3d", ScenesHost))
    _submit(engine, "gallery", linked=False, origin="user")  # a user may leave a view unlinked
    gallery = reg.host("kobi")
    assert isinstance(gallery, GalleryHost) and ctx.links.inputs_of("kobi") == {}

    _submit(engine, "3d", origin="user")
    scenes = reg.host("kobi")
    assert isinstance(scenes, ScenesHost) and gallery.disposed
    assert scenes.received == [] and ctx.links.inputs_of("kobi") == {}
    assert ctx.health_of("kobi")["status"] != "error"


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
