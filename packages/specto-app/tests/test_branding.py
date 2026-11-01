"""The window icon (#77): the packed .ico, the window icon the app sets, and the
AppUserModelID that keeps Specto out of python.exe's taskbar group."""

from __future__ import annotations

import struct
import sys

import pytest

from specto_app import branding

ICO_SIZES = {16, 20, 24, 32, 40, 48, 64, 256}


def ico_entries(data: bytes) -> list[tuple[int, int]]:
    """(width, height) of every image in an .ico, read from the directory header; a
    width or height byte of 0 means 256."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), "not an .ico"
    out = []
    for i in range(count):
        w, h = struct.unpack_from("<BB", data, 6 + 16 * i)
        out.append((w or 256, h or 256))
    return out


def test_ico_holds_every_size():
    entries = ico_entries(branding.ICON_ICO.read_bytes())
    assert {w for w, _ in entries} == ICO_SIZES
    assert all(w == h for w, h in entries)


TILE, INK, AGENT = (0xF4, 0xEF, 0xE6, 255), (0x3A, 0x36, 0x30, 255), (0xB8, 0x6A, 0x2B, 255)


def frame(size: int) -> list[list[tuple]]:
    Image = pytest.importorskip("PIL.Image")
    im = Image.open(branding.ICON_ICO)
    im.size = (size, size)
    im.load()
    rgba = im.convert("RGBA")
    return [[rgba.getpixel((x, y)) for x in range(size)] for y in range(size)]


def runs(px: list, colour: tuple) -> int:
    """How many unbroken runs of ``colour`` the line holds."""
    n, inside = 0, False
    for p in px:
        if p == colour and not inside:
            n += 1
        inside = p == colour
    return n


@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_the_s_silhouette_is_hinted_onto_whole_pixels_at_small_sizes(size):
    """The tool moves the S's outermost top, bottom, left and right (a flat edge, a
    curve's extreme, a cap) so the shape's edge there lands on a pixel boundary, where a
    vector scaled to 16 px would leave a half-covered fringe. Whatever the S's shape; the
    .ico is what the tool draws (the staleness test below)."""
    from specto_app.logo import load_logo

    lay = load_tool().layout(size, load_logo())
    pts = [p for poly in lay.body.subpaths(48) for p in poly]
    half = lay.w / 2
    edges = {
        "left": lay.fx(min(x for x, _ in pts)) - half,
        "right": lay.fx(max(x for x, _ in pts)) + half,
        "top": lay.fy(min(y for _, y in pts)) - half,
        "bottom": lay.fy(max(y for _, y in pts)) + half,
    }
    for side, edge in edges.items():
        assert edge == pytest.approx(round(edge), abs=1e-9), (size, side, edge)
        assert 0 <= edge <= size, (size, side, edge)


def load_tool():
    """``tools/make_icon.py`` as a module (registered, so its dataclasses resolve)."""
    import importlib.util

    if "make_icon" in sys.modules:
        return sys.modules["make_icon"]
    path = branding.ASSETS.parents[3].parent / "tools" / "make_icon.py"
    spec = importlib.util.spec_from_file_location("make_icon", path)
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    sys.modules["make_icon"] = tool
    spec.loader.exec_module(tool)
    return tool


@pytest.mark.parametrize("size", [16, 24, 256])
def test_a_filled_outline_body_is_rasterised_as_a_fill(tmp_path, size):
    """``make_icon`` fills an outline body (here a square ring) with its hole open, its
    straight edges on whole pixels, and the eyes in the hole clear of the ink."""
    from test_logo import write_set

    from specto_app.logo import load_logo

    im = load_tool().draw(size, load_logo(write_set(tmp_path, filled=True))).convert("RGBA")
    k, o = 0.64 * size / 100 if size > 32 else 0.90 * size / 100, None
    o = size / 2 - 50 * k
    row = [im.getpixel((x, int(o + 60 * k))) for x in range(size)]
    # across the ring below the eyes: tile, the left band, the hole, the right band, tile
    assert set(row) <= {TILE, INK} or size < 24, row
    assert runs(row, INK) == 2, row
    assert row[size // 2] == TILE  # the hole stays open
    pixels = [im.getpixel((x, y)) for y in range(size) for x in range(size)]
    assert AGENT in pixels


@pytest.mark.parametrize("size", [16, 24, 32, 256])
def test_the_s_has_two_eyes_that_do_not_touch(size):
    """The row through the eyes' centres crosses two separate agent-coloured dots."""
    rows = frame(size)
    eye_row = max(rows, key=lambda r: r.count(AGENT))
    assert eye_row.count(AGENT) >= 2
    agentish = [p == AGENT for p in eye_row]
    assert runs(agentish, True) == 2, eye_row


def test_running_app_has_the_window_icon(app, qapp):
    """``SpectoApp`` sets the application window icon from the .ico before its window
    exists, so the title bar and the taskbar show the S, not Qt's default."""
    icon = qapp.windowIcon()
    assert not icon.isNull()
    sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
    assert {(16, 16), (24, 24), (32, 32), (256, 256)} <= sizes
    assert app.window is not None and not app.window.icon().isNull()


def _current_app_user_model_id() -> str | None:
    """What Windows holds for this process, or None when nothing was set."""
    import ctypes
    from ctypes import wintypes

    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    out = ctypes.c_wchar_p()
    hr = shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(out))
    if hr != 0 or not out.value:
        return None
    value = out.value
    ole32.CoTaskMemFree.argtypes = [wintypes.LPVOID]
    ole32.CoTaskMemFree(ctypes.cast(out, wintypes.LPVOID))
    return value


@pytest.mark.skipif(sys.platform != "win32", reason="AppUserModelID is a Windows notion")
def test_qt_setup_gives_the_process_its_own_app_user_model_id(qapp):
    """``prepare()`` (run by the ``qapp`` fixture, as by the app) tells Windows this
    process is Specto, so the taskbar groups it under the S, not under python.exe."""
    assert _current_app_user_model_id() == branding.APP_USER_MODEL_ID


def test_app_user_model_id_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert branding.set_app_user_model_id() is False


def test_the_ico_is_drawn_from_the_shipped_art():
    """``tools/make_icon.py`` was run after the last change to ``assets/logo``: every
    size in the .ico is what it draws from the design files now."""
    from specto_app.logo import load_logo

    tool = load_tool()
    art = load_logo()
    for size in ICO_SIZES:
        want = tool.draw(size, art).convert("RGBA")
        assert [[p for p in r] for r in frame(size)] == [
            [want.getpixel((x, y)) for x in range(size)] for y in range(size)
        ], f"{size} px is stale: run tools/make_icon.py"
