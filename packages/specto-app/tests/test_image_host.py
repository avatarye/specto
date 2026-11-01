"""Seam 2 (#46): the image view on ports and the gallery gestures around it, headless.
The image host shows the selected item of its collection and emits a selection when the
user steps; the tile menu's "Open image" opens an image view beside the gallery once
(a double-click or Enter opens the preview since #115); the gallery's inline sort
buttons reorder its items through ``params.sort``."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_doc_helpers import pin
from gallery_helpers import FakeGallery, copy_frames, needs_testdata

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.file_tree import FileTreeFactory
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_app.views.image_view import ImageFactory, ImageHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, Selection

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


def frames(root: Path, n: int = 4) -> Collection:
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(n):
        p = root / f"{i:05d}_17000000000{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes(16) + b"\xff\xd9")
        items.append(
            Item(
                key=f"{i:05d}",
                parts={".jpg": str(p)},
                meta={"rel": {".jpg": p.name}, "root": str(root), "mtime_ns": 1000 - i},
            )
        )
    return Collection(items=items, root=str(root))


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager())


def test_image_host_shows_the_selected_item_and_emits_the_selection_it_steps_to(
    engine: Engine, tmp_path: Path
):
    ctx = HostContext(engine, None)
    doc = engine.live.document()
    doc["views"]["tamu"] = {"type": "image", "label": "Cam A image", "layout": {}}
    pin(doc, "tamu")
    engine.live.submit(doc, origin="agent:s1", label="image")
    host = ImageHost(ctx, "tamu", ctx.manifest_for("image"), headless=True)
    assert host.key is None and ctx.health_of("tamu")["status"] == "paused"

    col = frames(tmp_path / "front")
    host.receive("data", col)
    assert host.key == "00000" and host.index == 0  # nothing picked yet: the first item
    assert host.url_of(0).endswith("00000_170000000000.jpg") and host.url_of(0).startswith("file:")
    assert host.caption_of(0) == "00000_170000000000.jpg"  # the footer shows n / N (#126)
    assert ctx.links.output_of("tamu", "selection") is None

    # a selection arriving on the port is shown, never re-emitted
    host.receive("selection", Selection(keys=["00002"], last="00002"))
    assert host.key == "00002" and ctx.links.output_of("tamu", "selection") is None
    host.receive("selection", Selection(keys=["99999"], last="99999"))
    assert host.key == "00002"  # unknown: unchanged

    # stepping (arrows, the footer buttons) is the user's pick: emitted
    assert host.step_item(1) == 3 and host.key == "00003"
    assert ctx.links.output_of("tamu", "selection") == Selection(keys=["00003"], last="00003")
    assert host.step_item(5) == 3  # clamped: nothing new emitted
    host.on_view_changed(2.0, False, 0.25, 0.75)
    snap = host.snapshot()
    assert snap["selected"] == {"index": 3, "key": "00003"} and snap["count"] == 4
    assert snap["zoom"] == 2.0 and snap["fit"] is False and snap["center"] == [0.25, 0.75]

    # a selection that arrives before the data is honoured once the data lands
    host.receive("data", None)
    assert host.key is None and ctx.health_of("tamu")["status"] == "paused"
    host.receive("selection", Selection(keys=["00001"], last="00001"))
    host.receive("data", col)
    assert host.key == "00001"


def gallery_fed(engine: Engine) -> tuple[HostRegistry, dict]:
    clear_pending()
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    class HeadlessImage(ImageFactory):
        def create(self, ctx, view_id, manifest):
            return ImageHost(ctx, view_id, manifest, headless=True)

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())
    reg.add_factory(HeadlessImage())
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {"size": [600, 400]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery shows the cam-a frames."},
    }
    engine.live.submit(doc, origin="agent:s1", label="gallery")
    return reg, fakes


@needs_testdata
def test_the_tile_menu_opens_the_image_view_beside_the_gallery_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 6)
    engine = Engine(project=proj, jobs=InlineJobManager())
    reg, fakes = gallery_fed(engine)
    gallery = reg.host("kobi")
    tree = reg.host("fege")
    assert isinstance(gallery, GalleryHost) and tree is not None  # the tree emits all files
    fake = fakes["kobi"]
    gallery.pump()
    assert fake.count() == 6 and list(engine.live.data["views"]) == ["fege", "kobi"]

    gallery.bridge.openImage(3)  # "Open image" in the fourth tile's menu

    doc = engine.live.data
    (vid,) = [v for v in doc["views"] if v not in ("fege", "kobi")]  # the app named it
    assert doc["views"][vid]["type"] == "image" and doc["views"][vid]["label"] == "Cam A image"
    assert doc["views"][vid]["layout"]["position"] == [620.0, 0.0]
    assert engine.live.history[-1].origin == "user"
    image = reg.host(vid)
    assert isinstance(image, ImageHost)
    assert image.keys == gallery.keys and image.key == "00003"  # same intaker, the pick
    assert engine.links.output_of("kobi", "selection") == Selection(keys=["00003"], last="00003")
    # the next one only moves the pick; the image view stays the one
    gallery.bridge.openImage(1)
    assert list(engine.live.data["views"]) == ["fege", "kobi", vid] and image.key == "00001"
    # stepping in the image view is its own pick; the follow link back is two-way (#41)
    image.step_item(1)
    assert engine.links.output_of(vid, "selection").last == "00002"
    assert engine.live.data["links"]["l4"]["two_way"] is True


def test_gallery_sort_buttons_reorder_the_items_through_params(engine: Engine, tmp_path: Path):
    ctx = HostContext(engine, None)
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {}}
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="gallery")
    fake = FakeGallery()
    host = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake)
    col = frames(tmp_path / "front")  # keys 00000..00003; mtime newest first
    host.receive("data", col)
    assert fake.captions[0] == ["00000", "00001", "00002", "00003"] and host.sort == ""

    n = len(engine.live.history)
    host.set_sort("key")
    assert host.sort == "key" and fake.captions[0] == ["00000", "00001", "00002", "00003"]
    host.set_sort("key")  # the active sort again: flip the direction
    assert host.sort == "-key" and fake.captions[0] == ["00003", "00002", "00001", "00000"]
    assert engine.live.data["views"]["kobi"]["params"]["sort"] == "-key"
    assert len(engine.live.history) == n + 2 and engine.live.history[-1].origin == "user"
    assert engine.live.history[-1].label == "sort Cam A by key, descending"

    host.set_sort("time")  # oldest first: the mtimes run backwards in this collection
    assert fake.captions[0] == ["00003", "00002", "00001", "00000"]
    host.set_sort("name")
    assert fake.captions[0] == ["00000", "00001", "00002", "00003"]
    assert host.snapshot()["sort"] == "name"

    # an agent's params edit sorts too; the selection follows its item
    fake.user_click(0)  # 00000
    doc = engine.live.document()
    doc["views"]["kobi"]["params"]["sort"] = "-name"
    engine.live.submit(doc, origin="agent:s1", label="newest first")
    host.on_patch([{"op": "replace", "path": "/views/kobi/params/sort", "value": "-name"}], {})
    assert fake.captions[0] == ["00003", "00002", "00001", "00000"] and fake.selected() == 3
    # a new collection keeps the order
    host.receive("data", frames(tmp_path / "front", 5))
    assert fake.captions[0][:2] == ["00004", "00003"]


def real_frames(root: Path, n: int) -> list[Item]:
    """``n`` decodable JPEGs, keys 00000.. (the QML Image must reach Ready)."""
    from PySide6.QtGui import QColor, QImage

    root.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(n):
        p = root / f"{i:05d}_17000000000{i}.jpg"
        img = QImage(64, 48, QImage.Format.Format_RGB32)
        img.fill(QColor(20 * i, 80, 160))
        assert img.save(str(p))
        items.append(Item(key=f"{i:05d}", parts={".jpg": str(p)}, meta={"root": str(root)}))
    return items


def test_growing_picks_keep_the_shown_image_and_leave_loading(engine: Engine, tmp_path, spin):
    """#116: the picks grow from one frame to eight; the image view keeps showing the
    frame it showed (its image already decoded) and says ``ok``, never stuck on
    ``loading``."""
    from PySide6.QtQml import QQmlEngine

    doc = engine.live.document()
    doc["views"]["tamu"] = {"type": "image", "label": "Cam A image", "layout": {}}
    pin(doc, "tamu")
    engine.live.submit(doc, origin="agent:s1", label="image")
    ctx = HostContext(engine, QQmlEngine())
    host = ImageHost(ctx, "tamu", ctx.manifest_for("image"))
    host.item.setWidth(400)
    host.item.setHeight(300)
    items = real_frames(tmp_path / "front", 10)
    root = str(tmp_path / "front")

    def settled() -> bool:
        return ctx.health_of("tamu")["status"] != "loading"

    host.receive("data", Collection(items=[items[2]], root=root))
    spin(until=settled)
    assert ctx.health_of("tamu") == {"status": "ok", "message": "00002 64x48", "hint": ""}

    # the picks grow from the shown frame on: same item, same image
    host.receive("data", Collection(items=items[2:10], root=root))
    spin(until=settled)
    assert host.key == "00002" and ctx.health_of("tamu")["status"] == "ok"

    # they grow before it too: the view stays on the frame it shows
    host.receive("data", Collection(items=items[:8], root=root))
    spin(until=settled)
    assert host.key == "00002" and host.index == 2
    assert ctx.health_of("tamu") == {"status": "ok", "message": "00002 64x48", "hint": ""}
    host.dispose()
