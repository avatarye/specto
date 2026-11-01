"""The file tree's rows (#115): the name only by default, a long one cut in the middle
(the start and the extension stay) and shown in full in the hover tip; the details
button in the tree's own footer (#122; the status bar has none) adds a second line
under each name, smaller: the modified time coloured by its age (theme tokens) and the
size. Each row shows its type icon (#122). Details are a per-user view setting (the app
settings, never the canvas document). Offscreen Qt, the real shell."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRectF, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest

from specto_core import CallContext, Project

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.theme import COLORS
from specto_app.view_settings import TREE_DETAILS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
LONG_NAME = "00002_frame_with_a_very_long_name_for_the_tree_7255.jpg"
DEEP = "a/b/c/d/e/f/" + LONG_NAME
#: path: seconds old, and the theme token its time is drawn in
AGES = {
    "fresh.md": (60, "age_minutes"),
    "hour.md": (20 * 60, "age_hour"),
    "day.md": (5 * 3600, "age_day"),
    "old.md": (40 * 86400, "age_old"),
}


@pytest.fixture(autouse=True)
def _details_off():
    """Every test starts (and leaves) with details off: the setting is per user."""
    QSettings().remove(TREE_DETAILS)
    yield
    QSettings().remove(TREE_DETAILS)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A six-folder chain (opened by the tests: the top has other entries, so it is no
    single-child chain) ending in a long name, and four files at the top, one per age."""
    proj = Project.init(tmp_path / "proj", "demo")
    deep = proj.root / DEEP
    deep.parent.mkdir(parents=True)
    deep.write_bytes(b"x" * 2048)
    now = time.time()
    for rel, (age, _) in AGES.items():
        (proj.root / rel).write_bytes(b"x" * 1536)
        os.utime(proj.root / rel, (now - age, now - age))
    return proj


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def rect(it):
    return it.mapRectToScene(QRectF(0, 0, it.width(), it.height()))


def tree_of_width(app, spin, width: int) -> None:
    doc = app.live.document()
    doc["views"]["fege"]["layout"]["size"] = [width, 600]
    app.engine.call("canvas.submit", {"document": doc, "label": "narrow"}, AGENT)
    app.hosts.hosts["fege"].expand_path("a/b/c/d/e/f")  # collapsed by default (#231)
    spin(until=lambda: find_item(app.window.contentItem(), f"treeRow:fege:{DEEP}") is not None)
    spin(until=lambda: abs(item(app, f"treeRow:fege:{DEEP}").width() - (width - 2)) < 30)
    spin(30)


def qml_eval(it, expression: str):
    """``expression`` in the item's own QML scope (a Text's elide mode has no Python
    converter)."""
    expr = QQmlExpression(QQmlEngine.contextForObject(it), it, expression)
    return expr.evaluate()[0]


def click(app, it) -> None:
    pos = rect(it).center().toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def test_rows_show_the_name_only_by_default(app, spin):
    tree_of_width(app, spin, 400)
    row = item(app, "treeRow:fege:old.md")
    assert row.height() == 20  # the #123 design's row
    assert not item(app, "treeDetails:fege:old.md").isVisible()
    assert find_item(app.window.contentItem(), "treeSize:fege:old.md") is None  # no columns
    assert find_item(app.window.contentItem(), "treeModified:fege:old.md") is None


def test_the_details_button_adds_the_time_by_age_and_the_size_under_each_name(app, spin):
    tree_of_width(app, spin, 400)
    assert find_item(app.window.contentItem(), "treeDetailsButton") is None  # not global
    button = item(app, "treeDetailsButton:fege")
    footer = rect(item(app, "fileTreeCaption:fege"))
    assert button.isVisible() and abs(rect(button).center().y() - footer.center().y()) < 4
    assert rect(button).right() <= rect(item(app, "treeRow:fege:old.md")).right() + 2
    doc, steps = app.live.document(), len(app.live.history_summaries())
    click(app, button)
    spin(until=lambda: item(app, "treeRow:fege:old.md").height() > 30)
    # on: the #123 design's soft accent square behind the accent icon
    assert QColor(button.property("color")) == QColor(COLORS["accent_soft"])

    assert QSettings().value(TREE_DETAILS, False, type=bool) is True  # the user's, kept
    assert app.live.document() == doc and len(app.live.history_summaries()) == steps
    for rel, (age, token) in AGES.items():
        stamp = item(app, f"treeStamp:fege:{rel}")
        size = item(app, f"treeDetailSize:fege:{rel}")
        assert stamp.isVisible() and size.isVisible()
        want = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - age))
        assert stamp.property("text").startswith(want[:13])  # to the hour: no flake
        assert len(stamp.property("text")) == len("2026-10-22 18:49:03")
        assert size.property("text") == "· 1.5 KB"
        assert QColor(stamp.property("color")) == QColor(COLORS[token]), rel
        name = item(app, f"treeName:fege:{rel}")
        assert stamp.property("font").pixelSize() < name.property("font").pixelSize()
        assert rect(stamp).top() >= rect(name).bottom() - 2  # under the name
    # a folder: its newest file and the total under it
    assert item(app, "treeDetailSize:fege:a").property("text") == "· 2.0 KB"

    click(app, button)
    spin(until=lambda: item(app, "treeRow:fege:old.md").height() == 20)
    assert QSettings().value(TREE_DETAILS, True, type=bool) is False


def test_a_long_name_is_cut_in_the_middle_and_shows_in_full_in_the_tip(app, spin):
    tree_of_width(app, spin, 260)
    name = item(app, f"treeName:fege:{DEEP}")
    assert name.property("truncated")
    assert qml_eval(name, "elide === Text.ElideMiddle")  # the start and the extension stay
    row = rect(item(app, f"treeRow:fege:{DEEP}"))
    n = rect(name)
    char_w = name.implicitWidth() / len(LONG_NAME)
    assert n.width() >= 8 * char_w  # a few characters each side of the cut
    assert row.left() <= n.left() and n.right() <= row.right()

    QTest.mouseMove(app.window, QPoint(1, 1))
    spin(20)
    QTest.mouseMove(app.window, n.center().toPoint())
    spin(60)
    tip = item(app, f"treeTip:fege:{DEEP}")
    assert tip.isVisible() and tip.property("text") == LONG_NAME


def test_a_name_that_fits_is_not_cut_and_has_no_tip(app, spin):
    tree_of_width(app, spin, 400)
    name = item(app, "treeName:fege:old.md")
    assert not name.property("truncated") and name.property("text") == "old.md"
    QTest.mouseMove(app.window, QPoint(1, 1))
    spin(20)
    QTest.mouseMove(app.window, rect(name).center().toPoint())
    spin(60)
    tip = find_item(app.window.contentItem(), "treeTip:fege:old.md")
    assert tip is None or not tip.isVisible()


def test_each_row_shows_its_type_icon_left_of_the_name(app, spin):
    """#122: a 16 px icon per row, of the file's type or the folder's name, open or
    closed, loaded from the vendored set, between the chevron's slot and the name."""
    tree_of_width(app, spin, 400)
    for path, icon in (("old.md", "markdown"), ("a", "folder-open"), (DEEP, "image")):
        it = item(app, f"treeIcon:fege:{path}")
        spin(until=lambda it=it: qml_eval(it, "status === Image.Ready"))
        assert qml_eval(it, "String(source)").endswith(f"icons/{icon}.svg"), path
        assert (it.width(), it.height()) == (16, 16)
        assert rect(it).right() < rect(item(app, f"treeName:fege:{path}")).left()


def test_rows_follow_the_design_chevron_icon_name_and_the_footer_counts_the_picks(app, spin):
    """#126 design, file tree: 6 px, a 10 px chevron, 4 px, the 16 px type icon, 4 px,
    the name, 10 px of indent per level, 20 px rows; the footer (the shared 24 px one)
    reads "<k> of <n> selected" (or "<n> files") left of the details toggle, a round
    chrome button that is on while the details show."""
    tree_of_width(app, spin, 400)
    row = rect(item(app, "treeRow:fege:a"))
    chevron = rect(item(app, "treeChevron:fege:a"))
    icon = rect(item(app, "treeIcon:fege:a"))
    name = rect(item(app, "treeName:fege:a"))
    assert (chevron.width(), chevron.height()) == (10, 10)
    assert row.height() == 20
    assert chevron.left() - row.left() == pytest.approx(6, abs=1)
    assert icon.left() - chevron.right() == pytest.approx(4, abs=1)
    assert name.left() - icon.right() == pytest.approx(4, abs=1)
    child = rect(item(app, "treeIcon:fege:a/b"))
    assert child.left() - icon.left() == pytest.approx(10, abs=1)  # one level in
    assert find_item(app.window.contentItem(), "treeChevron:fege:old.md") is None  # a file

    caption = item(app, "fileTreeCaption:fege")
    assert caption.property("text") == "5 files"
    click(app, item(app, "treeName:fege:old.md"))
    spin(until=lambda: caption.property("text") == "1 of 5 selected")
    toggle = item(app, "treeDetailsButton:fege")
    assert rect(caption).right() < rect(toggle).left()
    assert item(app, "footer:fege").height() == 24
    assert toggle.width() == toggle.height() == 22 and toggle.property("radius") == 11
    assert not toggle.property("on")
    click(app, toggle)
    spin(until=lambda: toggle.property("on"))
    click(app, toggle)
    spin(until=lambda: not toggle.property("on"))
