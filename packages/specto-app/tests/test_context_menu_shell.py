"""The shell's one context menu (#154), offscreen in the real shell: a right-click on a
file tree row opens it at the pointer with the browser entries, a click on Copy path puts
the row's path on the clipboard; the Menu key and Shift+F10 open it for the focused
tree's selection and the keys pick a row."""

from __future__ import annotations

import ctypes.wintypes
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication
from PySide6.QtTest import QTest
from shiboken6 import VoidPtr

from specto_core import Project

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.menu.shell import VK_F10, WM_SYSKEYUP, F10MenuModeGuard


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    (proj.root / "notes").mkdir()
    for name in ("alpha.md", "beta.md"):
        (proj.root / "notes" / name).write_text(f"# {name}\n", encoding="utf-8")
    return proj


def locate(app, name: str) -> dict:
    return app.engine.call("shell.locate", {"name": name})


def centre(app, name: str) -> QPoint:
    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    return item.mapToScene(item.boundingRect().center()).toPoint()


def row(app, spin, path: str) -> str:
    name = f"treeRow:fege:{path}"
    spin(until=lambda: locate(app, name)["visible"])
    return name


def test_right_click_on_a_tree_row_opens_the_menu_there_and_copies_the_path(app, spin):
    QGuiApplication.clipboard().setText("")
    at = centre(app, row(app, spin, "notes/alpha.md"))
    QTest.mouseClick(app.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, at)
    spin(until=lambda: locate(app, "menu:copy.path")["visible"])
    assert locate(app, "menu:copy.relative_path")["visible"]
    # the rows are laid out (a frame later; a click before that hits overlapping rows)
    spin(
        until=lambda: (
            locate(app, "menu:copy.relative_path")["rect"][1]
            > locate(app, "menu:copy.path")["rect"][1]
        )
    )
    # the tree's open entries come first (SPEC-32): the menu's corner is the first row's
    x, y = locate(app, "menu:tree.open_dedicated")["rect"][:2]
    assert abs(x - at.x()) <= 2 and 0 <= y - at.y() <= 8  # the menu's corner at the pointer
    assert app.hosts.snapshot("fege")["selected"] == []  # a right-click is no pick

    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        centre(app, "menu:copy.path"),
    )
    spin(until=lambda: not locate(app, "menu:copy.path")["visible"])
    assert QGuiApplication.clipboard().text() == os.path.join(
        str(app.project.root), "notes", "alpha.md"
    )


@pytest.mark.parametrize(
    "keys",
    [
        (Qt.Key.Key_Menu, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier),
    ],
)
def test_the_menu_key_opens_the_menu_for_the_focused_selection(app, spin, keys):
    QGuiApplication.clipboard().setText("")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        centre(app, row(app, spin, "notes/alpha.md")),
    )
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        centre(app, row(app, spin, "notes/beta.md")),
    )
    spin(until=lambda: len(app.hosts.snapshot("fege")["selected"]) == 2)
    assert app.context_menus.focused() == "fege"  # the tree took the keyboard focus

    QTest.keyClick(app.window, keys[0], keys[1])
    spin(until=lambda: locate(app, "menu:copy.path")["visible"])
    # the first row is the keyboard's (Open with system default, #186): Down past "Open
    # with" and Copy path to Copy relative path, Enter runs it
    for _ in range(3):
        QTest.keyClick(app.window, Qt.Key.Key_Down)
    QTest.keyClick(app.window, Qt.Key.Key_Return)
    spin(until=lambda: not locate(app, "menu:copy.path")["visible"])
    assert QGuiApplication.clipboard().text() == "\n".join(
        [os.path.join("notes", "alpha.md"), os.path.join("notes", "beta.md")]
    )


def keys_holder(app) -> str:
    """The view whose item holds the window's keyboard focus ("" for none; no fallback
    to the canvas's focused view)."""
    items = {id(h.item): vid for vid, h in app.hosts.hosts.items() if getattr(h, "item", None)}
    item = app.window.activeFocusItem()
    while item is not None:
        if id(item) in items:
            return items[id(item)]
        item = item.parentItem()
    return ""


def windows_menu_key(app) -> None:
    """Shift+F10 or the Menu key as Windows delivers them: a keyboard ContextMenu event
    on the window, no key press (#165)."""
    QGuiApplication.sendEvent(
        app.window,
        QContextMenuEvent(
            QContextMenuEvent.Reason.Keyboard,
            QPoint(0, 0),
            QPoint(0, 0),
            Qt.KeyboardModifier.ShiftModifier,
        ),
    )


def right_click_menu(app, spin, path: str) -> None:
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
        centre(app, row(app, spin, path)),
    )
    spin(until=lambda: locate(app, "menu:copy.path")["visible"])
    spin(
        until=lambda: (
            locate(app, "menu:copy.relative_path")["rect"][1]
            > locate(app, "menu:copy.path")["rect"][1]
        )
    )


def test_after_copy_path_windows_shift_f10_opens_the_menu_again(app, spin):
    """#165: a right-click and Copy path leave the keys with the tree, and Shift+F10 as
    Windows sends it opens the tree's menu again."""
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        centre(app, row(app, spin, "notes/alpha.md")),
    )
    spin(until=lambda: keys_holder(app) == "fege")
    right_click_menu(app, spin, "notes/beta.md")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        centre(app, "menu:copy.path"),
    )
    spin(until=lambda: not locate(app, "menu:copy.path")["visible"])
    spin(until=lambda: keys_holder(app) == "fege")  # the action handed the keys back

    windows_menu_key(app)
    spin(until=lambda: locate(app, "menu:copy.path")["visible"])
    # the keyboard's menu on one file: its first row is "Open in its own view" (SPEC-32),
    # then the contextual one and the launchers' two (#186); four down is Copy path
    for _ in range(4):
        QTest.keyClick(app.window, Qt.Key.Key_Down)
    QTest.keyClick(app.window, Qt.Key.Key_Return)
    spin(until=lambda: not locate(app, "menu:copy.path")["visible"])
    assert QGuiApplication.clipboard().text() == os.path.join(
        str(app.project.root), "notes", "alpha.md"
    )
    spin(until=lambda: keys_holder(app) == "fege")


def test_esc_closes_a_right_click_menu_and_runs_nothing(app, spin):
    """#165: a menu opened by the pointer takes the keys too: Esc closes it, nothing
    runs, and the tree has the keys again."""
    QGuiApplication.clipboard().setText("before")
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        centre(app, row(app, spin, "notes/alpha.md")),
    )
    spin(until=lambda: keys_holder(app) == "fege")
    right_click_menu(app, spin, "notes/beta.md")
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(until=lambda: not locate(app, "menu:copy.path")["visible"])
    assert QGuiApplication.clipboard().text() == "before"
    spin(until=lambda: keys_holder(app) == "fege")
    windows_menu_key(app)
    spin(until=lambda: locate(app, "menu:copy.path")["visible"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows menu mode")
def test_f10_key_up_never_reaches_the_windows_menu_mode():
    """#165: F10's key-up is swallowed (else the system menu takes the keyboard menu's
    Down and Enter); other keys' key-ups and F10's key-down pass."""
    guard = F10MenuModeGuard()
    kind = QByteArray(b"windows_generic_MSG")

    def native(message: int, key: int) -> tuple[bool, int]:
        msg = ctypes.wintypes.MSG(None, message, key, 0)
        # as Qt hands it over: a VoidPtr (it has no truth value)
        return guard.nativeEventFilter(kind, VoidPtr(ctypes.addressof(msg)))

    assert native(WM_SYSKEYUP, VK_F10) == (True, 0)
    assert native(WM_SYSKEYUP, 0x12)[0] is False  # Alt
    assert native(0x0104, VK_F10)[0] is False  # WM_SYSKEYDOWN: Windows' WM_CONTEXTMENU
