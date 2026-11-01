"""The native views' footers (#126, design 05 section 4): one 24 px ViewFooter shared by
every view, the step controls first (22 px round buttons, always visible with two or
more items) and the position "n / N", then what each view needs: the image's file
name, resolution and one zoom button; the pdf's name, page readout and one zoom
button; the gallery's picked item, position and one sort button. Offscreen Qt, the
real shell."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from specto_core import CallContext, Project

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


def pictures(folder: Path, n: int, size=(64, 48)) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = QImage(*size, QImage.Format.Format_RGB32)
        img.fill(QColor(30 * i, 90, 160))
        assert img.save(str(folder / f"{i:05d}.jpg"))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    pictures(proj.root / "front", 3)
    return proj


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def text(app, name: str) -> str:
    return item(app, name).property("text")


def click(app, spin, name: str) -> None:
    it = item(app, name)
    p = it.mapToScene(it.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    spin(10)


def add_view(app, spin, vid: str, vtype: str, sources: list[str], size=(480, 360)) -> None:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": vtype,
        "label": vid.title(),
        "layout": {"position": [380, 40], "size": list(size)},
    }
    pin(doc, vid, sources=sources)
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {vid}"}, AGENT)
    spin(until=lambda: vid in app.hosts.hosts)


def test_the_image_footer_steps_and_shows_position_name_resolution_and_one_zoom(app, spin):
    add_view(app, spin, "zila", "image", ["front"])
    footer = item(app, "footer:zila")
    spin(until=lambda: text(app, "position:zila") == "1 / 3")
    assert footer.height() == 24
    prev, nxt = item(app, "step:zila:prev"), item(app, "step:zila:next")
    assert prev.width() == prev.height() == 22 and prev.property("radius") == 11
    assert prev.property("name") == "chevron-left" and nxt.property("name") == "chevron-right"
    assert prev.property("disabled") and not nxt.property("disabled")  # at the first item
    assert prev.property("tip") == "previous (Left or PageUp)"
    assert nxt.property("tip") == "next (Right or PageDown)"
    spin(until=lambda: text(app, "image:zila:caption") == "00000.jpg")
    spin(until=lambda: text(app, "resolution:zila") == "64\N{MULTIPLICATION SIGN}48")

    click(app, spin, "step:zila:next")
    spin(until=lambda: text(app, "position:zila") == "2 / 3")
    assert text(app, "image:zila:caption") == "00001.jpg"

    # one zoom button: it reads the zoom, a click goes between fit and 1:1
    zoom = item(app, "zoom:zila")
    image = app.hosts.hosts["zila"].item
    spin(until=lambda: image.property("fit"))
    fitted = f"{round(image.property('zoom') * 100)}%"
    assert zoom.property("text") == fitted and fitted != "100%"
    click(app, spin, "zoom:zila")
    spin(until=lambda: zoom.property("text") == "100%")
    assert not image.property("fit")
    click(app, spin, "zoom:zila")
    spin(until=lambda: image.property("fit") and zoom.property("text") == fitted)


def test_the_pdf_footer_shows_name_page_readout_and_one_zoom_between_width_and_page(app, spin):
    from pdf_helpers import make_pdf

    make_pdf(Path(app.project.root) / "papers" / "a-report.pdf", 6)
    make_pdf(Path(app.project.root) / "papers" / "b-notes.pdf", 2)
    app.engine.links.resolve("l1")
    add_view(app, spin, "sari", "pdf", ["papers"])
    pdf = app.hosts.hosts["sari"].item
    spin(until=lambda: pdf.property("status") == "ready")
    assert text(app, "position:sari") == "1 / 2"
    # PageUp / PageDown turn the pages here: the arrows step the pdfs
    assert item(app, "step:sari:next").property("tip") == "next (Right)"
    assert text(app, "pdf:sari:caption") == "a-report.pdf"
    assert text(app, "pdf:sari:page") == "p 1 / 6"
    # the old page arrows, -/+ and the separate fit buttons are gone: one zoom button
    for gone in ("prev", "next", "zoomout", "zoomin", "fitw", "fitp"):
        assert find_item(app.window.contentItem(), f"pdf:sari:{gone}") is None
    zoom = item(app, "zoom:sari")
    assert pdf.property("fit") == "width"
    assert zoom.property("text") == f"{round(pdf.property('zoom') * 100)}%"
    click(app, spin, "zoom:sari")
    spin(until=lambda: pdf.property("fit") == "page")
    assert zoom.property("text") == f"{round(pdf.property('zoom') * 100)}%"
    click(app, spin, "zoom:sari")
    spin(until=lambda: pdf.property("fit") == "width")

    pdf.forceActiveFocus()
    QTest.keyClick(app.window, Qt.Key.Key_PageDown)
    spin(until=lambda: text(app, "pdf:sari:page") == "p 2 / 6")
    click(app, spin, "step:sari:next")
    spin(until=lambda: text(app, "pdf:sari:caption") == "b-notes.pdf")
    assert text(app, "position:sari") == "2 / 2"


def test_the_gallery_footer_shows_the_pick_its_position_and_one_sort_button(app, spin):
    add_view(app, spin, "kobi", "gallery", ["front"])
    host = app.hosts.hosts["kobi"]
    spin(until=lambda: host.gallery.item.property("count") == 3)
    footer = item(app, "footer:kobi")
    assert footer.height() == 24 and footer.isVisible()
    # a browser has no step controls: the picked item and where it is
    assert not item(app, "step:kobi:prev").isVisible()
    assert text(app, "position:kobi") == "\u2013 / 3"
    tiles = host.gallery.item
    p = tiles.mapToScene(tiles.rectOf(1).center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    spin(until=lambda: text(app, "position:kobi") == "2 / 3")
    assert text(app, "picked:kobi") == "00001"
    # the key / name / time pills and the "4 col 149 px" readout are gone: one sort button
    for gone in ("sort:key:kobi", "sort:name:kobi", "sort:time:kobi"):
        assert find_item(app.window.contentItem(), gone) is None
    sort = item(app, "sort:kobi")
    assert sort.property("text") == "sort"
    click(app, spin, "sort:kobi")
    spin(until=lambda: find_item(app.window.contentItem(), "menu:sort:kobi:name") is not None)
    click(app, spin, "menu:sort:kobi:name")
    spin(until=lambda: host.sort == "name")
    assert sort.property("text") == "name \u2193"
    # the active sort again flips the order
    click(app, spin, "sort:kobi")
    spin(until=lambda: item(app, "menu:sort:kobi:name").isVisible())
    click(app, spin, "menu:sort:kobi:name")
    spin(until=lambda: host.sort == "-name")
    assert sort.property("text") == "name \u2191"


def test_the_gallery_scroll_bar_is_a_thin_overlay_shown_while_tiles_overflow(app, spin):
    pictures(Path(app.project.root) / "many", 60)
    app.engine.links.resolve("l1")
    add_view(app, spin, "kobi", "gallery", ["many"], size=(420, 260))
    host = app.hosts.hosts["kobi"]
    spin(until=lambda: host.gallery.item.property("count") == 60)
    bar = item(app, "scrollbar:kobi")
    spin(until=lambda: bar.isVisible() and bar.property("opacity") == 1)
    assert bar.width() == 4
    gallery = host.gallery.item
    # over the tiles, inside the body's right edge
    right = gallery.mapToScene(gallery.boundingRect().topRight()).x()
    assert right - 4 <= bar.mapToScene(bar.boundingRect().topRight()).x() <= right


def test_the_preview_footer_names_the_kind_and_its_header_has_no_auto_update(app, spin):
    from pdf_helpers import make_pdf

    from specto_app.views.file_icons import icon_for

    make_pdf(Path(app.project.root) / "front" / "report.pdf", 6)
    app.engine.links.resolve("l1")
    add_view(app, spin, "tegi", "preview", ["front"])
    # the preview follows what it is fed: it has no auto-update and no refresh
    assert app.controller.views["tegi"]["autoUpdate"] is None
    assert not item(app, "autoUpdate:tegi").isVisible()
    # the renderer's own footer (the image's here) steps the preview's collection
    spin(until=lambda: find_item(app.window.contentItem(), "position:tegi") is not None)
    spin(until=lambda: text(app, "position:tegi") == "1 / 4")
    kind = item(app, "kindIcon:tegi")
    assert kind.isVisible() and kind.width() == 16
    assert kind.property("source").toString().endswith(f"/{icon_for('a.jpg', is_dir=False)}.svg")
    assert text(app, "image:tegi:caption") == "00000.jpg"
    # stepping onto the pdf: the pdf's footer, its icon, its page readout
    for _ in range(3):
        click(app, spin, "step:tegi:next")
    spin(until=lambda: find_item(app.window.contentItem(), "pdf:tegi:page") is not None)
    spin(until=lambda: text(app, "pdf:tegi:page") == "p 1 / 6")
    assert text(app, "position:tegi") == "4 / 4"
    kind = item(app, "kindIcon:tegi")
    assert kind.property("source").toString().endswith(f"/{icon_for('a.pdf', is_dir=False)}.svg")


def test_a_sort_menu_row_over_another_header_never_clicks_the_button_beneath(app, spin):
    add_view(app, spin, "kobi", "gallery", ["front"])
    doc = app.live.document()
    # sari's header buttons lie right under kobi's sort menu
    doc["views"]["sari"] = {
        "type": "gallery",
        "label": "Cam B",
        "layout": {"position": [380, 420], "size": [480, 300]},
    }
    pin(doc, "sari", sources=["front"])
    app.engine.call("canvas.submit", {"document": doc, "label": "add sari"}, AGENT)
    spin(until=lambda: "sari" in app.hosts.hosts)
    spin(until=lambda: app.hosts.hosts["kobi"].gallery.item.property("count") == 3)
    click(app, spin, "sort:kobi")
    spin(until=lambda: find_item(app.window.contentItem(), "menu:sort:kobi:time") is not None)
    spin(400)  # the menu settles
    rows = [item(app, f"menu:sort:kobi:{n}") for n in ("key", "name", "time")]
    covered = None
    for name in ("autoUpdate", "refresh", "lock", "float", "collapse", "maximize", "close"):
        button = item(app, f"{name}:sari")
        c = button.mapToScene(button.boundingRect().center())
        if any(r.mapRectToScene(r.boundingRect()).contains(c) for r in rows):
            covered = c
            break
    assert covered is not None, "no header button under the menu: move sari"
    before = app.live.document()["views"]["sari"]
    QTest.mouseClick(
        app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, covered.toPoint()
    )
    spin(until=lambda: app.hosts.hosts["kobi"].sort != "")
    spin(200)
    assert app.live.document()["views"]["sari"] == before


def test_the_video_footer_has_a_speaker_and_a_volume_slider_muted_at_rest(app, spin):
    """#216: the transport row carries a speaker button (muted at rest, faint until the
    file's audio track is known; offscreen mpv never opens the file, so it stays faint)
    and a short volume slider at 100 %. M toggles the view's sound and the slider's
    press picks a volume on the 5 % grid; both land in the view's params as navigation."""
    import shutil

    from gallery_helpers import TESTDATA

    from specto_app.views import gallery_native

    src = TESTDATA / "runs" / "run-001" / "video" / "cam-a.mp4"
    if not gallery_native.is_built():
        pytest.skip(gallery_native.BUILD_HINT)
    if not src.exists():
        pytest.skip("no cam-a.mp4 in the test data")
    vdir = Path(app.project.root) / "video"
    vdir.mkdir()
    shutil.copy2(src, vdir / "cam-a.mp4")
    shutil.copy2(src.with_name("cam-a.timeline.json"), vdir / "cam-a.timeline.json")
    app.engine.links.resolve("l1")
    add_view(app, spin, "kali", "video", ["video"])
    host = app.hosts.hosts["kali"]
    spin(until=lambda: host.key == "cam-a")
    depth = app.live.undo_info()["depth"]

    speaker = item(app, "video:kali:mute")
    assert speaker.property("name") == "volume-off" and not speaker.property("on")
    assert speaker.property("disabled") and speaker.property("tip") == "no audio track"
    slider = item(app, "video:kali:volumeSlider")
    assert slider.property("fraction") == 1.0 and slider.property("muted")
    thumb = item(app, "video:kali:volumeThumb")
    assert thumb.x() == pytest.approx(slider.width() - thumb.width(), abs=0.01)
    assert host.muted and host.volume == 1.0
    assert "muted" not in app.live.document()["views"]["kali"].get("params", {})

    # M: sound on (remembered, no undo step); the speaker shows it
    host.item.forceActiveFocus()
    QTest.keyClick(app.window, Qt.Key.Key_M)
    spin(until=lambda: not host.muted)
    assert speaker.property("name") == "volume" and speaker.property("on")
    assert app.live.document()["views"]["kali"]["params"]["muted"] is False
    assert not slider.property("muted")
    # the slider's middle: 50 %, remembered on release
    click(app, spin, "video:kali:volumeSlider")
    spin(until=lambda: host.volume == 0.5)
    assert app.live.document()["views"]["kali"]["params"]["volume"] == 0.5
    assert app.live.undo_info()["depth"] == depth
