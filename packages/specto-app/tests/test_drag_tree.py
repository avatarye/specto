"""Seam 2 (#185): dragging file tree rows out, offscreen, in the real shell. A left drag
past the platform's drag distance carries the row (or the selection it is in) into a
QML DropArea with the drag contract's mime data; a click still picks, Shift still
ranges, and the wheel still scrolls the list."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from drag_helpers import DropTarget, drags_into

from specto_core import Project

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest

from specto_app.app import find_item

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    for rel in ("notes/alpha.md", "notes/beta.md", "notes/gamma.md", "runs/a.jpg"):
        f = proj.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    return proj


@pytest.fixture
def target(qapp):
    t = DropTarget()
    yield t
    t.close()


def row_point(app, spin, path: str) -> QPoint:
    spin(until=lambda: find_item(app.window.contentItem(), f"treeName:fege:{path}") is not None)
    name = find_item(app.window.contentItem(), f"treeName:fege:{path}")
    return name.mapToScene(name.boundingRect().center()).toPoint()


def open_all(app, spin) -> None:
    host = app.hosts.hosts["fege"]
    spin(until=lambda: "notes" in host.model._nodes)
    host.load_all()  # every folder listed (#231) ...
    host.model.expanded = {p for p, n in host.model._nodes.items() if n.is_dir}
    host.model._set_rows(host.model._walk())  # ... and open
    spin(30)


def drag_from(app, at: QPoint, dx: int = 60, dy: int = 4, steps: int = 6, mods=NONE) -> None:
    QTest.mousePress(app.window, LEFT, mods, at)
    for i in range(1, steps + 1):
        QTest.mouseMove(app.window, at + QPoint(dx * i // steps, dy * i // steps))
    QTest.mouseRelease(app.window, LEFT, mods, at + QPoint(dx, dy))


def test_a_row_drags_out_with_its_file_and_picks_nothing(app, spin, target, project):
    open_all(app, spin)
    host = app.hosts.hosts["fege"]
    at = row_point(app, spin, "notes/beta.md")
    with drags_into(target) as drags:
        drag_from(app, at)
        spin(30)
    assert len(drags) == 1 and len(target.drops) == 1
    drop = target.drops[0]
    beta = os.path.join(str(project.root), "notes", "beta.md")
    assert drop["text"] == beta
    assert drop["urls"] == [Path(beta).as_uri()]
    doc = target.specto()
    assert doc["project"] == str(project.root).replace("\\", "/")
    assert [(i["view"], i["type"], i["key"], i["path"], i["kind"]) for i in doc["items"]] == [
        ("fege", "file_tree", "notes/beta.md", "notes/beta.md", "file")
    ]
    assert host.model.selected == set()  # a drag is no pick


def test_a_row_inside_the_selection_drags_the_whole_selection(app, spin, target):
    open_all(app, spin)
    host = app.hosts.hosts["fege"]
    host.select_paths(["notes/alpha.md", "notes/gamma.md", "runs/a.jpg"])
    with drags_into(target):
        drag_from(app, row_point(app, spin, "notes/gamma.md"))
        spin(30)
    items = target.specto()["items"]
    assert [(i["key"], i["kind"]) for i in items] == [
        ("notes/alpha.md", "file"),
        ("notes/gamma.md", "file"),
        ("runs/a.jpg", "image"),
    ]
    assert len(target.drops[0]["urls"]) == 3
    # a row outside the selection drags alone, and leaves the selection as it was
    with drags_into(target):
        drag_from(app, row_point(app, spin, "notes/beta.md"))
        spin(30)
    assert [i["key"] for i in target.specto()["items"]] == ["notes/beta.md"]
    assert host.model.selected == {"notes/alpha.md", "notes/gamma.md", "runs/a.jpg"}


def many_files(app, spin, project) -> None:
    """Enough rows that the list scrolls."""
    for i in range(80):
        (project.root / "many" / f"f{i:03d}.md").parent.mkdir(exist_ok=True)
        (project.root / "many" / f"f{i:03d}.md").write_bytes(b"x")
    host = app.hosts.hosts["fege"]
    spin(until=lambda: "many" in host.model._nodes, timeout=10)
    open_all(app, spin)
    lst = find_item(app.window.contentItem(), "fileTreeList:fege")
    spin(until=lambda: lst.property("contentHeight") > lst.height() + 100)


def test_a_vertical_drag_drags_the_row_and_does_not_flick_the_list(app, spin, target, project):
    many_files(app, spin, project)
    lst = find_item(app.window.contentItem(), "fileTreeList:fege")
    with drags_into(target) as drags:
        drag_from(app, row_point(app, spin, "many/f010.md"), dx=6, dy=-80)
        spin(30)
    assert len(drags) == 1 and [i["key"] for i in target.specto()["items"]] == ["many/f010.md"]
    assert lst.property("contentY") == 0


def test_clicks_and_shift_ranges_still_pick_and_start_no_drag(app, spin, target):
    open_all(app, spin)
    host = app.hosts.hosts["fege"]
    with drags_into(target) as drags:
        at = row_point(app, spin, "notes/alpha.md")
        QTest.mousePress(app.window, LEFT, NONE, at)
        QTest.mouseMove(app.window, at + QPoint(2, 1))  # inside the drag distance
        QTest.mouseRelease(app.window, LEFT, NONE, at + QPoint(2, 1))
        spin(30)
        assert host.model.selected == {"notes/alpha.md"}
        QTest.mouseClick(
            app.window,
            LEFT,
            Qt.KeyboardModifier.ShiftModifier,
            row_point(app, spin, "notes/gamma.md"),
        )
        spin(30)
    assert host.model.selected == {"notes/alpha.md", "notes/beta.md", "notes/gamma.md"}
    assert drags == [] and target.drops == []


def test_the_wheel_still_scrolls_the_tree(app, spin, project):
    many_files(app, spin, project)
    lst = find_item(app.window.contentItem(), "fileTreeList:fege")
    at = lst.mapToScene(QPointF(lst.width() / 2, lst.height() / 2))
    ev = QWheelEvent(
        at,
        app.window.mapToGlobal(at),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        NONE,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QGuiApplication.sendEvent(app.window, ev)
    spin(until=lambda: lst.property("contentY") > 0)


def test_space_and_a_drag_over_a_row_pans_the_canvas_and_drags_nothing(app, spin, target):
    open_all(app, spin)
    at = row_point(app, spin, "notes/alpha.md")
    with drags_into(target) as drags:
        QTest.keyPress(app.window, Qt.Key.Key_Space)
        spin(5)
        drag_from(app, at, dx=-120, dy=-40)
        QTest.keyRelease(app.window, Qt.Key.Key_Space)
        spin(30)
    assert drags == []
    assert app.controller.pan == {"x": 120.0, "y": 40.0}
