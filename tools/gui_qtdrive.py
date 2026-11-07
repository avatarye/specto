"""Run GUI flows (``tools/gui_flows/*.txt``) through the app's test driver (#237): the
app starts offscreen with ``--test-driver``, positions come from ``shell.locate`` and the
input is Qt test events on the app's own GUI thread (``specto_app.testdrive``), so a run
takes no window, no focus, no real mouse and leaves the foreground window, the cursor and
the clipboard alone. The flow language is ``gui_drive.py``'s (its docstring lists the
lines); that script stays the real-mouse executor for what Qt events cannot reach.

    uv run python tools/gui_qtdrive.py --project <folder> --out <dir> [--fresh] [--app-log <file>]
        [--extra "<specto-app flags>"] [--pool] [--welcome [--welcome-recent] [--welcome-only]]
        [--shot-prefix P] [--report <json>] flow1.txt [flow2.txt ...]

Per session one app process on its own user settings dir (``<project>/.specto/gui-user``
unless ``SPECTO_USER_DIR`` is set), the offscreen platform with a 2560 x 1440 screen
(popups clamp to the screen), the software Qt Quick backend unless ``QT_QUICK_BACKEND``
is set, and the stub USD source unless ``SPECTO_DISABLE_CAPABILITIES`` is set; sessions
run in parallel without touching each other. ``--report`` writes one JSON entry per flow
(name, ok, seconds, error) for ``v2_suite.py``.

Not here, by design: ``dragout`` / ``close_probe`` / ``assert_dropped`` (a native drag
into another app: the real-mouse tier) and ``shotwin`` (another app's window).
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import gui_scratch  # noqa: E402
from console_utf8 import utf8_stdio  # noqa: E402
from gui_platform import kill_tree, popen_kwargs  # noqa: E402
from gui_refs import Unresolved, has_refs, resolve_refs, tokens  # noqa: E402

#: the offscreen screen the app runs on (window px; QT_SCALE_FACTOR scales on top)
SCREEN = (2560, 1440)
#: how long a session waits for the driver, then for the endpoint
START_S = 90.0


class Flow(Exception):
    """A flow line failed."""


def connect(info: dict) -> Any:
    """A binary stream to a QLocalServer: the named pipe on Windows, the unix socket
    elsewhere (``gui_drive.connect``'s rules)."""
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


class Lines:
    """Newline-delimited JSON over a stream."""

    def __init__(self, f: Any) -> None:
        self.f = f
        self.buf = b""
        self.n = 0

    def send(self, msg: dict) -> None:
        self.f.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.f.flush()

    def recv(self) -> dict:
        while b"\n" not in self.buf:
            chunk = self.f.read(65536)
            if not chunk:
                raise Flow("the app closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)


class Drive:
    """The test driver's channel (``<user dir>/drive.json``)."""

    def __init__(self, info: dict) -> None:
        self.io = Lines(connect(info))

    def call(self, op: str, **args: Any) -> Any:
        self.io.n += 1
        rid = self.io.n
        self.io.send({"id": rid, "op": op, "args": args})
        while True:
            msg = self.io.recv()
            if msg.get("id") == rid:
                if "error" in msg:
                    raise Flow(f"drive.{op}: {msg['error']}")
                return msg.get("result")


class Endpoint:
    """The app's command endpoint (``.specto/endpoint.json``), as ``gui_drive.Endpoint``."""

    def __init__(self, project: Path, origin: str = "user") -> None:
        info = json.loads((project / ".specto" / "endpoint.json").read_text())
        self.io = Lines(connect(info))
        self.request("hello", {"client": "qtdrive", "origin": origin, "api_level": 1})

    def request(self, method: str, params: dict) -> dict:
        self.io.n += 1
        rid = self.io.n
        self.io.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self.io.recv()
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"]))
                return msg["result"]

    def call(self, name: str, args: dict | None = None) -> dict:
        return self.request("call", {"name": name, "args": dict(args or {})})


def _img(path: Path) -> Any:
    from PIL import Image

    img = Image.open(path)
    img.load()
    return img


class QtFlow:
    """The flow language over the driver. Positions are window px (``shell.locate``
    rects); ``{project}`` and ``{env:NAME}`` expand in every argument."""

    def __init__(self, drive: Drive, project: Path, out: Path, ep: Endpoint | None) -> None:
        self.drive = drive
        self.project = project
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.ep = ep
        self.agent_ep: Endpoint | None = None
        self.marks: dict[str, list[float]] = {}
        self.children: list[subprocess.Popen] = []
        self.tmp = Path(tempfile.mkdtemp(prefix="qtdrive-"))
        self.env: dict[str, str] = dict(os.environ)  # what spawned helpers run with

    # -- helpers ---------------------------------------------------------------------------
    def endpoint(self) -> Endpoint:
        if self.ep is None:
            raise Flow("no endpoint yet (the welcome card has none)")
        return self.ep

    def agent(self) -> Endpoint:
        if self.agent_ep is None:
            self.agent_ep = Endpoint(self.project, origin="agent:drive")
        return self.agent_ep

    def locate(self, name: str) -> dict:
        return self.drive.call("locate", name=name)

    def views(self) -> dict:
        return self.endpoint().call("canvas.state", {})["document"]["views"]

    def expand_arg(self, arg: str) -> str:
        arg = arg.replace("{project}", str(self.project))
        while "{env:" in arg:
            start = arg.index("{env:")
            end = arg.index("}", start)
            name = arg[start + 5 : end]
            if not os.environ.get(name):
                raise Flow(f"{arg}: {name} is not set")
            arg = arg[:start] + os.environ[name] + arg[end + 1 :]
        return arg

    def resolve(self, line: str, wait: float = 2.0) -> str:
        t0 = time.monotonic()
        while True:
            try:
                return resolve_refs(line, self.views())
            except Unresolved as e:
                if time.monotonic() - t0 > wait:
                    raise Flow(f"{line}: {e}") from None
                time.sleep(0.1)

    def point(
        self, name: str, fx: float = 0.5, fy: float = 0.5, reach: bool = True
    ) -> tuple[float, float]:
        """The window point at (fx, fy) of the item's rect; ``reach``: the pointer goes
        there, so the point must be in the part its clipping ancestors show (#167)."""
        name = name.replace("~", " ")
        info: dict = {}
        for _ in range(20):
            info = self.locate(name)
            if info["found"] and info["visible"]:
                break
            time.sleep(0.1)
        else:
            raise Flow(f"item {name!r} not found/visible: {info}")
        x, y, w, h = info["rect"]
        px, py = x + w * fx, y + h * fy
        if reach and "shown" in info:
            s = info["shown"]
            if s is None or not (s[0] <= px <= s[0] + s[2] and s[1] <= py <= s[1] + s[3]):
                raise Flow(
                    f"item {name!r} at ({fx}, {fy}) is clipped out of view: rect {info['rect']}, "
                    f"shown {s}"
                )
        return px, py

    def rect(self, name: str) -> list[float]:
        x0, y0 = self.point(name, 0, 0, reach=False)
        x1, y1 = self.point(name, 1, 1, reach=False)
        return [x0, y0, x1 - x0, y1 - y0]

    def shown(self, name: str) -> bool:
        try:
            name = resolve_refs(name, self.views()) if has_refs(name) else name
        except Unresolved:
            return False
        info = self.locate(name)
        return bool(info["found"] and info["visible"])

    def tree_expanded(self, view: str) -> list[str]:
        snap = self.endpoint().call("snap", {"target": view})
        return ((snap.get("meta") or {}).get("host") or {}).get("expanded") or []

    def tree_settled(self, view: str, limit: float = 5.0) -> None:
        """Wait until every open folder of the tree is listed: a folder opened a moment
        ago has its rows still to arrive from the worker (#231), and when they do the
        rows below it move, so a chevron located before that would be clicked where it
        no longer is."""
        t0 = time.monotonic()
        while True:
            snap = self.endpoint().call("snap", {"target": view})
            host = (snap.get("meta") or {}).get("host") or {}
            if set(host.get("expanded") or []) <= set(host.get("loaded") or []):
                return
            if time.monotonic() - t0 > limit:
                raise Flow(f"{view}: open folders still unlisted after {limit:.0f} s")
            time.sleep(0.05)

    def still_point(self, name: str, fx: float = 0.5, fy: float = 0.5) -> tuple[float, float]:
        """The item's point once it has stopped moving: the same on two reads 80 ms
        apart (a list lays its rows out again a moment after its model changed)."""
        last = self.point(name, fx, fy)
        for _ in range(25):
            time.sleep(0.08)
            now = self.point(name, fx, fy)
            if now == last:
                return now
            last = now
        return last

    def fold(self, target: str, want_open: bool) -> None:
        """Open or close a file tree folder by clicking its chevron (a toggle) only when it
        is not that way already. Since #231 everything starts closed but a single-folder
        chain from the root, and a folder is listed when it is first opened: opening a
        folder opens the closed folders above it first, each one's chevron in turn (as
        ``gui_drive.fold``), and returns once the opened folder's rows are there. A
        chevron is clicked where it has come to rest; a click the row moved from under
        is retried where the chevron is now."""
        view, _, path = target.partition(":")
        parts = path.split("/")
        steps = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)] if want_open else [path]
        for step in steps:
            if (step in self.tree_expanded(view)) == want_open:
                continue
            self.tree_settled(view)
            name = f"treeChevron:{view}:{step}"
            x, y = self.still_point(name)
            self.click(x, y)
            t0 = time.monotonic()
            t_click = t0
            while (step in self.tree_expanded(view)) != want_open:
                if time.monotonic() - t0 > 5:
                    state = "open" if want_open else "closed"
                    raise Flow(f"folder {view}:{step} is not {state}")
                if time.monotonic() - t_click > 1.0:
                    nx, ny = self.still_point(name)
                    if (nx, ny) != (x, y):
                        x, y = nx, ny
                        self.click(x, y)
                    t_click = time.monotonic()
                time.sleep(0.1)
        if want_open:
            # the rows are in the model; a turn of the app's loop lays them out
            self.tree_settled(view)
            self.drive.call("wait", ms=80)
            self.still_point(f"treeRow:{view}:{path}")

    def web_point(
        self, view: str, path: str, fx: float = 0.5, fy: float = 0.5
    ) -> tuple[float, float]:
        """A window point inside a web view from the page's own report (CSS px under
        ``snap`` -> ``meta.host.page``), as ``gui_drive.web_point``."""
        snap = self.endpoint().call("snap", {"target": view})
        page = ((snap.get("meta") or {}).get("host") or {}).get("page") or {}
        node: Any = page
        for key in path.split("."):
            node = node[int(key)] if isinstance(node, list) else node[key]
        if isinstance(node, dict) and "rect" in node:
            node = node["rect"]
        head = self.locate(f"header:{view}")
        if not head.get("found"):
            raise Flow(f"no header for {view}")
        hx, hy, _hw, hh = head["rect"]
        # CSS px to window px: the page's devicePixelRatio over the window's
        dpr = float(page.get("dpr") or 1.0) / float(self.drive.call("geometry")["dpr"] or 1.0)
        hh += float(page.get("top") or 0.0) * dpr
        if isinstance(node, dict):
            return hx + node["x"] * dpr, hy + hh + node["y"] * dpr
        x, y, w, h = node
        return hx + (x + w * fx) * dpr, hy + hh + (y + h * fy) * dpr

    # -- input primitives ------------------------------------------------------------------
    def move(self, x: float, y: float) -> None:
        self.drive.call("move", x=x, y=y)

    def glide(
        self, x0: float, y0: float, x1: float, y1: float, steps: int = 8, dt: float = 0.015
    ) -> None:
        """Move in steps (drag handlers want more than one move past the threshold)."""
        for i in range(1, steps + 1):
            self.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
            time.sleep(dt)

    def click(
        self, x: float, y: float, button: str = "left", mods: str = "", count: int = 1
    ) -> None:
        self.drive.call("click", x=x, y=y, button=button, mods=mods or None, count=count)

    def press(self, x: float, y: float, button: str = "left", mods: str = "") -> None:
        self.drive.call("press", x=x, y=y, button=button, mods=mods or None)

    def release(self, x: float, y: float, button: str = "left", mods: str = "") -> None:
        self.drive.call("release", x=x, y=y, button=button, mods=mods or None)

    def key(self, name: str, action: str = "click") -> None:
        self.drive.call("key", key=name, action=action)

    def wheel(self, x: float, y: float, notches: int, mods: str = "", pause: float = 0.08) -> None:
        step = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            self.drive.call("wheel", x=x, y=y, notches=step, mods=mods or None)
            time.sleep(pause)

    def shot(self, name: str, rect: list[float] | None = None, zoom: int = 1) -> Path:
        p = self.out / name
        self.drive.call("shot", path=str(p), rect=rect, zoom=zoom)
        print(f"[OK] shot {p}")
        return p

    def grab(self, rect: list[float]) -> Any:
        """A PIL image of a window rect (device px)."""
        p = self.tmp / f"grab-{time.monotonic_ns()}.png"
        self.drive.call("shot", path=str(p), rect=rect)
        try:
            return _img(p).convert("RGB")
        finally:
            p.unlink(missing_ok=True)

    def close(self) -> None:
        for proc in self.children:
            if proc.poll() is None:
                kill_tree(proc)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the lines ---------------------------------------------------------------------------
    def run(self, line: str) -> None:
        line = line.replace("$PROJECT", str(self.project))
        words = tokens(line)
        if not words or words[0].startswith("#"):
            return
        while words and words[0] in ("win:", "posix:", "mouse:", "driver:"):
            if words[0] in ("win:", "posix:") and (words[0] == "win:") != (sys.platform == "win32"):
                return
            if words[0] == "mouse:":  # the real-mouse executor's line (gui_drive.py)
                return
            line = line.split(None, 1)[1] if len(words) > 1 else ""
            words = words[1:]
        if not words:
            return
        print(f"> {line}", flush=True)
        if words[0] in ("wait_until", "wait_while", "assert_missing"):
            parts = words
        elif words[0] != "type" and has_refs(line):
            line = self.resolve(line)
            print(f"  = {line}")
            parts = line.split()
        else:
            parts = line.split()
        op, args = parts[0], [self.expand_arg(a) for a in parts[1:]]
        fn = getattr(self, f"op_{op}", None)
        if fn is None:
            raise Flow(f"unknown op {op}")
        fn(args)

    def _fxy(self, args: list[str], at: int = 1) -> tuple[float, float]:
        return (float(args[at]), float(args[at + 1])) if len(args) >= at + 2 else (0.5, 0.5)

    def op_shot(self, a: list[str]) -> None:
        time.sleep(0.15)
        self.shot(a[0])

    def op_shotwin(self, a: list[str]) -> None:
        raise Flow("shotwin (another app's window) is not available offscreen")

    def op_keydown(self, a: list[str]) -> None:
        self.key(a[0], "down")

    def op_keyup(self, a: list[str]) -> None:
        self.key(a[0], "up")

    def op_key(self, a: list[str]) -> None:
        self.key(a[0])

    def op_selectall(self, a: list[str]) -> None:
        self.key("ctrl+a")

    def op_type(self, a: list[str]) -> None:
        self.drive.call("type", text=" ".join(a))

    def op_click(self, a: list[str]) -> None:
        fx, fy = self._fxy(a)
        dx, dy = (int(a[3]), int(a[4])) if len(a) >= 5 else (0, 0)
        x, y = self.point(a[0], fx, fy)
        self.move(x + dx, y + dy)
        self.click(x + dx, y + dy)

    def _modclick(self, a: list[str], mod: str) -> None:
        fx, fy = self._fxy(a)
        x, y = self.point(a[0], fx, fy)
        self.move(x, y)
        self.click(x, y, mods=mod)

    def op_shiftclick(self, a: list[str]) -> None:
        self._modclick(a, "shift")

    def op_ctrlclick(self, a: list[str]) -> None:
        self._modclick(a, "ctrl")

    def op_altclick(self, a: list[str]) -> None:
        self._modclick(a, "alt")

    def op_rclick(self, a: list[str]) -> None:
        fx, fy = self._fxy(a)
        x, y = self.point(a[0], fx, fy)
        self.move(x, y)
        self.click(x, y, button="right")

    def op_dblclick(self, a: list[str]) -> None:
        fx, fy = self._fxy(a)
        x, y = self.point(a[0], fx, fy)
        self.move(x, y)
        self.click(x, y, count=2)

    def op_ctrldblclick(self, a: list[str]) -> None:
        """Ctrl held through a double-click (SPEC-32: the contextual preview)."""
        fx, fy = self._fxy(a)
        x, y = self.point(a[0], fx, fy)
        self.move(x, y)
        self.click(x, y, mods="ctrl", count=2)

    def op_clickat(self, a: list[str]) -> None:
        x, y = self.point(a[0])
        x, y = x + int(a[1]), y + int(a[2])
        self.move(x, y)
        self.click(x, y)

    def op_webclick(self, a: list[str]) -> None:
        fx, fy = self._fxy(a, 2)
        x, y = self.web_point(a[0], a[1], fx, fy)
        self.move(x, y)
        self.click(x, y)

    def op_hover(self, a: list[str]) -> None:
        fx, fy = self._fxy(a)
        dx, dy = (int(a[3]), int(a[4])) if len(a) >= 5 else (0, 0)
        x, y = self.point(a[0], fx, fy)
        self.move(x + dx - 3, y + dy - 3)  # a real pointer arrives, it does not appear
        time.sleep(0.03)
        self.move(x + dx, y + dy)

    def op_expand(self, a: list[str]) -> None:
        self.fold(a[0], True)

    def op_collapse(self, a: list[str]) -> None:
        self.fold(a[0], False)

    def op_wheel(self, a: list[str]) -> None:
        fx, fy = self._fxy(a, 2)
        ctrl = "ctrl" in a[4:]
        x, y = self.point(a[0], fx, fy)
        self.move(x, y)
        self.wheel(x, y, int(a[1]), "ctrl" if ctrl else "")

    def op_scroll(self, a: list[str]) -> None:
        x, y = self.point(a[0])
        self.move(x, y)
        self.wheel(x, y, int(a[1]), pause=0.03)
        time.sleep(0.3)

    def op_resize(self, a: list[str]) -> None:
        self.drive.call("resize", w=int(a[0]), h=int(a[1]))
        time.sleep(0.5)

    def op_spawn(self, a: list[str]) -> None:
        # a helper (drip_files.py, a seed) runs with the app's platform settings, so any
        # window it opens is offscreen too
        self.children.append(subprocess.Popen(a, cwd=str(ROOT), env=self.env, **popen_kwargs()))

    def op_drag(self, a: list[str]) -> None:
        fx, fy = self._fxy(a, 2)
        x0, y0 = self.point(a[0])
        x1, y1 = self.point(a[1], fx, fy)
        self.move(x0, y0)
        self.press(x0, y0)
        self.glide(x0, y0, x0 + 12, y0 + 12, steps=3)
        self.glide(x0 + 12, y0 + 12, x1, y1, steps=10)
        time.sleep(0.15)
        self.release(x1, y1)

    def op_hover_drag(self, a: list[str]) -> None:
        fx, fy = self._fxy(a, 2)
        x0, y0 = self.point(a[0])
        x1, y1 = self.point(a[1], fx, fy)
        self.move(x0, y0)
        self.press(x0, y0)
        self.glide(x0, y0, x0 + 12, y0 + 12, steps=3)
        self.glide(x0 + 12, y0 + 12, x1, y1, steps=10)
        time.sleep(0.3)
        self.shot(a[4] if len(a) >= 5 else "drag.png")
        self.release(x1, y1)

    def op_dragxy(self, a: list[str]) -> None:
        # dragxy <from> dx dy [fx fy] [shot=name.png] [button=right|middle|both] [alt] [mod=<key>]
        opts = dict(o.split("=", 1) for o in a[3:] if "=" in o)
        rest = [o for o in a[3:] if "=" not in o and o != "alt"]
        held = opts.get("shot")
        button = opts.get("button", "left")
        buttons = ["left", "right"] if button == "both" else [button]
        mod = opts.get("mod") or ("alt" if "alt" in a[3:] else None)
        fx, fy = (float(rest[0]), float(rest[1])) if len(rest) >= 2 else (0.5, 0.5)
        x0, y0 = self.point(a[0], fx, fy)
        dx, dy = int(a[1]), int(a[2])
        mods = mod or ""
        self.move(x0, y0)
        if mod:
            self.key(mod, "down")
        for b in buttons:
            self.press(x0, y0, button=b, mods=mods)
        sx, sy = x0 + (8 if dx else 0), y0 + (8 if dy else 0)
        self.glide(x0, y0, sx, sy, steps=2)
        self.glide(sx, sy, x0 + dx, y0 + dy, steps=10, dt=0.03)
        time.sleep(0.15)
        if held:
            time.sleep(0.3)
            self.shot(held)
        for b in reversed(buttons):
            self.release(x0 + dx, y0 + dy, button=b, mods=mods)
        if mod:
            self.key(mod, "up")

    def op_dragout(self, a: list[str]) -> None:
        raise Flow("dragout (a native drag into another app) needs the real-mouse tier")

    def op_close_probe(self, a: list[str]) -> None:
        raise Flow("close_probe belongs to dragout flows: the real-mouse tier")

    def op_assert_dropped(self, a: list[str]) -> None:
        raise Flow("assert_dropped belongs to dragout flows: the real-mouse tier")

    def op_mousedown(self, a: list[str]) -> None:
        opts = dict(o.split("=", 1) for o in a[1:] if "=" in o)
        rest = [o for o in a[1:] if "=" not in o]
        fx, fy = (float(rest[0]), float(rest[1])) if len(rest) >= 2 else (0.5, 0.5)
        x, y = self.point(a[0], fx, fy)
        self.move(x, y)
        self.press(x, y, button=opts.get("button", "left"))
        self._held = (x, y, opts.get("button", "left"))

    def op_mouseup(self, a: list[str]) -> None:
        opts = dict(o.split("=", 1) for o in a if "=" in o)
        x, y, _ = getattr(self, "_held", (0.0, 0.0, "left"))
        self.release(x, y, button=opts.get("button", "left"))

    def op_wait(self, a: list[str]) -> None:
        time.sleep(int(a[0]) / 1000)

    def op_agent(self, a: list[str]) -> None:
        res = self.agent().call(a[0], json.loads(" ".join(a[1:]) or "{}"))
        print(json.dumps(res)[:400])

    def op_submit(self, a: list[str]) -> None:
        doc = json.loads(Path(a[0]).read_text(encoding="utf-8"))
        label = " ".join(a[1:]) or Path(a[0]).stem
        res = self.agent().call("canvas.submit", {"document": doc, "label": label})
        print(json.dumps({k: res.get(k) for k in ("txn", "views", "links", "empty")}))

    def op_params(self, a: list[str]) -> None:
        doc = self.agent().call("canvas.state", {})["document"]
        doc["views"][a[0]].setdefault("params", {}).update(json.loads(" ".join(a[1:])))
        res = self.agent().call("canvas.submit", {"document": doc, "label": f"params {a[0]}"})
        print(f"[OK] params {a[0]}: {doc['views'][a[0]]['params']} (txn {res.get('txn')})")

    def op_submit_refused(self, a: list[str]) -> None:
        doc = json.loads(Path(a[0]).read_text(encoding="utf-8"))
        label = " ".join(a[1:]) or Path(a[0]).stem
        try:
            self.agent().call("canvas.submit", {"document": doc, "label": label})
        except RuntimeError as e:
            err = json.loads(str(e))
            data = err.get("data") or {}
            problems = data.get("problems") or (data.get("data") or {}).get("problems") or []
            print(f"[OK] refused: {err.get('message', '')[:200].splitlines()[0]}")
            for p in problems:
                print(f"  - {p['path']}: {p['message']}\n    fix: {p['fix']}")
        else:
            raise Flow(f"submit of {a[0]} was accepted; it should have been refused")

    def op_call(self, a: list[str]) -> None:
        self.endpoint().call(a[0], json.loads(" ".join(a[1:]) or "{}"))

    def op_print(self, a: list[str]) -> None:
        res = self.endpoint().call(a[0], json.loads(" ".join(a[1:]) or "{}"))
        print(json.dumps(res, indent=1)[:3000])

    def op_call_refused(self, a: list[str]) -> None:
        body = " ".join(a[1:])
        end = body.rfind("}") + 1
        want = body[end:].strip()
        try:
            self.endpoint().call(a[0], json.loads(body[:end] or "{}"))
        except RuntimeError as e:
            err = json.loads(str(e))
            said = json.dumps(err)
            if want and want not in said:
                raise Flow(
                    f"call_refused {a[0]}: refused, but not with {want!r}: {said[:400]}"
                ) from None
            print(f"[OK] call_refused {a[0]}: {err.get('message', said)[:200]}")
        else:
            raise Flow(f"call_refused {a[0]}: it went through")

    def _poll_shown(self, op: str, a: list[str]) -> None:
        limit = int(a[1]) / 1000 if len(a) > 1 else 60.0
        t0 = time.monotonic()
        while True:
            if self.shown(a[0]) == (op == "wait_until"):
                print(f"[OK] {op} {a[0]} after {time.monotonic() - t0:.1f} s")
                return
            if time.monotonic() - t0 > limit:
                state = "missing" if op == "wait_until" else "there"
                raise Flow(f"{op} {a[0]}: still {state} after {limit:.0f} s")
            time.sleep(0.25)

    def op_wait_until(self, a: list[str]) -> None:
        self._poll_shown("wait_until", a)

    def op_wait_while(self, a: list[str]) -> None:
        self._poll_shown("wait_while", a)

    def _poll_meta(self, op: str, a: list[str]) -> None:
        want = json.loads(a[2])
        limit = int(a[3]) / 1000 if len(a) > 3 else 10.0
        t0 = time.monotonic()
        while True:
            snap = self.endpoint().call("snap", {"target": a[0]})
            node: Any = snap.get("meta") or {}
            if op != "assert_meta":
                node = node.get("host") or {}
            if op == "assert_web":
                node = node.get("page") or {}
            for key in a[1].split("."):
                if isinstance(node, list) and key.isdigit():
                    node = node[int(key)] if int(key) < len(node) else None
                else:
                    node = node.get(key) if isinstance(node, dict) else None
            if node == want:
                print(f"[OK] {a[0]} {a[1]} = {a[2]} after {time.monotonic() - t0:.1f} s")
                return
            if time.monotonic() - t0 > limit:
                raise Flow(f"{a[0]} {a[1]} is {node!r}, not {want!r}")
            time.sleep(0.25)

    def op_assert_web(self, a: list[str]) -> None:
        self._poll_meta("assert_web", a)

    def op_assert_host(self, a: list[str]) -> None:
        self._poll_meta("assert_host", a)

    def op_assert_meta(self, a: list[str]) -> None:
        self._poll_meta("assert_meta", a)

    def op_assert_found(self, a: list[str]) -> None:
        info = self.locate(a[0])
        if not (info["found"] and info["visible"]):
            raise Flow(f"{a[0]} missing: {info}")

    def op_assert_missing(self, a: list[str]) -> None:
        if self.shown(a[0]):
            raise Flow(f"{a[0]} still visible")

    def op_mark(self, a: list[str]) -> None:
        info = self.locate(a[0])
        if not (info["found"] and info["visible"]):
            raise Flow(f"{a[0]} missing: {info}")
        self.marks[a[0]] = info["rect"]

    def _moved(self, a: list[str], by: list[float], rest: list[str]) -> None:
        info = self.locate(a[0])
        if not (info["found"] and info["visible"]):
            raise Flow(f"{a[0]} missing: {info}")
        tol = float(rest[0]) if rest else 2.0
        was, now = self.marks[a[0]], info["rect"]
        print(f"  {a[0]} at {now[:2]} (marked at {was[:2]}, expected +{by})")
        if not all(abs(w + d - n) <= tol for w, d, n in zip(was[:2], by, now[:2], strict=True)):
            raise Flow(f"{a[0]} moved from {was[:2]} to {now[:2]}, not by {by}")

    def op_assert_unmoved(self, a: list[str]) -> None:
        self._moved(a, [0.0, 0.0], a[1:])

    def op_assert_moved(self, a: list[str]) -> None:
        self._moved(a, [float(a[1]), float(a[2])], a[3:])

    def op_assert_text(self, a: list[str]) -> None:
        info = self.locate(a[0])
        want = " ".join(a[1:])
        print(f"[OK] text {a[0]}: {info.get('text')!r}")
        said = info.get("text") or ""
        if want not in said:
            raise Flow(f"{a[0]} says {said!r}, not {want!r}")

    def op_assert_clipboard(self, a: list[str]) -> None:
        want = " ".join(a).split("|")
        if sys.platform != "win32":
            want = [w.replace("\\", "/") for w in want]
        got = str(self.drive.call("clipboard")["text"]).splitlines()
        print(f"[OK] clipboard: {got!r}")
        if got != want:
            raise Flow(f"clipboard holds {got!r}, not {want!r}")

    def op_assert_files(self, a: list[str]) -> None:
        n = int(a[1])
        end = time.monotonic() + (float(a[2]) if len(a) > 2 else 20000.0) / 1000.0
        found: list[str] = []
        while time.monotonic() < end:
            found = glob.glob(a[0])
            if len(found) >= n:
                break
            time.sleep(0.1)
        print(f"[OK] files {a[0]}: {len(found)}")
        if len(found) < n:
            raise Flow(f"{len(found)} files match {a[0]}, not {n}")

    def op_assert_pixel(self, a: list[str]) -> None:
        x, y = self.point(a[0], float(a[1]), float(a[2]), reach=False)
        time.sleep(0.2)
        got = self.grab([x, y, 1, 1]).getpixel((0, 0))
        want = tuple(int(a[4].lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        same = sum(abs(p - q) for p, q in zip(got, want, strict=True)) <= 24
        hexed = "#{:02x}{:02x}{:02x}".format(*got)
        print(f"[OK] pixel {a[0]} at {a[1]},{a[2]}: {hexed}")
        if same != (a[3] == "is"):
            raise Flow(f"{a[0]}: pixel {hexed} {a[3]} {a[4]} failed")

    def op_shotcrop(self, a: list[str]) -> None:
        pad, zoom = int(a[1]), int(a[2])
        x, y, w, h = self.rect(a[0])
        time.sleep(0.2)
        self.shot(a[3], rect=[x - pad, y - pad, w + 2 * pad, h + 2 * pad], zoom=zoom)

    def op_assert_painted(self, a: list[str]) -> None:
        limit = float(a[1]) if len(a) > 1 else 0.75
        time.sleep(0.2)
        img = self.grab(self.rect(a[0])).reduce(4)
        colours = img.getcolors(img.width * img.height) or []
        top = max(colours)[0] / (img.width * img.height)
        print(f"[OK] painted {a[0]}: dominant colour {top:.0%} of pixels, {len(colours)} colours")
        if top > limit:
            raise Flow(f"{a[0]} looks blank: one colour covers {top:.0%} (> {limit:.0%})")

    def op_assert_smooth(self, a: list[str]) -> None:
        held = next((o[5:] for o in a[5:] if o.startswith("shot=")), None)
        least = float(next((o[4:] for o in a[5:] if o.startswith("min=")), 0.5))
        cx, cy = self.point(a[0], reach=False)
        dx, dy, w, h = (int(float(o)) for o in a[1:5])
        time.sleep(0.2)
        img = self.grab([cx + dx - w // 2, cy + dy - h // 2, w, h])
        px = img.load()
        rows = [len({px[x, y] for x in range(img.width)}) for y in range(img.height)]
        crossing = [n for n in rows if n >= 2]
        smooth = [n for n in crossing if n >= 3]
        if held:
            img.resize((img.width * 8, img.height * 8), 0).save(self.out / held)
            print(f"[OK] shot {self.out / held}")
        share = len(smooth) / len(crossing) if crossing else 0.0
        print(
            f"[OK] smooth {a[0]}: {len(smooth)}/{len(crossing)} crossing rows show "
            f"intermediate tones, colours per row {sorted(set(rows))}"
        )
        if not crossing:
            raise Flow(f"{a[0]}: no curve crosses the patch")
        if share < least:
            raise Flow(f"{a[0]}: the curve is aliased ({share:.0%} smooth rows)")


# -- sessions ----------------------------------------------------------------------------------
def app_env(
    project: Path, environ: dict[str, str], user_dir: Path, platform: str = "offscreen"
) -> dict[str, str]:
    """The app's environment: its own user settings dir and unbuffered output for the app
    log; on the offscreen platform (the default) a big screen, the software Qt Quick
    backend and the stub USD source unless the caller chose otherwise. ``platform``
    ``window`` (the GPU tier, #237) keeps the system platform, the GPU scene graph and
    the real renderers: the driver still opens the window without the focus."""
    env = {**environ, "PYTHONUNBUFFERED": "1", "SPECTO_USER_DIR": str(user_dir)}
    user_dir.mkdir(parents=True, exist_ok=True)
    if platform == "window":
        return env
    cfg = user_dir / "offscreen.json"
    cfg.write_text(
        json.dumps(
            {
                "screens": [
                    {
                        "name": "qtdrive",
                        "x": 0,
                        "y": 0,
                        "width": SCREEN[0],
                        "height": SCREEN[1],
                        "logicalDpi": 96,
                        "logicalBaseDpi": 96,
                        "dpr": 1.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # the plugin splits its argument on ":", so a Windows drive letter cannot appear:
    # the path is relative to the app's cwd (the repo root); on another drive the file
    # goes under the repo's own git-ignored .venv instead
    try:
        rel = os.path.relpath(cfg, ROOT)
    except ValueError:
        local = ROOT / ".venv" / f"qtdrive-offscreen-{os.getpid()}.json"
        shutil.copyfile(cfg, local)
        rel = os.path.relpath(local, ROOT)
    env["QT_QPA_PLATFORM"] = f"offscreen:configfile={Path(rel).as_posix()}"
    if sys.platform == "win32":
        # the offscreen plugin's FreeType font database knows no system fonts on Windows
        # (text would draw as boxes): point it at the Windows fonts
        env.setdefault(
            "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        )
    env.setdefault("QT_QUICK_BACKEND", "software")
    env.setdefault("QSG_RHI_BACKEND", "opengl")
    env.setdefault("SPECTO_DISABLE_CAPABILITIES", "ovrtx,storm")
    return env


def wait_for(path: Path, app: subprocess.Popen, what: str, timeout: float = START_S) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if app.poll() is not None:
            raise Flow(f"app exited early with {app.returncode} before {what}")
        time.sleep(0.1)
    raise Flow(f"{what} never appeared ({path})")


def open_drive(user_dir: Path, app: subprocess.Popen) -> Drive:
    path = user_dir / "drive.json"
    wait_for(path, app, "the test driver")
    # the file names the pipe by the app's own pid (uv run wraps it, so not ``app.pid``);
    # a stale file from an earlier run fails the ping and is polled past
    deadline = time.monotonic() + 20
    while True:
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            d = Drive(info)
            d.call("ping")
            return d
        except (OSError, ValueError, Flow) as e:
            if time.monotonic() > deadline:
                raise Flow(f"cannot reach the test driver: {e}") from None
            time.sleep(0.2)


def welcome(flow: QtFlow, project: Path, *, prefix: str, recent: bool, only: bool) -> None:
    """The welcome card: screenshot it, open the project by typing its folder and Enter or
    by the first recent row; with ``only`` screenshot the card again and stop."""
    deadline = time.monotonic() + 30
    while not flow.locate("welcomeCard").get("visible"):
        if time.monotonic() > deadline:
            raise Flow("no welcome card")
        time.sleep(0.1)
    time.sleep(0.8)
    flow.shot(f"{prefix}-01-welcome.png")
    if recent:
        x, y, w, _h = flow.rect("recentList")
        flow.move(x + w * 0.5, y + 23)
        flow.click(x + w * 0.5, y + 23)
    else:
        x, y = flow.point("pathField")
        flow.move(x, y)
        flow.click(x, y)
        flow.drive.call("type", text=str(project))
        time.sleep(0.3)
        flow.shot(f"{prefix}-01b-welcome-typed.png")
        flow.key("enter")
    if only:
        time.sleep(1.5)
        flow.shot(f"{prefix}-02-welcome-after.png")


def run_session(ns: argparse.Namespace) -> list[dict[str, Any]]:
    """One app process, the flows in order; a list of per-flow results."""
    results: list[dict[str, Any]] = []
    # --fresh wipes only a folder a GUI session created, and under the desktop lock, so
    # never one a mouse-tier session holding it is using (SPEC-7)
    if ns.fresh:
        gui_scratch.wipe_fresh(ns.project, f"gui-qtdrive-{os.getpid()}")
    else:
        gui_scratch.prepare_project(ns.project, fresh=False)
    ep_path = ns.project / ".specto" / "endpoint.json"
    if ep_path.exists():
        ep_path.unlink()
    user_dir = Path(os.environ.get("SPECTO_USER_DIR") or ns.project / ".specto" / "gui-user")
    (user_dir / "drive.json").unlink(missing_ok=True)
    env = app_env(ns.project, dict(os.environ), user_dir, getattr(ns, "platform", "offscreen"))
    log = open(ns.app_log, "w", encoding="utf-8") if ns.app_log else None  # noqa: SIM115  (closed in finally)
    cmd = ["uv", "run", "--no-sync", "specto-app", "--test-driver", *ns.extra.split()]
    if not ns.pool:
        cmd.append("--inline-jobs")
    if not ns.welcome:
        cmd += ["--project", str(ns.project)]
    app = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=log or subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=env,
        **popen_kwargs(),
    )
    print(f"[OK] app pid {app.pid}")
    flow: QtFlow | None = None
    t0 = time.monotonic()
    current = "session"
    try:
        drive = open_drive(user_dir, app)
        flow = QtFlow(drive, ns.project, ns.out, None)
        flow.env = env
        if ns.welcome:
            current = "welcome"
            welcome(
                flow,
                ns.project,
                prefix=ns.shot_prefix,
                recent=ns.welcome_recent,
                only=ns.welcome_only,
            )
            results.append({"flow": "welcome", "ok": True, "seconds": time.monotonic() - t0})
            if ns.welcome_only:
                print("[OK] welcome done")
                return results
        wait_for(ep_path, app, "the endpoint")
        time.sleep(0.5)
        flow.ep = Endpoint(ns.project)
        for path in ns.flows:
            current = path.name
            t1 = time.monotonic()
            print(f"[..] flow {path}", flush=True)
            for line in path.read_text(encoding="utf-8").splitlines():
                flow.run(line.strip())
            results.append({"flow": path.name, "ok": True, "seconds": time.monotonic() - t1})
            print(f"[OK] flow {path.name} ({time.monotonic() - t1:.0f} s)", flush=True)
        print("[OK] flows done")
        return results
    except (Flow, AssertionError, RuntimeError, OSError, KeyError, IndexError, ValueError) as e:
        why = f"{type(e).__name__}: {e}" if not isinstance(e, Flow) else str(e)
        print(f"[ERROR] {current}: {why}", flush=True)
        results.append(
            {"flow": current, "ok": False, "seconds": time.monotonic() - t0, "error": why}
        )
        try:
            if flow is not None:
                flow.shot(f"{Path(current).stem}-FAILED.png")
        except Exception:
            pass
        return results
    finally:
        if flow is not None:
            flow.close()
        if not ns.keep and app.poll() is None:
            # the normal close first (the canvas saved, the agent session ended), the
            # tree killed only if the app lingers
            with contextlib.suppress(Exception):
                if flow is not None:
                    flow.drive.call("quit")
                    app.wait(8)
        if not ns.keep and app.poll() is None:
            kill_tree(app)
            try:
                app.wait(5)
            except subprocess.TimeoutExpired:
                app.kill()
        if log:
            log.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--app-log", type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="leave the app running")
    ap.add_argument("--pool", action="store_true", help="worker pool instead of --inline-jobs")
    ap.add_argument("--extra", default="", help="extra specto-app flags")
    ap.add_argument("--welcome", action="store_true", help="start on the welcome card")
    ap.add_argument("--shot-prefix", default="final")
    ap.add_argument("--welcome-recent", action="store_true")
    ap.add_argument("--welcome-only", action="store_true")
    ap.add_argument("--report", type=Path, default=None, help="write per-flow results as JSON")
    ap.add_argument(
        "--platform",
        choices=("offscreen", "window"),
        default="offscreen",
        help="offscreen (default) or a real window that never takes the focus (the GPU tier)",
    )
    ap.add_argument("flows", nargs="*", type=Path)
    ns = ap.parse_args()
    utf8_stdio()
    results = run_session(ns)
    if ns.report:
        ns.report.parent.mkdir(parents=True, exist_ok=True)
        ns.report.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0 if all(r["ok"] for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
