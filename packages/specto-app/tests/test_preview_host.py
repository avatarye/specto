"""Seam 2 (#106): the preview view on ports, headless. A preview takes a collection on
``data`` and a pick on ``selection`` and renders the picked item (or the first) with the
host of its kind: the image, pdf, markdown, json, html, video or 3D view's own host,
never a copy. A pick of another kind drops the renderer it had and makes the new one;
an item no renderer shows gets a quiet card with its name and size."""

from __future__ import annotations

from pathlib import Path

import pytest
from pdf_helpers import make_pdf
from PySide6.QtCore import Qt
from scene3d_helpers import write_cloud_ply
from web_helpers import new_engine, registry

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.views.image_view import ImageHost
from specto_app.views.markdown_view import MarkdownViewHost
from specto_app.views.pdf_view import PdfHost
from specto_app.views.preview_view import PreviewHost
from specto_app.views.scene3d_view import Scene3DHost
from specto_app.views.video_view import VideoHost
from specto_core import Engine
from specto_core.links import Selection


def seed_mixed(root: Path) -> Path:
    """One file of each kind under ``mixed/``: a.jpg, b.pdf, c.md, d.mp4, e.ply, f.bin."""
    mixed = root / "mixed"
    mixed.mkdir(parents=True, exist_ok=True)
    (mixed / "a.jpg").write_bytes(b"\xff\xd8\xff" + bytes(16) + b"\xff\xd9")
    make_pdf(mixed / "b.pdf", 2, title="b")
    (mixed / "c.md").write_text("# C\n\nA note.\n", encoding="utf-8")
    (mixed / "d.mp4").write_bytes(bytes(64))  # headless: the video host never opens it
    write_cloud_ply(mixed / "e.ply")
    (mixed / "f.bin").write_bytes(bytes(1500))
    return mixed


def compose_preview(engine: Engine) -> None:
    doc = engine.live.document()
    doc["views"]["pela"] = {"type": "preview", "label": "Look", "layout": {"size": [640, 480]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "pela", "port": "data"},
        "params": {"sources": ["mixed"]},
        "summary": {"sentence": "The preview shows the files under mixed/."},
    }
    engine.live.submit(doc, origin="agent:s1", label="a preview")


@pytest.fixture
def engine(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """QtPdf (and QPdfWriter's text) need a QGuiApplication, headless or not."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine, proj = new_engine(tmp_path)
    seed_mixed(proj.root)
    engine.links.resolve("l1")  # the project scan ran before the files were written
    compose_preview(engine)
    return engine


def pick(host: PreviewHost, key: str) -> None:
    host.receive("selection", Selection(keys=[key], last=key))


def item_named(item, name: str):
    """The visual descendant of ``item`` with that objectName, or None."""
    for child in item.childItems():
        if child.objectName() == name:
            return child
        found = item_named(child, name)
        if found is not None:
            return found
    return None


def test_each_pick_of_another_kind_drops_the_old_renderer_and_shows_the_item_in_the_new(
    engine: Engine,
):
    reg = registry(engine)
    host = reg.host("pela")
    assert isinstance(host, PreviewHost)
    # the first item until a pick says otherwise: the image, in the image view's own host
    image = host.renderer
    assert isinstance(image, ImageHost) and host.kind == "image" and host.key == "a"
    assert image.key == "a"

    pick(host, "b")
    pdf = host.renderer
    assert image.disposed and isinstance(pdf, PdfHost)
    assert host.kind == "pdf" and pdf.key == "b" and pdf.page_count == 2
    assert engine.links.output_of("pela", "selection") is None  # shown, not republished

    pick(host, "c")
    note = host.renderer
    assert pdf.disposed and isinstance(note, MarkdownViewHost)
    assert host.kind == "markdown" and note.current_key == "c"

    pick(host, "d")
    video = host.renderer
    assert note.disposed and isinstance(video, VideoHost)
    assert host.kind == "video" and video.key == "d"

    pick(host, "e")
    scene = host.renderer
    assert video.disposed and isinstance(scene, Scene3DHost)
    assert host.kind == "3d" and [e["id"] for e in scene.entries] == ["e"]
    assert scene.entries[0]["kind"] == "pointcloud"  # the intaker prepared it for 3D


def test_an_item_no_renderer_shows_gets_a_quiet_card_with_its_name_and_size(engine: Engine):
    reg = registry(engine)
    host = reg.host("pela")
    pick(host, "b")
    shown = host.renderer
    pick(host, "f")
    assert shown.disposed and host.renderer is None and host.kind is None
    assert host.card == {
        "name": "f.bin",
        "icon": "hex",  # the card's footer names the kind by its icon (#126)
        "size": "1.5K",
        "hint": "No preview for this kind of file: open it in its own view.",
    }
    assert engine.links.output_of("pela", "selection") is None
    snap = reg.snapshot("pela")
    assert snap["renderer"] is None and snap["card"]["name"] == "f.bin"
    assert snap["selected"] == {"index": 5, "key": "f"}
    # back to a kind it renders: the card goes, a renderer comes
    pick(host, "a")
    assert host.card is None and isinstance(host.renderer, ImageHost)


def test_a_pick_the_renderer_makes_goes_out_and_its_other_outputs_stay_inside(engine: Engine):
    """The markdown renderer gets the notes only (c.md, g.md); a wikilink to g is the
    preview's pick and goes out as its selection. The markdown renderer's content and
    the video's own selection on show never leave the preview."""
    proj_root = Path(engine.project.root)
    (proj_root / "mixed" / "g.md").write_text("# G\n\nBack to [[c]]\n", encoding="utf-8")
    engine.links.resolve("l1")
    engine.links.resolve("l2")
    reg = registry(engine)
    host = reg.host("pela")
    pick(host, "c")
    note = host.renderer
    assert isinstance(note, MarkdownViewHost) and list(note.items) == ["c", "g"]
    note.on_page_event("navigate", {"key": "g"})
    assert host.key == "g" and host.renderer is note and note.current_key == "g"
    assert engine.links.output_of("pela", "selection") == Selection(keys=["g"], last="g")
    assert engine.links.output_of("pela", "content") is None  # no content port

    pick(host, "d")  # the video emits itself on show: an echo of the pick, not a pick
    assert engine.links.output_of("pela", "selection") == Selection(keys=["g"], last="g")


def test_the_preview_steps_through_its_whole_collection_whatever_the_kind(engine: Engine):
    """#107's mechanism on the preview: previous / next go through every item in
    collection order, across kinds, stop at the ends and emit the item stepped to. The
    renderer's own controls and keys step the preview (not just the items of their
    kind), and they show where the preview is."""
    reg = registry(engine)
    host = reg.host("pela")
    assert host.step_keys() == ["a", "b", "c", "d", "e", "f"]
    assert host.step_state() == {"index": 0, "count": 6}
    assert host.step_item(1) == 1 and isinstance(host.renderer, PdfHost)
    assert engine.links.output_of("pela", "selection") == Selection(keys=["b"], last="b")
    assert host.step_item(-5) == 0 and isinstance(host.renderer, ImageHost)  # stopped
    image = host.renderer
    assert image.step_state() == {"index": 0, "count": 6}  # the preview's place

    # the image view's next (its footer, its keys) steps the preview into the pdf
    image.step_item(1)
    pdf = host.renderer
    assert image.disposed and isinstance(pdf, PdfHost) and host.key == "b"
    assert engine.links.output_of("pela", "selection") == Selection(keys=["b"], last="b")
    pdf.step_item(1)
    note = host.renderer
    assert isinstance(note, MarkdownViewHost) and host.key == "c"
    # a web renderer's controls hear the preview's place on its page
    assert note.page.pending()["notify"]["step"] == {"index": 2, "count": 6}
    assert host.step_item(9) == 5 and host.card is not None and host.renderer is None


def test_the_status_bar_names_the_step_keys_of_the_renderer_the_preview_shows(engine: Engine):
    from specto_app.canvas.controller import status_hint

    reg = registry(engine)
    host = reg.host("pela")
    assert host.step_type == "image"
    pick(host, "b")
    assert host.step_type == "pdf"
    hint = status_hint({"id": "pela", "type": "preview", "stepType": "pdf"}, hovered=True)
    assert "Left/Right previous / next item" in hint and "PageUp" not in hint


def test_the_renderer_item_fills_the_preview_and_leaves_it_on_a_switch(engine: Engine, spin):
    """With a QML engine: the image view's item fills the preview's frame; a pdf pick
    replaces it with the pdf view's item (the image item is gone), and a card hides the
    frame."""
    from PySide6.QtQml import QQmlEngine
    from PySide6.QtQuick import QQuickItem

    from specto_app.hosts import HostContext
    from specto_app.views.preview_view import PreviewHost

    qml = QQmlEngine()
    ctx = HostContext(engine, qml)
    host = PreviewHost(ctx, "pela", ctx.manifest_for("preview"))
    host.receive("data", engine.links.input_of("pela", "data"))
    slot = host.item.findChild(QQuickItem, "preview:pela:slot")
    host.item.setWidth(400)
    host.item.setHeight(300)
    spin(20)
    image_item = host.renderer.item
    card = host.item.findChild(QQuickItem, "preview:pela:card")
    assert slot.childItems() == [image_item] and slot.isVisible() and not card.isVisible()
    assert (image_item.width(), image_item.height()) == (400, 300)

    pick(host, "b")
    spin(20)
    pdf_item = host.renderer.item
    assert slot.childItems() == [pdf_item] and image_item.parentItem() is None
    host.item.setWidth(500)
    assert pdf_item.width() == 500

    assert slot.isVisible() and not card.isVisible()
    pick(host, "f")
    assert slot.childItems() == [] and not slot.isVisible()
    assert card.isVisible() and host.item.property("card")["name"] == "f.bin"
    # a card has no renderer, so it carries the previous / next controls itself
    assert (host.item.property("stepIndex"), host.item.property("stepCount")) == (5, 6)
    prev = item_named(host.item, "step:pela:card:prev")  # a Repeater's: not a QObject child
    assert prev is not None and prev.isVisible()
    host.bridge.step(-5)
    assert host.key == "a" and isinstance(host.renderer, ImageHost) and not card.isVisible()
    host.dispose()


def test_a_new_collection_with_the_same_items_of_the_shown_kind_leaves_the_renderer_be(
    engine: Engine,
):
    """More files of other kinds arrive (the user picks more in the tree): the pdf
    renderer's own items did not change, so it keeps its page instead of reloading."""
    reg = registry(engine)
    host = reg.host("pela")
    pick(host, "b")
    pdf = host.renderer
    pdf.on_view_changed(1, 1.5, "none")
    col = engine.links.input_of("pela", "data")
    host.receive("data", col.model_copy(update={"items": col.items[:3]}))
    assert host.renderer is pdf and pdf.page == 1


def test_a_gallery_tile_that_is_not_an_image_opens_the_gallery_preview(engine: Engine):
    """#106 with #105's mixed galleries: Enter or a double-click on a pdf tile opens a
    preview beside the gallery showing that pdf (not an image view upscaling its
    thumbnail); an image tile opens the same preview (#115: every kind does; the image
    view is the tile menu's and the agent's). The quick look and the hover
    preview of a tile that is not an image go to the preview too (the bridge says which
    tiles are images)."""
    from gallery_helpers import FakeGallery

    from specto_app.hosts import HostContext, HostRegistry, clear_pending
    from specto_app.views import install
    from specto_app.views.gallery import GalleryFactory, GalleryHost

    clear_pending()
    install()
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    reg = HostRegistry(HostContext(engine, None), load_entry_points=False)
    reg.add_factory(Factory())
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Mixed", "layout": {"size": [600, 400]}}
    doc["links"]["l3"] = {**doc["links"]["l2"], "target": {"view": "kobi", "port": "data"}}
    engine.live.submit(doc, origin="agent:s1", label="a mixed gallery")
    gallery = reg.host("kobi")
    gallery.pump()
    pdf, image = gallery.index_of("b"), gallery.index_of("a")
    assert gallery.bridge.isImage(image) and not gallery.bridge.isImage(pdf)

    before = set(engine.live.data["views"])
    fakes["kobi"].user_activate(pdf)
    (vid,) = set(engine.live.data["views"]) - before
    assert engine.live.data["views"][vid]["type"] == "preview"
    shown = reg.host(vid)
    assert isinstance(shown, PreviewHost) and shown.key == "b"
    assert isinstance(shown.renderer, PdfHost)

    fakes["kobi"].user_activate(image)  # an image tile: the same preview, no image view
    types = sorted(v["type"] for v in engine.live.data["views"].values())
    assert types.count("image") == 0 and types.count("preview") == 2  # pela and the new one
    assert shown.key == "a"  # the preview follows the gallery's pick as well

    gallery.bridge.openPreview(pdf)  # the quick look of a pdf tile: the same preview
    assert sorted(v["type"] for v in engine.live.data["views"].values()) == types
    assert shown.key == "b"


def test_a_double_click_puts_the_preview_on_the_tree_picks_and_later_clicks_change_it(
    engine: Engine,
):
    """#122: a Ctrl+double-click on a file row (its first click picks the row) re-points the
    preview, pinned to mixed/ here, at the file tree's picks in one submit: it shows the
    double-clicked file, and a later single click on another row shows that one (no
    submit), in the renderer of its kind."""
    from specto_app.views.file_tree import FileTreeHost

    reg = registry(engine)
    tree, preview = reg.host("fege"), reg.host("pela")
    assert isinstance(tree, FileTreeHost) and isinstance(preview, PreviewHost)
    tree.expand_path("mixed")  # listed on first open (#231)
    rows = {r["path"]: i for i, r in enumerate(tree.rows())}
    steps = len(engine.live.history)

    tree.click(rows["mixed/c.md"])
    ctrl = int(Qt.KeyboardModifier.ControlModifier.value)  # Ctrl+double-click (#43)
    opened = tree.open_row(rows["mixed/c.md"], ctrl)
    assert opened is not None and (opened.view, opened.action) == ("pela", "repointed")
    assert len(engine.live.history) == steps + 1
    assert Path(preview.part).name == "c.md" and isinstance(preview.renderer, MarkdownViewHost)

    tree.click(rows["mixed/b.pdf"])  # a later single click: the preview follows it
    assert Path(preview.part).name == "b.pdf" and isinstance(preview.renderer, PdfHost)
    tree.click(rows["mixed/a.jpg"])
    assert Path(preview.part).name == "a.jpg" and isinstance(preview.renderer, ImageHost)
    assert len(engine.live.history) == steps + 1  # picks are navigation
