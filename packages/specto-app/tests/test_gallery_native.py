"""The built ``Specto.Native`` module (packages/specto-app/native, built by build.py):
loads into a PySide6 process with the offscreen platform, exposes GalleryItem's
properties, invokables and signals to Python, lays out columns from the width, zooms
with the tile under the cursor anchored, scrolls, steps the selection from the keyboard
and drives the real host end to end (inline jobs, no GPU: uploads wait for a render
thread that never comes, so the tests check staging and layout, not pixels). Skips
cleanly when the plugin is not built."""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from app_doc_helpers import pin
from gallery_helpers import copy_frames, needs_testdata

from specto_app.views import gallery_native

pytestmark = pytest.mark.skipif(
    not gallery_native.is_built(), reason=f"native module not built ({gallery_native.BUILD_HINT})"
)
MODULE = gallery_native.NATIVE_MODULE_DIR


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    return QCoreApplication.instance() or QGuiApplication(sys.argv)


def gui_test(fn=None, *, own_process: bool = False):
    """Qt Quick items need a QGuiApplication and a process has only one application
    object. When another module already made a plain QCoreApplication (test_endpoint.py
    in the full run), run this test in a child pytest process instead of skipping it;
    ``own_process`` always does (tests that show a window and spin the event loop)."""
    if fn is None:
        return functools.partial(gui_test, own_process=own_process)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QGuiApplication

        existing = QCoreApplication.instance()
        if not os.environ.get("SPECTO_GALLERY_CHILD") and (
            own_process or (existing is not None and not isinstance(existing, QGuiApplication))
        ):
            env = {**os.environ, "SPECTO_GALLERY_CHILD": "1", "QT_QPA_PLATFORM": "offscreen"}
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            cmd.append(f"{__file__}::{fn.__name__}")
            r = subprocess.run(cmd, env=env, capture_output=True, text=True)
            tail = r.stdout[-4000:] + "\n" + r.stderr[-2000:]
            assert r.returncode == 0, f"child pytest failed:\n{tail}"
            return None
        return fn(*args, **kwargs)

    return wrapper


def test_the_tile_shader_reads_the_tile_flags_the_node_writes():
    """A tile's flags are defined in gallerynode.h (TileBits) and mirrored by tile.frag's
    #defines: the two must name the same flags with the same values."""
    native = Path(__file__).resolve().parents[1] / "native"
    header = (native / "gallerynode.h").read_text(encoding="utf-8")
    block = header.split("namespace TileBits {", 1)[1].split("}", 1)[0]
    cpp = {
        re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper(): int(value)
        for name, value in re.findall(r"constexpr int (\w+) = (\d+);", block)
    }
    shader = (native / "shaders" / "tile.frag").read_text(encoding="utf-8")
    glsl = {name: int(v) for name, v in re.findall(r"#define TILE_(\w+) (\d+)", shader)}
    assert cpp and cpp == glsl


def test_qmldir_and_qmltypes_describe_the_item():
    text = (MODULE / "qmldir").read_text()
    assert re.search(r"^module Specto\.Native$", text, re.M)
    assert re.search(r"^(optional )?plugin spectoqt$", text, re.M)
    plugin = {"win32": "spectoqt.dll", "darwin": "libspectoqt.dylib"}.get(
        sys.platform, "libspectoqt.so"
    )
    assert (MODULE / plugin).exists()
    types = (MODULE / "spectoqt.qmltypes").read_text()
    assert 'name: "GalleryItem"' in types
    for name in (
        "count",
        "columns",
        "cellWidth",
        "cellAspect",
        "scrollY",
        "firstWanted",
        "wantTier",
        "pushThumbShm",
        "missing",
        "userSelected",
        "activated",
        "quickLook",
        "zoomTo",
        "setView",
        "setCaptions",
        "currentIndex",
        "currentColor",
        "currentSuffix",
        "drawnCaption",
        "captionScrimColor",
    ):
        assert f'name: "{name}"' in types, name


def _item(app, source: bytes):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    engine = QQmlEngine()
    gallery_native.ensure_import_path(engine)
    comp = QQmlComponent(engine)
    comp.setData(source, QUrl())
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    item = comp.create()
    assert item is not None
    return engine, comp, item


def _view(app, source: bytes):
    """The item inside a shown offscreen QQuickView, so input events reach it."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtQuick import QQuickItem, QQuickView

    view = QQuickView()
    gallery_native.ensure_import_path(view.engine())
    comp = QQmlComponent(view.engine())
    comp.setData(source, QUrl())
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    root = comp.create()
    assert isinstance(root, QQuickItem)
    root.setParentItem(view.contentItem())
    view.resize(int(root.width()), int(root.height()))
    view.show()
    app.processEvents()
    return view, comp, root


GRID = (
    b"import QtQuick\nimport Specto.Native\n"
    b"GalleryItem { count: %d; width: %d; height: %d; columns: %d; gap: 8; margin: 8; "
    b"cellAspect: 1.0; captionMode: 0 }"
)


@gui_test
def test_galleryitem_loads_in_pyside6_offscreen(app):
    from PySide6.QtCore import QByteArray, qVersion
    from PySide6.QtQuick import QQuickItem

    assert qVersion() == "6.12.0", "pin: the plugin was built against the PySide6 wheel's Qt"
    engine, comp, g = _item(app, GRID % (50, 1000, 600, 4))
    assert isinstance(g, QQuickItem) and g.metaObject().className() == "GalleryItem"
    assert g.property("count") == 50 and g.property("thumbPx") == 176
    assert g.property("largePx") == 384 and g.property("miniPx") == 48
    # reflow: 4 columns fill the width exactly: (1000 - 2*8 - 3*8) / 4 = 240
    assert g.property("effectiveColumns") == 4 and g.property("cellWidth") == 240
    assert g.property("rows") == 13 and g.property("cellHeight") == 240
    assert g.property("contentHeight") == 13 * 248 - 8 + 16

    got: list = []
    g.selectedIndexChanged.connect(lambda: got.append("sel"))
    g.wantedChanged.connect(lambda: got.append("wanted"))
    g.userSelected.connect(lambda i, m: got.append(("user", i, m)))
    g.setProperty("selectedIndex", 7)
    assert g.property("selectedIndex") == 7 and "sel" in got
    assert ("user", 7, 0) not in got  # programmatic selection is not user intent

    # visible rows 0..2 (600 / 248), wanted = visible + margin, large tier at 240 px
    assert g.property("firstVisible") == 0 and g.property("lastVisible") == 11
    assert g.property("firstWanted") == 0 and g.property("lastWanted") >= 11
    assert g.property("wantTier") == 2  # 240 px > 176 * 1.15: the large tier
    assert len(g.missing(0, 49, 2)) == 50
    # a 4x4 RGBA thumb is staged (uploaded on the next render, which never comes here)
    assert g.pushThumb(3, QByteArray(b"\xff" * 64), 4, 4)
    assert not g.pushThumb(3, QByteArray(b"\xff" * 10), 4, 4)  # too short
    assert not g.pushThumbShm(4, "no-such-shared-memory", 0, 4, 4)
    assert len(g.missing(0, 49, 2)) == 49 and 3 not in g.missing(0, 49, 2)
    g.setFailed(5, True)
    assert 5 not in g.missing(0, 49, 2)
    r = g.rectOf(0)
    assert r.x() == 8 and r.y() == 8 and r.width() == 240
    assert g.indexAt(r.center().x(), r.center().y()) == 0
    assert g.indexAt(8 + 240 + 4, 20) == -1  # in the gap
    assert g.indexAt(8 + 248, 20) == 1
    g.clearThumbs()
    assert len(g.missing(0, 49, 2)) == 50  # clear resets staged and failed flags
    del g, comp, engine
    app.processEvents()


@gui_test
def test_reflow_scroll_and_anchored_zoom(app):
    engine, comp, g = _item(app, GRID % (200, 1000, 600, 4))
    # scrolling is clamped to the content and reported in rows too
    g.setProperty("scrollY", 100_000)
    assert g.property("scrollY") == g.property("maxScroll") == 50 * 248 + 8 - 600
    g.setProperty("scrollRows", 2.0)
    assert g.property("scrollY") == 2 * 248 and g.property("firstVisible") == 8
    # `columns: 4` at 1000 px set the zoom level as a tile size: 240 px
    assert g.property("tileWidth") == 240
    # widening reflows: the same tile size, more columns (1392 / 248 = 5.6 -> 6), cells
    # stretched a little to fill the row; the top-left item (8) stays in the top row
    g.setWidth(1400)
    assert g.property("effectiveColumns") == 6 and g.property("cellWidth") == (1400 - 16 - 40) / 6
    assert g.property("tileWidth") == 240 and g.property("columns") == 6
    assert g.property("scrollRows") == 1.0 and g.property("firstVisible") == 6
    # narrowing too: 592 / 248 = 2.4 -> 2 columns of 288 px
    g.setWidth(600)
    assert g.property("effectiveColumns") == 2 and g.property("cellWidth") == (600 - 16 - 8) / 2
    assert g.property("firstVisible") <= 8 <= g.property("lastVisible")
    # and back: the layout depends on the width alone, never on the resize history
    g.setWidth(1000)
    assert g.property("effectiveColumns") == 4 and g.property("cellWidth") == 240
    # a re-flow starts the top item's row at the top edge; a height change keeps the offset
    g.setProperty("scrollRows", 2.5)
    g.setWidth(1400)
    assert g.property("scrollRows") == 1.0 and g.property("firstVisible") == 6
    g.setWidth(1000)
    g.setProperty("scrollRows", 2.5)
    g.setHeight(500)
    assert abs(g.property("scrollRows") - 2.5) < 1e-9
    g.setHeight(600)
    g.setProperty("scrollRows", 2.0)
    # zoom in by one column with the cursor over tile 10's centre keeps it under the cursor
    r10 = g.rectOf(10)
    assert not r10.isEmpty()
    cx, cy = r10.center().x(), r10.center().y()
    g.zoomTo(3, cx, cy, False)
    assert g.property("effectiveColumns") == 3 and g.property("cellWidth") == (1000 - 16 - 16) / 3
    assert g.indexAt(cx, cy) == 10
    # and out: columns are clamped to minCellWidth (40 px): max columns = (1000-16+8)/48
    g.zoomTo(1000, -1, -1, False)
    assert g.property("effectiveColumns") == (1000 - 16 + 8) // 48
    g.zoomTo(0, -1, -1, False)
    assert g.property("effectiveColumns") == 1
    # setView is the programmatic form (pan_zoom2d follow): columns + rows, no signals
    seen: list = []
    g.userViewChanged.connect(lambda: seen.append("user"))
    g.setView(5, 3.0, False)
    assert g.property("effectiveColumns") == 5 and abs(g.property("scrollRows") - 3.0) < 1e-9
    assert seen == []
    # the tile size is the zoom level a host persists: 100 px -> 992 / 108 = 9.2 -> 9
    g.setProperty("tileWidth", 100.0)
    assert g.property("effectiveColumns") == 9 and g.property("tileWidth") == 100
    del g, comp, engine
    app.processEvents()


@gui_test
def test_notches_of_one_zoom_keep_the_first_anchor(app):
    """Two quick Ctrl+wheel notches: the item under the cursor at the first notch keeps
    its row under the cursor (a reflow moves it sideways, so the second notch must not
    re-pick whatever neighbour landed there)."""
    engine, comp, g = _item(app, GRID % (200, 1000, 600, 6))
    g.setProperty("scrollRows", 3.0)
    r = g.rectOf(26)
    cx, cy = r.center().x(), r.center().y()
    g.zoomTo(5, cx, cy, False)
    g.zoomTo(4, cx, cy, False)
    assert g.property("effectiveColumns") == 4
    cell = g.cellRectOf(26)
    assert cell.top() <= cy <= cell.bottom()
    del g, comp, engine
    app.processEvents()


@gui_test
def test_columns_before_width_and_reveal_before_height(app):
    """A host sets the zoom and reveals the selection before the shell has sized the
    item (it is created parked, 0 x 0): both apply at the first real layout, and the
    first row starts at the top margin, not scrolled under the view header."""
    engine, comp, g = _item(
        app,
        b"import QtQuick\nimport Specto.Native\n"
        b"GalleryItem { count: 200; gap: 8; margin: 8; cellAspect: 1.0; captionMode: 0 }",
    )
    assert g.property("width") == 0
    g.setProperty("columns", 4)
    g.revealIndex(0, False)
    assert g.property("scrollY") == 0
    g.setWidth(1000)
    assert g.property("effectiveColumns") == 4 and g.property("tileWidth") == 240
    g.setHeight(600)
    assert g.property("scrollY") == 0 and g.rectOf(0).y() == 8
    # a restored scroll position (rows, set while parked) survives the first layout
    engine2, comp2, g2 = _item(
        app,
        b"import QtQuick\nimport Specto.Native\n"
        b"GalleryItem { count: 200; gap: 8; margin: 8; cellAspect: 1.0; captionMode: 0 }",
    )
    g2.setProperty("tileWidth", 240.0)
    g2.setProperty("scrollRows", 5.5)
    g2.setWidth(1000)
    g2.setHeight(600)
    assert g2.property("effectiveColumns") == 4 and abs(g2.property("scrollRows") - 5.5) < 1e-9
    del g2, comp2, engine2
    # a selection further down is revealed once there is a height to reveal it in
    g.setHeight(0)
    g.revealIndex(100, False)
    g.setHeight(600)
    assert g.property("firstVisible") <= 100 <= g.property("lastVisible")
    del g, comp, engine
    app.processEvents()


@gui_test
def test_captions_and_aspect(app):
    engine, comp, g = _item(
        app,
        b"import QtQuick\nimport Specto.Native\n"
        b"GalleryItem { count: 10; width: 1000; height: 600; columns: 5; gap: 8; margin: 8; "
        b"cellAspect: 2.0; captionMode: 1 }",
    )
    assert g.property("cellWidth") == (1000 - 16 - 32) / 5
    assert abs(g.property("cellHeight") - g.property("cellWidth") / 2) < 1e-9
    # the caption overlays the thumbnail's bottom edge (SPEC-6): a band on the tile, no
    # height of its own in the row pitch, the cell, the content or the hit test
    one_line = g.property("captionHeight")
    assert one_line > 0
    assert g.property("rowPitch") == g.property("cellHeight") + 8
    assert g.cellRectOf(0).height() == g.property("cellHeight")
    content = g.property("contentHeight")
    g.setProperty("captionMode", 2)
    assert g.property("captionHeight") > one_line  # two lines: a taller band
    assert g.property("contentHeight") == content
    assert g.property("rowPitch") == g.property("cellHeight") + 8
    g.setCaptions([f"k{i}" for i in range(10)], [f"f{i}.jpg" for i in range(10)])
    assert g.captionOf(3) == "k3  f3.jpg"
    # the gap under a row is a gap: no caption strip to hit any more
    below = g.cellRectOf(0).bottom() + 4
    assert g.indexAt(g.cellRectOf(0).center().x(), below) == -1
    g.setProperty("captionMode", 0)
    assert g.property("captionHeight") == 0
    assert g.property("contentHeight") == content
    # narrow tiles hide captions (captionMinWidth)
    g.setProperty("captionMode", 1)
    g.setProperty("columns", 20)
    assert g.property("cellWidth") < 72 and g.property("captionHeight") == 0
    del g, comp, engine
    app.processEvents()


@gui_test
def test_the_current_item_is_marked_apart_from_the_selection(app):
    """The current (linked) item, the frame a time link points at, is its own index on
    the item: setting it moves neither the selection nor the scroll, and a shrinking
    collection drops it like the selection."""
    from PySide6.QtGui import QColor

    engine, comp, g = _item(app, GRID % (10, 1000, 600, 5))
    assert g.property("currentIndex") == -1
    changes: list[int] = []
    g.currentIndexChanged.connect(lambda item=g: changes.append(item.property("currentIndex")))
    g.setProperty("selectedIndex", 2)
    g.setProperty("currentIndex", 7)
    assert g.property("currentIndex") == 7 and g.property("selectedIndex") == 2
    assert changes == [7]
    g.setProperty("currentIndex", 99)  # out of range: none
    assert g.property("currentIndex") == -1
    g.setProperty("currentIndex", 4)
    g.setProperty("count", 3)
    assert g.property("currentIndex") == -1
    # its marker has its own colour token, and the caption its own scrim and label; the
    # item's defaults (what a gallery standing alone draws) are the theme's tokens
    from specto_app.theme import COLORS

    assert g.property("currentColor") == QColor(COLORS["current_mark"])
    assert g.property("captionScrimColor") == QColor(COLORS["image_scrim"])
    assert g.property("captionColor") == QColor(COLORS["text_on_image"])
    assert g.property("currentSuffix") == " · now"
    g.setProperty("currentColor", QColor("#123456"))
    assert g.property("currentColor") == QColor("#123456")
    del g, comp, engine
    app.processEvents()


@gui_test(own_process=True)
def test_keyboard_navigation_and_wheel(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest

    view, comp, g = _view(app, GRID % (200, 1000, 600, 4))
    g.forceActiveFocus()
    app.processEvents()
    assert g.hasActiveFocus()
    got: list = []
    g.userSelected.connect(lambda i, m: got.append(i))
    g.activated.connect(lambda i: got.append(("open", i)))
    g.quickLook.connect(lambda i: got.append(("quick", i)))
    QTest.keyClick(view, Qt.Key.Key_Right)
    assert g.property("selectedIndex") >= 0  # no selection: start near the centre tile
    QTest.keyClick(view, Qt.Key.Key_Home)
    QTest.keyClick(view, Qt.Key.Key_Right)
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert g.property("selectedIndex") == 5 and got[-1] == 5
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert g.property("selectedIndex") == 5 + 4 * 2  # two visible rows of four
    QTest.keyClick(view, Qt.Key.Key_End)
    assert g.property("selectedIndex") == 199
    QTest.keyClick(view, Qt.Key.Key_Home)
    assert g.property("selectedIndex") == 0
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert got[-1] == ("open", 0)
    QTest.keyClick(view, Qt.Key.Key_Space)
    assert got[-1] == ("quick", 0)
    QTest.keyClick(view, Qt.Key.Key_Plus)
    assert g.property("columns") == 3  # zoom in: fewer columns (animated towards it)
    QTest.keyClick(view, Qt.Key.Key_Minus)
    assert g.property("columns") == 4

    # plain wheel scrolls (eased: the target is below the current position), Ctrl+wheel
    # zooms one column per notch
    def wheel(dy: int, mods=Qt.KeyboardModifier.NoModifier):
        ev = QWheelEvent(
            QPointF(500, 300),
            QPointF(500, 300),
            QPoint(),
            QPoint(0, dy),
            Qt.MouseButton.NoButton,
            mods,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        app.sendEvent(g, ev)

    y0 = g.property("scrollY")
    wheel(-120)
    assert g.property("scrolling") is True
    QTest.qWait(400)
    assert g.property("scrollY") > y0
    wheel(120, Qt.KeyboardModifier.ControlModifier)
    assert g.property("columns") == 3
    wheel(-120, Qt.KeyboardModifier.ControlModifier)
    assert g.property("columns") == 4
    for _ in range(30):  # the easing settles in ~400 ms; a loaded full run takes longer
        QTest.qWait(100)
        if g.property("scrolling") is False:
            break
    assert g.property("scrolling") is False
    view.close()
    del comp, view
    app.processEvents()


@gui_test(own_process=True)
def test_two_quick_clicks_open_once(app):
    """A real mouse double-click is press, release, press (+ a double-click event Qt
    may or may not deliver): the second press on the same tile opens it, once. Times are
    the events' own timestamps (QTest spaces its clicks apart, so events are built here)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    view, comp, g = _view(app, GRID % (40, 1000, 600, 4))
    got: list = []
    g.activated.connect(lambda i: got.append(("open", i)))
    g.userSelected.connect(lambda i, m: got.append(("sel", i)))
    r = g.rectOf(5)
    pos = QPointF(r.center().x(), r.center().y())
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    interval = app.styleHints().mouseDoubleClickInterval()

    def send(kind, t: int) -> None:
        down = kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
        buttons = left if down else Qt.MouseButton.NoButton
        ev = QMouseEvent(kind, pos, pos, view.mapToGlobal(pos), left, buttons, none)
        ev.setTimestamp(t)
        app.sendEvent(view, ev)

    press, release = QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease
    t = 1_000_000
    send(press, t)
    send(release, t + 60)
    send(press, t + 140)
    send(QEvent.Type.MouseButtonDblClick, t + 140)  # Qt's own event, when it sends one
    send(release, t + 200)
    assert got == [("sel", 5), ("open", 5)]
    # a slow second click is a click, not an open
    got.clear()
    t += 10_000
    send(press, t)
    send(release, t + 50)
    send(press, t + 50 + interval + 50)
    send(release, t + 50 + interval + 100)
    assert ("open", 5) not in got and got.count(("sel", 5)) == 2
    view.close()
    del comp
    app.processEvents()


@needs_testdata
@gui_test
def test_host_drives_the_native_item(app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from PySide6.QtQml import QQmlEngine

    import specto_intake  # noqa: F401
    from specto_app.hosts import HostContext
    from specto_app.views.gallery import GalleryHost
    from specto_core import Engine
    from specto_core.links import FilesValue
    from specto_intake.resolve import resolve_intaker

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None)
    root = copy_frames(tmp_path / "runs" / "cam-a", 5)
    link = {"params": {"sources": ["."], "screener": ["@images"], "keys": r"^(?P<frame>\d{5})_"}}
    col = resolve_intaker(
        link, FilesValue(root=str(root)), root=str(root), env={"aliases": {"@images": [".jpg"]}}
    )
    qml = QQmlEngine()
    ctx = HostContext(engine, qml)
    host = GalleryHost(ctx, "kobi", engine.plugins.view("gallery"))
    assert host.item is not None and host.item.property("viewId") == "kobi"
    host.item.setWidth(800)
    host.item.setHeight(600)
    host.receive("data", col)
    host.pump()
    assert host.decoded == 5 and host.failed == 0
    assert host.gallery.count() == 5 and host.gallery.missing(0, 4, 2) == []  # all staged
    assert host.gallery.item.captionOf(0).startswith("00000  00000_")
    snap = host.snapshot()
    assert snap["count"] == 5 and snap["visible"]["keys"] == [f"0000{i}" for i in range(5)]
    assert snap["view"]["columns"] >= 1 and snap["wanted"]["tier"] in (0, 1, 2)
    host.gallery.select(2)
    assert host.snapshot()["selected"]["key"] == "00002"
    assert host.bridge.previewUrl(2).startswith("file:///")
    assert host.bridge.captionOf(2).startswith("00002")
    host.dispose()
    assert host.item is None
    app.processEvents()


@needs_testdata
@gui_test(own_process=True)
def test_ctrl_wheel_size_survives_a_click_and_a_relaunch(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#173: Ctrl+wheel grows the thumbnails, a click on a tile keeps that size (the pick's
    params write used to re-apply the stale ``params.tile``), the size is remembered as
    ``params.tile`` once per gesture with no undo step, and a reopened gallery shows it."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtQuick import QQuickView
    from PySide6.QtTest import QTest

    import specto_intake  # noqa: F401
    from specto_app.hosts import HostContext
    from specto_app.views.gallery import GalleryHost
    from specto_core import Engine
    from specto_core.links import FilesValue
    from specto_intake.resolve import resolve_intaker

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None)
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Cam A",
        "layout": {"position": [320, 0], "size": [800, 600]},
        "params": {"tile": 192},
    }
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="cam-a gallery")
    cursor = engine.live.cursor
    root = copy_frames(tmp_path / "runs" / "cam-a", 24)
    link = {"params": {"sources": ["."], "screener": ["@images"], "keys": r"^(?P<frame>\d{5})_"}}
    col = resolve_intaker(
        link, FilesValue(root=str(root)), root=str(root), env={"aliases": {"@images": [".jpg"]}}
    )

    def open_gallery() -> tuple[QQuickView, GalleryHost, Callable[[], None]]:
        view = QQuickView()
        view.resize(800, 600)
        host = GalleryHost(
            HostContext(engine, view.engine()), "kobi", engine.plugins.view("gallery")
        )
        host.item.setParentItem(view.contentItem())
        host.item.setWidth(800)
        host.item.setHeight(600)
        off = engine.live.on_patch(host.on_patch)  # what the registry relays to its hosts
        host.receive("data", col)
        view.show()
        app.processEvents()
        return view, host, off

    view, host, off = open_gallery()
    g = host.gallery.item
    assert g.property("tileWidth") == 192
    cols = g.property("effectiveColumns")

    ev = QWheelEvent(
        QPointF(400, 200),
        QPointF(400, 200),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    app.sendEvent(g, ev)  # Ctrl+wheel up: one column fewer, bigger thumbnails
    QTest.qWait(600)  # the zoom animates and the wheel goes idle: the gesture ends
    grown = g.property("tileWidth")
    assert g.property("effectiveColumns") == cols - 1 and grown > 192
    assert engine.live.data["views"]["kobi"]["params"]["tile"] == pytest.approx(grown, abs=0.1)

    r = g.rectOf(2)
    QTest.mouseClick(
        view, Qt.MouseButton.LeftButton, pos=QPoint(int(r.center().x()), int(r.center().y()))
    )
    app.processEvents()
    assert engine.live.data["views"]["kobi"]["params"]["selected"] == "00002"
    assert g.property("tileWidth") == pytest.approx(grown, abs=0.1)  # the click kept the size
    assert g.property("effectiveColumns") == cols - 1
    assert engine.live.cursor == cursor  # zoom and pick: navigation, no undo step

    off()
    host.dispose()
    view.close()
    app.processEvents()

    # a relaunch: a new host over the same document shows the remembered size
    view, again, off = open_gallery()
    assert again.gallery.item.property("tileWidth") == pytest.approx(grown, abs=0.1)
    off()
    again.dispose()
    view.close()
    app.processEvents()


@pytest.mark.skip(reason="the image view moves onto ports with its ticket (#41)")
@needs_testdata
@gui_test(own_process=True)
def test_image_view_item_loads_and_steps(app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from PySide6.QtQml import QQmlEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest

    import specto_intake  # noqa: F401
    from specto_app.hosts import HostContext
    from specto_app.views.image_view import IMAGE_MANIFEST, IMAGE_PLUGIN, ImageHost
    from specto_core import Engine

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None)
    engine.plugins.register(IMAGE_PLUGIN)
    c = engine.new_canvas("image")
    engine.call("frame.new", {"canvas": c.id})
    root = copy_frames(tmp_path / "runs" / "cam-a", 4)
    engine.call(
        "intake.new",
        {"canvas": c.id, "spec": {"sources": [str(root)], "keys": r"^(?P<frame>\d{5})_"}},
    )
    vid = engine.call(
        "view.new",
        {
            "canvas": c.id,
            "type": "image",
            "source": "c1",
            "state": {"selected": "00002"},
            "dock": {},
        },
    )["view"]
    qml = QQmlEngine()
    ctx = HostContext(engine, c.id, qml)
    host = ImageHost(ctx, vid, IMAGE_MANIFEST)
    assert host.item is not None and host.key == "00002"
    host.item.setWidth(600)
    host.item.setHeight(400)
    assert host.item.property("source").toString().startswith("file:///")
    assert host.item.findChild(QQuickItem, "image") is not None
    # the QML Image decodes on Qt's loader thread; wait for it, then the view fits it
    for _ in range(200):
        app.processEvents()
        if host.image_status == "ready":
            break
        QTest.qWait(20)
    assert host.image_status == "ready" and host.image_size[0] > 0
    snap = host.snapshot()
    assert snap["fit"] is True and 0 < snap["zoom"] <= 1.0
    assert snap["image"]["width"] == host.image_size[0]
    assert host.step_item(1) == 3 and host.key == "00003"
    assert host.view["state"]["selected"] == "00003"
    host.dispose()
    app.processEvents()


def _mouse(app, view, kind, pos, buttons) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    p = QPointF(pos)
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    app.sendEvent(view, QMouseEvent(kind, p, p, view.mapToGlobal(p), left, buttons, none))


def _drag(app, view, start, dx: float, dy: float, steps: int = 6, release: bool = True) -> None:
    from PySide6.QtCore import QEvent, QPointF, Qt

    left = Qt.MouseButton.LeftButton
    _mouse(app, view, QEvent.Type.MouseButtonPress, start, left)
    for i in range(1, steps + 1):
        at = QPointF(start.x() + dx * i / steps, start.y() + dy * i / steps)
        _mouse(app, view, QEvent.Type.MouseMove, at, left)
    if release:
        end = QPointF(start.x() + dx, start.y() + dy)
        _mouse(app, view, QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)


@gui_test
def test_a_mouse_drag_on_a_tile_asks_to_drag_it_out_and_a_drag_on_a_gap_scrolls(app):
    """#185: past the platform's drag distance a press on a tile becomes a drag out
    (``dragRequested``, no pick, no scroll); a smaller wobble is still a click, and a
    drag that starts on the gap between tiles still scrolls."""
    view, comp, g = _view(app, GRID % (60, 1000, 600, 4))
    asked: list = []
    picked: list = []
    g.dragRequested.connect(lambda i, m: asked.append(i))
    g.userSelected.connect(lambda i, m: picked.append(i))
    tile = g.cellRectOf(5).center()
    dist = app.styleHints().startDragDistance()

    _drag(app, view, tile, 2, 1, steps=1)  # a wobble inside the drag distance: a click
    assert picked == [5] and asked == []
    _drag(app, view, tile, 0, dist + 30)
    assert asked == [5] and picked == [5]
    assert g.property("scrollY") == 0 and not g.property("dragging")

    cell = g.cellRectOf(5)
    gap = cell.topRight()
    gap.setX(gap.x() + 4)  # between columns 1 and 2
    gap.setY(gap.y() + cell.height() / 2)
    assert g.indexAt(gap.x(), gap.y()) == -1
    _drag(app, view, gap, 0, -150)
    assert g.property("scrollY") > 0 and asked == [5]
    view.close()
    del comp
    app.processEvents()


@gui_test(own_process=True)
def test_a_gallery_tile_drags_its_image_into_a_drop_area(app, tmp_path: Path, monkeypatch):
    """#185 end to end: the host's native item, a real mouse drag on a tile, the drag
    contract's mime data in a QML DropArea; the selection does not move."""
    from drag_helpers import DropTarget, drags_into
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtQuick import QQuickView

    import specto_intake  # noqa: F401
    from specto_app.hosts import HostContext
    from specto_app.views.gallery import GalleryHost
    from specto_core import Engine
    from specto_core.links import FilesValue
    from specto_intake.resolve import resolve_intaker

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "runs" / "cam-a"
    root.mkdir(parents=True)
    for i in range(6):
        img = QImage(64, 48, QImage.Format.Format_RGB32)
        img.fill(QColor(40 * i, 90, 160))
        assert img.save(str(root / f"{i:05d}_x.jpg"))
    link = {"params": {"sources": ["."], "screener": ["@images"], "keys": r"^(?P<frame>\d{5})_"}}
    col = resolve_intaker(
        link, FilesValue(root=str(root)), root=str(root), env={"aliases": {"@images": [".jpg"]}}
    )
    engine = Engine(project=None)
    view = QQuickView()
    ctx = HostContext(engine, view.engine())
    host = GalleryHost(ctx, "kobi", engine.plugins.view("gallery"))
    host.item.setParentItem(view.contentItem())
    host.item.setWidth(800)
    host.item.setHeight(600)
    view.resize(800, 600)
    view.show()
    host.receive("data", col)
    host.pump()
    app.processEvents()
    host.gallery.select(1)
    target = DropTarget()
    g = host.gallery.item
    tile = g.mapToScene(g.cellRectOf(3).center())
    with drags_into(target) as drags:
        _drag(app, view, tile, 80, 0)
        app.processEvents()
    assert len(drags) == 1 and len(target.drops) == 1
    jpg = root / "00003_x.jpg"
    drop = target.drops[0]
    assert drop["urls"] == [jpg.as_uri()]
    assert drop["text"] == str(jpg)
    items = target.specto()["items"]
    assert [(i["view"], i["type"], i["key"], i["kind"], i["label"]) for i in items] == [
        ("kobi", "gallery", "00003", "image", "00003_x.jpg")
    ]
    assert items[0]["abs"] == jpg.as_posix()
    assert host.gallery.selected() == 1  # a drag is no pick
    target.close()
    host.dispose()
    view.close()
    app.processEvents()
