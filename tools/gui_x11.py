"""The X11 half of gui_platform.py: find, place, focus and resize top-level windows through
Xlib (python-xlib, pyautogui's own dependency on Linux), on whatever ``DISPLAY`` names.
Its own module so that process control (``kill_tree``) does not need python-xlib:
gui_platform loads it on first use of a window function."""

from __future__ import annotations

from typing import Any

from gui_platform import _title_matches
from Xlib import X, Xatom, display, error
from Xlib.protocol import event

_display: display.Display | None = None


def _dpy() -> display.Display:
    global _display
    if _display is None:
        _display = display.Display()
    return _display


def _atom(name: str) -> int:
    return _dpy().intern_atom(name)


def _prop(win: Any, name: str, kind: int = X.AnyPropertyType) -> list | None:
    try:
        p = win.get_full_property(_atom(name), kind)
    except error.XError:
        return None
    return None if p is None else list(p.value) if not isinstance(p.value, bytes) else p.value


def _title(win: Any) -> str:
    for name in ("_NET_WM_NAME", "WM_NAME"):
        v = _prop(win, name)
        if v:
            return (v if isinstance(v, bytes) else bytes(v)).decode("utf-8", "replace")
    return ""


def _wm_supports(name: str) -> bool:
    return _atom(name) in (_prop(_dpy().screen().root, "_NET_SUPPORTED", Xatom.ATOM) or [])


def _top_levels() -> list[Any]:
    """Client windows, the topmost first: the window manager's stacking list when one
    runs, else the root's mapped children (no window manager, a bare Xephyr), each
    or its first named child (a reparenting manager without the EWMH lists)."""
    dpy = _dpy()
    root = dpy.screen().root
    ids = _prop(root, "_NET_CLIENT_LIST_STACKING", Xatom.WINDOW)
    if ids:
        return [dpy.create_resource_object("window", i) for i in reversed(ids)]
    out = []
    for child in reversed(root.query_tree().children):
        try:
            if child.get_attributes().map_state != X.IsViewable:
                continue
            if _title(child):
                out.append(child)
                continue
            named = [c for c in child.query_tree().children if _title(c)]
            out.extend(named[:1])
        except error.XError:
            continue
    return out


def find_window(prefix: str = "Specto") -> int:
    for win in _top_levels():
        try:
            viewable = win.get_attributes().map_state == X.IsViewable
        except error.XError:
            continue
        if viewable and _title_matches(_title(win), prefix):
            return win.id
    raise SystemExit(f"no window titled {prefix}*")


def _win(wid: int) -> Any:
    return _dpy().create_resource_object("window", wid)


def client_origin(wid: int) -> tuple[int, int, int, int]:
    win = _win(wid)
    root = _dpy().screen().root
    at = root.translate_coords(win, 0, 0)
    geo = win.get_geometry()
    return at.x, at.y, geo.width, geo.height


def shot_region(wid: int) -> tuple[int, int, int, int]:
    """The client rect plus the window manager's frame (``_NET_FRAME_EXTENTS``), or
    the client rect alone without one."""
    x, y, w, h = client_origin(wid)
    ext = _prop(_win(wid), "_NET_FRAME_EXTENTS", Xatom.CARDINAL) or [0, 0, 0, 0]
    left, right, top, bottom = (int(v) for v in ext[:4])
    return x - left, y - top, w + left + right, h + top + bottom


def activate(wid: int) -> None:
    dpy = _dpy()
    root = dpy.screen().root
    win = _win(wid)
    if _wm_supports("_NET_ACTIVE_WINDOW"):
        ev = event.ClientMessage(
            window=win,
            client_type=_atom("_NET_ACTIVE_WINDOW"),
            data=(32, [2, X.CurrentTime, 0, 0, 0]),  # 2: a pager, not an app
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        root.send_event(ev, event_mask=mask)
    else:
        win.configure(stack_mode=X.Above)
        win.set_input_focus(X.RevertToParent, X.CurrentTime)
    dpy.sync()


def _top_of(win: Any) -> int:
    """The top-level window holding ``win`` (the focus may sit on a child)."""
    root = _dpy().screen().root.id
    while True:
        parent = win.query_tree().parent
        if parent is None or parent.id == root or parent.id == 0:
            return win.id
        win = parent


def is_foreground(wid: int) -> bool:
    dpy = _dpy()
    if _wm_supports("_NET_ACTIVE_WINDOW"):
        act = _prop(dpy.screen().root, "_NET_ACTIVE_WINDOW", Xatom.WINDOW) or [0]
        return int(act[0]) == wid
    focus = dpy.get_input_focus().focus
    if isinstance(focus, int):  # PointerRoot / None
        return False
    try:
        return focus.id == wid or _top_of(focus) == wid
    except error.XError:
        return False


def resize_client(wid: int, w: int, h: int) -> None:
    """Resize the client area to w x h (X sizes the client; a frame wraps it)."""
    _win(wid).configure(width=w, height=h)
    _dpy().sync()
