"""Seam 1 and 2 of the image view's channel toggles (#171): which channels an image offers
(probed once from the file's header: RGB, RGBA, greyscale, greyscale plus alpha), what a
click, a Ctrl/Alt+click, a key and the agent's ``params.channels`` show, and the round trip
of the param through the document (a user toggle is one undo step the agent sees)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from app_doc_helpers import pin
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.app import find_item
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.channels import ChannelInfo, ChannelView, probe
from specto_app.views.image_view import ImageFactory, ImageHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager

FORMATS = {
    "rgb": QImage.Format.Format_RGB888,
    "rgba": QImage.Format.Format_ARGB32,
    "l": QImage.Format.Format_Grayscale8,
    "la": None,  # Qt writes no grey+alpha PNG: written by hand below
}


def write_png(path: Path, kind: str) -> Path:
    """A 4x2 PNG of ``kind`` (rgb, rgba, l, la), saved the way a camera or a tool would."""
    if kind == "la":
        _write_la_png(path)
        return path
    img = QImage(4, 2, FORMATS[kind])
    img.fill(QColor(200, 100, 50, 128) if kind == "rgba" else QColor(200, 100, 50))
    assert img.save(str(path))
    return path


def _write_la_png(path: Path) -> None:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    w, h = 4, 2
    raw = b"".join(b"\x00" + bytes([150, 64] * w) for _ in range(h))  # filter 0, L=150 A=64
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 4, 0, 0, 0)  # 8 bit, colour type 4 (grey+alpha)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    ("kind", "offered"), [("rgb", "rgb"), ("rgba", "rgba"), ("l", ""), ("la", "la")]
)
def test_an_image_offers_the_channels_its_file_has(tmp_path: Path, kind: str, offered: str):
    info = probe(str(write_png(tmp_path / f"{kind}.png", kind)))
    assert info.offered == offered
    assert info.depth == 8


def test_a_16_bit_png_says_its_depth_and_a_grey_jpeg_offers_nothing(qapp, tmp_path: Path):
    img = QImage(4, 2, QImage.Format.Format_RGBA64)
    img.fill(QColor(10, 20, 30, 40))
    assert img.save(str(tmp_path / "deep.png"))
    deep = probe(str(tmp_path / "deep.png"))
    assert (deep.offered, deep.depth) == ("rgba", 16)
    grey = QImage(4, 2, QImage.Format.Format_Grayscale8)
    grey.fill(QColor(90, 90, 90))
    assert grey.save(str(tmp_path / "grey.jpg"))
    assert probe(str(tmp_path / "grey.jpg")).offered == ""
    assert probe(str(tmp_path / "missing.png")).offered == "rgb"  # unknown: colour, opaque
    # a broken header (the JPEG handler then reports an uninitialised format; image
    # plugins load with the application)
    (tmp_path / "broken.jpg").write_bytes(bytes([0xFF, 0xD8, 0xFF, *bytes(16), 0xFF, 0xD9]))
    assert probe(str(tmp_path / "broken.jpg")).offered == "rgb"


RGBA, RGB, LA = ChannelInfo("rgba"), ChannelInfo("rgb"), ChannelInfo("la")


def test_a_click_solos_a_channel_and_the_same_click_again_shows_them_all():
    view = ChannelView(RGBA)
    assert view.shown == "rgba" and view.mode == "normal"
    assert view.click("r") == "r"
    assert (view.shown, view.mode) == ("r", "grey")
    assert view.click("g") == "g"  # another solo replaces it
    assert view.click("g") == ""  # the soloed one again: the normal view
    assert (view.shown, view.mode) == ("rgba", "normal")
    assert view.click("a") == "a"
    assert (view.shown, view.mode) == ("a", "alpha")
    assert view.click("x") == "a"  # not a channel: nothing changes


def test_alt_click_toggles_a_channel_within_the_mix():
    view = ChannelView(RGBA)
    assert view.mix("b") == "rga"  # off: the rest show, transparency over the checkerboard
    assert (view.shown, view.mode) == ("rga", "mix")
    assert view.mix("a") == "rg"  # alpha off: the colour shows opaque
    assert view.mix("r") == "g"  # one colour channel left: it shows as greyscale
    assert view.mode == "grey"
    assert view.mix("g") == ""  # nothing left: every channel again
    assert view.mode == "normal"
    view.set_param("r")
    assert view.mix("a") == "ra"  # R over the checkerboard by the alpha
    assert view.mode == "grey"


def test_the_param_is_read_against_the_channels_the_image_has():
    view = ChannelView(RGB, "a")
    assert (view.shown, view.mode) == ("rgb", "normal")  # no alpha to show
    view.set_param("bR ")
    assert view.shown == "rb"  # letters in footer order, case and spaces ignored
    view.set_param("rgb")
    assert view.mode == "normal" and view.param == "rgb"  # kept as written
    assert ChannelView(RGBA, "rgb").mode == "mix"  # it means the same on the next image
    grey = ChannelView(LA, "r")
    assert (grey.shown, grey.mode) == ("l", "grey")  # r on a grey image is its luminance
    assert ChannelView(ChannelInfo(""), "a").shown == ""  # plain grey: nothing to pick
    assert ChannelView(RGBA, "l").shown == "rgb"  # l on a colour image: its colour
    # stepping to an image with other channels keeps the param, read against the new one
    view = ChannelView(RGBA, "a")
    view.info = RGB
    assert (view.param, view.shown) == ("a", "rgb")


def test_the_uniforms_tell_the_shader_what_to_draw():
    u = ChannelView(RGBA).uniforms()
    assert (u["active"], u["checker"], u["grey"]) == (True, 1.0, 0.0)  # alpha over the board
    assert u["mask"] == [1.0, 1.0, 1.0, 1.0]
    assert ChannelView(RGB).uniforms()["active"] is False  # the plain image draws itself
    u = ChannelView(RGBA, "g").uniforms()
    assert (u["grey"], u["pick"], u["checker"]) == (1.0, [0.0, 1.0, 0.0, 0.0], 0.0)
    u = ChannelView(RGBA, "a").uniforms()
    assert (u["grey"], u["pick"]) == (1.0, [0.0, 0.0, 0.0, 1.0])
    u = ChannelView(RGBA, "rb").uniforms()
    assert (u["grey"], u["mask"], u["checker"]) == (0.0, [1.0, 0.0, 1.0, 0.0], 0.0)
    u = ChannelView(LA, "l").uniforms()
    assert (u["grey"], u["pick"]) == (1.0, [1.0, 0.0, 0.0, 0.0])  # grey: its red is its L


def pictures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qml_engine=None
) -> tuple[Engine, ImageHost]:
    """A project with an RGBA, an RGB, a grey+alpha and a grey PNG and an image view on
    them (headless without ``qml_engine``)."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "pictures")
    folder = proj.root / "imgs"
    folder.mkdir()
    for name, kind in (("a_rgba", "rgba"), ("b_rgb", "rgb"), ("c_la", "la"), ("d_l", "l")):
        write_png(folder / f"{name}.png", kind)
    engine = Engine(project=proj, jobs=InlineJobManager())
    engine.links.resolve("l1")  # the project scan ran before the files were written
    clear_pending()

    class HeadlessImage(ImageFactory):
        def create(self, ctx, view_id, manifest):
            return ImageHost(ctx, view_id, manifest, headless=qml_engine is None)

    reg = HostRegistry(HostContext(engine, qml_engine), load_entry_points=False)
    reg.add_factory(HeadlessImage())
    doc = engine.live.document()
    doc["views"]["tamu"] = {"type": "image", "label": "Pictures", "layout": {}}
    pin(doc, "tamu", sources=["imgs"])
    engine.live.submit(doc, origin="agent:s1", label="image")
    host = reg.host("tamu")
    assert isinstance(host, ImageHost)
    return engine, host


def test_the_toggles_write_params_channels_and_the_agent_sets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine, host = pictures(tmp_path, monkeypatch)
    assert host.caption_of(host.index) == "a_rgba.png"
    assert host.snapshot()["channels"] == {
        "offered": "rgba", "depth": 8, "float": False, "param": "", "shown": "rgba",
        "mode": "normal",
    }  # fmt: skip

    def param() -> str | None:
        return engine.live.data["views"]["tamu"].get("params", {}).get("channels")

    host.bridge.channelClicked("r", False)  # a click (or the R key) solos red
    assert param() == "r" and engine.live.history[-1].origin == "user"
    assert host.snapshot()["channels"]["mode"] == "grey"
    host.bridge.channelClicked("b", True)  # Ctrl/Alt+click adds blue to the mix
    assert param() == "rb" and host.snapshot()["channels"]["mode"] == "mix"
    host.bridge.channelReset()  # the menu's every channel: the param leaves the document
    assert param() is None and host.snapshot()["channels"]["shown"] == "rgba"
    engine.call("canvas.undo", {})  # one gesture, one undo step
    assert param() == "rb" and host.snapshot()["channels"]["shown"] == "rb"

    # the agent sets it like any param
    doc = engine.live.document()
    doc["views"]["tamu"]["params"]["channels"] = "a"
    engine.live.submit(doc, origin="agent:s1", label="alpha")
    assert host.snapshot()["channels"]["mode"] == "alpha"

    # the next file has no alpha: it shows all it has, the param stays for the next one
    host.step_item(1)
    ch = host.snapshot()["channels"]
    assert (ch["offered"], ch["shown"], ch["param"]) == ("rgb", "rgb", "a")
    host.step_item(1)
    ch = host.snapshot()["channels"]
    assert (ch["offered"], ch["shown"], ch["mode"]) == ("la", "a", "alpha")
    host.bridge.channelClicked("a", False)  # A again: the normal view
    assert param() is None


def test_a_preview_showing_an_image_keeps_the_channels_in_its_own_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The preview's image mode is the image view's own host (#106): the same toggles,
    the choice in the preview's ``params.channels``."""
    from specto_app.views.preview_view import PreviewFactory

    engine, _ = pictures(tmp_path, monkeypatch)
    reg = HostRegistry(HostContext(engine, None), load_entry_points=False)
    reg.add_factory(PreviewFactory())
    doc = engine.live.document()
    doc["views"]["pela"] = {"type": "preview", "label": "Look", "layout": {}}
    pin(doc, "pela", sources=["imgs"])
    engine.live.submit(doc, origin="agent:s1", label="preview")
    preview = reg.host("pela")
    renderer = preview.renderer
    assert isinstance(renderer, ImageHost)
    renderer.bridge.channelClicked("g", False)
    assert engine.live.data["views"]["pela"]["params"]["channels"] == "g"
    assert preview.snapshot()["shown"]["channels"]["shown"] == "g"
    doc = engine.live.document()
    doc["views"]["pela"]["params"]["channels"] = "rb"
    engine.live.submit(doc, origin="agent:s1", label="red and blue")
    assert preview.snapshot()["shown"]["channels"]["mode"] == "mix"


@pytest.fixture
def window(qapp, spin):
    from PySide6.QtQuick import QQuickView

    from specto_app.qt_setup import install_qml

    view = QQuickView()
    view.resize(480, 360)
    view._theme = install_qml(view.engine())
    view.show()
    yield view
    view.close()
    view.deleteLater()
    spin(20)


def test_the_footer_offers_the_image_s_channels_and_the_keys_solo_only_with_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window, spin
):
    engine, host = pictures(tmp_path, monkeypatch, window.engine())
    item = host.item
    item.setParentItem(window.contentItem())
    item.setSize(window.contentItem().size())

    def ready() -> bool:  # the shown file decoded (stepping says "loading" first)
        return host.ctx.health_of("tamu")["status"] == "ok"

    def toggles() -> str:
        row = find_item(item, "channelToggles:tamu")
        if row is None or not row.isVisible():
            return ""
        return "".join(
            c for c in "rgbla" if (t := find_item(item, f"channel:tamu:{c}")) and t.isVisible()
        )

    def param() -> str | None:
        return engine.live.data["views"]["tamu"].get("params", {}).get("channels")

    spin(until=ready)
    assert toggles() == "rgba"
    assert item.property("channels")["active"] is True  # the host's push: alpha on the board
    item.forceActiveFocus()
    spin(until=item.hasActiveFocus)
    QTest.keyClick(window, Qt.Key.Key_R)
    assert param() == "r" and engine.live.history[-1].origin == "user"
    assert item.property("channels")["pick"] == [1.0, 0.0, 0.0, 0.0]
    assert find_item(item, "channel:tamu:r").property("on") is True
    assert find_item(item, "channel:tamu:g").property("off") is True
    QTest.keyClick(window, Qt.Key.Key_R)  # the same key again: every channel
    assert param() is None
    QTest.keyClick(window, Qt.Key.Key_L)  # not a channel of this image: nothing
    assert param() is None
    item.setFocus(False)
    window.contentItem().forceActiveFocus()
    spin(until=lambda: not item.hasActiveFocus())
    QTest.keyClick(window, Qt.Key.Key_A)  # the view has no focus: not its key (#113)
    assert param() is None

    shown = []
    for _ in range(3):
        host.step_item(1)
        spin(until=ready)
        shown.append(toggles())
    assert shown == ["rgb", "la", ""]  # plain grey offers nothing


def test_ctrl_click_and_alt_click_toggle_channels_in_the_mix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window, spin
):
    """#182: Ctrl+click R then Ctrl+click G shows R+G; Alt+click still mixes, a plain
    click solos."""
    from PySide6.QtCore import QPointF

    engine, host = pictures(tmp_path, monkeypatch, window.engine())
    item = host.item
    item.setParentItem(window.contentItem())
    spin(until=lambda: host.ctx.health_of("tamu")["status"] == "ok")
    item.setSize(window.contentItem().size())  # the content item has its size by now
    spin(until=lambda: (t := find_item(item, "channel:tamu:r")) is not None and t.isVisible())

    def click(c: str, modifier=Qt.KeyboardModifier.NoModifier) -> None:
        toggle = find_item(item, f"channel:tamu:{c}")
        at = toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, modifier, at)

    def param() -> str | None:
        return engine.live.data["views"]["tamu"].get("params", {}).get("channels")

    ctrl, alt = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.AltModifier
    click("r", ctrl)  # from every channel, Ctrl+click takes red out
    assert param() == "gba"
    click("r")  # a plain click solos
    assert param() == "r"
    click("g", ctrl)  # Ctrl+click adds green: R+G
    assert param() == "rg"
    assert host.snapshot()["channels"]["mode"] == "mix"
    click("b", alt)  # Alt+click still mixes
    assert param() == "rgb"
    click("g", ctrl)  # and Ctrl+click again takes it out
    assert param() == "rb"
    assert "Ctrl+click" in find_item(item, "channel:tamu:r").property("tip")


CHILD = Path(__file__).resolve().parent / "image_channels_child.py"
COLOUR = (200, 100, 50)
DARK, LIGHT = (0xC4,) * 3, (0xF2,) * 3  # theme checker_dark / checker_light
# the known image, 64 x 32 fitted at 4x into the 256 x 168 viewport (y from 20): its left
# half opaque, its right half at alpha 128; sampled on the left, and on the right over a
# dark and a light checkerboard cell (8 px)
POINTS = [(60, 88), (196, 88), (204, 88)]


def known_png(path: Path) -> Path:
    img = QImage(64, 32, QImage.Format.Format_ARGB32)
    for y in range(32):
        for x in range(64):
            img.setPixelColor(x, y, QColor(*COLOUR, 255 if x < 32 else 128))
    assert img.save(str(path))
    return path


def over(board: tuple[int, ...], colour: tuple[int, ...], a: float = 128 / 255) -> tuple:
    return tuple(round(b + (c - b) * a) for b, c in zip(board, colour, strict=True))


def close(got: list[int], want: tuple[int, ...], tol: int = 6) -> bool:
    return all(abs(g - w) <= tol for g, w in zip(got, want, strict=True))


@pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("DISPLAY"),
    reason="the offscreen OpenGL path needs a display (windows or X11)",
)
def test_the_shader_shows_solo_mix_and_alpha_on_the_gpu(tmp_path: Path):
    """The shader over the decoded texture, rendered with OpenGL offscreen (a child: the
    tests' own Qt Quick is the software one, which runs no shaders)."""
    cases = [{"channels": c} for c in ("", "r", "g", "a", "rb", "rga")]
    cases.append({"channels": "r", "zoom": 8.0})  # zoomed: the effect follows, no reload
    plan = {"png": str(known_png(tmp_path / "known.png")), "cases": cases, "points": POINTS}
    r = subprocess.run(
        [sys.executable, str(CHILD), json.dumps(plan)], capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))
    out = json.loads(line[len("RESULT ") :])
    assert out["gpu"] is True
    got = {(c["channels"], c["zoom"]): c for c in out["cases"]}
    grey = (200, 200, 200)
    want = {
        ("", 4.0): [COLOUR, over(DARK, COLOUR), over(LIGHT, COLOUR)],  # transparency on the board
        ("r", 4.0): [grey, grey, grey],  # red as greyscale, opaque
        ("g", 4.0): [(100,) * 3] * 3,
        ("a", 4.0): [(255,) * 3, (128,) * 3, (128,) * 3],  # the alpha as greyscale
        ("rb", 4.0): [(200, 0, 50)] * 3,  # red and blue, no alpha: opaque
        ("rga", 4.0): [(200, 100, 0), over(DARK, (200, 100, 0)), over(LIGHT, (200, 100, 0))],
        ("r", 8.0): [grey] * 3,
    }
    for key, pixels in want.items():
        case = got[key]
        assert case["status"] == "ready", case
        assert all(close(g, w) for g, w in zip(case["pixels"], pixels, strict=True)), (key, case)
        assert case["effect"][0] is True  # every case draws through the shader
    assert got[("r", 8.0)]["effect"][3] == 64 * 8  # the effect is the zoomed image


def test_the_committed_shader_is_compiled_from_its_source():
    """``shaders/channels.frag.qsb`` is committed; a changed ``.frag`` needs a rebuild."""
    tool = Path(__file__).resolve().parents[3] / "tools" / "build_qml_shaders.py"
    r = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
