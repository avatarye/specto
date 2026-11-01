"""The stub USD source (#139): the stub test-pattern source plus the USD control methods
the ``usd`` view's host calls, with a fake stage. The stand-in for ``specto-usd-source``
(#137) in seam-2 tests and the chrome's seam-3 flow. Standard library only; it never
opens the file beyond reading ``upAxis`` from a ``.usda`` header.

    python -m specto_app.frames.stub_usd_source [stub_source's flags] [--phase-ms 0]
        [--renderer NAME]

``--renderer`` names the renderer it stands in for, on the command line (the host reads
it there, #155) and in its start phase, ``health {state: compiling shaders}`` for rtx
and ``starting`` for storm as the process starts (#156); its ``loaded`` events still say
``stub``.

Methods on top of the lifecycle set (:mod:`.control`):

- ``load {path}``: answered at once; the source's loop then walks the start phases as
  ``health`` events (``composing``, ``first frame``; ``--phase-ms`` apart), sends
  ``loaded {path, name, renderer, up_axis, bounds, prims, start_time_code,
  end_time_code, time_codes_per_second, animated}`` (#141: the root layer's time codes,
  USD's fallbacks where it authors none; ``animated``: something is time-sampled, here
  a ``.timeSamples`` anywhere in a ``.usda``), draws the stage's first frame and sends
  ``health {state: ok, seq}``, ``seq`` that frame's (#166: as ``specto-usd-source``,
  the stage's first complete frame). A missing file is a ``health {state: error}``;
- ``set_time {t, timecode}`` (#141): ``t`` seconds on the ``usd`` timeline, ``timecode``
  the stage's; remembered, nothing more;
- ``query_tree {path, depth, offset, limit}``: the children of ``path`` (``"/"``: the
  root prims), ``depth`` levels deep, a page of ``limit`` from ``offset``; each node is
  ``{path, name, type, count}`` (``count``: its children) plus ``children`` while depth
  lasts; ``{filter}`` instead returns ``matches``, prims whose name holds the text;
- ``state``: what the source holds (``loaded``, ``camera``, ``paused``, size, time,
  ``timecode``, ``selected``);
- selection (#140), as ``specto-usd-source`` answers it: ``pick {x, y}`` (NDC, -1..1,
  +y up) ``{hit: true, path, position, normal}`` or ``{hit: false}``; ``select {paths, color}``
  (the outline: only recorded); ``properties {path}`` ``{path, name, type, kind,
  schemas, properties}``; ``bounds {path}`` ``{path, bounds}``; ``reveal {path,
  limit}`` ``{path, levels}``, the ``query_tree`` pages from ``"/"`` down to the prim;
- view modes (#153), as ``specto-usd-source`` answers them: ``set_view_mode {mode}``
  (``lit``, ``unlit``, ``wireframe``, ``collision``) ``{mode, note}``, an unknown mode an
  error; ``view_modes`` ``{mode, modes: [{mode, available, reason}]}``, all four
  available (the fake bases are colliders); every load starts lit. Only recorded: the
  picture stays the test pattern;
- ``crash {code, stderr}`` (tests only, #155): writes the ``stderr`` lines to stderr and
  ends the process at once with exit code ``code`` (``0xC0000005``: a renderer crash; on
  POSIX that one dies by SIGSEGV instead, as a real crash there does).

``--settle-frames N`` (#166): as a settled renderer, it draws ``N`` frames after its
start and after each load, then none until the next load (0, the default: it never
stops).

``--crash-after-ms N --crash-marker FILE`` (a scripted flow, #155): a stub crashes so,
with an RTX device-lost log, ``N`` ms after its first stage is up, unless ``FILE``
exists; it makes ``FILE``, so the stubs after it (Retry) live.

The fake stage: ``/World`` (Xform) with ``Blocks`` (three blocks of a top and a base
each), ``Sky`` (a DomeLight), ``Cameras/Ground`` and ``Vehicles`` (250 vans, so the
outliner pages). Its fake picture for picks: the sky over the top fifth, then the three
blocks left to right (x -40, 0, 40), their tops above the middle, their bases below.
"""

from __future__ import annotations

import argparse
import faulthandler
import itertools
import os
import re
import signal
import sys
import threading
import time

from .stub_source import StubSource

RENDERER = "stub"
VIEW_MODES = ("lit", "unlit", "wireframe", "collision")
#: the start phase of the renderer it stands in for (``specto-usd-source``'s)
START_PHASE = {"rtx": "compiling shaders", "storm": "starting"}
#: what ``--crash-after-ms`` writes to stderr before it crashes: the #155 log, shortened
CRASH_LOG = [
    "[Error] [omni.rtx] Resource upload failed (error code: 2): a device lost, out of"
    " memory, or an unexpected bug",
    "[Error] [gpu.foundation] submitToQueueCommon: VkResult: ERROR_DEVICE_LOST",
    "[Fatal] Waiting on Semaphore 4 for longer than 60 sec: TextureStreaming copy engine",
]
ACCESS_VIOLATION = 0xC0000005
#: what a Windows crash code is on POSIX: the signal the process dies by
POSIX_CRASH = {ACCESS_VIOLATION: signal.SIGSEGV, 0xC0000409: signal.SIGABRT}
BOUNDS = [[-60.0, -40.0, 0.0], [60.0, 40.0, 12.0]]
VANS = 250
BLOCK_X = (-40.0, 0.0, 40.0)


def fake_stage() -> dict[str, tuple[str, list[str]]]:
    """``{prim path: (type, child paths)}`` of the fake stage, children in order."""
    prims: dict[str, tuple[str, list[str]]] = {}

    def add(path: str, kind: str) -> None:
        prims[path] = (kind, [])
        parent = path.rsplit("/", 1)[0] or "/"
        if parent in prims:
            prims[parent][1].append(path)

    prims["/"] = ("", [])
    add("/World", "Xform")
    add("/World/Blocks", "Scope")
    for i in range(1, 4):
        block = f"/World/Blocks/Block_{i:02d}"
        add(block, "Xform")
        add(f"{block}/Top", "Mesh")
        add(f"{block}/Base", "Mesh")
    add("/World/Sky", "DomeLight")
    add("/World/Cameras", "Scope")
    add("/World/Cameras/Ground", "Camera")
    add("/World/Vehicles", "Scope")
    for i in range(VANS):
        add(f"/World/Vehicles/Van_{i:03d}", "Xform")
    return prims


def block_x(path: str) -> float | None:
    """The x of the fake block ``path`` is in (or is), else None."""
    m = re.match(r"/World/Blocks/Block_0(\d)(/|$)", path)
    return BLOCK_X[int(m[1]) - 1] if m else None


def fake_pick(x: float, y: float) -> dict:
    """The fake picture's prim at NDC ``(x, y)`` (-1..1, +y up)."""
    u, v = (x + 1) / 2, (1 - y) / 2  # from the top left
    if v < 0.2:
        return {"hit": False}
    i = min(2, max(0, int(u * 3)))
    block = f"/World/Blocks/Block_0{i + 1}"
    if v < 0.5:
        return {
            "hit": True,
            "path": f"{block}/Top",
            "position": [BLOCK_X[i], 0.0, 10.0],
            "normal": [0.0, 0.0, 1.0],
        }
    return {
        "hit": True,
        "path": f"{block}/Base",
        "position": [BLOCK_X[i], -6.0, 4.0],
        "normal": [0.0, -1.0, 0.0],
    }


def up_axis_of(path: str) -> str:
    if not path.lower().endswith(".usda"):
        return "Y"
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    m = re.search(r'upAxis\s*=\s*"([YZ])"', head)
    return m.group(1) if m else "Y"


def time_codes_of(path: str) -> dict:
    """The ``loaded`` event's time fields from a ``.usda``'s header (USD's fallbacks: a
    range of 0 .. 0 and 24 time codes a second); ``animated`` when anything in the file
    is time-sampled."""
    text = ""
    if path.lower().endswith(".usda"):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()

    def number(name: str) -> float | None:
        m = re.search(rf"\b{name}\s*=\s*([-+0-9.eE]+)", text[:4096])
        return float(m.group(1)) if m else None

    fps = number("framesPerSecond")
    tcps = number("timeCodesPerSecond") or fps or 24.0
    return {
        "start_time_code": number("startTimeCode") or 0.0,
        "end_time_code": number("endTimeCode") or 0.0,
        "time_codes_per_second": tcps,
        "animated": ".timeSamples" in text,
    }


class StubUsdSource(StubSource):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self.phase_s = max(0.0, args.phase_ms / 1000.0)
        self.prims = fake_stage()
        self.loaded: str | None = None
        self.timecode: float | None = None
        self._want: str | None = None
        self.selected: list[str] = []
        self.view_mode = "lit"
        self.start_phase = START_PHASE.get(args.renderer)
        self.crash_after_s = max(0.0, args.crash_after_ms / 1000.0)
        self.crash_marker = args.crash_marker
        self.settle_frames = max(0, args.settle_frames)
        self._left = self.settle_frames  # frames still to draw before it idles
        self._ok_due = False  # the stage loaded: ``ok`` follows its first frame

    def run(self) -> int:
        if self.start_phase:
            self.control.emit("health", state=self.start_phase, message="")
        return super().run()

    def handle(self, method: str, params: dict) -> dict:
        if method == "load":
            self._want = str(params["path"])
            return {}
        if method == "crash":
            self.crash(int(params.get("code", 1)), [str(x) for x in params.get("stderr") or []])
        if method == "set_time" and "timecode" in params:
            self.timecode = float(params["timecode"])
        if method == "query_tree":
            return self.query_tree(params)
        if method == "state":
            return {
                "loaded": self.loaded,
                "camera": self.camera,
                "paused": self.paused,
                "width": self.width,
                "height": self.height,
                "time": self.time,
                "selected": self.selected,
                "timecode": self.timecode,
                "view_mode": self.view_mode,
            }
        stage_methods = ("pick", "properties", "bounds", "reveal", "set_view_mode", "view_modes")
        if method in stage_methods and self.loaded is None:
            raise ValueError("no stage loaded")
        if method == "set_view_mode":
            mode = str(params.get("mode") or "")
            if mode not in VIEW_MODES:
                raise ValueError(f"unknown view mode {mode!r} (have: {', '.join(VIEW_MODES)})")
            self.view_mode = mode
            return {"mode": mode, "note": "3 colliders" if mode == "collision" else ""}
        if method == "view_modes":
            modes = [{"mode": m, "available": True, "reason": ""} for m in VIEW_MODES]
            return {"mode": self.view_mode, "modes": modes}
        if method == "pick":
            return fake_pick(float(params["x"]), float(params["y"]))
        if method == "select":
            self.selected = [str(p) for p in params.get("paths") or []]
            return {}
        if method == "properties":
            return self.properties(self._prim(params))
        if method == "bounds":
            path = self._prim(params)
            x = block_x(path)
            bounds = BOUNDS if x is None else [[x - 8, -6.0, 0.0], [x + 8, 6.0, 12.0]]
            return {"path": path, "bounds": bounds}
        if method == "reveal":
            return self.reveal(self._prim(params), max(1, int(params.get("limit", 100))))
        return super().handle(method, params)

    def crash(self, code: int, lines: list[str]) -> None:
        for line in lines:
            sys.stderr.write(line + "\n")
        sys.stderr.flush()
        sig = POSIX_CRASH.get(code & 0xFFFFFFFF) if os.name == "posix" else None
        if sig is not None:  # crash as a POSIX process does: by the signal, not a code
            faulthandler.disable()  # its handler's traceback is not the renderer's log
            signal.raise_signal(sig)  # to this thread, at once (os.kill races the _exit)
        os._exit(code - (1 << 32) if code > 0x7FFFFFFF else code)  # an NTSTATUS is unsigned

    # -- the fake stage ------------------------------------------------------------------
    def node(self, path: str, depth: int) -> dict:
        kind, children = self.prims[path]
        out = {"path": path, "name": path.rsplit("/", 1)[-1], "type": kind, "count": len(children)}
        if depth > 0 and children:
            out["children"] = [self.node(c, depth - 1) for c in children]
        return out

    def query_tree(self, params: dict) -> dict:
        if self.loaded is None:
            raise ValueError("no stage loaded")
        text = str(params.get("filter") or "").lower()
        limit = max(1, int(params.get("limit", 100)))
        if text:
            hits = [p for p in self.prims if p != "/" and text in p.rsplit("/", 1)[-1].lower()]
            return {
                "filter": text,
                "matches": [self.node(p, 0) for p in hits[:limit]],
                "total": len(hits),
            }
        path = str(params.get("path") or "/")
        if path not in self.prims:
            raise ValueError(f"no prim {path}")
        depth = max(1, int(params.get("depth", 1)))
        offset = max(0, int(params.get("offset", 0)))
        children = self.prims[path][1]
        page = children[offset : offset + limit]
        return {
            "path": path,
            "offset": offset,
            "total": len(children),
            "children": [self.node(c, depth - 1) for c in page],
        }

    def _prim(self, params: dict) -> str:
        path = str(params.get("path") or "")
        if path not in self.prims or path == "/":
            raise ValueError(f"no prim {path}")
        return path

    def properties(self, path: str) -> dict:
        kind = self.prims[path][0]
        props: dict = {"visibility": "inherited", "purpose": "default"}
        block = re.match(r"/World/Blocks/(Block_0\d)", path)
        if block:
            props["specto:block"] = block[1]
        if kind == "Mesh":
            props["points"] = {"length": 240, "head": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]}
        return {
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "type": kind,
            "kind": "component" if block and path.count("/") == 3 else None,
            "schemas": [],
            "properties": props,
        }

    def reveal(self, path: str, limit: int) -> dict:
        chain = [path]
        while chain[-1] != "/":
            chain.append(chain[-1].rsplit("/", 1)[0] or "/")
        chain.reverse()
        levels = []
        for parent, child in itertools.pairwise(chain):
            kids = self.prims[parent][1]
            page = kids[: max(limit, kids.index(child) + 1)]
            levels.append(
                {
                    "path": parent,
                    "offset": 0,
                    "total": len(kids),
                    "children": [self.node(c, 0) for c in page],
                }
            )
        return {"path": path, "levels": levels}

    # -- the start phases, on the frame loop ---------------------------------------------
    def draw(self) -> None:
        want, self._want = self._want, None
        if want is not None:
            self._load(want)
        if self.settle_frames:
            if self._left <= 0:
                return
            self._left -= 1
        super().draw()
        if self._ok_due:
            self._ok_due = False
            self.control.emit("health", state="ok", message="", seq=self.frames.ring.seq)
            if self.crash_after_s and self.crash_marker:
                self._crash_once(self.crash_marker)

    def _load(self, path: str) -> None:
        self.loaded = None
        if not os.path.isfile(path):
            self.control.emit("health", state="error", message=f"cannot open {path}")
            return
        self.control.emit("health", state="composing", message=os.path.basename(path))
        time.sleep(self.phase_s)
        self.control.emit("health", state="first frame", message="")
        time.sleep(self.phase_s)
        self.loaded = path
        self.timecode = None
        self.view_mode = "lit"
        self.control.emit(
            "loaded",
            path=path,
            name=os.path.basename(path),
            renderer=RENDERER,
            up_axis=up_axis_of(path),
            bounds=BOUNDS,
            prims=len(self.prims) - 1,
            **time_codes_of(path),
        )
        self._ok_due = True
        self._left = self.settle_frames

    def _crash_once(self, marker: str) -> None:
        if os.path.exists(marker):
            return
        with open(marker, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        delay, self.crash_after_s = self.crash_after_s, 0.0
        timer = threading.Timer(delay, self.crash, (ACCESS_VIOLATION, CRASH_LOG))
        timer.daemon = True
        timer.start()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Specto stub USD source (test pattern, fake stage)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--name", default="")
    ap.add_argument("--max-width", type=int, default=0)
    ap.add_argument("--max-height", type=int, default=0)
    ap.add_argument("--phase-ms", type=float, default=0.0)
    ap.add_argument("--renderer", default="stub", help="the renderer it stands in for")
    ap.add_argument("--settle-frames", type=int, default=0, help="frames after a load, 0: all")
    ap.add_argument("--crash-after-ms", type=float, default=0.0, help="flows: crash once")
    ap.add_argument("--crash-marker", default="", help="flows: made by the one that crashed")
    return StubUsdSource(ap.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
