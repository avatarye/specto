"""The test driver (#237): the GUI flows (``tools/gui_flows/*.txt``) driven through Qt's
public test API instead of a real mouse. Enabled only by ``specto-app --test-driver``;
a normal launch never starts it (``enabled()`` is False and nothing here runs).

When enabled, :func:`start` opens a ``QLocalServer`` of its own (``specto-drive-<pid>``,
written to ``<user data dir>/drive.json``) and answers one JSON line per request on the
GUI thread: ``{"id": n, "op": "click", "args": {...}}`` -> ``{"id": n, "result": ...}``
or ``{"id": n, "error": "..."}``. The operations locate items by ``objectName`` in the
current top-level window (the welcome card or the shell), send mouse, wheel and key
events with ``QTest`` and synthetic Qt events, grab the window to PNGs, resize it and
read the fake clipboard. The runner is ``tools/gui_qtdrive.py``.

A driver run takes no attention and leaves nothing behind (issue #237 decisions 4 and 5):

- windows never ask for the OS focus (``raise_window`` skips ``raise_()`` and
  ``requestActivate()``; off the offscreen platform the shell opens with
  ``Qt.WindowDoesNotAcceptFocus`` and is lowered to the bottom of the z order as it
  shows, so it sits behind the user's work instead of over it, #34), and keys go to the
  window's own focus item: with no focus window in the process Qt Quick treats the
  window as focused once it has had a ``FocusIn``, which :meth:`TestDriver.ensure_focus`
  sends in-process;
- the clipboard is :mod:`specto_app.clipboard`'s in-memory one, the video view's mpv plays
  into the ``null`` audio output, and launchers (#186) start nothing but this Python (the
  flows' fake programs); every fake call is logged to ``<user data dir>/launches.jsonl``.

Nothing here is reachable through the command endpoint, so the generated SDK and
``api-info.json`` do not change.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import shiboken6
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QFocusEvent, QGuiApplication, QKeyEvent, QWheelEvent, QWindow
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from . import clipboard
from .recents import user_data_dir

if TYPE_CHECKING:
    from .app import SpectoApp

_enabled = False
_driver: TestDriver | None = None

#: one wheel notch, in eighths of a degree (what ``QWheelEvent.angleDelta`` counts)
NOTCH = 120

_BUTTONS = {
    "left": Qt.MouseButton.LeftButton,
    "right": Qt.MouseButton.RightButton,
    "middle": Qt.MouseButton.MiddleButton,
}
_MODS = {
    "ctrl": Qt.KeyboardModifier.ControlModifier,
    "control": Qt.KeyboardModifier.ControlModifier,
    "shift": Qt.KeyboardModifier.ShiftModifier,
    "alt": Qt.KeyboardModifier.AltModifier,
    "meta": Qt.KeyboardModifier.MetaModifier,
    "win": Qt.KeyboardModifier.MetaModifier,
}
#: pyautogui's key names (what the flows spell) to Qt keys
_KEYS = {
    "enter": Qt.Key.Key_Return,
    "return": Qt.Key.Key_Return,
    "esc": Qt.Key.Key_Escape,
    "escape": Qt.Key.Key_Escape,
    "tab": Qt.Key.Key_Tab,
    "space": Qt.Key.Key_Space,
    "backspace": Qt.Key.Key_Backspace,
    "delete": Qt.Key.Key_Delete,
    "del": Qt.Key.Key_Delete,
    "home": Qt.Key.Key_Home,
    "end": Qt.Key.Key_End,
    "pageup": Qt.Key.Key_PageUp,
    "pgup": Qt.Key.Key_PageUp,
    "pagedown": Qt.Key.Key_PageDown,
    "pgdn": Qt.Key.Key_PageDown,
    "up": Qt.Key.Key_Up,
    "down": Qt.Key.Key_Down,
    "left": Qt.Key.Key_Left,
    "right": Qt.Key.Key_Right,
    "insert": Qt.Key.Key_Insert,
    "menu": Qt.Key.Key_Menu,
    "apps": Qt.Key.Key_Menu,
    # a modifier held on its own (``dragxy ... mod=alt`` presses it before the button)
    "alt": Qt.Key.Key_Alt,
    "ctrl": Qt.Key.Key_Control,
    "control": Qt.Key.Key_Control,
    "shift": Qt.Key.Key_Shift,
    "meta": Qt.Key.Key_Meta,
    "win": Qt.Key.Key_Meta,
    "`": Qt.Key.Key_QuoteLeft,
    "backtick": Qt.Key.Key_QuoteLeft,
    "-": Qt.Key.Key_Minus,
    "=": Qt.Key.Key_Equal,
    "+": Qt.Key.Key_Plus,
    ",": Qt.Key.Key_Comma,
    ".": Qt.Key.Key_Period,
    "/": Qt.Key.Key_Slash,
    "[": Qt.Key.Key_BracketLeft,
    "]": Qt.Key.Key_BracketRight,
    ";": Qt.Key.Key_Semicolon,
    "'": Qt.Key.Key_Apostrophe,
    "\\": Qt.Key.Key_Backslash,
}
for _n in range(1, 25):
    _KEYS[f"f{_n}"] = getattr(Qt.Key, f"Key_F{_n}")
#: the text a key press carries (pyautogui types it; Qt's text field of the event)
_KEY_TEXT = {
    Qt.Key.Key_Return: "\r",
    Qt.Key.Key_Tab: "\t",
    Qt.Key.Key_Space: " ",
    Qt.Key.Key_Backspace: "\b",
    Qt.Key.Key_Escape: "\x1b",
    Qt.Key.Key_QuoteLeft: "`",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_Equal: "=",
    Qt.Key.Key_Plus: "+",
    Qt.Key.Key_Comma: ",",
    Qt.Key.Key_Period: ".",
    Qt.Key.Key_Slash: "/",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_Semicolon: ";",
    Qt.Key.Key_Apostrophe: "'",
    Qt.Key.Key_Backslash: "\\",
}


def enable() -> None:
    """``--test-driver``: arm the driver; :func:`start` opens it once a ``QGuiApplication``
    exists. Also swaps in the fakes that need no Qt (the clipboard, the launchers)."""
    global _enabled
    _enabled = True
    clipboard.use_fake(True)
    _fake_launchers()


def enabled() -> bool:
    return _enabled


def offscreen() -> bool:
    return QGuiApplication.platformName() == "offscreen"


def start() -> TestDriver | None:
    """Open the driver's server (idempotent); None when the driver is not enabled."""
    global _driver
    if not _enabled:
        return None
    if _driver is None:
        _driver = TestDriver()
    return _driver


def attach(app: SpectoApp) -> None:
    """The shell is up: items are located through its snap provider (``tile:`` and the
    hosts' own rects included) and the window is the shell's."""
    drv = start()
    if drv is not None:
        drv.app = app


def driver() -> TestDriver | None:
    return _driver


def parse_mods(spec: Any) -> Qt.KeyboardModifier:
    """``"ctrl+shift"``, ``["ctrl", "shift"]`` or nothing as Qt modifiers."""
    mods = Qt.KeyboardModifier.NoModifier
    names = spec.split("+") if isinstance(spec, str) else list(spec or [])
    for n in names:
        if n:
            mods |= _MODS[n.lower()]
    return mods


def parse_key(name: str) -> tuple[Qt.Key, Qt.KeyboardModifier, str]:
    """A pyautogui-style key name (``ctrl+z``, ``shift+f10``, ``escape``, ``w``, `````)
    as the Qt key, its modifiers and the text the press carries."""
    parts = name.split("+") if name != "+" else ["+"]
    # "ctrl+-": the last part is the key, the rest modifiers
    key_name, mod_names = parts[-1], parts[:-1]
    if name.endswith("++"):  # "ctrl++"
        key_name, mod_names = "+", parts[:-2]
    mods = parse_mods(mod_names)
    low = key_name.lower()
    if low in _KEYS:
        key = _KEYS[low]
        text = _KEY_TEXT.get(key, "")
    elif len(key_name) == 1:
        ch = key_name
        if ch.isalpha():
            key = getattr(Qt.Key, f"Key_{ch.upper()}")
            if ch.isupper():
                mods |= Qt.KeyboardModifier.ShiftModifier
        elif ch.isdigit():
            key = getattr(Qt.Key, f"Key_{ch}")
        else:
            raise ValueError(f"unknown key {name!r}")
        text = ch
    else:
        raise ValueError(f"unknown key {name!r}")
    if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
        text = ""  # a shortcut types nothing
    return key, mods, text


def _fake_launchers() -> None:
    """Launchers (#186) start nothing real from a driver run: the system default,
    Explorer and the editor do nothing, and a launch may start this Python alone (the
    flows' fake program that records its argv). Each call is logged."""
    from .launchers import run

    real_spawn = run.spawn_program
    me = os.path.normcase(os.path.abspath(sys.executable))

    def log(kind: str, **what: Any) -> None:
        try:
            path = user_data_dir() / "launches.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": kind, **what}) + "\n")
        except OSError:
            pass

    def spawn(argv: Any, cwd: str = "", env: Any = None) -> int:
        argv = list(argv)
        if os.path.normcase(os.path.abspath(argv[0])) != me:
            log("refused", argv=argv)
            raise OSError(f"the test driver may launch only {sys.executable}, not {argv[0]}")
        log("spawn", argv=argv, cwd=cwd)
        return real_spawn(argv, cwd, env)

    run.spawn_program = spawn  # type: ignore[assignment]
    run.start_file = lambda path: log("start_file", path=str(path))  # type: ignore[assignment]
    run.reveal = lambda paths: log("reveal", paths=[str(p) for p in paths])  # type: ignore[assignment]
    run.edit_file = lambda path: log("edit_file", path=str(path))  # type: ignore[assignment]


def window_flags(base: Qt.WindowType) -> Qt.WindowType:
    """The flags a shell or welcome window opens with under the driver: off the offscreen
    platform the window must not take the focus (a real window on the GPU tier opens
    behind the user's work, #237 decision 4)."""
    if _enabled and not offscreen():
        return base | Qt.WindowType.WindowDoesNotAcceptFocus
    return base


#: ops answered once the window has synchronised its next frame (:class:`NextSync`)
NEXT_FRAME_OPS = frozenset({"locate"})
#: how long an op waits for that frame (a hidden or unexposed window draws none)
FRAME_WAIT_MS = 500


class NextSync(QObject):
    """Run ``fn`` once ``win`` has synchronised its next frame (SPEC-41), at most
    ``timeout_ms`` later. A ListView applies its model's row changes at its polish, and
    until then a removed row's delegate is still in the item tree, visible; every render
    loop polishes the window's items just before it synchronises, so after the next
    ``beforeSynchronizing`` the tree is the one that frame draws. That signal, not
    ``frameSwapped``: a frame already rendering when the wait starts (the threaded loop)
    swaps without the polish it is waiting for, while a synchronisation always follows a
    polish done on the GUI thread after it. Nothing is forced: ``win.update()`` asks for
    the frame the window would draw anyway. Queued, so ``fn`` runs on the GUI thread."""

    def __init__(self, win: QQuickWindow, fn: Any, timeout_ms: int = FRAME_WAIT_MS) -> None:
        super().__init__()
        self._win = win
        self._fn = fn
        self._done = False
        #: it ran on the timeout, not on a frame
        self.timed_out = False
        win.beforeSynchronizing.connect(self._synced, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._timer.start(int(timeout_ms))
        win.update()

    @property
    def done(self) -> bool:
        return self._done

    @Slot()
    def _synced(self) -> None:
        self._finish()

    @Slot()
    def _timed_out(self) -> None:
        self.timed_out = not self._done
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        with contextlib.suppress(RuntimeError, TypeError):  # the window is gone
            self._win.beforeSynchronizing.disconnect(self._synced)
        self._fn()


class TestDriver(QObject):
    """One ``QLocalServer``; every connection speaks the one-line JSON protocol."""

    def __init__(self) -> None:
        super().__init__()
        self.app: SpectoApp | None = None
        self.server = QLocalServer(self)
        self.name = f"specto-drive-{os.getpid()}"
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            raise RuntimeError(
                f"test driver cannot listen on {self.name}: {self.server.errorString()}"
            )
        self.server.newConnection.connect(self._on_new_connection)
        self._bufs: dict[int, bytes] = {}
        self._socks: dict[int, QLocalSocket] = {}
        #: a socket's op waiting for the next frame (its later lines wait behind it)
        self._waits: dict[int, NextSync] = {}
        self.info = {
            "pid": os.getpid(),
            "address": self.server.fullServerName(),
            "kind": "pipe" if sys.platform == "win32" else "unix",
            "platform": QGuiApplication.platformName(),
        }
        self.path = user_data_dir() / "drive.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.info), encoding="utf-8")
        # a real window (the GPU tier) opens behind the user's work: lowered as it shows
        # and on its first expose (#34); ``WindowDoesNotAcceptFocus`` alone leaves a new
        # window on top of the z order, covering what the user looks at
        self._lowered: set[int] = set()
        if not offscreen():
            QGuiApplication.instance().installEventFilter(self)
        print(f"[OK] test driver {self.info['address']} ({self.info['platform']})")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        t = event.type()
        if (
            t in (QEvent.Type.Show, QEvent.Type.Expose)
            and isinstance(obj, QWindow)
            and obj.isTopLevel()
            and obj.type() not in (Qt.WindowType.Popup, Qt.WindowType.ToolTip)
        ):
            if t == QEvent.Type.Show:
                obj.lower()
            elif id(obj) not in self._lowered:
                self._lowered.add(id(obj))
                obj.lower()
        return False

    # -- the channel (slots, as every signal receiver in the app: no per-instance meta
    # object, test_qml_meta_objects) -----------------------------------------------------
    @Slot()
    def _on_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._socks[id(sock)] = sock
            self._bufs[id(sock)] = b""
            sock.readyRead.connect(self._on_ready_read)
            sock.disconnected.connect(self._on_disconnected)

    @Slot()
    def _on_disconnected(self) -> None:
        sock = self.sender()
        key = id(sock)
        self._socks.pop(key, None)
        self._bufs.pop(key, None)
        self._waits.pop(key, None)
        if sock is not None and shiboken6.isValid(sock):
            sock.deleteLater()

    @Slot()
    def _on_ready_read(self) -> None:
        sock = self.sender()
        key = id(sock)
        if sock is None or key not in self._socks or not shiboken6.isValid(sock):
            return
        self._bufs[key] += bytes(sock.readAll().data())
        self._drain(sock, key)

    def _drain(self, sock: QLocalSocket, key: int) -> None:
        """Answer the socket's complete lines in order; an op in ``NEXT_FRAME_OPS`` is
        answered after the window's next frame, and the lines behind it wait for it."""
        while key not in self._waits and b"\n" in self._bufs.get(key, b""):
            line, self._bufs[key] = self._bufs[key].split(b"\n", 1)
            text = line.decode("utf-8", "replace")
            if not text.strip():
                continue
            win = self._frame_window(text)
            if win is not None:
                self._waits[key] = NextSync(
                    win, lambda text=text: self._answer_later(sock, key, text)
                )
                return
            self._write(sock, self.handle_line(text))

    def _frame_window(self, text: str) -> QQuickWindow | None:
        """The window to wait a frame for, for an op in ``NEXT_FRAME_OPS``; None to
        answer at once (another op, bad json, no visible window)."""
        try:
            op = str(json.loads(text).get("op", ""))
        except (ValueError, AttributeError):
            return None
        if op not in NEXT_FRAME_OPS:
            return None
        try:
            win = self.window()
        except RuntimeError:
            return None
        return win if win.isVisible() else None

    def _answer_later(self, sock: QLocalSocket, key: int, text: str) -> None:
        self._waits.pop(key, None)
        if key not in self._socks or not shiboken6.isValid(sock):
            return
        self._write(sock, self.handle_line(text))
        self._drain(sock, key)

    @staticmethod
    def _write(sock: QLocalSocket, reply: dict[str, Any]) -> None:
        sock.write((json.dumps(reply) + "\n").encode("utf-8"))
        sock.flush()

    def handle_line(self, line: str) -> dict[str, Any]:
        try:
            msg = json.loads(line)
        except ValueError as e:
            return {"id": None, "error": f"bad json: {e}"}
        rid = msg.get("id")
        op = str(msg.get("op", ""))
        args = msg.get("args") or {}
        fn = getattr(self, f"op_{op}", None)
        if fn is None:
            return {"id": rid, "error": f"unknown op {op!r}"}
        try:
            return {"id": rid, "result": fn(**args)}
        except Exception as e:  # the runner reports it as the flow line's failure
            return {"id": rid, "error": f"{type(e).__name__}: {e}"}

    # -- the window ----------------------------------------------------------------------
    def window(self) -> QQuickWindow:
        """The shell's window once attached, else the visible top-level Quick window
        (the welcome card)."""
        if self.app is not None and self.app.window is not None:
            return self.app.window
        wins = [
            w
            for w in QGuiApplication.topLevelWindows()
            if isinstance(w, QQuickWindow) and w.isVisible()
        ]
        if not wins:
            raise RuntimeError("no visible window")
        return wins[0]

    def ensure_focus(self) -> None:
        """Keys reach the window's focus item without any OS focus: Qt Quick counts a
        window with no focus window in the process as focused once its content item has
        the focus, which a ``FocusIn`` sent in-process gives it (#237 decision 4)."""
        win = self.window()
        if win.activeFocusItem() is None:
            QCoreApplication.sendEvent(
                win, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.ActiveWindowFocusReason)
            )

    def _locate(self, name: str) -> dict[str, Any] | None:
        if self.app is not None and self.app.window is not None:
            return self.app.host.shell.locate(name)
        from .app import find_item, locate_item

        win = self.window()
        item = find_item(win.contentItem(), name)
        return None if item is None else locate_item(win, item)

    # -- operations ----------------------------------------------------------------------
    def op_ping(self) -> dict[str, Any]:
        win = self.window() if QGuiApplication.topLevelWindows() else None
        return {
            **self.info,
            "window": win.title() if win is not None else None,
            "attached": self.app is not None,
            "focus_window": QGuiApplication.focusWindow() is not None,
        }

    def op_locate(self, name: str) -> dict[str, Any]:
        info = self._locate(name)
        if info is None:
            return {"name": name, "found": False, "visible": False}
        return {"name": name, "found": True, **info}

    def op_geometry(self) -> dict[str, Any]:
        win = self.window()
        return {
            "width": win.width(),
            "height": win.height(),
            "dpr": win.devicePixelRatio(),
            "title": win.title(),
        }

    def op_resize(self, w: int, h: int) -> dict[str, Any]:
        win = self.window()
        win.resize(int(w), int(h))
        return {"width": win.width(), "height": win.height()}

    def op_move(self, x: float, y: float) -> None:
        win = self.window()
        QTest.mouseMove(win, QPoint(int(x), int(y)))

    def op_press(self, x: float, y: float, button: str = "left", mods: Any = None) -> None:
        win = self.window()
        QTest.mousePress(win, _BUTTONS[button], parse_mods(mods), QPoint(int(x), int(y)))

    def op_release(self, x: float, y: float, button: str = "left", mods: Any = None) -> None:
        win = self.window()
        QTest.mouseRelease(win, _BUTTONS[button], parse_mods(mods), QPoint(int(x), int(y)))

    def op_click(
        self, x: float, y: float, button: str = "left", mods: Any = None, count: int = 1
    ) -> None:
        win = self.window()
        p = QPoint(int(x), int(y))
        QTest.mouseMove(win, p)
        if int(count) == 2:
            QTest.mouseDClick(win, _BUTTONS[button], parse_mods(mods), p)
        else:
            QTest.mouseClick(win, _BUTTONS[button], parse_mods(mods), p)

    def op_wheel(
        self, x: float, y: float, notches: float, mods: Any = None, horizontal: bool = False
    ) -> None:
        win = self.window()
        p = QPoint(int(x), int(y))
        d = round(float(notches) * NOTCH)
        delta = QPoint(d, 0) if horizontal else QPoint(0, d)
        ev = QWheelEvent(
            QPointF(p),
            QPointF(win.mapToGlobal(p)),
            QPoint(),
            delta,
            Qt.MouseButton.NoButton,
            parse_mods(mods),
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        # a real wheel event carries the time: Flickable paces its scroll by the gap
        # between notches, and treats a run of zero-timestamp events as one decaying flick
        ev.setTimestamp(int(time.monotonic() * 1000))
        QCoreApplication.sendEvent(win, ev)

    def op_key(self, key: str, action: str = "click", mods: Any = None) -> dict[str, Any]:
        """``action``: click, down or up; ``key`` a pyautogui-style name (``ctrl+z``)."""
        win = self.window()
        self.ensure_focus()
        qkey, kmods, text = parse_key(key)
        kmods |= parse_mods(mods)
        if action == "down":
            QTest.keyPress(win, qkey, kmods)
        elif action == "up":
            QTest.keyRelease(win, qkey, kmods)
        else:
            QTest.keyClick(win, qkey, kmods)
        return {"key": int(qkey), "text": text}

    def op_type(self, text: str, delay_ms: int = 0) -> None:
        """Type text into the focus item, one key event per character (non-ASCII through
        a plain ``QKeyEvent`` carrying the character)."""
        win = self.window()
        self.ensure_focus()
        for ch in text:
            if ch == "\n":
                QTest.keyClick(win, Qt.Key.Key_Return)
            elif ch.isascii() and ch.isprintable():
                mods = (
                    Qt.KeyboardModifier.ShiftModifier
                    if ch.isupper()
                    else Qt.KeyboardModifier.NoModifier
                )
                QTest.keyClick(win, ch, mods)
            else:
                for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    QCoreApplication.sendEvent(
                        win, QKeyEvent(kind, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, ch)
                    )
            if delay_ms:
                QTest.qWait(int(delay_ms))

    def op_shot(self, path: str, rect: list[float] | None = None, zoom: int = 1) -> dict[str, Any]:
        """Grab the window to a PNG; ``rect`` (window px) crops it, ``zoom`` scales the
        crop up (nearest). Returns the image size and the device pixel ratio."""
        win = self.window()
        img = win.grabWindow()
        if img.isNull():
            raise RuntimeError("window grab produced no image")
        dpr = img.devicePixelRatio() or win.devicePixelRatio() or 1.0
        if rect is not None:
            x, y, w, h = rect
            r = QRect(
                round(x * dpr), round(y * dpr), max(1, round(w * dpr)), max(1, round(h * dpr))
            )
            img = img.copy(r.intersected(img.rect()))
        if int(zoom) > 1:
            img = img.scaled(img.width() * int(zoom), img.height() * int(zoom))
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not img.save(str(p), "PNG"):
            raise RuntimeError(f"cannot write {p}")
        return {"path": str(p), "size": [img.width(), img.height()], "dpr": dpr}

    def op_clipboard(self, text: str | None = None) -> dict[str, Any]:
        if text is not None:
            clipboard.set_text(text)
        return {"text": clipboard.text()}

    def op_windows(self) -> list[dict[str, Any]]:
        return [
            {
                "title": w.title(),
                "visible": w.isVisible(),
                "geometry": [w.x(), w.y(), w.width(), w.height()],
                "quick": isinstance(w, QQuickWindow),
            }
            for w in QGuiApplication.topLevelWindows()
        ]

    def op_wait(self, ms: int) -> None:
        """Spin the loop for ``ms`` (QTest.qWait); the runner normally sleeps itself."""
        QTest.qWait(int(ms))

    def op_time(self) -> float:
        return time.monotonic()

    def op_quit(self) -> None:
        """Close the app the normal way (the canvas is saved, the agent session ends)."""
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, QGuiApplication.instance().quit)


__all__ = [
    "NOTCH",
    "TestDriver",
    "attach",
    "driver",
    "enable",
    "enabled",
    "offscreen",
    "parse_key",
    "parse_mods",
    "start",
    "window_flags",
]
