"""Opening or closing a folder keeps the file tree where it was (#110): the list does not
jump back to the top, and the folder's row stays under the mouse. Offscreen Qt, the
real shell, a mouse click on the chevron."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtTest import QTest

from specto_core import Project

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item

FOLDERS = 120


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """One top folder (open by default) holding many closed folders of a few files."""
    proj = Project.init(tmp_path / "proj", "demo")
    for i in range(FOLDERS):
        d = proj.root / "vault" / f"d{i:03d}"
        d.mkdir(parents=True)
        for j in range(4):
            (d / f"note{j}.md").write_bytes(b"x")
    return proj


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def scene_y(it) -> float:
    return it.mapRectToScene(QRectF(0, 0, it.width(), it.height())).top()


def scrolled_tree(app, spin, content_y: float):
    spin(until=lambda: find_item(app.window.contentItem(), "treeRow:fege:vault/d000") is not None)
    tree = item(app, "fileTreeList:fege")
    spin(until=lambda: tree.property("contentHeight") > 2 * tree.height())
    tree.setProperty("contentY", content_y)
    spin(30)
    return tree


def click_chevron(app, spin, path: str) -> None:
    """A real click on the folder's chevron (the 14 px box left of its icon, #122)."""
    name = item(app, f"treeIcon:fege:{path}")
    at = name.mapToScene(name.boundingRect().topLeft())
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(at.x()) - 10, int(at.y() + name.height() / 2)),
    )
    spin(60)


@pytest.mark.parametrize(("path", "content_y"), [("vault/d040", 600), ("vault/d070", 1200)])
def test_opening_a_folder_far_down_keeps_the_scroll_position(app, spin, path, content_y):
    tree = scrolled_tree(app, spin, content_y)
    before_y = tree.property("contentY")
    row_y = scene_y(item(app, f"treeRow:fege:{path}"))

    click_chevron(app, spin, path)
    host = app.hosts.hosts["fege"]
    assert path in host.model.expanded  # it did open
    assert tree.property("contentY") == pytest.approx(before_y, abs=1)
    assert scene_y(item(app, f"treeRow:fege:{path}")) == pytest.approx(row_y, abs=1)
    assert find_item(app.window.contentItem(), f"treeRow:fege:{path}/note0.md") is not None

    click_chevron(app, spin, path)  # and closing it again
    assert path not in host.model.expanded
    assert tree.property("contentY") == pytest.approx(before_y, abs=1)
    assert scene_y(item(app, f"treeRow:fege:{path}")) == pytest.approx(row_y, abs=1)


def test_picking_a_row_far_down_keeps_the_scroll_position(app, spin):
    tree = scrolled_tree(app, spin, 800)
    before_y = tree.property("contentY")
    name = item(app, "treeName:fege:vault/d050")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        name.mapToScene(name.boundingRect().center()).toPoint(),
    )
    spin(60)
    assert app.hosts.hosts["fege"].model.selected == {"vault/d050"}
    assert tree.property("contentY") == pytest.approx(before_y, abs=1)


def test_a_rescan_that_brings_new_files_keeps_the_scroll_position(app, spin, project):
    tree = scrolled_tree(app, spin, 800)
    before_y = tree.property("contentY")
    host = app.hosts.hosts["fege"]
    n = len(host.rows())
    # a new folder in the loaded vault/, above the view (d001 is closed, so a file in it
    # would not be listed until it is opened, #231)
    (project.root / "vault" / "d000a").mkdir()
    name = item(app, "refresh:project")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        name.mapToScene(name.boundingRect().center()).toPoint(),
    )
    spin(until=lambda: len(host.rows()) == n + 1)
    spin(60)
    assert tree.property("contentY") == pytest.approx(before_y, abs=1)


def test_a_row_half_scrolled_out_is_located_by_its_shown_part(app, spin):
    # #167: the wheel can leave the top row half under the card's header; shell.locate
    # says which part of it is in view, and the chevron there still opens the folder
    tree = scrolled_tree(app, spin, 830)  # 20 px rows under vault/: d040 starts 10 px above
    top = scene_y(tree)
    info = app.engine.call("shell.locate", {"name": "treeChevron:fege:vault/d040"})
    x, y, w, h = info["rect"]
    sx, sy, sw, sh = info["shown"]
    assert y < top
    assert sy == pytest.approx(top, abs=0.5) and sy + sh == pytest.approx(y + h, abs=0.5)
    assert (sx, sw) == pytest.approx((x, w))
    hidden = app.engine.call("shell.locate", {"name": "treeRow:fege:vault/d030"})
    assert hidden["visible"] and hidden["shown"] is None  # still a delegate, scrolled away

    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(sx + sw / 2), int(sy + sh / 2)),
    )
    spin(60)
    assert "vault/d040" in app.hosts.hosts["fege"].model.expanded
