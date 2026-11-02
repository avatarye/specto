"""Real-mouse driver for the Specto shell (M1.5 exit: a scripted human test). Finds the
app window by title, asks the running app over its endpoint where named items are
(``shell.locate``), and drives pyautogui. Windows (win32), Linux X11 (Xlib) and macOS
(Quartz): the window system parts live in ``gui_platform.py``.

    # Windows
    uv venv /tmp/pyauto && uv pip install --python /tmp/pyauto/Scripts/python.exe pyautogui pillow
    uv run specto-app --project <proj> --inline-jobs      # on screen
    /tmp/pyauto/Scripts/python.exe tools/gui_drive.py <proj> <outdir> \\
        tools/gui_flows/shell-00-seed.txt "Specto - <name>"

    # Linux (X11; xclip for assert_clipboard). A nested Xephyr keeps the run off the
    # user's desktop: nested_display.py starts one, runs the command on it, stops it
    uv venv --no-config /tmp/pyauto
    uv pip install --no-config --python /tmp/pyauto/bin/python pyautogui pillow python-xlib
    uv run python tools/nested_display.py run -- /tmp/pyauto/bin/python tools/gui_session.py ...

    # macOS: give the terminal Screen Recording (window titles, screenshots) and
    # Accessibility (synthetic input, raising and resizing the window) under System
    # Settings > Privacy & Security first; positions are points, not Retina pixels
    uv venv --no-config /tmp/pyauto
    uv pip install --no-config --python /tmp/pyauto/bin/python \\
        pyautogui pillow pyobjc-framework-Quartz
    /tmp/pyauto/bin/python tools/gui_session.py ...

The window title argument is a prefix ("Specto - proj1"); append "$" for an exact match.
Scripts: tools/gui_flows/*.txt (seed collections, add views, link/dock/undo, drop zones).

Script lines (one per line, # comments):
    shot name.png                 screenshot of the window
    shotwin <title-prefix> name.png   screenshot of another app window (a second frame window)
    keydown tab / keyup tab       hold and release a key (Tab peeks the overworld)
    click <objectName> [fx fy [dx dy]]   click an item (fx, fy: 0..1 of its rect, plus pixels);
                                  write a space in a name as ~ (menu:Open~as~gallery)
    rclick <objectName> [fx fy]   right-click
    dblclick <objectName> [fx fy]
    expand <view>:<path>          open a file tree folder on its chevron, unless it is open
                                  already (the view's snapshot says), and wait until it is;
                                  the closed folders above it open first, one by one (#231:
                                  the tree starts collapsed and lists a folder when opened)
    collapse <view>:<path>        close one the same way
    hover <objectName> [fx fy [dx dy]]   move the pointer there (plus pixels), no click
    wheel <objectName> <notches> [fx fy] [ctrl]   wheel over an item (+ up, - down); ctrl: zoom
    selectall                     Ctrl+A (fields)
    scroll <objectName> <clicks>  wheel over an item (negative = down)
    clickat <objectName> dx dy    click dx, dy pixels from the item's centre
    drag <from> <to> [fx fy]      press on <from>, move to <to> at (fx, fy), release
    dragxy <from> dx dy [fx fy] [shot=held.png] [button=right|middle|both] [alt|mod=alt]
                                  press on <from> (at fx, fy), move by pixels,
                                  screenshot while held (snap lines), release; both: a
                                  left+right chord; alt (or mod=<key>): that key held through it
    dragout <from> <probe.json> [fx fy] [mod=ctrl|shift]   drag the item out (#185) into the
                                  drop probe window (gui_flows/drop_probe.py, which writes
                                  its screen rect to probe.json once it is ready; waited
                                  for, #195): press, past the drag distance, over the
                                  probe, release
    close_probe <probe.json>      close the drop probe (gui_drive also closes every probe it
                                  dragged into when it exits, pass or fail)
    assert_dropped <probe.json> <n> <words...> [ms]   poll the probe until it has n drops
                                  and the n-th one (its JSON) holds the words (a substring)
    mousedown <ref> [fx fy] [button=right]   press and hold a button there
    mouseup [button=right]        let it go
    key ctrl+z                    hotkey
    type text...                  type
    wait 500                      ms
    call name {json}              a command through the endpoint (origin user)
    call_refused name {json} [words...]   a command the endpoint must refuse; fail if it
                                  went through or the error lacks the words (#204)
    submit <doc.json> [label...]  canvas.submit the document in that file as agent:drive
    params <view> {json}          merge into that view's params and submit the canvas as
                                  agent:drive (the agent turning html network off, #93)
    assert_found <objectName>     fail if missing/invisible
    mark <objectName>             remember where the item is on screen
    assert_unmoved <objectName> [px]   fail unless the item is where `mark` found it, within
                                  px (2): a folder row opened far down a scrolled tree (#110)
    assert_moved <objectName> dx dy [px]   fail unless the item moved by (dx, dy) from where
                                  `mark` found it, within px (2): a drop that stays put (#116)
    wait_until <objectName> [ms]  poll until the item is visible (default 60000 ms), fail otherwise
    wait_while <objectName> [ms]  poll until the item is gone (a busy indicator), fail otherwise
    assert_missing <objectName>
    assert_text <objectName> <words...>   fail unless the text item says those words (a
                                  substring): assert_text statusHint Pick files (#101)
    assert_clipboard <line>[|<line>...]   fail unless the clipboard holds exactly those lines
                                  ({project} expands; a context menu's Copy path, #154);
                                  {env:NAME} in any argument expands to that variable
                                  written with Windows separators; a backslash reads / elsewhere
    assert_files <glob> <n> [ms]  poll until at least n files match the glob (default 20000
                                  ms; {project} expands): a launcher's fake program ran (#186)
    assert_painted <objectName> [max]   fail if one colour covers more than max (0.75) of the
                                  item's pixels: a 3D view that has drawn its scene (#85)
    assert_smooth <objectName> dx dy w h [shot=name.png] [min=0.5]   fail unless a curve crossing
                                  w x h px patch at the item's centre + (dx, dy) is
                                  antialiased: the rows it crosses show intermediate tones,
                                  not just curve and background (#96), in at least min of
                                  them (min=0 only reports); shot= saves the patch 8x
    assert_pixel <objectName> fx fy is|not <#rrggbb>   fail unless the pixel at that fraction
                                  of the item's rect is (or is not) that colour, within 24 of
                                  each channel's sum (#92: an identicon inside a port circle)
    shotcrop <objectName> <pad> <zoom> <file>   screenshot the item plus pad px around it,
                                  scaled up zoom times (nearest), a close-up
    print name {json}             call and print the result
    resize <w> <h>                resize the app window's client area (a user maximising) in
                                  logical px (times QT_SCALE_FACTOR)
    shiftclick / ctrlclick / altclick <objectName> [fx fy]   modified clicks (multi-select;
                                  ctrl/altclick: an image channel into the mix, #171/#182)
    webclick <view> <path> [fx fy]   click inside a web view at a rect the page reported
                                  (snap meta.host.page): webclick tamu buttons.fold,
                                  webclick @map points.0 (an x, y point),
                                  webclick @3d items.torus.checkbox
    assert_web <view> <path> <json> [ms]   poll the page's own report (snap meta.host.page)
                                  until the value at path equals the JSON value (default
                                  10000 ms), fail otherwise: assert_web kobi
                                  environment.ready true (#175); a list takes an index
                                  (headings.0.text, #184)
    assert_host <view> <path> <json> [ms]  the same poll on the host's own snapshot (snap
                                  meta.host): assert_host rena muted true (#216)
    assert_meta <view> <path> <json> [ms]  the same poll on the view's whole snap meta:
                                  assert_meta rena params.volume 0.5 (what was remembered)
    spawn <command ...>           start a background process (drip_files.py during a watch test)
    win: <line> / posix: <line>   run the line on that platform only (a crash reads as an
                                  NTSTATUS on Windows, a signal elsewhere)
    mouse: <line> / driver: <line>  run the line under this executor (the real mouse) or
                                  the Qt driver only: what the driver fakes differs (mpv
                                  plays into the null audio output there, #34)

Views by type or label (#101, ``gui_refs.py``): view names are generated, so a flow that
did not fix a name in a document it submitted names the view instead, anywhere a view
name goes (slot:, header:, tile:<view>:<key>, treeRow:<view>:<path>, webclick, params,
a JSON target). The line is resolved against ``canvas.state`` just before it runs;
wait_until, wait_while and assert_missing resolve it on every poll (no such view yet is
"not shown").
    @html                         the first html view in document order (new views go last)
    @gallery#2                    the second gallery
    slot:"Front frames"           a label right after a name prefix
    @"Front frames"               a label anywhere else (webclick @"Notes" wikilinks.0,
                                  {"target": "@"Notes""}); #n picks among equal labels
"""

from __future__ import annotations

import atexit
import glob
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import pyautogui
from console_utf8 import utf8_stdio
from gui_platform import (
    WHEEL_NOTCH,
    activate,
    backing_scale,
    client_origin,
    clipboard_text,
    find_window,
    is_foreground,
    resize_client,
    screenshot,
    shot_region,
)
from gui_refs import Unresolved, has_refs, resolve_refs, tokens

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.12

# the app's Qt scale factor (a 150 % check sets it per process): item rects are logical px
SCALE = float(os.environ.get("QT_SCALE_FACTOR", "1"))


#: drop probes this run dragged into: closed when gui_drive exits, pass or fail (#195)
PROBES: set[Path] = set()


def close_probe(state: Path, wait: float = 5.0) -> bool:
    """Ask the drop probe to close (``<state>.quit``, which it deletes as it goes);
    True once it has."""
    quit_file = state.with_suffix(".quit")
    quit_file.write_text("quit", "utf-8")
    deadline = time.monotonic() + wait
    while quit_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    return not quit_file.exists()


def _close_probes() -> None:
    for state in sorted(PROBES):
        if not close_probe(state):
            print(f"[WARN] drop probe did not close: {state}")


atexit.register(_close_probes)


def probe_rect(state: Path, timeout: float = 30.0) -> list[int]:
    """The drop probe's screen rect (physical px) once its window is ready (#195: it writes
    its state file only then); fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            data = json.loads(state.read_text("utf-8"))
            if data.get("ready"):
                return list(data["rect"])
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"[ERROR] drop probe not ready after {timeout:.0f} s: {state}")
        time.sleep(0.2)


def connect(info: dict):
    """A binary stream to the app's endpoint (``.specto/endpoint.json``): the named pipe on
    Windows; elsewhere QLocalServer's unix socket, a path or a name in the temp dir (the
    SDK's ``open_transport`` rules; this venv has no specto_sdk)."""
    address = info["address"]
    if sys.platform == "win32" and info.get("kind", "pipe") == "pipe":
        return open(address, "r+b", buffering=0)
    if info.get("kind") == "tcp":
        host, _, port = address.rpartition(":")
        sock = socket.create_connection((host or "127.0.0.1", int(port)))
    else:
        path = address if os.sep in address else os.path.join(tempfile.gettempdir(), address)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(path)
    return sock.makefile("rwb", buffering=0)


class Endpoint:
    def __init__(self, project: Path, origin: str = "user") -> None:
        info = json.loads((project / ".specto" / "endpoint.json").read_text())
        self.f = connect(info)
        self.n = 0
        self.buf = b""
        self.request("hello", {"client": "drive", "origin": origin, "api_level": 1})

    def request(self, method: str, params: dict) -> dict:
        self.n += 1
        self.f.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
                + "\n"
            ).encode()
        )
        self.f.flush()
        while True:
            while b"\n" not in self.buf:
                self.buf += self.f.read(65536)
            line, self.buf = self.buf.split(b"\n", 1)
            msg = json.loads(line)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"]))
                return msg["result"]

    def call(self, name: str, args: dict | None = None) -> dict:
        return self.request("call", {"name": name, "args": dict(args or {})})

    def locate(self, name: str) -> dict:
        return self.call("shell.locate", {"name": name})


class Driver:
    def __init__(self, project: Path, out: Path, title: str = "Specto") -> None:
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.hwnd = find_window(title)
        activate(self.hwnd)
        time.sleep(0.3)
        x, y, w, h = client_origin(self.hwnd)
        pyautogui.click(x + w // 2, y + h - 6)  # the status bar: focus without side effects
        time.sleep(0.2)
        self.ep_project = project
        self.ep = Endpoint(project)
        self.marks: dict[str, list[float]] = {}  # mark / assert_unmoved

    def foreground(self) -> None:
        """Bring the app back to the front if another window stole the focus."""
        if not is_foreground(self.hwnd):
            activate(self.hwnd)
            time.sleep(0.3)

    def point(
        self, name: str, fx: float = 0.5, fy: float = 0.5, reach: bool = True
    ) -> tuple[int, int]:
        """The screen point at (fx, fy) of the item's rect; ``reach``: the pointer goes there,
        so the point must be in the part of the item its clipping ancestors show."""
        name = name.replace("~", " ")  # object names with spaces ("menu:Open~as~gallery")
        self.foreground()
        for _ in range(20):
            info = self.ep.locate(name)
            if info["found"] and info["visible"]:
                break
            time.sleep(0.1)
        else:
            raise SystemExit(f"[ERROR] item {name!r} not found/visible: {info}")
        # a row half scrolled out of its list: the point would land on whatever covers it
        # (the card's header), not on the item (#167)
        if reach and info.get("rect") and "shown" in info:
            x, y, w, h = info["rect"]
            px, py, s = x + w * fx, y + h * fy, info["shown"]
            if s is None or not (s[0] <= px <= s[0] + s[2] and s[1] <= py <= s[1] + s[3]):
                raise SystemExit(
                    f"[ERROR] item {name!r} at ({fx}, {fy}) is clipped out of view: "
                    f"rect {info['rect']}, shown {s}"
                )
        if info.get("screen"):
            # M2: the item may live in another window; the app reports screen pixels
            x, y, w, h = (v * SCALE for v in info["screen"])
            return int(x + w * fx), int(y + h * fy)
        x, y, w, h = (v * SCALE for v in info["rect"])
        ox, oy, _, _ = client_origin(self.hwnd)
        return int(ox + x + w * fx), int(oy + y + h * fy)

    def views(self) -> dict:
        return self.ep.call("canvas.state", {})["document"]["views"]

    def expand_arg(self, arg: str) -> str:
        """``{project}`` is the project folder; ``{env:NAME}`` is that environment
        variable (how a flow names a prim or file of the user's own data without the
        repo naming it). An unset variable fails the flow."""
        arg = arg.replace("{project}", str(self.ep_project))
        while "{env:" in arg:
            start = arg.index("{env:")
            end = arg.index("}", start)
            name = arg[start + 5 : end]
            if not os.environ.get(name):
                raise SystemExit(f"[ERROR] {arg}: {name} is not set")
            arg = arg[:start] + os.environ[name] + arg[end + 1 :]
        return arg

    def resolve(self, line: str, wait: float = 2.0) -> str:
        """The line with its view references (``gui_refs``) swapped for view names; a view
        a gesture has just made can take a moment to reach the document."""
        t0 = time.monotonic()
        while True:
            try:
                return resolve_refs(line, self.views())
            except Unresolved as e:
                if time.monotonic() - t0 > wait:
                    raise SystemExit(f"[ERROR] {line}: {e}") from None
                time.sleep(0.1)

    def tree_expanded(self, view: str) -> list[str]:
        snap = self.ep.call("snap", {"target": view})
        return ((snap.get("meta") or {}).get("host") or {}).get("expanded") or []

    def fold(self, target: str, want_open: bool) -> None:
        """Open or close a file tree folder by clicking its chevron (a toggle) only when it
        is not that way already. Since #231 everything starts closed but a single-folder
        chain from the root, and a folder is listed when it is first opened: opening a
        folder opens the closed folders above it first, each one's chevron in turn."""
        view, _, path = target.partition(":")
        parts = path.split("/")
        steps = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)] if want_open else [path]
        for step in steps:
            if (step in self.tree_expanded(view)) == want_open:
                continue
            self.tree_settled(view)
            x, y = self.point(f"treeChevron:{view}:{step}")
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y)
            t0 = time.monotonic()
            while (step in self.tree_expanded(view)) != want_open:
                if time.monotonic() - t0 > 5:
                    state = "open" if want_open else "closed"
                    raise SystemExit(f"[ERROR] folder {view}:{step} is not {state}")
                time.sleep(0.1)
        if want_open:
            self.tree_settled(view)
            time.sleep(0.2)

    def tree_settled(self, view: str, limit: float = 5.0) -> None:
        """Wait until every open folder of the tree is listed: a folder opened a moment
        ago has its rows still to arrive from the worker (#231), and when they do the
        rows below it move, so a chevron located before that would be clicked where it
        no longer is (as ``gui_qtdrive.tree_settled``)."""
        t0 = time.monotonic()
        while True:
            snap = self.ep.call("snap", {"target": view})
            host = (snap.get("meta") or {}).get("host") or {}
            if set(host.get("expanded") or []) <= set(host.get("loaded") or []):
                return
            if time.monotonic() - t0 > limit:
                raise SystemExit(f"[ERROR] {view}: open folders still unlisted after {limit:.0f} s")
            time.sleep(0.05)

    def shown(self, name: str) -> bool:
        """Is the item there and visible; a reference to no view (yet) is not shown."""
        try:
            name = resolve_refs(name, self.views()) if has_refs(name) else name
        except Unresolved:
            return False
        info = self.ep.locate(name)
        return bool(info["found"] and info["visible"])

    def resize(self, w: int, h: int) -> None:
        """Resize the main window so its client area is w x h (frame borders added)."""
        resize_client(self.hwnd, w, h)
        time.sleep(0.8)

    def web_point(self, view: str, path: str, fx: float = 0.5, fy: float = 0.5) -> tuple[int, int]:
        """A screen point inside a web view from the page's own report (CSS px rects under
        ``snap`` -> ``meta.host.page``); the web view fills the slot under its header, or
        starts ``page.top`` px below it."""
        self.foreground()
        snap = self.ep.call("snap", {"target": view})
        page = ((snap.get("meta") or {}).get("host") or {}).get("page") or {}
        node: object = page
        for key in path.split("."):
            node = node[int(key)] if isinstance(node, list) else node[key]  # type: ignore[index]
        if isinstance(node, dict) and "rect" in node:
            node = node["rect"]
        head = self.ep.locate(f"header:{view}")
        hx, hy, _hw, hh = head["screen"]
        # CSS px to screen units: the page's devicePixelRatio counts pixels, and a Retina
        # screen has backing_scale() of them per point (1 off macOS)
        dpr = float(page.get("dpr") or 1.0) / backing_scale()
        # a view with chrome of its own above the page (the scratch pad's tabs, #184)
        # reports how far down the page starts, in px
        hh += float(page.get("top") or 0.0) * dpr
        if isinstance(node, dict):
            return int(hx + node["x"] * dpr), int(hy + hh + node["y"] * dpr)
        x, y, w, h = node  # type: ignore[misc]
        return int(hx + (x + w * fx) * dpr), int(hy + hh + (y + h * fy) * dpr)

    def shot(self, name: str, hwnd: int | None = None) -> None:
        region = shot_region(hwnd or self.hwnd)
        # GDI's grab fails now and then while a WebGL view composites ("screen grab
        # failed", #129): try again a few times before giving up
        for attempt in range(4):
            try:
                img = screenshot(region)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(0.4)
        p = self.out / name
        img.save(p)
        print(f"[OK] shot {p}")

    def run(self, line: str) -> None:
        # $PROJECT: the project folder, so a flow can spawn a script against it
        line = line.replace("$PROJECT", str(self.ep_project))
        words = tokens(line)
        if not words or words[0].startswith("#"):
            return
        while words and words[0] in ("win:", "posix:", "mouse:", "driver:"):
            if words[0] in ("win:", "posix:") and (words[0] == "win:") != (
                sys.platform == "win32"
            ):
                return
            if words[0] == "driver:":  # the Qt driver's line, not this executor's
                return
            line = line.split(None, 1)[1] if len(words) > 1 else ""
            words = words[1:]
        if not words:
            return
        print(f"> {line}")
        if words[0] in ("wait_until", "wait_while", "assert_missing"):
            parts = words  # a view reference is resolved on every poll: it may not be there yet
        elif words[0] != "type" and has_refs(line):
            line = self.resolve(line)
            print(f"  = {line}")
            parts = line.split()
        else:
            parts = line.split()
        # {project} in any argument is the project folder (spawned tools, drip targets);
        # {env:NAME} is that environment variable (a prim path of the user's own world)
        op, args = parts[0], [self.expand_arg(a) for a in parts[1:]]
        if op == "shot":
            self.foreground()
            time.sleep(0.4)
            self.shot(args[0])
        elif op == "shotwin":
            time.sleep(0.4)
            self.shot(args[-1], find_window(" ".join(args[:-1])))
        elif op == "keydown":
            pyautogui.keyDown(args[0])
        elif op == "keyup":
            pyautogui.keyUp(args[0])
        elif op == "click":
            # click <objectName> [fx fy [dx dy]]: a fraction of the item's rect plus pixels
            fx, fy = (float(args[1]), float(args[2])) if len(args) >= 3 else (0.5, 0.5)
            dx, dy = (int(args[3]), int(args[4])) if len(args) >= 5 else (0, 0)
            x, y = self.point(args[0], fx, fy)
            pyautogui.moveTo(x + dx, y + dy, duration=0.15)
            pyautogui.click(x + dx, y + dy)
        elif op in ("shiftclick", "ctrlclick", "altclick"):
            fx, fy = (float(args[1]), float(args[2])) if len(args) >= 3 else (0.5, 0.5)
            x, y = self.point(args[0], fx, fy)
            mod = op.removesuffix("click")
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.keyDown(mod)
            pyautogui.click(x, y)
            pyautogui.keyUp(mod)
        elif op == "webclick":
            fx, fy = (float(args[2]), float(args[3])) if len(args) >= 4 else (0.5, 0.5)
            x, y = self.web_point(args[0], args[1], fx, fy)
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y)
        elif op == "resize":
            self.resize(int(int(args[0]) * SCALE), int(int(args[1]) * SCALE))
        elif op == "spawn":
            import subprocess

            subprocess.Popen(args, cwd=str(Path(__file__).resolve().parent.parent))
        elif op == "rclick":
            fx, fy = (float(args[1]), float(args[2])) if len(args) >= 3 else (0.5, 0.5)
            x, y = self.point(args[0], fx, fy)
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y, button="right")
        elif op == "clickat":
            # click at a pixel offset from an item's centre (a context-menu row that has no
            # objectName: the menu opens at the cursor, rows are ~29 px apart)
            x, y = self.point(args[0])
            x, y = x + int(args[1]), y + int(args[2])
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y)
        elif op == "scroll":
            # wheel over an item: negative clicks scroll down
            # one notch at a time (gui_platform.WHEEL_NOTCH: 120 on Windows, 1 on X11/macOS)
            x, y = self.point(args[0])
            pyautogui.moveTo(x, y, duration=0.15)
            n = int(args[1])
            step = WHEEL_NOTCH if n > 0 else -WHEEL_NOTCH
            for _ in range(abs(n)):
                pyautogui.scroll(step, x, y)
                time.sleep(0.03)
            time.sleep(0.4)
        elif op == "hover":
            # move the pointer to an item (fx fy fraction) plus a pixel offset, no click
            fx, fy = (float(args[1]), float(args[2])) if len(args) >= 3 else (0.5, 0.5)
            dx, dy = (int(args[3]), int(args[4])) if len(args) >= 5 else (0, 0)
            x, y = self.point(args[0], fx, fy)
            pyautogui.moveTo(x + dx, y + dy, duration=0.25)
        elif op in ("expand", "collapse"):
            self.fold(args[0], op == "expand")
        elif op == "dblclick":
            fx, fy = (float(args[1]), float(args[2])) if len(args) >= 3 else (0.5, 0.5)
            x, y = self.point(args[0], fx, fy)
            pyautogui.moveTo(x, y, duration=0.15)
            # two clicks 80 ms apart: a zero-interval doubleClick is not always a Qt double click
            pyautogui.click(x, y, clicks=2, interval=0.08)
        elif op == "wheel":
            fx, fy = (float(args[2]), float(args[3])) if len(args) >= 4 else (0.5, 0.5)
            ctrl = "ctrl" in args[4:]
            x, y = self.point(args[0], fx, fy)
            pyautogui.moveTo(x, y, duration=0.15)
            if ctrl:
                pyautogui.keyDown("ctrl")
            n = int(args[1])
            for _ in range(abs(n)):
                pyautogui.scroll(WHEEL_NOTCH if n > 0 else -WHEEL_NOTCH, x, y)
                time.sleep(0.08)
            if ctrl:
                pyautogui.keyUp("ctrl")
        elif op == "selectall":
            pyautogui.hotkey("ctrl", "a")
        elif op == "drag":
            fx, fy = (float(args[2]), float(args[3])) if len(args) >= 4 else (0.5, 0.5)
            x0, y0 = self.point(args[0])
            x1, y1 = self.point(args[1], fx, fy)
            pyautogui.moveTo(x0, y0, duration=0.15)
            pyautogui.mouseDown()
            pyautogui.moveTo(x0 + 12, y0 + 12, duration=0.15)
            pyautogui.moveTo(x1, y1, duration=0.5)
            time.sleep(0.2)
            pyautogui.mouseUp()
        elif op == "hover_drag":
            # press on <from>, move over <to> at (fx, fy) and screenshot while still held
            fx, fy = (float(args[2]), float(args[3])) if len(args) >= 4 else (0.5, 0.5)
            x0, y0 = self.point(args[0])
            x1, y1 = self.point(args[1], fx, fy)
            pyautogui.moveTo(x0, y0, duration=0.15)
            pyautogui.mouseDown()
            pyautogui.moveTo(x0 + 12, y0 + 12, duration=0.15)
            pyautogui.moveTo(x1, y1, duration=0.5)
            time.sleep(0.4)
            self.shot(args[4] if len(args) >= 5 else "drag.png")
            pyautogui.mouseUp()
        elif op == "agent":
            if not hasattr(self, "agent_ep"):
                self.agent_ep = Endpoint(self.ep_project, origin="agent:drive")
            res = self.agent_ep.call(args[0], json.loads(" ".join(args[1:]) or "{}"))
            print(json.dumps(res)[:400])
        elif op == "dragxy":
            # dragxy <from> dx dy [fx fy] [shot=name.png] [button=right|middle|both] [alt]
            # [mod=<key>]: press at (fx, fy) of the item, move by pixels, optionally
            # screenshot while still held (snap lines), release; both: left and right held
            # (a chord); alt or mod=<key>: that key held through the drag (#151, #152)
            opts = dict(a.split("=", 1) for a in args[3:] if "=" in a)
            rest = [a for a in args[3:] if "=" not in a and a != "alt"]
            held = opts.get("shot")
            button = opts.get("button", "left")
            buttons = ["left", "right"] if button == "both" else [button]
            mod = opts.get("mod") or ("alt" if "alt" in args[3:] else None)
            fx, fy = (float(rest[0]), float(rest[1])) if len(rest) >= 2 else (0.5, 0.5)
            x0, y0 = self.point(args[0], fx, fy)
            dx, dy = int(args[1]), int(args[2])
            pyautogui.moveTo(x0, y0, duration=0.15)
            if mod:
                pyautogui.keyDown(mod)
            for b in buttons:
                pyautogui.mouseDown(button=b)
            pyautogui.moveTo(x0 + (8 if dx else 0), y0 + (8 if dy else 0), duration=0.1)
            pyautogui.moveTo(x0 + dx, y0 + dy, duration=0.4)
            time.sleep(0.15)
            if held:
                time.sleep(0.3)
                self.shot(held)
            for b in reversed(buttons):
                pyautogui.mouseUp(button=b)
            if mod:
                pyautogui.keyUp(mod)
        elif op == "dragout":
            # dragout <from> <probe.json> [fx fy] [mod=key]: an OS drag into the drop probe
            opts = dict(a.split("=", 1) for a in args[2:] if "=" in a)
            rest = [a for a in args[2:] if "=" not in a]
            fx, fy = (float(rest[0]), float(rest[1])) if len(rest) >= 2 else (0.5, 0.5)
            PROBES.add(Path(args[1]).resolve())
            px, py, pw, ph = probe_rect(Path(args[1]))
            x0, y0 = self.point(args[0], fx, fy)
            mod = opts.get("mod")
            pyautogui.moveTo(x0, y0, duration=0.15)
            if mod:
                pyautogui.keyDown(mod)
            pyautogui.mouseDown()
            pyautogui.moveTo(x0 + 6, y0 + 6, duration=0.1)
            pyautogui.moveTo(x0 + 30, y0 + 30, duration=0.2)  # past the drag distance
            if mod:
                pyautogui.keyUp(mod)  # the drag has started; a held key would change its action
            pyautogui.moveTo(px + pw // 2, py + ph // 2, duration=0.6)
            pyautogui.moveTo(px + pw // 2 + 4, py + ph // 2 + 4, duration=0.2)
            time.sleep(0.2)
            pyautogui.mouseUp()
            time.sleep(0.3)
            self.foreground()
        elif op == "close_probe":
            state = Path(args[0]).resolve()
            PROBES.discard(state)
            if not close_probe(state):
                raise SystemExit(f"[ERROR] drop probe did not close: {state}")
            print(f"[OK] drop probe closed: {state}")
        elif op == "assert_dropped":
            # assert_dropped <probe.json> <n> <words...> [ms]
            n = int(args[1])
            words = args[2:]
            ms = int(words[-1]) if words and words[-1].isdigit() else 8000
            words = words[:-1] if words and words[-1].isdigit() else words
            want = " ".join(words)
            deadline = time.monotonic() + ms / 1000
            got = ""
            while time.monotonic() < deadline:
                try:
                    drops = json.loads(Path(args[0]).read_text("utf-8"))["drops"]
                except (OSError, ValueError, KeyError):
                    drops = []
                if len(drops) >= n:
                    got = json.dumps(drops[n - 1])
                    if want in got:
                        break
                time.sleep(0.2)
            else:
                raise SystemExit(f"[ERROR] drop {n} lacks {want!r}: {got[:600] or 'no such drop'}")
            print(f"[OK] drop {n}: {got[:300]}")
        elif op == "mousedown":
            opts = dict(a.split("=", 1) for a in args[1:] if "=" in a)
            rest = [a for a in args[1:] if "=" not in a]
            fx, fy = (float(rest[0]), float(rest[1])) if len(rest) >= 2 else (0.5, 0.5)
            x, y = self.point(args[0], fx, fy)
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.mouseDown(button=opts.get("button", "left"))
        elif op == "mouseup":
            opts = dict(a.split("=", 1) for a in args if "=" in a)
            pyautogui.mouseUp(button=opts.get("button", "left"))
        elif op == "key":
            pyautogui.hotkey(*args[0].split("+"))
        elif op == "type":
            pyautogui.typewrite(" ".join(args), interval=0.02)
        elif op == "wait":
            time.sleep(int(args[0]) / 1000)
        elif op == "submit":
            # the whole canvas document from a file, submitted as the agent would
            if not hasattr(self, "agent_ep"):
                self.agent_ep = Endpoint(self.ep_project, origin="agent:drive")
            doc = json.loads(Path(args[0]).read_text(encoding="utf-8"))
            label = " ".join(args[1:]) or Path(args[0]).stem
            res = self.agent_ep.call("canvas.submit", {"document": doc, "label": label})
            print(json.dumps({k: res.get(k) for k in ("txn", "views", "links", "empty")}))
        elif op == "params":
            # the agent setting a view's params: the live document with the keys merged
            if not hasattr(self, "agent_ep"):
                self.agent_ep = Endpoint(self.ep_project, origin="agent:drive")
            doc = self.agent_ep.call("canvas.state", {})["document"]
            doc["views"][args[0]].setdefault("params", {}).update(json.loads(" ".join(args[1:])))
            res = self.agent_ep.call(
                "canvas.submit", {"document": doc, "label": f"params {args[0]}"}
            )
            print(
                f"[OK] params {args[0]}: {doc['views'][args[0]]['params']} (txn {res.get('txn')})"
            )
        elif op == "submit_refused":
            # a submit the canvas must refuse (a lock, a quota): print the fix-its, fail if
            # it went through
            if not hasattr(self, "agent_ep"):
                self.agent_ep = Endpoint(self.ep_project, origin="agent:drive")
            doc = json.loads(Path(args[0]).read_text(encoding="utf-8"))
            label = " ".join(args[1:]) or Path(args[0]).stem
            try:
                self.agent_ep.call("canvas.submit", {"document": doc, "label": label})
            except RuntimeError as e:
                err = json.loads(str(e))
                data = err.get("data") or {}
                problems = data.get("problems") or (data.get("data") or {}).get("problems") or []
                print(f"[OK] refused: {err.get('message', '')[:200].splitlines()[0]}")
                for p in problems:
                    print(f"  - {p['path']}: {p['message']}\n    fix: {p['fix']}")
            else:
                raise SystemExit(f"submit of {args[0]} was accepted; it should have been refused")
        elif op in ("call", "print"):
            res = self.ep.call(args[0], json.loads(" ".join(args[1:]) or "{}"))
            if op == "print":
                print(json.dumps(res, indent=1)[:3000])
        elif op == "call_refused":
            # a command the endpoint must refuse (a peer message with Peer requests off)
            body = " ".join(args[1:])
            end = body.rfind("}") + 1
            want = body[end:].strip()
            try:
                self.ep.call(args[0], json.loads(body[:end] or "{}"))
            except RuntimeError as e:
                err = json.loads(str(e))
                said = json.dumps(err)
                if want and want not in said:
                    msg = f"[ERROR] call_refused {args[0]}: refused, but not with {want!r}"
                    raise SystemExit(f"{msg}: {said[:400]}") from None
                print(f"[OK] call_refused {args[0]}: {err.get('message', said)[:200]}")
            else:
                raise SystemExit(f"[ERROR] call_refused {args[0]}: it went through")
        elif op in ("wait_until", "wait_while"):
            # poll the item (a real agent's turn takes seconds, not a fixed wait)
            limit = int(args[1]) / 1000 if len(args) > 1 else 60.0
            t0 = time.monotonic()
            while True:
                shown = self.shown(args[0])
                if shown == (op == "wait_until"):
                    print(f"[OK] {op} {args[0]} after {time.monotonic() - t0:.1f} s")
                    break
                if time.monotonic() - t0 > limit:
                    state = "missing" if op == "wait_until" else "there"
                    raise SystemExit(f"[ERROR] {op} {args[0]}: still {state} after {limit:.0f} s")
                time.sleep(0.25)
        elif op in ("assert_web", "assert_host", "assert_meta"):
            want = json.loads(args[2])
            limit = int(args[3]) / 1000 if len(args) > 3 else 10.0
            t0 = time.monotonic()
            while True:
                snap = self.ep.call("snap", {"target": args[0]})
                node: object = snap.get("meta") or {}
                if op != "assert_meta":
                    node = node.get("host") or {}  # type: ignore[union-attr]
                if op == "assert_web":
                    node = node.get("page") or {}  # type: ignore[union-attr]
                for key in args[1].split("."):  # a list takes an index (headings.0.text)
                    if isinstance(node, list) and key.isdigit():
                        node = node[int(key)] if int(key) < len(node) else None
                    else:
                        node = node.get(key) if isinstance(node, dict) else None  # type: ignore[union-attr]
                if node == want:
                    print(
                        f"[OK] {args[0]} {args[1]} = {args[2]} after {time.monotonic() - t0:.1f} s"
                    )
                    break
                if time.monotonic() - t0 > limit:
                    raise SystemExit(f"[ERROR] {args[0]} {args[1]} is {node!r}, not {want!r}")
                time.sleep(0.25)
        elif op == "assert_found":
            info = self.ep.locate(args[0])
            assert info["found"] and info["visible"], f"{args[0]} missing: {info}"
        elif op == "assert_missing":
            assert not self.shown(args[0]), f"{args[0]} still visible"
        elif op in ("mark", "assert_unmoved", "assert_moved"):
            info = self.ep.locate(args[0])
            assert info["found"] and info["visible"], f"{args[0]} missing: {info}"
            if op == "mark":
                self.marks[args[0]] = info["rect"]
            else:
                by = [float(a) for a in args[1:3]] if op == "assert_moved" else [0.0, 0.0]
                rest = args[3:] if op == "assert_moved" else args[1:]
                tol = float(rest[0]) if rest else 2.0
                was, now = self.marks[args[0]], info["rect"]
                print(f"  {args[0]} at {now[:2]} (marked at {was[:2]}, expected +{by})")
                assert all(
                    abs(a + d - b) <= tol for a, d, b in zip(was[:2], by, now[:2], strict=True)
                ), f"{args[0]} moved from {was[:2]} to {now[:2]}, not by {by}"
        elif op == "assert_text":
            info = self.ep.locate(args[0])
            want = " ".join(args[1:])
            print(f"[OK] text {args[0]}: {info.get('text')!r}")
            said = info.get("text") or ""
            assert want in said, f"{args[0]} says {said!r}, not {want!r}"
        elif op == "assert_clipboard":
            want = " ".join(args).split("|")
            if sys.platform != "win32":  # the flows spell paths the Windows way
                want = [w.replace("\\", "/") for w in want]
            got = clipboard_text().splitlines()
            print(f"[OK] clipboard: {got!r}")
            assert got == want, f"clipboard holds {got!r}, not {want!r}"
        elif op == "assert_files":
            n = int(args[1])
            end = time.monotonic() + (float(args[2]) if len(args) > 2 else 20000.0) / 1000.0
            found: list[str] = []
            while time.monotonic() < end:
                found = glob.glob(args[0])
                if len(found) >= n:
                    break
                time.sleep(0.1)
            print(f"[OK] files {args[0]}: {len(found)}")
            assert len(found) >= n, f"{len(found)} files match {args[0]}, not {n}"
        elif op == "assert_pixel":
            x, y = self.point(args[0], float(args[1]), float(args[2]), reach=False)
            time.sleep(0.3)
            got = screenshot((x, y, 1, 1)).convert("RGB").getpixel((0, 0))
            want = tuple(int(args[4].lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            same = sum(abs(a - b) for a, b in zip(got, want, strict=True)) <= 24
            hexed = "#{:02x}{:02x}{:02x}".format(*got)
            print(f"[OK] pixel {args[0]} at {args[1]},{args[2]}: {hexed}")
            assert same == (args[3] == "is"), f"{args[0]}: pixel {hexed} {args[3]} {args[4]} failed"
        elif op == "shotcrop":
            pad, zoom = int(args[1]), int(args[2])
            x0, y0 = self.point(args[0], 0, 0, reach=False)
            x1, y1 = self.point(args[0], 1, 1, reach=False)
            time.sleep(0.4)
            box = (x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad)
            img = screenshot(box)
            img = img.resize((img.width * zoom, img.height * zoom), resample=0)
            img.save(self.out / args[3])
            print(f"[OK] shot {self.out / args[3]}")
        elif op == "assert_painted":
            # the item is not one flat colour: a blank 3D slot measured 91 % clear colour
            # with its item list open (#85), a loaded scene covers most of the canvas
            limit = float(args[1]) if len(args) > 1 else 0.75
            x0, y0 = self.point(args[0], 0, 0, reach=False)
            x1, y1 = self.point(args[0], 1, 1, reach=False)
            time.sleep(0.3)
            img = screenshot((x0, y0, x1 - x0, y1 - y0)).convert("RGB").reduce(4)
            colours = img.getcolors(img.width * img.height) or []
            top = max(colours)[0] / (img.width * img.height)
            print(f"[OK] painted {args[0]}: dominant colour {top:.0%}, {len(colours)} colours")
            assert top <= limit, (
                f"{args[0]} looks blank: one colour covers {top:.0%} (> {limit:.0%})"
            )
        elif op == "assert_smooth":
            # antialiased curves (#96): an aliased stroke over a flat background gives every
            # row it crosses exactly two colours; an antialiased one ramps between them
            held = next((a[5:] for a in args[5:] if a.startswith("shot=")), None)
            least = float(next((a[4:] for a in args[5:] if a.startswith("min=")), 0.5))
            cx, cy = self.point(args[0], reach=False)
            dx, dy, w, h = (int(float(a) * SCALE) for a in args[1:5])
            time.sleep(0.3)
            region = (cx + dx - w // 2, cy + dy - h // 2, w, h)
            img = screenshot(region).convert("RGB")
            px = img.load()
            rows = [len({px[x, y] for x in range(w)}) for y in range(h)]
            crossing = [n for n in rows if n >= 2]
            smooth = [n for n in crossing if n >= 3]
            if held:
                img.resize((w * 8, h * 8), 0).save(self.out / held)  # 0: nearest
                print(f"[OK] shot {self.out / held}")
            share = len(smooth) / len(crossing) if crossing else 0.0
            print(
                f"[OK] smooth {args[0]}: {len(smooth)}/{len(crossing)} crossing rows show "
                f"intermediate tones, colours per row {sorted(set(rows))}"
            )
            assert crossing, f"{args[0]}: no curve crosses the patch at {region}"
            assert share >= least, f"{args[0]}: the curve is aliased ({share:.0%} smooth rows)"
        else:
            raise SystemExit(f"unknown op {op}")


def main() -> None:
    utf8_stdio()
    project, out, script = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    title = sys.argv[4] if len(sys.argv) > 4 else "Specto"
    d = Driver(project, out, title)
    for line in script.read_text(encoding="utf-8").splitlines():
        d.run(line.strip())
    print("[OK] script done")


if __name__ == "__main__":
    main()
