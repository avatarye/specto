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
  frame (#166: past the frames in flight), which the source always draws. Once that frame
  is in the ring, ``load_timings {path, ...}`` says where the load's seconds went
  (SPEC-27, :class:`LoadTimings`): ``renderer_s`` (a fresh renderer's swap,
  ``--fresh-renderer``), ``open_s`` (usd-core opens the stage), ``populate_s`` (the
  renderer's stage filled), ``environment_s`` (the environment light set up),
  ``first_frame_s`` (from there to the frame ``ok`` names: geometry, materials,
  textures) and ``total_s``. The renderer
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
  with the reason (no prims with CollisionAPI, a renderer without view modes). A mode
  asked while another waits to be switched to takes its place, and both answer with
  its switch (SPEC-33); each switch is also said by a ``view_mode {mode, note}`` event.
  Collision first builds the stage's overlay on a thread of its own, once a stage, while
  frames go on and health says ``building collision`` (``N colliders``); a build that
  fails is said once as a ``warning`` and the next ask builds again, and one that takes
  longer than ``OVERLAY_TIMEOUT_S`` answers an error (the next ask builds again too).
  Every load starts lit: a mode asked before a ``load`` answers an error. ``view_modes`` answers
  ``{mode, modes: [{mode, available, reason}]}`` for the loaded stage; ``state`` reports
  ``view_mode``. In collision a pick names the collider, not the overlay prim that draws
  it;
- the environment light (SPEC-31, :mod:`.environment`): ``load {path, environment, sky,
  hdri, sky_controls, ground}`` takes the choice (``stage``, ``sky``, ``hdri``), the sky
  and HDRI names, the sky's controls (``{time_of_day, cloudiness, haze}``, each null for
  the sky's own value) and the sky's ground (the last ones asked when left out) and
  ``loaded`` carries ``environment``, what shows, ``environment_note`` when it is not
  what was asked (Storm cannot render the sky's MDL and shows the HDRI; a sky or an HDRI
  not fetched yet fall back the same way) and ``sky_controls``, the values in effect;
  ``set_environment {environment, sky, hdri, sky_controls, ground}`` switches without a
  reload and answers ``{environment, environment_note, renderer_note, sky_controls}``;
  ``set_sky_controls {time_of_day, cloudiness, haze}`` moves the sun, the clouds and the
  haze of the sky shown, live, answering ``{sky_controls}``; ``state`` reports
  ``environment``, ``sky``, ``hdri``, ``sky_controls`` and ``ground``. The assets are
  looked up at each load and switch, so a fetch made meanwhile is seen.

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
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_app.frames.control import ControlServer
from specto_app.frames.server import FrameServer
from specto_core.machine import INSTALL_HINT, STORM_HINT, openusd_prefix

from . import environment as env
from . import skies
from .load_timings import LoadTimings
from .nav import CameraState, Nav, usd_camera

#: a USD time step on of at most this many seconds plays (the simulation advances with
#: it); a longer one is a jump
MAX_TIME_STEP_S = 0.1
IN_FLIGHT = 2
#: steps after the last change, then idle: rtx's TAA and DLSS converge; Storm keeps no
#: history, a few cover its textures still loading
SETTLE_STEPS = {"rtx": 120, "storm": 8}
#: steps after a sky comes in (SPEC-31): rtx compiles its MDL off the step for some seconds
#: and draws black meanwhile, so the loop keeps stepping past the compile
SKY_SETTLE_STEPS = 600
MAX_PIXELS = 1920 * 1080
RENDERERS = ("rtx", "storm")
#: the health phase while the renderer starts
START_PHASE = {"rtx": "compiling shaders", "storm": "starting"}
OUTLINE_COLOR = "#2f6fab"  # the theme's accent; the host sends its own
#: read from the stage on the control thread, beside the render loop
STAGE_QUERIES = ("query_tree", "reveal", "properties", "bounds", "view_modes")
VIEW_MODES = ("lit", "unlit", "wireframe", "collision")
NO_COLLIDERS = "no prims with UsdPhysics CollisionAPI"
COLD_MESSAGE = "(first run, about 3 min)"
#: a collision switch waits at most this long for the stage's overlay (SPEC-33), then
#: answers an error; the host's own wait is longer (``usd_view.MODE_TIMEOUT_S``)
OVERLAY_TIMEOUT_S = 90.0


#: how long the process's end waits for a collision build to stop between colliders
OVERLAY_JOIN_S = 2.0


@dataclass
class OverlayBuild:
    """A stage's collision overlay building off the render thread. One runs at a time:
    a newer one stops the one before (``stop``, asked between colliders) and waits for
    its thread to end before it starts."""

    info: Any  # the stage.StageInfo it reads
    built: Future  # the CollisionOverlay
    started: float  # time.monotonic()
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


def settle(answers: list[Future], fn: Callable[[], Any]) -> Any:
    """Resolve every answer with ``fn()``'s result, or with what it raised; the result,
    None when it raised."""
    try:
        result = fn()
    except Exception as e:
        for answer in answers:
            answer.set_exception(e)
        return None
    for answer in answers:
        answer.set_result(result)
    return result


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
        # where the current load's time goes (seconds, SPEC-27): sent as ``load_timings``
        # once the frame its ``ok`` names is in the ring
        self.load_timings: LoadTimings = {}
        self._load_started = self._first_step_from = 0.0
        self._timings_at: int | None = None  # the ring seq that ends the load's timing
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
        self._picks: list[tuple[float, float, Future]] = []
        self._calls: list[tuple[Callable[[], dict], Future]] = []
        #: the view mode asked last and not yet switched to, with every answer waiting on it
        self._want_mode: str | None = None
        self._mode_answers: list[Future] = []
        #: the collision overlay built (or building) off the render thread, and whether
        #: health says so (a collision ask waits on it)
        self._overlay: OverlayBuild | None = None
        self._building_shown = False
        self.view_mode = "lit"
        self.environment = "stage"  # the environment asked for (SPEC-31), what shows, and why
        self.environment_shown = "stage"
        self.environment_note = ""
        self.sky = skies.DEFAULT_SKY  # the sky and HDRI named, and the sky's controls
        self.hdri = env.DEFAULT_HDRI
        self.sky_controls = env.SkyControls()
        self.ground = False  # the sky's preview floor
        self.assets = env.Assets(sky=None, hdri=None)
        self.authored: dict[str, Any] = {}  # what the sky shown authors (skies.authored)
        self._want_select: list[str] | None = None
        self.selected: list[str] = []
        self.outline_color = OUTLINE_COLOR
        self._budget = 0  # steps left before idling
        self._inflight: deque[tuple[Any, float]] = deque()
        self._first_frame = False  # the loaded stage's first frame is still to come
        #: the skies whose MDL this renderer has compiled (SPEC-31: the first step after a new
        #: sky comes in blocks on the compile for some seconds and draws black), and
        #: whether a step is compiling one now (health says so until it is out)
        self._compiled_skies: set[str] = set()
        self._compiling_sky = False
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

    def handle(self, method: str, params: dict) -> dict | Future:
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
            return self._ask_mode(str(params.get("mode") or ""))
        if method == "set_environment":
            self._take_choice(params)
            return self._ask(self._set_environment)
        if method == "set_sky_controls":
            self.sky_controls = env.SkyControls.of(params)
            return self._ask(self._set_sky_controls)
        with self._cond:
            result = self._handle(method, params)
            self._cond.notify()
        return result

    def _ask_pick(self, x: float, y: float) -> Future:
        """The render thread's pick at NDC ``(x, y)`` (it owns the renderer), answered
        when it comes; the control thread reads on meanwhile."""
        answer: Future = Future()
        with self._cond:
            self._picks.append((x, y, answer))
            self._cond.notify()
        return answer

    def _ask_mode(self, mode: str) -> Future:
        """Switch to view mode ``mode`` on the render thread. A mode asked while an
        earlier one waits gives way to it: both answer with the last one's switch."""
        if mode not in VIEW_MODES:
            raise ValueError(f"unknown view mode {mode!r} (have: {', '.join(VIEW_MODES)})")
        answer: Future = Future()
        with self._cond:
            self._want_mode = mode
            self._mode_answers.append(answer)
            self._cond.notify()
        return answer

    def _ask(self, fn: Callable[[], dict]) -> Future:
        """``fn`` run on the render thread (it owns the renderer), answered when it is
        done; the control thread reads on meanwhile (SPEC-33)."""
        answer: Future = Future()
        with self._cond:
            self._calls.append((fn, answer))
            self._cond.notify()
        return answer

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
            self._take_choice(params)
            # a view mode asked before the load was for the stage before: every load
            # starts lit
            answers, self._want_mode, self._mode_answers = self._mode_answers, None, []
            for answer in answers:
                answer.set_exception(RuntimeError("a new stage is loading"))
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
                "environment": self.environment_shown,
                "sky": self.sky,
                "hdri": self.hdri,
                "sky_controls": self._controls_in_effect(),
                "ground": self.ground,
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
            self._end_overlay()
            self._close_renderer()
            self.frames.close()
        return 0

    def _end_overlay(self) -> None:
        """The process ends: a collision build under way stops between colliders, so no
        thread is inside pxr or Qhull at exit."""
        job = self._overlay
        if job is not None and job.thread is not None:
            job.stop.set()
            job.thread.join(OVERLAY_JOIN_S)

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
        self._compiled_skies.clear()  # a fresh renderer compiles them again
        self._compiling_sky = False
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
                or self._mode_ready()
                or self._wants_steps()
            ):
                self._cond.wait(self._overlay_wait())
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
        modes: list[tuple[str, list[Future]]] = []
        try:
            if restart:
                self._restart()
            if load:
                started = self._begin_timing()  # the renderer swap counts in the load
                if self.fresh_renderer and self.info is not None and not restart:
                    self._close_renderer()  # no second stage in a renderer (#155)
                    self._start_renderer()
                    self.load_timings["renderer_s"] = time.perf_counter() - started
                self._load(load)
            modes = self._take_mode()  # against the stage loaded now
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
            self._switch(modes)
            self._step()
        except Exception as e:
            self._fail(e)
        finally:
            self._pick(picks)  # each one answered, an error when nothing could pick
            self._call(calls)
            self._switch(modes)
        return True

    # -- view modes and the collision overlay (SPEC-33) ------------------------------------
    def _rendering(self) -> bool:
        return self.renderer is not None and self.info is not None and not self.failed

    def _mode_ready(self) -> bool:
        """A view mode is asked and nothing it waits on is still building, or the build
        is overdue (under ``_cond``)."""
        if self._want_mode is None:
            return False
        if self._want_mode != "collision":
            return True
        job = self._overlay
        if job is None or job.info is not self.info or job.stop.is_set():
            return True  # to start one, or to answer why not
        return job.built.done() or self._overdue(job)

    def _overdue(self, job: OverlayBuild) -> bool:
        return time.monotonic() - job.started >= OVERLAY_TIMEOUT_S

    def _overlay_wait(self) -> float | None:
        """How long the loop may sleep (under ``_cond``): until the build a collision ask
        waits on is due; None, no bound."""
        job = self._overlay
        if self._want_mode != "collision" or job is None or job.built.done() or job.stop.is_set():
            return None
        return max(0.0, job.started + OVERLAY_TIMEOUT_S - time.monotonic())

    def _take_mode(self) -> list[tuple[str, list[Future]]]:
        """The view mode asked, with every answer waiting on it, when it can be switched
        to now. Collision waits for the stage's overlay, built off this thread (started
        here when there is none) and said as the ``building collision`` phase meanwhile;
        an overdue build is taken too, and the switch answers why."""
        with self._cond:
            mode = self._want_mode
            if mode is None:
                return []
            waiting = False
            if mode == "collision" and self._rendering():
                job = self._overlay
                if job is None or job.info is not self.info or job.stop.is_set():
                    job = self._start_overlay()
                waiting = not job.built.done() and not self._overdue(job)
            if not waiting:
                answers = self._mode_answers
                self._want_mode, self._mode_answers = None, []
        self._show_building(waiting)  # off the lock: it counts colliders and writes
        return [] if waiting else [(mode, answers)]

    def _start_overlay(self) -> OverlayBuild:
        """Build the loaded stage's collision overlay on a thread of its own (SPEC-33: a
        stage with many hulls takes a while, and the render loop goes on meanwhile). The
        stage keeps what it ends with (:meth:`.stage.StageInfo.collision`)."""
        info, timecode = self.info, self.timecode
        before = self._overlay
        if before is not None:
            before.stop.set()  # the newest wins: the one before stops between colliders
        job = OverlayBuild(info, Future(), time.monotonic())
        self._overlay = job

        def build() -> None:
            if before is not None and before.thread is not None:
                before.thread.join()  # one build at a time
            try:
                job.built.set_result(info.collision(timecode, cancelled=job.stop.is_set))
            except Exception as e:
                job.built.set_exception(e)
            with self._cond:
                self._cond.notify()  # the switch waiting on it goes ahead

        job.thread = threading.Thread(target=build, name="usd-collision", daemon=True)
        job.thread.start()
        return job

    def _show_building(self, on: bool) -> None:
        """The ``building collision`` phase while a collision ask waits on its overlay;
        ``ok`` once it does not (after the stage's first frame: before, that ``ok`` says
        it, and the phase follows it)."""
        if on == self._building_shown:
            return
        self._building_shown = on
        if on:
            n = self.info.collider_count()
            self.health("building collision", f"{n} collider{'s' if n != 1 else ''}")
        elif self.info is not None and not self._first_frame:
            self.health("ok", self.info.name)

    def _built_overlay(self) -> Any:
        """The loaded stage's collision overlay when it is built, else None."""
        job = self._overlay
        if job is None or job.info is not self.info or job.stop.is_set():
            return None
        if not job.built.done() or job.built.exception() is not None:
            return None
        return job.built.result()

    def _collision_overlay(self) -> Any:
        """The overlay a collision switch draws: built off this thread, never here. A
        build that failed or is overdue is dropped (a later ask builds again) and the
        switch answers why; a failure is said once as a ``warning`` too."""
        job = self._overlay
        if job is None or job.info is not self.info or job.stop.is_set():
            raise RuntimeError("the collision overlay is not built")
        if not job.built.done():
            job.stop.set()  # the next ask starts again, after this one stops
            raise TimeoutError(f"the collision overlay took longer than {OVERLAY_TIMEOUT_S:g} s")
        error = job.built.exception()
        if error is not None:
            job.stop.set()  # a later ask builds again
            message = f"the collision overlay failed: {error}"
            print(f"[WARN] {message}", file=sys.stderr, flush=True)
            self.control.emit("warning", message=message)
            raise error
        return job.built.result()

    def _switch(self, modes: list[tuple[str, list[Future]]]) -> None:
        """Switch to the view mode taken this turn; every answer waiting on it gets it,
        and the ``view_mode`` event says the mode the picture shows."""
        while modes:
            mode, answers = modes.pop(0)
            result = settle(answers, lambda mode=mode: self._set_view_mode(mode))
            if result is not None:
                self.control.emit("view_mode", **result)

    def _call(self, calls: list[tuple[Callable[[], dict], Future]]) -> None:
        while calls:
            fn, answer = calls.pop(0)
            settle([answer], fn)

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
            overlay = self._collision_overlay()
            if not overlay.colliders:
                raise ValueError(NO_COLLIDERS)
            if not overlay.sources:
                raise ValueError(f"no collider can be drawn: {overlay.note}")
        note = self.renderer.set_view_mode(mode, overlay) or ""
        self.view_mode = mode
        self._budget = self.settle_steps
        notes = (overlay.note if overlay is not None else "", note)
        return {"mode": mode, "note": "; ".join(n for n in notes if n)}

    # -- the environment light (SPEC-31) -----------------------------------------------------
    def _take_choice(self, params: dict) -> None:
        """The environment, sky, HDRI and sky controls a ``load`` or ``set_environment``
        names (those left out keep the last asked)."""
        if "environment" in params:
            self.environment = env.environment_of(params["environment"])
        if "sky" in params:
            self.sky = env.sky_of(params["sky"])
        if "hdri" in params:
            self.hdri = env.hdri_of(params["hdri"])
        if isinstance(params.get("sky_controls"), dict):
            self.sky_controls = env.SkyControls.of(params["sky_controls"])
        if "ground" in params:
            self.ground = bool(params["ground"])

    @property
    def _textured(self) -> bool:
        return skies.SKIES[self.sky].textured

    def _controls_in_effect(self) -> dict[str, float]:
        return env.effective(self.sky_controls, self.authored, self._textured)

    def _show_environment(self, shown: str, up_axis: str) -> None:
        """Hand the renderer what shows, with the sky's controls and ground."""
        self.renderer.set_environment(
            shown,
            up_axis,
            self.assets,
            self.sky_controls,
            self.authored,
            textured=self._textured,
            ground=self.ground,
        )

    def _resolve_environment(self) -> tuple[str, str]:
        """What shows for the environment asked, with this renderer and the assets on this
        machine (looked up now), and the note saying why when it differs."""
        self.assets = env.find_assets(self.sky, self.hdri)
        self.authored = skies.authored(self.sky) if self.assets.sky else {}
        renders_mdl = bool(getattr(self.renderer, "renders_mdl", False))
        return env.resolve(
            self.environment, self.assets, renders_mdl=renders_mdl, sky=self.sky, hdri=self.hdri
        )

    def _environment_answer(self) -> dict:
        return {
            "environment": self.environment_shown,
            "environment_note": self.environment_note,
            "renderer_note": self._renderer_note(),
            "sky_controls": self._controls_in_effect(),
        }

    def _set_sky_controls(self) -> dict:
        """The sky's controls, live on the render thread; the answer is what is in effect."""
        if self.renderer is None or self.info is None or self.failed:
            raise ValueError("no stage is rendering")
        if self.environment_shown == "sky" and hasattr(self.renderer, "set_sky_controls"):
            self.renderer.set_sky_controls(
                env.shader_inputs(self.sky_controls, self.authored, self._textured),
                env.sun_matrix(env.sun_for(self.sky_controls, self.authored), self.info.up_axis),
            )
            self._budget = self.settle_steps
        return {"sky_controls": self._controls_in_effect()}

    def _renderer_note(self) -> str:
        """The readout's note: what the renderer cannot show, and how the stage is lit."""
        notes = [getattr(self.renderer, "note", "")]
        stage_note = getattr(self.renderer, "stage_note", None)
        if stage_note is not None and self.info is not None:
            notes.append(stage_note(self.info.lighting, self.environment_shown))
        return "; ".join(n for n in notes if n)

    def _set_environment(self) -> dict:
        """Switch to the environment asked, on the render thread; the answer is what
        shows. Another sky or HDRI of the same kind is another reference: the renderer
        tells by the layer it would add."""
        if self.renderer is None or self.info is None or self.failed:
            raise ValueError("no stage is rendering")
        shown, note = self._resolve_environment()
        if hasattr(self.renderer, "set_environment"):
            self._show_environment(shown, self.info.up_axis)
            self._budget = self._environment_budget(shown)
            self._sky_compiling(shown)
        self.environment_shown, self.environment_note = shown, note
        return self._environment_answer()

    def _sky_compiling(self, shown: str) -> None:
        """A sky this renderer has not drawn yet: its MDL compiles in the next step, which
        blocks for some seconds and comes out black; health says so meanwhile."""
        if shown != "sky" or not self.assets.sky or self.assets.sky in self._compiled_skies:
            return
        self._compiled_skies.add(self.assets.sky)
        self._compiling_sky = True
        self.health("compiling sky", skies.label(self.sky))

    def _environment_budget(self, shown: str) -> int:
        """The steps after an environment switch: a sky's MDL compiles for some seconds."""
        return max(self.settle_steps, SKY_SETTLE_STEPS) if shown == "sky" else self.settle_steps

    def _pick(self, picks: list[tuple[float, float, Future]]) -> None:
        """Answer the picks taken this turn, against the camera of this turn."""
        while picks:
            x, y, answer = picks.pop(0)
            settle([answer], lambda x=x, y=y: self._pick_at(x, y))

    def _pick_at(self, x: float, y: float) -> dict:
        if not self._rendering():
            raise ValueError("no stage is rendering")
        if not hasattr(self.renderer, "pick"):
            raise ValueError(f"the {self.renderer_name} renderer cannot pick")
        hit = self.renderer.pick(x, y)
        self._warn()
        overlay = self._built_overlay()
        if hit and self.view_mode == "collision" and overlay is not None:
            collider = overlay.source_of(hit["path"])
            hit = {**hit, "path": collider or hit["path"]}
        return {"hit": True, **hit} if hit else {"hit": False}

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
        """Open ``path`` and hand it to the renderer; its first frame ends the load, and
        ``load_timings`` reports where the time went (SPEC-27). The caller began the
        load's timing (``_begin_timing``), so a fresh renderer's swap counts in it."""
        from .stage import open_stage

        self.health("composing", os.path.basename(path))
        self._building_shown = False  # the load's own phases say what goes on now
        self._drain()
        t = time.perf_counter()
        try:
            info = open_stage(path)
        except Exception as e:
            self.health("error", f"cannot open {path}: {e}")
            return
        # a reload of the same stage (a restart) keeps its time; a new one starts at default
        keep = self.timecode if self.info is not None and self.info.path == path else None
        self.timecode = None
        if self._overlay is not None:
            self._overlay.stop.set()  # no one needs the old stage's hulls
        self.load_timings["open_s"] = time.perf_counter() - t
        with self._info_lock:
            self.info = info
        self.camera = Nav(info.bounds, info.up_axis).state()
        # the environment asked for, as this renderer and machine can show it (SPEC-31)
        self.environment_shown, self.environment_note = self._resolve_environment()
        note = self._renderer_note()  # Storm's MDL; rtx: a stage without lights (#148)
        extra = {"renderer_note": note} if note else {}
        self.control.emit(
            "loaded",
            **info.loaded_event(self.renderer_name),
            environment=self.environment_shown,
            environment_note=self.environment_note,
            sky_controls=self._controls_in_effect(),
            **extra,
        )
        if self.renderer is None:
            return
        self.health("first frame")
        self.view_mode = "lit"  # a new stage starts lit
        t = time.perf_counter()
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
        self.load_timings["populate_s"] = time.perf_counter() - t
        t = time.perf_counter()
        if self.environment_shown != "stage" and hasattr(self.renderer, "set_environment"):
            self._show_environment(self.environment_shown, info.up_axis)
            self._sky_compiling(self.environment_shown)  # under the loading label
        self.load_timings["environment_s"] = time.perf_counter() - t
        self._first_step_from = time.perf_counter()
        self.failed = False
        self._first_frame = True
        # the frame ``ok`` names must come: a few frames past the first, however settled
        self._budget = max(self._environment_budget(self.environment_shown), IN_FLIGHT + 1)
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
            self._begin_timing()  # the restart's own load; the renderer start is not in it
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
        if self._timings_at is not None and self.frames.ring.seq >= self._timings_at:
            self._timings_at = None
            now = time.perf_counter()
            self.load_timings["first_frame_s"] = now - self._first_step_from
            self.load_timings["total_s"] = now - self._load_started
            timings = {k: round(v, 3) for k, v in self.load_timings.items()}
            self.control.emit("load_timings", path=self.info.path if self.info else "", **timings)

    def _begin_timing(self) -> float:
        """A load starts: its timing starts over (the one place it does). Returns now."""
        self.load_timings = {}
        self._timings_at = None
        self._load_started = time.perf_counter()
        return self._load_started

    def _finish(self, op: Any, started: float) -> None:
        self.renderer.finish(op, lambda pixels: self._publish(pixels, started))
        self.steps += 1
        if self._first_frame:
            # ``seq``: the stage's first complete frame, past those in flight behind this
            # one (drawn before the stage settled); the host's loading label lifts on it
            self._first_frame = False
            self._compiling_sky = False
            name = self.info.name if self.info is not None else ""
            seq = self.frames.ring.seq + IN_FLIGHT
            self._timings_at = seq  # the load's timing ends with the frame ``ok`` names
            self.health("ok", name, seq=seq)
            if self._building_shown:  # a collision ask still waits on its overlay
                self._building_shown = False
                self._show_building(True)
        elif self._compiling_sky:
            self._compiling_sky = False  # the step that compiled the sky is out
            self.health("ok", self.info.name if self.info is not None else "")

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
