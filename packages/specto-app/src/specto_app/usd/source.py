"""``specto-usd-source`` (#137): one usd view's frame-source process. It opens the stage
read-only with pxr (bounds, prims, the outliner), renders it with a renderer (``rtx``:
ovrtx, :mod:`.rtx`; ``storm``: Hydra Storm, :mod:`.storm`, #143) and writes the frames
into the frame ring.

    python -m specto_app.usd.source --renderer rtx|storm [--width 1920] [--height 1080]
        [--max-pixels N] [--name specto-usd-<pid>] [--settle-steps N] [--log-file F]
        [--openusd PREFIX] [--texture-streaming async|sync|off] [--fresh-renderer]

rtx reads the stage with usd-core (the rtx extra); storm with the OpenUSD imaging build
at ``--openusd`` (default ``specto_core.machine.openusd_prefix``), whose pxr has Storm.

The renderer logs to ``specto-usd-source-<pid>.log`` in the temp folder, removed on a
clean exit; crash-dump uploads are off.

#155: ``--texture-streaming`` picks ovrtx's texture streaming mode (``async``, its
default; ``sync``: feedback processed synchronously; ``off``), and ``--fresh-renderer``
starts a new renderer for each stage after the first instead of attaching the new stage
to the running one.

It speaks the frame-source protocol (:mod:`specto_app.frames.control`) with the stub USD
source's methods (#139), so the usd host drives either one:

- ``load {path}``: answered at once; the render thread then sends ``health`` phases
  (``composing``, ``first frame``, ``ok``) and ``loaded {path, name, renderer, up_axis,
  meters_per_unit, bounds, prims, start_time_code, end_time_code, time_codes_per_second,
  animated}`` (``animated``: a layer holds time samples, #141), plus ``renderer_note``
  when the renderer cannot show all of the stage (Storm: MDL materials) or lights it
  itself (rtx: a stage without lights gets a default dome, #148). ``ok`` comes
  with the stage's first frame in the ring, its ``seq`` the stage's first complete
  frame (#166: past the frames in flight), which the source always draws. The renderer
  itself starts with the process, as ``compiling shaders`` for rtx (about 2.5 minutes on
  a cold shader cache, said in its message, a few seconds warm), ``starting`` for storm;
- ``set_camera {position, target, up, fov_y, near, far, cut}`` in the stage's frame;
  ``cut`` (a jump) resets the render history so TAA and DLSS do not ghost;
- ``resize {width, height}``: capped at ``--max-pixels``, aspect kept;
- ``pick {x, y}``: the prim at NDC ``(x, y)`` (-1..1, +y up) of the current camera,
  answered once the render thread has taken the requests before it: ``{hit: false}`` or
  ``{hit: true, path, position, normal}`` (stage space). rtx answers it with one step
  that carries an ovrtx pick query (#140), Storm with ``TestIntersection`` (#143);
- ``set_time {t, timecode}`` (#141): the stage's USD time. rtx: playing on, the
  simulation clock advances with it, a jump or a step back resets the render history;
  storm: the next render's frame;
- ``pause``, ``resume``, ``query_tree`` (the outliner),
  ``state``, ``restart`` (after a failed step: a new renderer reloads the stage),
  ``shutdown``;
- selection (#140): ``select {paths, color}`` outlines the geometry under ``paths``
  (``color``: ``#rrggbb``, kept until the next one; ``[]`` clears; a renderer without
  outlines ignores it), answered at once; ``state`` reports ``selected``.
  ``properties {path}`` answers the prim as JSON (``{path, name, type, kind, schemas,
  properties}``) and ``bounds {path}`` the world bounds of its whole subtree at the
  current time (``{path, bounds}``; #39: an Xform frames all of its children); ``reveal
  {path, limit}`` the outliner pages down to it (``{path, levels: [query_tree pages]}``);
- view modes (#153): ``set_view_mode {mode}`` (``lit``, ``unlit``, ``wireframe``,
  ``collision``) answers once the render thread has switched, with the mode in effect and
  what it shows (``{mode, note}``; collision: the colliders drawn and how), or an error
  with the reason (no prims with CollisionAPI, a renderer without view modes). Every load
  starts lit. ``view_modes`` answers ``{mode, modes: [{mode, available, reason}]}`` for
  the loaded stage; ``state`` reports ``view_mode``. In collision a pick names the
  collider, not the overlay prim that draws it.

Frames: two steps in flight (48 fps against 39 synchronous at 1080p in the spike), each
mapped to the CPU (Storm: read into a PBO) and copied into the ring's free slot in place.
Stepping stops ``--settle-steps`` steps after the last change (camera, size, load,
resume; rtx 120 for TAA and DLSS to converge, storm a few), so an idle view costs no
GPU. A failed step is ``health {state: error, restartable: true}``; the
view keeps its last frame. An error the renderer survives (Storm: OpenUSD's Tf errors,
posted after the frame is drawn) is ``warning {message}``, once each a stage, and the
loop goes on. The process ends when the host closes stdin.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import math
import os
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from specto_app.frames.control import ControlServer
from specto_app.frames.server import FrameServer
from specto_core.machine import INSTALL_HINT, STORM_HINT, openusd_prefix

from .nav import CameraState, Nav, usd_camera

#: a USD time step on of at most this many seconds plays (the simulation advances with
#: it); a longer one is a jump
MAX_TIME_STEP_S = 0.1
IN_FLIGHT = 2
#: steps after the last change, then idle: rtx's TAA and DLSS converge; Storm keeps no
#: history, a few cover its textures still loading
SETTLE_STEPS = {"rtx": 120, "storm": 8}
MAX_PIXELS = 1920 * 1080
RENDERERS = ("rtx", "storm")
PICK_TIMEOUT_S = 10.0
#: the health phase while the renderer starts
START_PHASE = {"rtx": "compiling shaders", "storm": "starting"}
OUTLINE_COLOR = "#2f6fab"  # the theme's accent; the host sends its own
#: read from the stage on the control thread, beside the render loop
STAGE_QUERIES = ("query_tree", "reveal", "properties", "bounds", "view_modes")
VIEW_MODES = ("lit", "unlit", "wireframe", "collision")
NO_COLLIDERS = "no prims with UsdPhysics CollisionAPI"
MODE_TIMEOUT_S = 120.0  # a collision view builds its overlay first
COLD_MESSAGE = "(first run, about 3 min)"


def shader_cache() -> Path:
    """Where ovrtx keeps its compiled shaders, per version (``%LOCALAPPDATA%/ovrtx/0.6.0``,
    on Linux ``$XDG_CACHE_HOME/ovrtx/0.6.0`` when that is absolute, else
    ``~/.cache/ovrtx/0.6.0``), found without importing ovrtx."""
    try:
        version = ".".join(importlib.metadata.version("ovrtx").split(".")[:3])
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        base = xdg if os.path.isabs(xdg) else str(Path.home() / ".cache")
    return Path(base) / "ovrtx" / version


def shaders_cold() -> bool:
    """No compiled shaders yet: the renderer's start takes about 2.5 minutes, not seconds."""
    cache = shader_cache()
    return not cache.is_dir() or not any(cache.iterdir())


def capped(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    """``width x height`` scaled down to at most ``max_pixels``, aspect kept, never < 1."""
    w, h = max(1, int(width)), max(1, int(height))
    if max_pixels > 0 and w * h > max_pixels:
        k = math.sqrt(max_pixels / (w * h))
        w, h = max(1, int(w * k)), max(1, int(h * k))
    return w, h


def _camera_state(params: dict[str, Any]) -> CameraState:
    def vec(key: str) -> tuple[float, float, float]:
        x, y, z = (float(v) for v in params[key])
        return (x, y, z)

    return CameraState(
        eye=vec("position"),
        target=vec("target"),
        up=vec("up"),
        fov_y=float(params["fov_y"]),
        near=float(params["near"]),
        far=float(params["far"]),
    )


def _fill(dst: memoryview, pixels) -> None:
    """Copy a mapped (H, W, 4) uint8 image into a ring slot."""
    import numpy as np

    np.copyto(np.frombuffer(dst, np.uint8, pixels.size).reshape(pixels.shape), pixels)


class UsdSource:
    def __init__(
        self,
        args: argparse.Namespace,
        make_renderer: Callable[[], Any],
        stdio=(None, None),
    ) -> None:
        self.kind = args.renderer
        self.max_pixels = args.max_pixels
        self.width, self.height = capped(args.width, args.height, self.max_pixels)
        settle = args.settle_steps if args.settle_steps is not None else SETTLE_STEPS[self.kind]
        self.settle_steps = max(1, settle)
        self.make_renderer = make_renderer
        self.fresh_renderer = bool(getattr(args, "fresh_renderer", False))
        self.renderer: Any = None
        self.info: Any = None  # stage.StageInfo of the loaded stage
        self.camera: CameraState | None = None
        self.paused = False
        self.failed = False
        self.time = 0.0
        self.timecode: float | None = None  # the stage's USD time; None: its default
        self.steps = 0  # steps finished since the process started
        self._cond = threading.Condition()
        self._info_lock = threading.Lock()
        self._stop = False
        self._want_load: str | None = None
        self._want_camera: CameraState | None = None
        self._want_cut = False
        self._want_size: tuple[int, int] | None = None
        self._want_restart = False
        self._want_time: float | None = None
        self._delta: float | None = None  # the next step's delta_time (None: the default)
        self._picks: list[tuple[float, float, threading.Event, list]] = []
        self._calls: list[tuple[Callable[[], dict], threading.Event, list]] = []
        self.view_mode = "lit"
        self._want_select: list[str] | None = None
        self.selected: list[str] = []
        self.outline_color = OUTLINE_COLOR
        self._budget = 0  # steps left before idling
        self._inflight: deque[tuple[Any, float]] = deque()
        self._first_frame = False  # the loaded stage's first frame is still to come
        self.control = ControlServer(self.handle, on_eof=self.stop, stdin=stdio[0], stdout=stdio[1])
        self.frames = FrameServer(
            args.name or f"specto-usd-{os.getpid()}",
            self.width,
            self.height,
            on_ring=lambda name: self.control.emit("ring", name=name),
        )

    # -- control (the control thread) ----------------------------------------------------
    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def handle(self, method: str, params: dict) -> dict:
        if method in STAGE_QUERIES:
            with self._info_lock:
                info = self.info
            if info is None:
                raise ValueError("no stage loaded")
            if method == "query_tree":
                return info.query_tree(params)
            if method == "reveal":
                return info.reveal(params)
            if method == "properties":
                return info.properties(str(params.get("path") or ""))
            if method == "view_modes":
                return self._view_modes(info)
            # the prim's subtree bounds at the time the picture shows (#39)
            return info.bounds_of(str(params.get("path") or ""), self.timecode)
        if method == "pick":
            return self._ask_pick(float(params["x"]), float(params["y"]))
        if method == "set_view_mode":
            mode = str(params.get("mode") or "")
            return self._ask(lambda: self._set_view_mode(mode), MODE_TIMEOUT_S)
        with self._cond:
            result = self._handle(method, params)
            self._cond.notify()
        return result

    def _ask_pick(self, x: float, y: float) -> dict:
        """Wait for the render thread to pick at NDC ``(x, y)`` (it owns the renderer)."""
        done, box = threading.Event(), []
        with self._cond:
            self._picks.append((x, y, done, box))
            self._cond.notify()
        if not done.wait(PICK_TIMEOUT_S):
            raise TimeoutError(f"no pick in {PICK_TIMEOUT_S} s")
        if isinstance(box[0], Exception):
            raise box[0]
        return box[0]

    def _ask(self, fn: Callable[[], dict], timeout: float) -> dict:
        """Run ``fn`` on the render thread (it owns the renderer) and answer its result."""
        done, box = threading.Event(), []
        with self._cond:
            self._calls.append((fn, done, box))
            self._cond.notify()
        if not done.wait(timeout):
            raise TimeoutError(f"no answer in {timeout} s")
        if isinstance(box[0], Exception):
            raise box[0]
        return box[0]

    def _view_modes(self, info: Any) -> dict:
        """The loaded stage's view modes: which this renderer can show, and why not."""
        modes = []
        for mode in VIEW_MODES:
            reason = ""
            if not hasattr(self.renderer, "set_view_mode") and mode != "lit":
                reason = f"the {self.renderer_name} renderer has no view modes"
            elif mode == "collision" and not info.collider_count():
                reason = NO_COLLIDERS
            modes.append({"mode": mode, "available": not reason, "reason": reason})
        return {"mode": self.view_mode, "modes": modes}

    def _handle(self, method: str, params: dict) -> dict:
        if method == "load":
            self._want_load = str(params["path"])
            return {}
        if method == "set_camera":
            self._want_camera = _camera_state(params)
            self._want_cut = self._want_cut or bool(params.get("cut"))
            return {}
        if method == "resize":
            size = capped(params["width"], params["height"], self.max_pixels)
            self._want_size = size
            return {"width": size[0], "height": size[1]}
        if method == "set_time":
            self.time = float(params.get("t", 0.0))
            if "timecode" in params:
                self._want_time = float(params["timecode"])
            return {}
        if method in ("pause", "resume"):
            self.paused = method == "pause"
            if not self.paused:
                self._budget = self.settle_steps
            return {}
        if method == "restart":
            self._want_restart = True
            return {}
        if method == "select":
            self.selected = [str(p) for p in params.get("paths") or []]
            self.outline_color = str(params.get("color") or self.outline_color)
            self._want_select = list(self.selected)
            return {}
        if method == "state":
            return {
                "loaded": self.info.path if self.info is not None else None,
                "renderer": getattr(self.renderer, "name", None),
                "paused": self.paused,
                "failed": self.failed,
                "width": self.width,
                "height": self.height,
                "steps": self.steps,
                "stepping": bool(self._budget or self._inflight),
                "time": self.time,
                "selected": self.selected,
                "timecode": self.timecode,
                "view_mode": self.view_mode,
            }
        if method == "shutdown":
            self._stop = True
            return {}
        raise ValueError(f"unknown method {method!r}")

    # -- the render thread ---------------------------------------------------------------
    def health(self, state: str, message: str = "", **extra: Any) -> None:
        self.control.emit("health", state=state, message=message, **extra)

    def run(self) -> int:
        self.control.start()
        self.control.emit(
            "ready",
            ring=self.frames.ring_name,
            wake=self.frames.wake,
            width=self.width,
            height=self.height,
            pid=os.getpid(),
        )
        try:
            self._start_renderer()
            while self._next():
                pass
        finally:
            self._close_renderer()
            self.frames.close()
        return 0

    def _start_renderer(self) -> None:
        cold = self.kind == "rtx" and shaders_cold()
        self.health(START_PHASE[self.kind], COLD_MESSAGE if cold else "")
        try:
            self.renderer = self.make_renderer()
        except ModuleNotFoundError as e:
            self.renderer = None
            hint = STORM_HINT if self.kind == "storm" else INSTALL_HINT
            self.health("error", f"{e.name} is not installed: {hint}")
        except Exception as e:
            self.renderer = None
            label = "Storm" if self.kind == "storm" else "RTX"
            self.health("error", f"the {label} renderer did not start: {e}", restartable=True)
            traceback.print_exc()

    def _close_renderer(self) -> None:
        self._inflight.clear()
        renderer, self.renderer = self.renderer, None
        if renderer is not None:
            try:
                renderer.close()
            except Exception:
                traceback.print_exc()

    def _wants_steps(self) -> bool:
        return (
            self.renderer is not None
            and self.info is not None
            and not self.failed
            and not self.paused
            and self._budget > 0
        )

    def _next(self) -> bool:
        """One turn of the loop: take the requests, step; False when the source stops."""
        with self._cond:
            while not (
                self._stop
                or self._want_load
                or self._want_restart
                or self._want_size
                or self._want_camera is not None
                or self._want_select is not None
                or self._want_time is not None
                or self._inflight
                or self._picks
                or self._calls
                or self._wants_steps()
            ):
                self._cond.wait()
            if self._stop:
                return False
            load, self._want_load = self._want_load, None
            restart, self._want_restart = self._want_restart, False
            size, self._want_size = self._want_size, None
            camera, self._want_camera = self._want_camera, None
            cut, self._want_cut = self._want_cut, False
            timecode, self._want_time = self._want_time, None
            picks, self._picks = self._picks, []
            calls, self._calls = self._calls, []
            select, self._want_select = self._want_select, None
            color = self.outline_color
        try:
            if restart:
                self._restart()
            if load:
                if self.fresh_renderer and self.info is not None and not restart:
                    self._close_renderer()  # no second stage in a renderer (#155)
                    self._start_renderer()
                self._load(load)
            if self.renderer is None or self.info is None or self.failed:
                if timecode is not None:
                    self.timecode = timecode  # a restart's reload goes there
                if size:
                    self.width, self.height = size  # the next load renders at it (#180)
                return True
            if size and size != (self.width, self.height):
                self.width, self.height = size
                self.renderer.resize(*size, self._usd_camera())
                self._budget = self.settle_steps
            if camera is not None:
                self.camera = camera
                self.renderer.set_camera(self._usd_camera(), cut=cut)
                self._budget = self.settle_steps
            if select is not None and hasattr(self.renderer, "outline"):
                self.renderer.outline(self.info.gprims_under(select), color)
                self._budget = self.settle_steps
            if timecode is not None:
                self._set_time(timecode)
            self._pick(picks)
            self._call(calls)
            self._step()
        except Exception as e:
            self._fail(e)
        finally:
            self._pick(picks)  # each one answered, an error when nothing could pick
            self._call(calls)
        return True

    def _call(self, calls: list[tuple[Callable[[], dict], threading.Event, list]]) -> None:
        while calls:
            fn, done, box = calls.pop(0)
            try:
                box.append(fn())
            except Exception as e:
                box.append(e)
            done.set()

    def _set_view_mode(self, mode: str) -> dict:
        """Switch the renderer to ``mode`` (#153); the answer is the mode in effect."""
        if mode not in VIEW_MODES:
            raise ValueError(f"unknown view mode {mode!r} (have: {', '.join(VIEW_MODES)})")
        if self.renderer is None or self.info is None or self.failed:
            raise ValueError("no stage is rendering")
        if not hasattr(self.renderer, "set_view_mode"):
            raise ValueError(f"the {self.renderer_name} renderer has no view modes")
        overlay = None
        if mode == "collision":
            overlay = self.info.collision(self.timecode)
            if not overlay.colliders:
                raise ValueError(NO_COLLIDERS)
            if not overlay.sources:
                raise ValueError(f"no collider can be drawn: {overlay.note}")
        note = self.renderer.set_view_mode(mode, overlay) or ""
        self.view_mode = mode
        self._budget = self.settle_steps
        notes = (overlay.note if overlay is not None else "", note)
        return {"mode": mode, "note": "; ".join(n for n in notes if n)}

    def _pick(self, picks: list[tuple[float, float, threading.Event, list]]) -> None:
        """Answer the picks taken this turn, against the camera of this turn."""
        while picks:
            x, y, done, box = picks.pop(0)
            try:
                if self.renderer is None or self.info is None or self.failed:
                    raise ValueError("no stage is rendering")
                if not hasattr(self.renderer, "pick"):
                    raise ValueError(f"the {self.renderer_name} renderer cannot pick")
                hit = self.renderer.pick(x, y)
                self._warn()
                if hit and self.view_mode == "collision":
                    collider = self.info.collision().source_of(hit["path"])
                    hit = {**hit, "path": collider or hit["path"]}
                box.append({"hit": True, **hit} if hit else {"hit": False})
            except Exception as e:
                box.append(e)
            done.set()

    def _usd_camera(self) -> dict[str, Any]:
        """The camera as the renderer takes it (rtx: :func:`.nav.usd_camera`)."""
        adapt = getattr(self.renderer, "camera_of", usd_camera)
        return adapt(self.camera, self.width / self.height)

    def _set_time(self, timecode: float) -> None:
        """Drive the renderer to ``timecode`` (#141). rtx has two clocks: USD time (the
        time-sampled attributes, in seconds) and the simulation clock, the next step's
        ``delta_time``. Playing (a step on of at most ``MAX_TIME_STEP_S``), the
        simulation advances with USD time; a jump or a step back resets the render history
        instead (the product's reset request: ``renderer.reset`` stalls a second a call)
        and steps as usual. Storm renders the next frame at that time and ignores the
        rest. A static stage has nothing to re-evaluate."""
        info = self.info
        if info is None or not info.animated:
            self.timecode = timecode
            return
        tcps = info.time_codes_per_second
        dt = None if self.timecode is None else (timecode - self.timecode) / tcps
        playing = dt is not None and 0 <= dt <= MAX_TIME_STEP_S
        self.renderer.set_time(timecode / tcps, cut=not playing)
        self._delta = dt if playing and dt > 0 else None
        self.timecode = timecode
        self._budget = self.settle_steps

    def _load(self, path: str) -> None:
        from .stage import open_stage

        self.health("composing", os.path.basename(path))
        self._drain()
        try:
            info = open_stage(path)
        except Exception as e:
            self.health("error", f"cannot open {path}: {e}")
            return
        # a reload of the same stage (a restart) keeps its time; a new one starts at default
        keep = self.timecode if self.info is not None and self.info.path == path else None
        self.timecode = None
        with self._info_lock:
            self.info = info
        self.camera = Nav(info.bounds, info.up_axis).state()
        notes = [getattr(self.renderer, "note", "")]
        stage_note = getattr(self.renderer, "stage_note", None)
        if stage_note is not None:
            notes.append(stage_note(info.lighting))  # rtx: a stage without lights (#148)
        note = "; ".join(n for n in notes if n)
        extra = {"renderer_note": note} if note else {}
        self.control.emit("loaded", **info.loaded_event(self.renderer_name), **extra)
        if self.renderer is None:
            return
        self.health("first frame")
        self.view_mode = "lit"  # a new stage starts lit
        self.renderer.load(
            info.path,
            info.up_axis,
            info.meters_per_unit,
            self._usd_camera(),
            self.width,
            self.height,
            (info.start_time_code, info.end_time_code, info.time_codes_per_second),
            lighting=info.lighting,
        )
        self.failed = False
        self._first_frame = True
        # the frame ``ok`` names must come: a few frames past the first, however settled
        self._budget = max(self.settle_steps, IN_FLIGHT + 1)
        if keep is not None:
            self._set_time(keep)

    @property
    def renderer_name(self) -> str:
        return getattr(self.renderer, "name", "") or self.kind

    def _restart(self) -> None:
        self._close_renderer()
        self.failed = False
        self._start_renderer()
        if self.info is not None and self._want_load is None:
            self._load(self.info.path)

    def _step(self) -> None:
        while len(self._inflight) < IN_FLIGHT and self._wants_steps():
            delta, self._delta = self._delta, None
            self._inflight.append((self.renderer.submit(delta), time.perf_counter()))
            self._budget -= 1
        self._warn()
        if self._inflight:
            self._finish(*self._inflight.popleft())

    def _warn(self) -> None:
        """The renderer's new warnings (an error it drew past), each sent once."""
        take = getattr(self.renderer, "take_warnings", None)
        for message in take() if take is not None else ():
            print(f"[WARN] {self.renderer_name}: {message}", file=sys.stderr, flush=True)
            self.control.emit("warning", message=message)

    def _drain(self) -> None:
        while self._inflight:
            self._finish(*self._inflight.popleft())

    def _publish(self, pixels, started: float) -> None:
        h, w = pixels.shape[:2]
        ms = (time.perf_counter() - started) * 1000
        with self.frames.frame(w, h, timestamp=time.time(), render_ms=ms) as dst:
            _fill(dst, pixels)

    def _finish(self, op: Any, started: float) -> None:
        self.renderer.finish(op, lambda pixels: self._publish(pixels, started))
        self.steps += 1
        if self._first_frame:
            # ``seq``: the stage's first complete frame, past those in flight behind this
            # one (drawn before the stage settled); the host's loading label lifts on it
            self._first_frame = False
            name = self.info.name if self.info is not None else ""
            self.health("ok", name, seq=self.frames.ring.seq + IN_FLIGHT)

    def _fail(self, e: Exception) -> None:
        traceback.print_exc()
        self._inflight.clear()
        self.failed = True
        self.health("error", f"the renderer failed: {e}", restartable=True)


def make_renderer(name: str, log_file: str, streaming: str = "async") -> Callable[[], Any]:
    if name == "rtx":

        def rtx() -> Any:
            from .rtx import RtxRenderer

            return RtxRenderer(log_file, streaming)

        return rtx
    if name == "storm":

        def storm() -> Any:
            from .storm import StormRenderer

            return StormRenderer()

        return storm
    raise SystemExit(f"[ERROR] unknown renderer {name!r} (have: {', '.join(RENDERERS)})")


def protocol_stdio():
    """The protocol's own handles on stdin and stdout; the standard handles then point
    elsewhere. stdout goes to stderr, so native code that prints (ovrtx, usd-core) cannot
    corrupt the control channel. stdin becomes NUL: on Windows a thread blocked reading
    a pipe holds every other call on that pipe, and each DLL whose C runtime starts up
    asks for the standard handles' type, so loading numpy, pxr or ovrtx would hang until
    the host's next message."""
    stdin = os.fdopen(os.dup(0), "rb", buffering=0)
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)
    os.close(nul)
    stdout = os.fdopen(os.dup(1), "wb", buffering=0)
    sys.stdout.flush()
    os.dup2(2, 1)
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 (for the process)
    sys.stdout = sys.stderr
    return stdin, stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Specto USD frame source")
    ap.add_argument("--renderer", default="rtx", choices=RENDERERS)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--max-pixels", type=int, default=MAX_PIXELS)
    ap.add_argument("--name", default="")
    ap.add_argument("--settle-steps", type=int, default=None, help="default: by renderer")
    ap.add_argument("--log-file", default="", help="the renderer's log (default: temp folder)")
    ap.add_argument("--openusd", default="", help="storm: the OpenUSD imaging build's folder")
    ap.add_argument("--texture-streaming", default="async", choices=("async", "sync", "off"))
    ap.add_argument("--fresh-renderer", action="store_true", help="a new renderer per stage")
    args = ap.parse_args(argv)
    if args.renderer == "storm":
        from .storm import use_build

        use_build(args.openusd or openusd_prefix())  # before anything imports pxr
    log_file = args.log_file or os.path.join(
        tempfile.gettempdir(), f"specto-usd-source-{os.getpid()}.log"
    )
    renderer = make_renderer(args.renderer, log_file, args.texture_streaming)
    source = UsdSource(args, renderer, protocol_stdio())
    code = source.run()
    if not args.log_file and not source.failed:
        with contextlib.suppress(OSError):
            os.remove(log_file)  # kept only when something went wrong
    return code


if __name__ == "__main__":
    sys.exit(main())
