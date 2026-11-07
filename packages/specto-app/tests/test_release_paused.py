"""Paused views release their GPU memory (SPEC-3; contracts section 16): a paused host
whose item can rebuild itself (``release_when_paused``) has the item taken out of the
scene, so the window deletes its scene-graph nodes and the textures, atlases and render
targets they hold; on resume the item goes back into its slot with its state. Offscreen
Qt; the GPU memory itself is measured by tools/bench_gpu_release.py on the GPU tier."""

from __future__ import annotations

import pytest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin
from gallery_helpers import FakeGallery

from specto_app.hosts.base import QuickViewHost

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


def submit(app, doc: dict, label: str) -> dict:
    return app.engine.call("canvas.submit", {"document": doc, "label": label}, AGENT)


def images(app, n: int = 12) -> None:
    from PIL import Image

    root = app.engine.project.root / "frames"
    root.mkdir(exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (20 * i % 255, 90, 160)).save(root / f"f{i:03}.png")


def gallery(app, spin, vid: str = "kobi", position: tuple = (300, 40)) -> QuickViewHost:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": "Frames",
        "layout": {"position": list(position), "size": [640, 400]},
    }
    pin(doc, vid, sources=["frames"])
    submit(app, doc, "a gallery")
    spin(until=lambda: vid in app.hosts.hosts and app.hosts.hosts[vid].gallery.count() == 12)
    return app.hosts.hosts[vid]


def in_scene(app, host: QuickViewHost, vid: str) -> bool:
    item = host.item
    return item.window() is app.window and item.parentItem() is app.hosts.slot_of(vid)


def test_a_paused_gallery_leaves_the_scene_and_comes_back_as_it_was(app, spin):
    images(app)
    host = gallery(app, spin)
    assert host.release_when_paused
    assert in_scene(app, host, "kobi")
    host.gallery.select(5)
    host.gallery.set_view(2, 1.5)
    view = host.gallery.view()

    app.controller.panBy(3000, 0)
    spin(until=lambda: app.hosts.is_paused("kobi"))
    assert app.hosts.is_released("kobi")
    assert host.item.window() is None and host.item.parentItem() is None
    assert app.controller.views["kobi"]["health"] == "paused"

    app.controller.home()
    spin(until=lambda: not app.hosts.is_paused("kobi"))
    assert not app.hosts.is_released("kobi")
    assert in_scene(app, host, "kobi")
    slot = app.hosts.slot_of("kobi")
    assert (host.item.width(), host.item.height()) == (slot.width(), slot.height())
    assert host.gallery.selected() == 5 and host.gallery.view() == view
    assert app.controller.views["kobi"]["health"] == "ok"


def test_the_overworld_releases_every_view_and_puts_each_back(app, spin):
    images(app)
    host = gallery(app, spin)
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    assert app.hosts.is_released("kobi") and host.item.window() is None
    ow.toggle()
    spin(until=lambda: not ow.active and not app.hosts.is_paused("kobi"))
    assert in_scene(app, host, "kobi")


def test_a_view_that_keeps_its_gpu_memory_stays_in_the_scene_while_paused(app, spin):
    images(app)
    host = gallery(app, spin)
    host.release_when_paused = False  # as a host whose engine cannot rebuild
    app.controller.panBy(3000, 0)
    spin(until=lambda: app.hosts.is_paused("kobi"))
    assert not app.hosts.is_released("kobi")
    assert host.item.window() is app.window
    app.controller.home()
    spin(until=lambda: not app.hosts.is_paused("kobi"))
    assert in_scene(app, host, "kobi")


def test_a_slot_rebuilt_while_released_gets_the_item_only_on_resume(app, spin):
    images(app)
    host = gallery(app, spin)
    app.controller.panBy(3000, 0)
    spin(until=lambda: app.hosts.is_released("kobi"))
    old = app.hosts.slot_of("kobi")
    if old is not None:
        app.hosts.detach("kobi", old)
    from PySide6.QtQuick import QQuickItem

    new = QQuickItem(app.window.contentItem())
    new.setWidth(320)
    new.setHeight(200)
    assert app.hosts.attach("kobi", new)
    assert host.item.parentItem() is None  # still paused: the item stays out
    app.hosts.resume("kobi")
    assert host.item.parentItem() is new and host.item.width() == 320
    app.hosts.detach("kobi", new)
    new.deleteLater()


def test_a_paused_image_lets_go_of_its_picture_and_shows_it_again_as_it_was(app, spin):
    from PIL import Image
    from PySide6.QtQuick import QQuickItem

    root = app.engine.project.root / "photo"
    root.mkdir()
    Image.new("RGB", (1600, 1200), (200, 120, 40)).save(root / "big.png")
    doc = app.live.document()
    doc["views"]["dena"] = {
        "type": "image",
        "label": "Photo",
        "layout": {"position": [300, 40], "size": [640, 400]},
    }
    pin(doc, "dena", sources=["photo"])
    submit(app, doc, "an image")
    spin(until=lambda: "dena" in app.hosts.hosts)
    host = app.hosts.hosts["dena"]
    img = host.item.findChild(QQuickItem, "image")
    spin(until=lambda: host.image_status == "ready", timeout=10)
    item = host.item
    item.setProperty("navZoom", 0.8)
    item.setProperty("navCx", 0.3)
    item.setProperty("navCy", 0.6)
    item.setProperty("navFit", False)
    item.setProperty("navSerial", item.property("navSerial") + 1)
    spin(until=lambda: abs(host.zoom - 0.8) < 1e-6)
    center = host.center
    assert not host.release_when_paused  # the texture follows the picture, not the node

    app.controller.panBy(3000, 0)
    spin(until=lambda: app.hosts.is_paused("dena"))
    assert img.property("source").toString() == ""  # the picture and its texture let go
    assert item.property("source").toString().endswith("big.png")  # what to show stays
    assert app.controller.views["dena"]["health"] == "paused"

    app.controller.home()
    spin(until=lambda: not app.hosts.is_paused("dena"))
    spin(until=lambda: host.image_status == "ready", timeout=10)
    assert img.property("source").toString().endswith("big.png")
    assert abs(host.zoom - 0.8) < 1e-6 and not host.fit
    assert all(abs(a - b) < 1e-6 for a, b in zip(host.center, center, strict=True))
    assert app.controller.views["dena"]["health"] == "ok"


def test_a_resumed_gallery_decodes_its_thumbnails_again_after_its_first_frame(tmp_path, qapp):
    """The atlas went with the scene graph: the first frame back in the scene reports
    nothing resident, and the pump decodes the visible thumbnails again."""
    from PIL import Image

    from specto_app.hosts import HostContext
    from specto_app.views.gallery import GalleryHost
    from specto_core import Engine, Project
    from specto_core.jobs import InlineJobManager
    from specto_core.kinds import Item
    from specto_core.links import Collection

    project = Project.init(tmp_path / "proj")
    root = project.root / "frames"
    root.mkdir()
    items = []
    for i in range(8):
        p = root / f"f{i}.png"
        Image.new("RGB", (64, 48), (30 * i, 90, 160)).save(p)
        st = p.stat()
        items.append(
            Item(
                key=p.stem,
                parts={".png": str(p)},
                meta={"rel": {".png": p.name}, "stat": {".png": [st.st_size, st.st_mtime_ns]}},
            )
        )
    ctx = HostContext(Engine(project=project, jobs=InlineJobManager()), None)
    fake = FakeGallery()
    host = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake)
    host.receive("data", Collection(items=items, root=str(root)))
    host.pump()
    qapp.processEvents()  # the pump the collection scheduled
    assert len(fake.pushed) == 8

    host.pause()
    fake.drop_atlas()  # what leaving the scene does to the native item
    host.resume()
    qapp.processEvents()
    assert not fake.pushed  # the item has not drawn back in the scene yet
    fake.frame()
    qapp.processEvents()
    assert len(fake.pushed) == 8
