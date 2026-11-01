"""Pure pieces of the shell: the icon set, the snapshot store, theme flattening, the
member image provider, and the Qt-free import of the command module."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QSize

from specto_app.canvas.snapshots import SnapshotStore
from specto_app.icons import icon_names, render_icon
from specto_app.members import MemberImageProvider, image_url_for
from specto_app.theme import COLORS, GLYPHS, flat_theme, theme_for_web, theme_tokens


def test_icon_set_renders_tinted(qapp):
    names = icon_names()
    wanted = {"link", "undo", "redo", "lock", "unlock", "close", "overworld"}
    assert wanted <= set(names)
    img = render_icon("link", "2f6fab", 32)
    assert img.width() == 32 and not img.isNull()
    # some pixel carries the tint (blue channel high), the corners stay transparent
    hit = any(
        img.pixelColor(x, y).alpha() > 0 and img.pixelColor(x, y).blue() > 150
        for x in range(32)
        for y in range(32)
    )
    assert hit and img.pixelColor(0, 0).alpha() == 0
    assert render_icon("no-such-icon", "#000000", 16).pixelColor(8, 8).alpha() == 0


def test_snapshot_store_urls_and_ages(qapp):
    t = [100.0]
    store = SnapshotStore(clock=lambda: t[0])
    from PySide6.QtGui import QImage

    img = QImage(800, 600, QImage.Format.Format_ARGB32)
    img.fill(0xFF00FF00)
    rev = store.put("files", img)
    assert rev == 1 and store.url("files") == "image://snaps/files?1"
    assert store.get("files").width() == 640  # scaled down to MAX_EDGE
    t[0] += 30
    assert store.age_s("files") == 30 and store.url("lito") == "" and store.age_s("lito") is None
    store.drop("files")
    assert store.views() == []


def test_theme_flattening():
    web = theme_for_web()
    assert web["color-surface"] == COLORS["surface"]
    assert web["glyph-added"] == GLYPHS["added"]
    assert web["metric-pad"] == "12px" and web["metric-ghost-alpha"] == "0.45"
    assert web["metric-flash-ms"] == "900"
    assert web["font-mono"]
    flat = flat_theme()
    assert flat["card"] == COLORS["surface"] and flat["glyph_changed"] == "~" and flat["pad"] == 12
    tokens = theme_tokens()
    assert tokens["style"] == "B Calm Canvas" and set(tokens) >= {"colors", "glyphs", "metrics"}


def test_member_image_provider_and_urls(qapp, tmp_path: Path):
    z = tmp_path / "a.zip"
    img = tmp_path / "x.png"
    Image.new("RGB", (40, 30), (10, 200, 10)).save(img)
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(img, "front/x.png")
    plain = image_url_for(str(img))
    assert plain.startswith("file:") and plain.endswith("x.png")
    assert image_url_for("specto://items/c1/k/.jpg") == "specto://items/c1/k/.jpg"
    virt = image_url_for(f"{z}#/front/x.png")
    assert virt.startswith("image://member/") and "%23%2F" in virt
    provider = MemberImageProvider()
    size = QSize()
    out = provider.requestImage(virt[len("image://member/") :], size, QSize())
    assert (out.width(), out.height()) == (40, 30) and size == QSize(40, 30)
    scaled = provider.requestImage(virt[len("image://member/") :], QSize(), QSize(20, 20))
    assert scaled.width() == 20 and scaled.height() == 15
    missing = provider.requestImage("nope.zip%23%2Fx.png", QSize(), QSize())
    assert missing.width() == 1


def test_commands_import_without_pyside():
    """tools/gen_api.py imports specto_app.commands in a process that may lack Qt."""
    code = (
        "import sys; sys.modules['PySide6'] = None; sys.modules['PySide6.QtCore'] = None\n"
        "import specto_app.commands as c\n"
        "from specto_core.registry import registry\n"
        "assert 'snap' in registry and 'shell.locate' in registry\n"
        "assert not any(m.startswith('PySide6.') and sys.modules[m] for m in sys.modules)\n"
        "print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


@pytest.mark.parametrize("name", ["Main", "CanvasArea", "ViewSlot", "CanvasControls", "StatusBar"])
def test_shell_qml_is_listed(name: str):
    from specto_app.hosts.base import SHELL_QML

    assert (SHELL_QML / f"{name}.qml").is_file()
    assert f"{name} 1.0 {name}.qml" in (SHELL_QML / "qmldir").read_text(encoding="utf-8")
