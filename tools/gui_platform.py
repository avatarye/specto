"""The window system under the GUI tools (gui_drive.py, gui_session.py): find a top-level
window by title, its client rect on screen, bring it to the front, resize it, read the
clipboard, end the app's process tree. Windows through user32; Linux through Xlib (X11,
pyautogui's own dependency there: python-xlib), on whatever ``DISPLAY`` names, a nested
Xephyr included (``nested_display.py``); macOS through Quartz window lists and AppKit
(pyobjc-framework-Quartz, which pyautogui needs there too), plus System Events for what
only Accessibility may do to another app's window (raise it, resize it).

    # macOS: a throwaway venv for gui_drive / gui_session
    uv venv --no-config /tmp/pyauto
    uv pip install --no-config --python /tmp/pyauto/bin/python \\
        pyautogui pillow pyobjc-framework-Quartz

macOS asks the terminal that runs the tools for two permissions (System Settings >
Privacy & Security): Screen Recording (other apps' window titles and every screenshot;
without it ``find_window`` sees no titles and grabs show only the desktop) and
Accessibility (pyautogui's synthetic mouse and keys, raising and resizing the window);
the first ``activate`` or ``resize_client`` may also ask to let it control System Events.

A window is an int: an HWND on Windows, an X window id on Linux, a Quartz window number
on macOS. macOS coordinates are logical points (what pyautogui and Qt use there), not
pixels: on a Retina screen one point is ``backing_scale()`` (2) pixels, and screenshots
are scaled down to points so a region and its image agree.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from typing import Any

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

#: one wheel notch for ``pyautogui.scroll``: the Windows backend passes the value straight
#: to mouse_event as the wheel delta (120 a notch); the X11 one clicks button 4/5 that
#: many times; the macOS one posts a scroll of that many lines (one line: one notch in Qt)
WHEEL_NOTCH = 120 if WINDOWS else 1


def _title_matches(title: str, prefix: str) -> bool:
    """``prefix`` matches a title that starts with it; a trailing ``$`` asks for an exact
    match ("Specto$": the welcome window, not "Specto - proj")."""
    return title == prefix[:-1] if prefix.endswith("$") else title.startswith(prefix)


if WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()

    def find_window(prefix: str = "Specto") -> int:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if _title_matches(buf.value, prefix):
                    found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        if not found:
            raise SystemExit(f"no window titled {prefix}*")
        return found[0]

    def client_origin(hwnd: int) -> tuple[int, int, int, int]:
        pt = wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        rc = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        return pt.x, pt.y, rc.right, rc.bottom

    def shot_region(hwnd: int) -> tuple[int, int, int, int]:
        """The window's client rect plus its title bar and borders (Windows 11 frames)."""
        x, y, w, h = client_origin(hwnd)
        return x - 8, y - 32, w + 16, h + 40

    def activate(hwnd: int) -> None:
        user32.SetForegroundWindow(hwnd)

    def is_foreground(hwnd: int) -> bool:
        return user32.GetForegroundWindow() == hwnd

    def resize_client(hwnd: int, w: int, h: int) -> None:
        """Resize the window so its client area is w x h (frame borders added)."""
        wr = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        _, _, cw, ch = client_origin(hwnd)
        fw, fh = (wr.right - wr.left) - cw, (wr.bottom - wr.top) - ch
        user32.SetWindowPos(hwnd, 0, wr.left, wr.top, w + fw, h + fh, 0x0004 | 0x0010)

    def clipboard_text() -> str:
        """The clipboard's text ("" when it holds none)."""
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user32.GetClipboardData.restype = ctypes.c_void_p
        if not user32.OpenClipboard(None):
            raise RuntimeError("cannot open the clipboard")
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            try:
                return ctypes.c_wchar_p(ptr).value or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def popen_kwargs() -> dict[str, Any]:
        return {}

    def kill_tree(proc: subprocess.Popen) -> None:
        """End the process and its children (``uv run`` wraps the app)."""
        subprocess.call(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

elif MACOS:
    # pyobjc is imported on first use, not here: gui_session's tests import this module
    # in the workspace venv, which has no pyobjc

    def _quartz() -> Any:
        import Quartz

        return Quartz

    def _windows(option: int, wid: int = 0) -> list[dict]:
        q = _quartz()
        return list(q.CGWindowListCopyWindowInfo(option, wid) or [])

    def _on_screen() -> list[dict]:
        """On-screen app windows (layer 0, not transparent), the frontmost first."""
        q = _quartz()
        opts = q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements
        return [
            w
            for w in _windows(opts)
            if w.get("kCGWindowLayer", 0) == 0 and w.get("kCGWindowAlpha", 1) > 0
        ]

    def _info(wid: int) -> dict:
        q = _quartz()
        for w in _windows(q.kCGWindowListOptionIncludingWindow, wid):
            if int(w["kCGWindowNumber"]) == wid:
                return w
        raise SystemExit(f"[ERROR] window {wid} is gone")

    def _bounds(wid: int) -> tuple[int, int, int, int]:
        """The window's frame in points, title bar included (no shadow)."""
        b = _info(wid)["kCGWindowBounds"]
        return int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"])

    def _title_bar() -> int:
        """A standard titled window's title bar height in points (28 on recent macOS): the
        frame AppKit would give a content rect, less the rect. Qt's windows use that
        standard frame; a window with a unified toolbar or a full-size content view would
        not match."""
        from AppKit import NSWindow

        mask = 1 | 2 | 4 | 8  # titled, closable, miniaturizable, resizable
        frame = NSWindow.frameRectForContentRect_styleMask_(((0, 0), (100, 100)), mask)
        return round(frame.size.height - 100)

    def backing_scale() -> float:
        """Pixels per point on the main screen (2 on Retina)."""
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        return float(screen.backingScaleFactor()) if screen is not None else 1.0

    def find_window(prefix: str = "Specto") -> int:
        wins = _on_screen()
        for w in wins:
            if _title_matches(w.get("kCGWindowName") or "", prefix):
                return int(w["kCGWindowNumber"])
        if wins and not any(w.get("kCGWindowName") for w in wins):
            raise SystemExit(
                f"no window titled {prefix}* (no window has a title: grant this terminal "
                "Screen Recording permission)"
            )
        raise SystemExit(f"no window titled {prefix}*")

    def client_origin(wid: int) -> tuple[int, int, int, int]:
        x, y, w, h = _bounds(wid)
        tb = _title_bar()
        return x, y + tb, w, h - tb

    def shot_region(wid: int) -> tuple[int, int, int, int]:
        """The window's frame: client rect plus title bar (macOS draws no side borders)."""
        return _bounds(wid)

    def _system_events(script: str, *args: object) -> None:
        """Run an AppleScript ``on run argv`` handler through osascript; System Events
        acting on another app's windows needs Accessibility permission."""
        r = subprocess.run(
            ["osascript", "-e", script, *(str(a) for a in args)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"osascript failed (Accessibility permission for this terminal?): "
                f"{r.stderr.strip()}"
            )

    # System Events picks the window by its position (Quartz and Accessibility share the
    # top-left point space), not by its title: a title needs Screen Recording to read
    _AX_WINDOW = """on run argv
        set {px, py} to {item 2 of argv as integer, item 3 of argv as integer}
        tell application "System Events"
            set p to first process whose unix id is (item 1 of argv as integer)
            set win to missing value
            repeat with w in windows of p
                set {wx, wy} to position of w
                if wx = px and wy = py then
                    set win to w
                    exit repeat
                end if
            end repeat
            if win is missing value then error "no window of that process at " & px & "," & py
            %s
        end tell
    end run"""

    _RAISE = _AX_WINDOW % 'set frontmost of p to true\n            perform action "AXRaise" of win'

    def activate(wid: int) -> None:
        """Bring the window's app to the front, and the window to the top of it: AppKit
        first (no permission needed, but macOS 14+ may decline a request from a process
        that is not the active app), then System Events (Accessibility)."""
        from AppKit import NSRunningApplication

        info = _info(wid)
        pid = int(info["kCGWindowOwnerPID"])
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None:
            app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
        for _ in range(10):
            if is_foreground(wid):
                return
            time.sleep(0.05)
        x, y, _, _ = _bounds(wid)
        _system_events(_RAISE, pid, x, y)

    def is_foreground(wid: int) -> bool:
        """The window is the frontmost app window on screen."""
        wins = _on_screen()
        return bool(wins) and int(wins[0]["kCGWindowNumber"]) == wid

    _RESIZE = (
        _AX_WINDOW % "set size of win to {item 4 of argv as integer, item 5 of argv as integer}"
    )

    def resize_client(wid: int, w: int, h: int) -> None:
        """Resize the window so its client area is w x h points (title bar added): through
        System Events, as only Accessibility may resize another app's window."""
        pid = int(_info(wid)["kCGWindowOwnerPID"])
        x, y, _, _ = _bounds(wid)
        _system_events(_RESIZE, pid, x, y, w, h + _title_bar())

    def clipboard_text() -> str:
        """The general pasteboard's text ("" when it holds none)."""
        from AppKit import NSPasteboard, NSPasteboardTypeString

        text = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
        return str(text) if text is not None else ""

else:
    #: the window functions live in gui_x11 (python-xlib), loaded on first use
    _X11 = (
        "activate",
        "client_origin",
        "find_window",
        "is_foreground",
        "resize_client",
        "shot_region",
    )

    def __getattr__(name: str) -> Any:
        if name in _X11:
            import gui_x11

            return getattr(gui_x11, name)
        raise AttributeError(f"module 'gui_platform' has no attribute {name!r}")

    def clipboard_text() -> str:
        """The CLIPBOARD selection's text through xclip or xsel ("" when it holds none)."""
        for cmd in (
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ):
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=5)
            except FileNotFoundError:
                continue
            return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
        raise RuntimeError("reading the clipboard needs xclip or xsel")


if not MACOS:

    def backing_scale() -> float:
        """Pixels per screen unit: 1, the Windows and X11 backends work in pixels."""
        return 1.0


if not WINDOWS:

    def popen_kwargs() -> dict[str, Any]:
        """A process group of its own, so ``kill_tree`` reaches the app under ``uv run``."""
        return {"start_new_session": True}

    def kill_tree(proc: subprocess.Popen) -> None:
        """End the process group ``popen_kwargs`` started: SIGTERM, then SIGKILL."""
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)


def screenshot(region: tuple[int, int, int, int]) -> Any:
    """A PIL image of the screen region (x, y, w, h). pyautogui's own grab on Windows; on
    X11 Pillow's XCB grab of ``DISPLAY`` (pyscreeze would want gnome-screenshot or
    scrot, and grab the whole screen to crop it); on macOS Pillow's screencapture grab of
    the region in points, scaled down from Retina pixels to points."""
    x, y, w, h = region
    if WINDOWS:
        import pyautogui

        return pyautogui.screenshot(region=(x, y, w, h))
    from PIL import ImageGrab

    if MACOS:
        try:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h), scale_down=True)
        except subprocess.CalledProcessError as e:  # an OSError, so gui_drive retries it
            raise OSError(f"screencapture failed (Screen Recording permission?): {e}") from e

    return ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=None)


__all__ = [
    "MACOS",
    "WHEEL_NOTCH",
    "WINDOWS",
    "activate",
    "backing_scale",
    "client_origin",
    "clipboard_text",
    "find_window",
    "is_foreground",
    "kill_tree",
    "popen_kwargs",
    "resize_client",
    "screenshot",
    "shot_region",
]
