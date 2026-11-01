"""The Qt side of the shared context menu (#154): ``ContextMenus``, the QML context
property ``contextMenus``.

A view asks for the menu with a hit and a window point (``request``; QML through its
bridge, a web page through ``contextmenu {x, y, target}``); the view's host turns the hit
into a target (``context_target``), the registry into rows, and ``openRequested`` tells
``Main.qml`` to open its one ``PopMenu`` there. A picked row comes back as ``trigger(id)``
and runs on the target the menu was opened for. The Menu key and Shift+F10 open it for the
focused view's selection (``eventFilter`` on the window; a text field or a web page keeps
them: a page turns them into its own ``contextmenu``). On Windows F10's key-up would also put
the window in menu mode, whose system menu takes the menu's keys (``keep_f10``, #165).
"""

from __future__ import annotations

import ctypes.wintypes
import sys
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QEvent,
    QObject,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QContextMenuEvent

from specto_core.errors import SpectoError

from .. import clipboard
from . import default_registry
from .registry import ContextMenuRegistry, MenuCall, MenuTarget


def _set_clipboard(text: str) -> None:
    clipboard.set_text(text)


def _plain(value: Any) -> Any:
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    return value


WM_SYSKEYUP = 0x0105
VK_F10 = 0x79


class F10MenuModeGuard(QAbstractNativeEventFilter):
    """Windows: the default handling of F10's key-up enters menu mode, and a window
    without a menu bar then drops its system menu on the next Down and takes Enter: the
    context menu Shift+F10 just opened never sees them (#165). Swallowing that key-up keeps
    F10 the shell's; Alt and Alt+Space still reach the system menu."""

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:
        # ``message`` is a shiboken VoidPtr, which has no truth value: test its address
        if bytes(event_type) == b"windows_generic_MSG" and int(message):
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_SYSKEYUP and msg.wParam == VK_F10:
                return True, 0
        return False, 0


class ContextMenus(QObject):
    #: rows (``PopMenu`` entries without actions), the window point, opened by a key
    openRequested = Signal("QVariantList", float, float, bool)

    def __init__(
        self,
        hosts: Any,
        registry: ContextMenuRegistry | None = None,
        *,
        clipboard: Callable[[str], None] | None = None,
        focused: Callable[[], str] | None = None,
        notices: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.hosts = hosts
        self.registry = registry or default_registry()
        self.clipboard = clipboard or _set_clipboard
        self.focused = focused or (lambda: "")
        self.notices = notices
        #: what the open (or last) menu is about, and its view's host
        self.target: MenuTarget | None = None
        self._host: Any = None
        self._f10: F10MenuModeGuard | None = None

    def keep_f10(self) -> None:
        """On Windows, keep F10 out of the window's menu mode (``F10MenuModeGuard``)."""
        app = QCoreApplication.instance()
        if sys.platform == "win32" and app is not None and self._f10 is None:
            self._f10 = F10MenuModeGuard()
            app.installNativeEventFilter(self._f10)

    # -- opening ---------------------------------------------------------------------------
    def request(
        self, view_id: str, hit: dict[str, Any] | None, x: float, y: float
    ) -> list[dict[str, Any]]:
        """Open the menu for ``hit`` in the view at the window point (x, y); the rows
        shown, [] when none applies (no menu opens)."""
        host, target = self._target(view_id, hit)
        return self._open(host, target, x, y, keyboard=False)

    @Slot(str, "QVariant", float, float)
    def open(self, view_id: str, hit: Any, x: float, y: float) -> None:
        self.request(view_id, _plain(hit) or None, x, y)

    @Slot(str, result=bool)
    def openFor(self, view_id: str) -> bool:
        """The keyboard's menu for the view's focused item (its selection), near it."""
        host, target = self._target(view_id, None)
        if target is None:
            return False
        try:
            where = host.context_anchor(target)
        except Exception as e:
            self._fail(e, view_id)
            where = None
        x, y = where or (0.0, 0.0)
        return bool(self._open(host, target, x, y, keyboard=True))

    @Slot(result=bool)
    def openFocused(self) -> bool:
        return self.openFor(self.focused())

    def _target(self, view_id: str, hit: dict[str, Any] | None) -> tuple[Any, MenuTarget | None]:
        host = self.hosts.hosts.get(view_id) if view_id and self.hosts is not None else None
        if host is None or not hasattr(host, "context_target"):
            return host, None
        try:
            return host, host.context_target(hit)
        except Exception as e:  # a broken view never takes the shell down
            self._fail(e, view_id)
            return host, None

    def _open(
        self, host: Any, target: MenuTarget | None, x: float, y: float, *, keyboard: bool
    ) -> list[dict[str, Any]]:
        rows = self.registry.rows(target) if target is not None else []
        if not rows:
            return []
        self.target, self._host = target, host
        self.openRequested.emit(rows, float(x), float(y), keyboard)
        return rows

    # -- running -----------------------------------------------------------------------------
    @Slot(str, result="QVariant")
    def trigger(self, entry_id: str) -> Any:
        """Run the picked entry on the menu's target; its result (None when it did not
        run)."""
        if self.target is None:
            return None
        call = MenuCall(target=self.target, host=self._host, clipboard=self.clipboard)
        try:
            return self.registry.run(entry_id, call)
        except Exception as e:
            self._fail(e, self.target.view_id, entry_id)
            return None

    def close(self) -> None:
        """The shell is going: let go of the hosts (whose context points back here) and
        the app's focus, so nothing keeps the closed app alive."""
        ctx = getattr(self.hosts, "ctx", None)
        if ctx is not None and ctx.context_menus is self:
            ctx.context_menus = None
        self.hosts = None
        app = QCoreApplication.instance()
        if self._f10 is not None and app is not None:
            app.removeNativeEventFilter(self._f10)
        self._f10 = None
        self.focused = lambda: ""
        self.target = self._host = None

    def _fail(self, e: Exception, view_id: str, entry_id: str = "") -> None:
        what = f"context menu {entry_id or 'target'} in {view_id}"
        if isinstance(e, SpectoError) and self.notices is not None:
            self.notices.error(e.message, fix=e.fix, code=e.code, command=what)
        elif self.notices is not None:
            self.notices.error(f"{what}: {type(e).__name__}: {e}")
        else:
            print(f"[ERROR] {what}: {type(e).__name__}: {e}")

    # -- keys ----------------------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """On the window: the Menu key and Shift+F10 open the focused view's menu, unless a
        text field or a web page has the focus (they keep the keys). Windows sends neither
        as a key press: Qt turns both into a keyboard ``ContextMenu`` event (#165)."""
        if event.type() == QEvent.Type.ContextMenu:
            menu_key = event.reason() == QContextMenuEvent.Reason.Keyboard
        elif event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            key, mods = event.key(), event.modifiers()
            menu_key = key == Qt.Key.Key_Menu or (
                key == Qt.Key.Key_F10 and mods & Qt.KeyboardModifier.ShiftModifier
            )
        else:
            return False
        if not menu_key or _editing_text(obj):
            return False
        self.openFocused()
        return True


def _editing_text(window: QObject) -> bool:
    from specto_app.canvas.controller import _editing_text as editing

    return editing(window)


__all__ = ["ContextMenus", "F10MenuModeGuard"]
