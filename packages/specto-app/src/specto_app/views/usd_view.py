"""The ``usd`` view host (#139; design ``usd.md`` rev 2): one OpenUSD stage, read-only,
rendered out of process by a frame source and shown by the native ``FrameItem``.

One :class:`UsdHost` per live usd view, and one frame-source process per host
(``frames.control.FrameSourceProcess``; a crash or a GPU hang freezes only this view).
The source is ``specto-usd-source`` with RTX (#137) or Storm (#143), or the stub USD
source (``frames.stub_usd_source``: the test pattern and a fake stage) on a machine with
neither; :func:`source_command` is the one place that chooses the process.
The host:

- receives its collection on ``data`` and shows **one stage at a time**, the last
  picked: the stage a pick added to the collection, else the one shown, else the item
  a ``selection`` received names (``<item key>#<prim path>`` or a bare item key), else
  ``params.selected``, else the first; the footer's previous / next and PageUp /
  PageDown step through the rest (``hosts.stepping``), loading each into the same
  source. A collection without stages closes the source, which frees its VRAM;
- walks **health** through the start: ``loading`` "starting renderer" from the spawn,
  then the source's ``health`` events (``composing``, ``compiling shaders``, ``first
  frame``), ``ok`` with the stage's name; a source that exits on its own is ``error``
  and a **failure** (#155): the exit code in plain words (:func:`exit_reason`), the
  GPU's own words when it lost the device, the renderer's last error lines from its
  stderr, and Retry / Open with Storm over the picture;
- feeds the **PRIMS** outliner (read-only, right panel) from ``query_tree``: the root
  prims and the default prim's children on load, more on expand, a page of
  ``TREE_PAGE`` at a time ("n more" row), and the source's own search on ``filter``;
- drives the camera through :class:`CameraControl`, the seam #138's navigation model
  fills: the viewport's buttons, drags, wheel and keys (Unreal Editor's scheme, #151:
  ``usd.nav``'s table) and the **VIEW** panel's speed, World origin, camera mode and
  Frame selected go in, each camera change comes out as one ``set_camera`` to the
  source. The free camera's Alt+LMB orbit pivots on the selection's picked point, else
  its centre (``bounds``), else the point under the press (``pick``, not selecting);
  **orbit mode** (#39, ``params.camera_mode``: ``free`` or ``orbit``, one user submit
  per change from the panel's control or the agent's param; the camera stays put) turns
  around the selection's centre, kept on the camera as its anchor while the selection
  lasts, else the point under the press, else the focus point. F and the panel's Frame
  selected frame the selection's bounds, the union over each selected prim's whole
  subtree (the source's ``bounds`` answers a prim's subtree: an Xform with no geometry
  of its own frames its children; a picked point without any: a small cube), else the
  whole stage; the speed the wheel sets while RMB is held is one user submit when RMB
  comes up. The status bar's gesture line follows the mode (``nav_keys``);
- keeps the side panels' ``{collapsed, width}`` in ``params.panels`` (``view``,
  ``prims``) and the speed in ``params.camera_speed``, each change one user submit;
- **pauses** the source when the view is culled (off-screen, collapsed) and **kills** it
  after ``IDLE_KILL_S`` paused, so an unseen view holds no VRAM; on resume a live source
  resumes and a killed one is relaunched and reloads the stage;
- **selects prims** (#140): a click in the viewport asks the source to ``pick`` (Ctrl
  adds or takes out), a click in the outliner selects its row, a double-click frames
  the prim's bounds; a ``selection`` received names prims as ``<item key>#<prim
  path>``. A selection is outlined by the source (``select``, the theme's accent),
  revealed in the outliner (``reveal``: the rows down to it open, the view scrolls to
  it), the last prim's properties (``properties``) go out on ``content`` as one JSON
  entry, and a pick made in the view goes out on ``selection`` (a selection received is
  shown, not republished). Showing another stage clears it;
- shows the stage in a **view mode** (#153): ``params.view_mode`` (``lit``, ``unlit``,
  ``wireframe``, ``collision``; one user submit per change from the footer's mode pill,
  or the agent's param) goes to the source as ``set_view_mode`` once a stage is loaded
  (each load starts lit), when the source's ``view_modes`` offers it (collision only
  with prims with CollisionAPI): else the view stays lit. The pill's menu lists the
  modes on offer; the mode is kept across stages; what shows is the source's answer or
  its ``view_mode`` event, so the pill follows the picture even after the host stopped
  waiting (SPEC-33). While the source switches, the pill names the mode on its way
  (``Collision…``) and health says ``building the collision overlay`` while it builds.
  ``snap`` reports ``view_mode`` (shown), ``view_modes`` (on offer) and
  ``view_mode_note``, the names the 3d view uses;
- lights the stage with an **environment light** (SPEC-31, ``usd.environment``):
  ``params.environment`` (``stage``, ``sky``, ``hdri``; one user submit per change from
  the VIEW panel's control, or the agent's param) goes to the source with each ``load``
  and as ``set_environment`` on a change, so the choice is kept across stages. What
  shows is the source's answer: the sky needs MDL (RTX) and the fetched sky, the HDRI
  the web build, else each falls back a step with a note the panel shows under the
  control. ``snap`` reports ``environment`` (shown) and ``environment_note``.

QML: ``Specto/Usd/UsdView.qml`` (the viewport ``FrameItem``, the VIEW and PRIMS side
panels, ``ViewFooter``), talking to the host through :class:`UsdBridge`.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import signal
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQuick import QQuickItem

from specto_app.frames.control import FrameSourceProcess, SourceError
from specto_app.hosts.base import HostContext, QuickViewHost
from specto_app.hosts.stepping import Stepping
from specto_core.contents import ContentEntry, ContentValue
from specto_core.errors import SpectoError
from specto_core.kinds import Item, ViewManifest
from specto_core.links import CameraValue, Collection, Selection, TimeValue
from specto_core.machine import disabled, faked, openusd_prefix, usd_candidates, usd_renderer
from specto_core.timebase import OWN_TIMELINE

from ..theme import COLORS
from ..timeline.controller import TimelineController
from ..usd import skies
from ..usd.environment import (
    DEFAULT_HDRI,
    ENVIRONMENTS,
    HDRIS,
    SkyControls,
    controls_of,
    environment_of,
    hdri_of,
    sky_of,
)
from ..usd.environment import LABELS as ENVIRONMENT_LABELS
from ..usd.timecodes import USD_TIMELINE, StageTime
from . import gallery_native
from .scene3d_view import initial_camera

USD_QML = Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Usd" / "UsdView.qml"
DATA, SELECTION, CONTENT, TIME, CAMERA = "data", "selection", "content", "time", "camera"
#: the user's navigation goes out on ``camera`` at most this often (the 3d page's rate)
CAMERA_EMIT_MS = 120
USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
IDLE_KILL_S = 60.0  # paused this long, the source is killed (design: Quotas and GPU memory)
POLL_MS = 30  # the source's events are taken on the GUI thread this often
TREE_PAGE = 100  # outliner children per query
PLAY_TICK_MS = 10  # play looks at the clock this often; it moves a whole timecode at a time
#: Qt's mouse buttons as the camera names them
BUTTONS = {1: "left", 2: "right", 4: "middle"}
DEFAULT_SIZE = (640, 360)  # frame size before the viewport reports its own
MAX_PIXELS = 1920 * 1080  # params.max_pixels by default (the manifest's)
SPEED_MIN, SPEED_MAX = -3.0, 3.0  # the VIEW panel's slider: log2 of the speed, 1/8x .. 8x
#: ``params.camera_mode`` (#39): Unreal Editor's free camera, or one turning around a pivot
CAMERA_MODES = ("free", "orbit")
#: the status bar's gesture line in orbit mode (free: the canvas controller's NAV_KEYS,
#: the 3d view's line); the 3d view's style
ORBIT_KEYS = (
    "drag orbits the selection or the point under it, middle-drag pans, "
    "right-drag and wheel dolly toward it, F frames"
)
CONTROLS_SEND_MS = 60  # a sky slider's live changes go to the source at most this often
PANELS = ("view", "prims")
PANEL_WIDTH = {"view": 200, "prims": 240}
PANEL_MIN, PANEL_MAX = 140, 480
#: the source's start phases as the view's health message
PHASES = {
    "starting": "starting renderer",
    "composing": "composing",
    "compiling shaders": "compiling RTX shaders",  # the source adds "(first run ...)" when cold
    "first frame": "first frame",
    "compiling sky": "compiling the sky's material",  # SPEC-31: the picture is black meanwhile
    # SPEC-33: a collision switch waits on the stage's overlay; the picture goes on as it was
    "building collision": "building the collision overlay",
}
#: the loading label's phase by the source's health state (#156)
COVER_PHASES = {
    "starting": "starting renderer",
    "compiling shaders": "compiling shaders",
    "composing": "opening stage",
    "first frame": "rendering the first frame",
    "building collision": "building collision overlay",
}
#: the label's last phase: the first frame is out, those in flight behind it still come
STREAMING = "streaming textures"


def cover_words(state: str, message: str) -> str:
    """A start phase in the loading label's words: shader compiling says cold (the
    source's message) or cached; the stage's name is the label's own line."""
    words = COVER_PHASES.get(state, state)
    if state == "compiling shaders":
        return f"{words} {message or '(cached)'}"
    return words


#: the footer's renderer badge by the source's renderer name
RENDERER_LABELS = {"rtx": "RTX", "storm": "Storm", "stub": "Stub"}
#: the view modes (#153), as the source and the 3d view name them, with the chip's words
VIEW_MODES = {"lit": "Lit", "unlit": "Unlit", "wireframe": "Wireframe", "collision": "Collision"}
#: a first collision view builds the overlay (the source answers within its 90 s bound),
#: a sky compiles its MDL
MODE_TIMEOUT_S = 130.0
#: Windows exit codes (NTSTATUS) of a process that crashed, in words
CRASH_CODES = {
    0xC0000005: "access violation",
    0xC0000017: "out of memory",
    0xC000001D: "illegal instruction",
    0xC00000FD: "stack overflow",
    0xC0000374: "heap corruption",
    0xC0000409: "fail-fast abort",
    0xE06D7363: "unhandled C++ exception",
}
#: POSIX signals that mean a crash (a child's negative return code), in words
CRASH_SIGNALS = {
    getattr(signal, name): words
    for name, words in (
        ("SIGSEGV", "segmentation fault"),
        ("SIGBUS", "bus error"),  # not on Windows
        ("SIGILL", "illegal instruction"),
        ("SIGFPE", "floating-point exception"),
        ("SIGABRT", "abort"),
    )
    if hasattr(signal, name)
}
SIGNAL_MAX = 128  # above any POSIX signal number
#: a renderer's stderr line worth showing when it died
ERROR_LINE = re.compile(r"error|fatal|exception|traceback|failed|device lost", re.IGNORECASE)
ERROR_LINES = 3  # the last this many shown
GPU_LOST = re.compile(r"DEVICE_LOST|device lost", re.IGNORECASE)
GPU_LOST_WORDS = "the GPU device was lost: out of video memory, or a driver reset"


def exit_reason(code: int | None) -> str:
    """A source's exit code in plain words: a crash (a Windows NTSTATUS, signed or not,
    or a POSIX crash signal) by name, abort's 3, a clean stop, any other code as it is."""
    if code is None:
        return "the renderer exited"
    if -SIGNAL_MAX <= code < 0:  # POSIX: killed by a signal (a signed NTSTATUS is far lower)
        if -code in CRASH_SIGNALS:
            sig = signal.Signals(-code)
            return f"the renderer crashed ({CRASH_SIGNALS[sig]}, {sig.name})"
        return f"the renderer was killed (signal {-code})"
    status = code & 0xFFFFFFFF
    if status in CRASH_CODES:
        return f"the renderer crashed ({CRASH_CODES[status]}, 0x{status:08X})"
    if status >= 0xC0000000:
        return f"the renderer crashed (0x{status:08X})"
    if code == 0:
        return "the renderer stopped"
    if code == 3:
        return "the renderer aborted (code 3)"
    return f"the renderer exited (code {code})"


def renderer_errors(lines: list[str]) -> list[str]:
    """The last ``ERROR_LINES`` error lines of a renderer's stderr, each cut to 240."""
    errors = [line.strip()[:240] for line in lines if ERROR_LINE.search(line)]
    return errors[-ERROR_LINES:]


USD_MANIFEST = ViewManifest(type="usd", label="USD", host="frame")  # core's is the real one

#: ``caps()``: this machine's capabilities (``engine.capabilities``), asked for only when
#: a choice needs them
Capabilities = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True)
class SourceChoice:
    """The frame-source process :func:`source_command` chose: its command line, and why
    it is the stub when no renderer is found here (a sentence; "" = it is not, or the
    stub was asked for)."""

    argv: list[str]
    fallback_reason: str = ""


#: ``(view entry, caps, width, height) -> SourceChoice``: see :func:`source_command`
SourceCommand = Callable[[dict[str, Any], Capabilities, int, int], SourceChoice]
#: ``on_camera(set_camera params, cut)``: one coalesced camera change for the source;
#: ``cut`` is a jump (frame all, World origin, a new stage) that resets render history
OnCamera = Callable[[dict[str, Any], bool], None]


def stub_launcher(width: int, height: int) -> list[str]:
    """The stub USD source's command line (until #137's ``specto-usd-source``);
    ``SPECTO_USD_STUB_PHASE_MS`` slows its start phases down for a scripted flow, and
    ``SPECTO_USD_STUB_CRASH_MS`` crashes the first one this process starts that long
    after its stage is up (#155)."""
    argv = [sys.executable, "-m", "specto_app.frames.stub_usd_source"]
    argv += ["--width", str(width), "--height", str(height)]
    phase = os.environ.get("SPECTO_USD_STUB_PHASE_MS")
    crash = os.environ.get("SPECTO_USD_STUB_CRASH_MS")
    if crash:
        marker = Path(tempfile.gettempdir()) / f"specto-usd-stub-crashed-{os.getpid()}"
        argv += ["--crash-after-ms", crash, "--crash-marker", str(marker)]
    return [*argv, "--phase-ms", phase] if phase else argv


def rtx_launcher(width: int, height: int) -> list[str]:
    """``specto-usd-source`` with the RTX renderer (#137), a fresh renderer for each stage
    after the first: both #155 crashes were GPU page faults about 3 s after a stage was
    attached to a running renderer. A fresh one costs 1-1.5 s more per switch, up to 4 s
    after a large stage (SPEC-27, tools/bench_usd_load.py), next to the 4-7 s the first
    frame of a large world takes either way."""
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx"]
    return [*argv, "--width", str(width), "--height", str(height), "--fresh-renderer"]


def storm_launcher(width: int, height: int) -> list[str]:
    """``specto-usd-source`` with Hydra Storm (#143) from the machine's OpenUSD build."""
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "storm"]
    argv += ["--openusd", openusd_prefix()]
    return [*argv, "--width", str(width), "--height", str(height)]


#: each renderer's machine capability and the launcher of its source process
RENDERERS: dict[str, tuple[str, Callable[[int, int], list[str]]]] = {
    "rtx": ("ovrtx", rtx_launcher),
    "storm": ("storm", storm_launcher),
}


def source_command(
    view: dict[str, Any], caps: Capabilities, width: int, height: int
) -> SourceChoice:
    """The frame-source process for ``view`` (its document entry), with a first frame of
    ``width x height``: the one place the renderer is chosen, and the reason when that is
    the stub. ``specto-usd-source`` with rtx (#137) when ``params.renderer`` asks for it,
    or is ``auto`` on Windows or Linux and ``caps()`` has ``ovrtx`` (``usd_renderer``);
    with Storm (#143) otherwise, where ``caps()`` has ``storm`` (the OpenUSD imaging
    build; on macOS ``auto`` is always Storm); the stub USD source where it has neither,
    its ``fallback_reason`` naming what each renderer tried lacks (SPEC-26). ``caps`` is
    not called for an explicit rtx (detecting ovrtx starts a child Python; it never falls
    back, its health says why it cannot start). A renderer whose capability is faked
    (``SPECTO_FAKE_CAPABILITIES``, scripted flows) is played by the stub, ``--renderer``
    naming the one it stands in for (its start phase; its panel still says Stub). Every
    source speaks the stub USD source's protocol."""
    params = view.get("params") or {}
    asked = params.get("renderer") or "auto"
    found: Mapping[str, Any] = {} if asked == "rtx" else caps()
    renderer = usd_renderer(params, found)
    if renderer == "storm" and not getattr(found.get("storm"), "present", False):
        # no Storm build here either
        return SourceChoice(stub_launcher(width, height), fallback_reason(params, found))
    capability, launcher = RENDERERS[renderer]
    if capability in faked():
        return SourceChoice([*stub_launcher(width, height), "--renderer", renderer])
    return SourceChoice(launcher(width, height))


def fallback_reason(params: Mapping[str, Any], found: Mapping[str, Any]) -> str:
    """Why the stub plays: what each renderer the view tried (``usd_candidates``) lacks,
    from the machine capability detection; "" when a test or flow asked for it (a
    capability disabled or faked)."""
    tried = usd_candidates(params)
    if (disabled() | faked()) & {RENDERERS[r][0] for r in tried}:
        return ""
    missing = [
        f"{RENDERER_LABELS[r]}: {getattr(found.get(RENDERERS[r][0]), 'reason', '') or 'not found'}"
        for r in tried
    ]
    return "no USD renderer, showing the test pattern; " + "; ".join(missing)


def usd_part(item: Item) -> str | None:
    """The item's stage file: its first part with a USD extension."""
    for path in item.parts.values():
        if str(path).lower().endswith(USD_EXTENSIONS):
            return str(path)
    return None


def speed_of(value: Any) -> float:
    """A camera speed from a param: clamped to 1/8 .. 8, anything else 1."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(2.0**SPEED_MIN, min(2.0**SPEED_MAX, v)) if v > 0 else 1.0


def mode_of(value: Any) -> str:
    """A camera mode from a param: ``free`` or ``orbit``, anything else free."""
    return str(value) if value in CAMERA_MODES else "free"


def frame_size(width: float, height: float, dpr: float, max_pixels: int) -> tuple[int, int]:
    """The source's frame for a viewport of ``width x height`` logical px: device pixels,
    scaled down to at most ``max_pixels``, never below 1."""
    w, h = max(1.0, width * dpr), max(1.0, height * dpr)
    if max_pixels > 0 and w * h > max_pixels:
        k = math.sqrt(max_pixels / (w * h))
        w, h = w * k, h * k
    return max(1, int(w)), max(1, int(h))


# -- the camera seam (#138) ---------------------------------------------------------------
class CameraControl(Protocol):
    """What the usd view's input drives: #138's navigation model (``usd.nav.Nav`` with its
    ``NavTicker``) behind a small adapter. Made per host by ``camera_factory(on_camera)``;
    every change, input or jump, reaches the host as one ``on_camera(params, cut)`` call
    (``params``: ``set_camera``'s ``{position, target, up, fov_y, near, far}`` in the
    stage's frame). Pixel deltas are logical px since the last call."""

    camera_speed: float
    mode: str  # ``free`` or ``orbit`` (#39); setting it keeps the camera where it is

    def load(self, bounds: list[list[float]] | None, up_axis: str) -> None:
        """A stage opened: its world bounds and ``upAxis``; go to the first look."""

    def press(self, button: str, alt: bool) -> None:
        """A mouse button (``left right middle``) went down over the picture."""

    def release(self, button: str) -> None:
        """A mouse button came up."""

    @property
    def pivoting(self) -> bool:
        """A gesture that pivots is on: the free camera's orbit (Alt+LMB), orbit mode's
        orbit (LMB) or its dolly toward the pivot (RMB)."""

    def set_pivot(self, point: list[float]) -> None:
        """The gesture's pivot, in the stage's frame (#151)."""

    def set_anchor(self, point: list[float] | None) -> None:
        """Orbit mode's standing pivot (#39): the selection's centre, in the stage's
        frame; None when nothing selected draws anything."""

    def drag(self, dx: float, dy: float) -> None:
        """The pointer moved with buttons held (``usd.nav``'s table says what it does)."""

    def wheel(self, angle_delta_y: float) -> None:
        """A wheel event (Qt's ``angleDelta().y()``, 120 a notch): a dolly, or with RMB
        held the camera speed (``camera_speed`` then changes)."""

    def set_key(self, key: str, down: bool) -> None:
        """A navigation key (``w a s d q e shift``) went down or up."""

    def release_keys(self) -> None:
        """The view lost the keyboard focus."""

    def world_origin(self) -> None:
        """The VIEW panel's World origin."""

    def frame_all(self) -> None:
        """F (or the footer's target) with nothing selected."""

    def frame(self, bounds: list[list[float]]) -> None:
        """Frame these world bounds (F on a selection, #151; an outliner double-click on
        a prim, #140)."""

    def frame_point(self, point: list[float]) -> None:
        """F on a selection with no bounds but a picked point (#151)."""

    def stop(self) -> None:
        """The view paused: stop coasting and ticking."""

    def apply_value(self, value: dict[str, Any]) -> None:
        """A ``camera`` port value (the 3d view's shape and frame, #176): go there."""

    def value(self) -> dict[str, Any]:
        """The camera as a ``camera`` port value (the 3d view's shape and frame)."""


class PlaceholderCamera:
    """The camera until #138: the first look and World origin go to the 3d view's origin
    look (``scene3d_view.initial_camera``); every other input is only recorded
    (``calls``), so the wiring can be tested."""

    def __init__(self, on_camera: OnCamera) -> None:
        self._on_camera = on_camera
        self.camera_speed = 1.0
        self.mode = "free"
        self.bounds: list[list[float]] | None = None
        self.pivoting = False
        self.calls: list[tuple[Any, ...]] = []

    def load(self, bounds: list[list[float]] | None, up_axis: str) -> None:
        self.calls.append(("load", bounds, up_axis))
        self.bounds = bounds
        self._origin()

    def press(self, button: str, alt: bool) -> None:
        self.calls.append(("press", button, alt))
        if self.mode == "orbit":
            self.pivoting = button in ("left", "right")
        else:
            self.pivoting = alt and button == "left"

    def release(self, button: str) -> None:
        self.calls.append(("release", button))
        self.pivoting = False

    def set_pivot(self, point: list[float]) -> None:
        self.calls.append(("pivot", point))

    def set_anchor(self, point: list[float] | None) -> None:
        self.calls.append(("anchor", point))

    def drag(self, dx: float, dy: float) -> None:
        self.calls.append(("drag", dx, dy))

    def wheel(self, angle_delta_y: float) -> None:
        self.calls.append(("wheel", angle_delta_y))

    def set_key(self, key: str, down: bool) -> None:
        self.calls.append(("key", key, down))

    def release_keys(self) -> None:
        self.calls.append(("release_keys",))

    def world_origin(self) -> None:
        self.calls.append(("world_origin",))
        self._origin()

    def frame_all(self) -> None:
        self.calls.append(("frame_all",))

    def frame(self, bounds: list[list[float]]) -> None:
        self.calls.append(("frame", bounds))

    def frame_point(self, point: list[float]) -> None:
        self.calls.append(("frame_point", point))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def apply_value(self, value: dict[str, Any]) -> None:
        self.calls.append(("apply", value))
        self._value = dict(value)
        self._on_camera(dict(value), True)

    def value(self) -> dict[str, Any]:
        return getattr(self, "_value", None) or initial_camera([])

    def _origin(self) -> None:
        self._on_camera(initial_camera([{"bounds": self.bounds}] if self.bounds else []), True)


CameraFactory = Callable[[OnCamera], CameraControl]


# -- QML ------------------------------------------------------------------------------------
class UsdBridge(QObject):
    """Handed to ``UsdView.qml`` as ``bridge``: what the panels and the footer show, and
    the user's intent back (viewport input, the VIEW panel, the outliner, the panels)."""

    stageChanged = Signal()
    rowsChanged = Signal()
    panelsChanged = Signal()
    speedChanged = Signal()
    cameraModeChanged = Signal()
    healthChanged = Signal()
    selectionChanged = Signal()
    viewModeChanged = Signal()
    environmentChanged = Signal()
    failureChanged = Signal()
    coverChanged = Signal()
    #: scroll the outliner to this row (a selection revealed in it)
    revealRequested = Signal(int)

    def __init__(self, host: UsdHost) -> None:
        super().__init__()
        self._host = host
        self._health = ("ok", "")
        host.healthChanged.connect(self._on_health)

    @Slot(str, str)
    def _on_health(self, status: str, message: str) -> None:
        self._health = (status, message)
        self.healthChanged.emit()

    # the start phase or the failure, over the picture
    @Property(str, notify=healthChanged)
    def healthStatus(self) -> str:
        return self._health[0]

    @Property(str, notify=healthChanged)
    def healthMessage(self) -> str:
        return self._health[1]

    # the loading label (#156): over the picture until the new stage's first complete frame
    @Property(bool, notify=coverChanged)
    def covered(self) -> bool:
        return self._host.cover is not None

    @Property(str, notify=coverChanged)
    def coverFile(self) -> str:
        return (self._host.cover or {}).get("file", "")

    @Property(str, notify=coverChanged)
    def coverPhase(self) -> str:
        return (self._host.cover or {}).get("phase", "")

    @Slot(int)
    def frameShown(self, seq: int) -> None:
        """The viewport's FrameItem shows frame ``seq`` of the source's ring."""
        self._host.frame_shown(int(seq))

    @Slot()
    def framesUnavailable(self) -> None:
        """The FrameItem cannot draw (no OpenGL): no frame will come to lift the label."""
        self._host.frames_unavailable()

    # the renderer died (#155): why, its last errors, and what the user can do
    @Property(bool, notify=failureChanged)
    def failed(self) -> bool:
        return self._host.failure is not None

    @Property(str, notify=failureChanged)
    def failureReason(self) -> str:
        return (self._host.failure or {}).get("reason", "")

    @Property(str, notify=failureChanged)
    def failureCause(self) -> str:
        return (self._host.failure or {}).get("cause", "")

    @Property("QVariantList", notify=failureChanged)
    def failureErrors(self) -> list[str]:
        return list((self._host.failure or {}).get("errors", []))

    @Property(str, notify=failureChanged)
    def failureLog(self) -> str:
        return (self._host.failure or {}).get("log", "")

    @Property(bool, notify=failureChanged)
    def canStorm(self) -> bool:
        return bool((self._host.failure or {}).get("storm"))

    @Slot()
    def retry(self) -> None:
        self._host.retry()

    @Slot()
    def openWithStorm(self) -> None:
        self._host.open_with_storm()

    # -- what QML shows ------------------------------------------------------------------
    @Property(str, notify=stageChanged)
    def stageName(self) -> str:
        return Path(self._host.path).name if self._host.path else ""

    @Property(str, notify=stageChanged)
    def renderer(self) -> str:
        return self._host.renderer_label

    # what the renderer cannot show of the stage (Storm: MDL materials), under the readout
    @Property(str, notify=stageChanged)
    def rendererNote(self) -> str:
        return str(self._host.stage.get("renderer_note", ""))

    @Property(str, notify=stageChanged)
    def upAxis(self) -> str:
        return self._host.stage.get("up_axis", "")

    @Property(int, notify=stageChanged)
    def primCount(self) -> int:
        return int(self._host.stage.get("prims", 0))

    @Property(bool, notify=stageChanged)
    def animated(self) -> bool:
        """The stage has time samples: the footer shows its transport (#141)."""
        return self._host.time is not None and self._host.time.animated

    @Property("QVariantList", notify=rowsChanged)
    def rows(self) -> list[dict[str, Any]]:
        return self._host.outliner_rows()

    @Property(str, notify=rowsChanged)
    def filter(self) -> str:
        return self._host.tree_filter

    @Property("QVariantMap", notify=panelsChanged)
    def panels(self) -> dict[str, Any]:
        return self._host.panels

    @Property(float, notify=speedChanged)
    def speed(self) -> float:
        return self._host.camera_speed

    @Property(float, notify=speedChanged)
    def speedLog2(self) -> float:
        return math.log2(self._host.camera_speed)

    # the camera mode (#39): free or orbit, the VIEW panel's control
    @Property(str, notify=cameraModeChanged)
    def cameraMode(self) -> str:
        return self._host.camera_mode

    @Property("QVariantList", notify=selectionChanged)
    def selected(self) -> list[str]:
        return list(self._host.selected)

    # the view mode (#153): the one showing, what it shows, and the menu's rows
    @Property(str, notify=viewModeChanged)
    def viewMode(self) -> str:
        return self._host.view_mode_shown

    @Property(str, notify=viewModeChanged)
    def viewModeLabel(self) -> str:
        """The mode shown, or the one asked with an ellipsis while the source switches
        (SPEC-33: collision builds its overlay first)."""
        host = self._host
        pending = host.view_mode_pending
        if pending is not None and pending != host.view_mode_shown:
            return VIEW_MODES.get(pending, pending) + "…"
        return VIEW_MODES.get(host.view_mode_shown, host.view_mode_shown)

    @Property(str, notify=viewModeChanged)
    def viewModeNote(self) -> str:
        return self._host.view_mode_note

    @Property("QVariantList", notify=viewModeChanged)
    def viewModes(self) -> list[dict[str, Any]]:
        return self._host.view_mode_rows()

    # the environment light (SPEC-31): the choice (the VIEW panel's control), what shows, why,
    # the sky and HDRI lists, the sky's controls and its fetch
    @Property(str, notify=environmentChanged)
    def environment(self) -> str:
        return self._host.environment

    @Property(str, notify=environmentChanged)
    def environmentShown(self) -> str:
        return self._host.environment_shown

    @Property(str, notify=environmentChanged)
    def environmentNote(self) -> str:
        return self._host.environment_words

    @Property("QVariantList", constant=True)
    def environments(self) -> list[dict[str, Any]]:
        return [{"environment": e, "label": ENVIRONMENT_LABELS[e]} for e in ENVIRONMENTS]

    @Property("QVariantList", constant=True)
    def skies(self) -> list[dict[str, Any]]:
        return [{"sky": name, "label": sky.label} for name, sky in skies.SKIES.items()]

    @Property("QVariantList", constant=True)
    def hdris(self) -> list[dict[str, Any]]:
        return [{"hdri": name, "label": label} for name, label in HDRIS.items()]

    @Property(str, notify=environmentChanged)
    def sky(self) -> str:
        return self._host.sky

    @Property(str, notify=environmentChanged)
    def skyLabel(self) -> str:
        return skies.label(self._host.sky)

    @Property(str, notify=environmentChanged)
    def hdri(self) -> str:
        return self._host.hdri

    @Property(str, notify=environmentChanged)
    def hdriLabel(self) -> str:
        return HDRIS.get(self._host.hdri, self._host.hdri)

    @Property(bool, notify=environmentChanged)
    def skyControlsOn(self) -> bool:
        """The sky shows, so its controls (those it takes) act; under Storm none do."""
        return bool(self._host.sky_controls_offered())

    @Property(bool, notify=environmentChanged)
    def timeOfDayOn(self) -> bool:
        return "time_of_day" in self._host.sky_controls_offered()

    @Property(bool, notify=environmentChanged)
    def cloudinessOn(self) -> bool:
        return "cloudiness" in self._host.sky_controls_offered()

    @Property(bool, notify=environmentChanged)
    def hazeOn(self) -> bool:
        return "haze" in self._host.sky_controls_offered()

    @Property(bool, notify=environmentChanged)
    def ground(self) -> bool:
        return self._host.ground

    @Property(bool, notify=environmentChanged)
    def hdriStandsIn(self) -> bool:
        """The HDRI shows for the sky (Storm, or the sky on its way): the panel offers
        the HDRI list beside the sky's."""
        return self._host.hdri_stands_in

    @Property(float, notify=environmentChanged)
    def timeOfDay(self) -> float:
        return float(self._host.sky_values()["time_of_day"])

    @Property(float, notify=environmentChanged)
    def cloudiness(self) -> float:
        return float(self._host.sky_values()["cloudiness"])

    @Property(float, notify=environmentChanged)
    def haze(self) -> float:
        return float(self._host.sky_values()["haze"])

    # -- intent --------------------------------------------------------------------------
    @Slot(str)
    def setViewMode(self, mode: str) -> None:
        self._host.set_view_mode(str(mode))

    @Slot(str)
    def setEnvironment(self, environment: str) -> None:
        """The VIEW panel's Stage / Sky / HDRI control (SPEC-31)."""
        self._host.set_environment(str(environment))

    @Slot(str)
    def setSky(self, name: str) -> None:
        self._host.set_sky(str(name))

    @Slot(str)
    def setHdri(self, name: str) -> None:
        self._host.set_hdri(str(name))

    @Slot(bool)
    def setGround(self, on: bool) -> None:
        self._host.set_ground(bool(on))

    @Slot(float, bool)
    def setTimeOfDay(self, value: float, final: bool) -> None:
        self._host.set_sky_control("time_of_day", float(value), final=bool(final))

    @Slot(float, bool)
    def setCloudiness(self, value: float, final: bool) -> None:
        self._host.set_sky_control("cloudiness", float(value), final=bool(final))

    @Slot(float, bool)
    def setHaze(self, value: float, final: bool) -> None:
        self._host.set_sky_control("haze", float(value), final=bool(final))

    @Slot(int)
    def step(self, delta: int) -> None:
        self._host.step_item(int(delta))

    @Slot(float, float, float)
    def viewportResized(self, width: float, height: float, dpr: float) -> None:
        self._host.viewport_resized(float(width), float(height), float(dpr))

    @Slot(int, bool, float, float)
    def press(self, button: int, alt: bool, x: float, y: float) -> None:
        """A mouse button (Qt's: 1 left, 2 right, 4 middle) went down over the picture
        at NDC ``(x, y)``; ``alt``: Alt was held (#151)."""
        name = BUTTONS.get(int(button))
        if name is not None:
            self._host.press(name, bool(alt), float(x), float(y))

    @Slot(int)
    def release(self, button: int) -> None:
        name = BUTTONS.get(int(button))
        if name is not None:
            self._host.release(name)

    @Slot(float, float)
    def drag(self, dx: float, dy: float) -> None:
        """The pointer moved ``dx, dy`` logical px with buttons held."""
        self._host.camera.drag(float(dx), float(dy))

    @Slot(float)
    def wheel(self, angle_delta_y: float) -> None:
        self._host.wheel(float(angle_delta_y))

    @Slot(str, bool)
    def key(self, key: str, down: bool) -> None:
        self._host.camera.set_key(str(key), bool(down))

    @Slot()
    def focusLost(self) -> None:
        self._host.camera.release_keys()

    @Slot()
    def frame(self) -> None:
        """F and the footer's target: the selection, else the whole stage (#151)."""
        self._host.frame_selection()

    @Slot()
    def worldOrigin(self) -> None:
        self._host.camera.world_origin()

    @Slot(float, bool)
    def setSpeedLog2(self, value: float, final: bool) -> None:
        self._host.set_camera_speed(2.0 ** float(value), final=bool(final))

    @Slot(str)
    def setCameraMode(self, mode: str) -> None:
        """The VIEW panel's Free / Orbit control (#39)."""
        self._host.set_camera_mode(str(mode))

    @Slot(str)
    def toggleRow(self, path: str) -> None:
        self._host.toggle_row(str(path))

    @Slot(str)
    def moreRows(self, path: str) -> None:
        self._host.more_rows(str(path))

    @Slot(str)
    def setFilter(self, text: str) -> None:
        self._host.set_tree_filter(str(text))

    @Slot(float, float, bool)
    def pick(self, x: float, y: float, add: bool) -> None:
        """A click on the picture at NDC ``(x, y)`` (-1..1, +y up); ``add``: Ctrl."""
        self._host.pick(float(x), float(y), add=bool(add))

    @Slot(str, bool)
    def selectRow(self, path: str, add: bool) -> None:
        self._host.select_row(str(path), add=bool(add))

    @Slot(str)
    def frameRow(self, path: str) -> None:
        self._host.frame_prim(str(path))

    @Slot(str, bool)
    def setPanelCollapsed(self, name: str, collapsed: bool) -> None:
        self._host.set_panel(str(name), collapsed=bool(collapsed))

    @Slot(str, float)
    def setPanelWidth(self, name: str, width: float) -> None:
        self._host.set_panel(str(name), width=float(width))


class UsdHost(Stepping, QuickViewHost):
    """One live usd view; see the module docstring."""

    #: (on_result, on_error, result, error), to the GUI thread
    _answered = Signal(object, object, object, object)
    #: the sky fetcher's worker thread reports a change (SPEC-31), to the GUI thread
    _fetch_changed = Signal()

    def __init__(
        self,
        ctx: HostContext,
        view_id: str,
        manifest: ViewManifest,
        *,
        headless: bool = False,
        source: SourceCommand = source_command,
        camera_factory: CameraFactory = PlaceholderCamera,
        idle_kill_s: float = IDLE_KILL_S,
        fetcher_factory: Callable[..., Any] = skies.AssetFetcher,
    ) -> None:
        super().__init__(ctx, view_id, manifest)
        self.source_command = source
        self.idle_kill_s = idle_kill_s
        self._fetcher_factory = fetcher_factory  # tests hand in one that fetches nothing
        self.collection: Collection | None = None
        self.items: list[Item] = []
        self.keys: list[str] = []
        self.index = -1
        self.path: str | None = None
        self.source: FrameSourceProcess | None = None
        self.ready: dict[str, Any] | None = None  # the source's ready event
        self.stage: dict[str, Any] = {}  # the source's loaded event for self.path
        self.killed = False  # the idle kill closed the source; resume relaunches it
        #: the source died on its own (#155): {reason, cause, errors, log, retry, storm}
        self.failure: dict[str, Any] | None = None
        self._renderer_kind = ""  # the renderer the source runs, from its command line
        # why the source is the stub (no renderer on this machine, SPEC-26): the hint health
        # carries while the view shows the test pattern; told once in a notice
        self.fallback = ""
        self._fallback_told = False
        #: the loading label (#156): {file, phase} from the open or reload until the new
        #: stage's first complete frame is on screen; None: the picture shows
        self.cover: dict[str, str] | None = None
        self._cover_loaded = False  # the source sent ``loaded`` for the stage covered
        #: the label lifts once the viewport shows this frame or a later one: the stage's
        #: first complete frame, as the source's ``ok`` names it (#166)
        self.cover_frame: int | None = None
        self.shown_seq: int | None = None  # the frame on screen (the viewport reports it)
        self._no_frames = False  # the viewport cannot draw frames (no OpenGL)
        self.size = DEFAULT_SIZE
        self.camera_speed = 1.0
        self.camera_mode = "free"  # params.camera_mode (#39)
        #: orbit mode's pivot: the selection's centre (stage frame), None without one
        self._anchor: list[float] | None = None
        self.panels: dict[str, dict[str, Any]] = {}
        self.tree: dict[str, dict[str, Any]] = {}  # prim path -> {path, name, type, count, kids}
        self.expanded: set[str] = set()
        self.tree_filter = ""
        self.matches: list[dict[str, Any]] = []
        self.match_total = 0
        self.time: StageTime | None = None  # the loaded stage's clock (#141)
        self.timecode = 0.0
        self._wanted_key: str | None = None  # the item a received selection names
        self.selected: list[str] = []  # the selected prims of the stage shown, last picked last
        self.hit: dict[str, Any] | None = None  # the last pick's {path, position, normal}
        self.revealed: str | None = None  # the prim the outliner last scrolled to
        self._wanted_prims: tuple[str, list[str]] | None = None  # (item key, prims) to select
        self._picks: deque[tuple[float, float, bool]] = deque()  # one in flight at a time
        self._closing: set[int] = set()  # pids this host closed on purpose
        self._last_camera: dict[str, Any] | None = None
        #: a camera port value is being applied: the change it makes is not the user's
        #: and does not go out again (#176)
        self._camera_in = False
        self._camera_wanted: CameraValue | None = None  # arrived before the stage opened
        self._camera_out = QTimer(self)
        self._camera_out.setSingleShot(True)
        self._camera_out.setInterval(CAMERA_EMIT_MS)
        self._camera_out.timeout.connect(self._emit_camera)
        self.view_mode = "lit"  # params.view_mode: the mode asked for (#153)
        self.view_mode_shown = "lit"  # the source's answer: the mode the picture shows
        self.view_mode_note = ""
        self.view_modes: list[dict[str, Any]] = []  # the source's view_modes for the stage
        self._mode_asked = "lit"  # the last set_view_mode sent for this stage
        #: the mode on its way while the source switches (the chip says it), else None
        self.view_mode_pending: str | None = None
        self.environment = "stage"  # params.environment: the choice asked (SPEC-31)
        self.environment_shown = "stage"  # the source's answer: what lights the picture
        self.environment_note = ""  # why it differs from the choice (the source's words)
        self.sky = skies.DEFAULT_SKY  # params.sky and params.hdri: the names chosen
        self.hdri = DEFAULT_HDRI
        self.sky_controls = SkyControls()  # params.time_of_day, cloudiness, haze
        self.sky_shown: dict[str, float] = {}  # the controls in effect (the source's answer)
        self.sky_fetch_note = ""  # the fetch of the sky chosen: progress, or why it failed
        self.ground = False  # params.ground: the sky's preview floor
        self._fetcher: Any = None  # skies.AssetFetcher, made on the first asset not here
        self._fetching: set[str] = set()  # the keys asked of it, applied once they land
        self._controls_out = QTimer(self)  # the sliders' live changes, coalesced
        self._controls_out.setSingleShot(True)
        self._controls_out.setInterval(CONTROLS_SEND_MS)
        self._controls_out.timeout.connect(self._send_sky_controls)
        self._fetch_changed.connect(self._on_fetch_changed)
        self._params: dict[str, Any] = {}
        self._answered.connect(self._on_answer)
        self.tl = TimelineController(self)  # the footer's scrubber (#141)
        self.tl.on_scrub = self.scrub_time
        self.tl.on_step = self.step_time
        self.tl.on_toggle_play = self.toggle_play
        self.tl.on_hover = self._on_time_hover
        self._play = QTimer(self)
        self._play.setTimerType(Qt.TimerType.PreciseTimer)
        self._play.setInterval(PLAY_TICK_MS)
        self._play.timeout.connect(self._play_tick)
        self._play_from = (0.0, 0.0)  # (monotonic s, timecode) where play started
        self._was_playing = False  # for pause / resume
        self.bridge = UsdBridge(self)
        self.camera: CameraControl = camera_factory(self._on_camera)
        self.frame_item: QQuickItem | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._take_events)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._idle_kill)
        if ctx.qml_engine is not None and not headless:
            self._create_item()
        self._apply_params(initial=True)
        self.reload()

    def _create_item(self) -> None:
        if not gallery_native.is_built():
            raise SpectoError(
                "native plugin Specto.Native is not built", fix=gallery_native.BUILD_HINT
            )
        gallery_native.ensure_import_path(self.ctx.qml_engine)
        self.item = self.ctx.create_item(
            USD_QML, properties={"viewId": self.view_id, "bridge": self.bridge, "tl": self.tl}
        )
        self.frame_item = self.item.findChild(QQuickItem, "frame")
        if self.frame_item is None:
            raise SpectoError("UsdView.qml has no child named 'frame'")

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            self.reload()
        elif port == SELECTION and isinstance(value, Selection):
            picked = value.picked
            key = picked.split("#", 1)[0] if picked else None
            self._wanted_key = key
            if key in self.keys and key != self.key:
                self._show(self.keys.index(key))
            prims = [k.split("#", 1)[1] for k in value.keys if k.startswith(f"{key}#")]
            if picked and picked.startswith(f"{key}#"):  # the picked prim goes last
                last = picked.split("#", 1)[1]
                prims = [p for p in prims if p != last] + [last]
            if key is not None and key == self.key and prims:
                self._wanted_prims = (key, prims)
                if self.stage:
                    self._select_wanted()
        elif port == TIME and isinstance(value, TimeValue):
            self._follow_time(value)
        elif port == CAMERA and isinstance(value, CameraValue):
            if self.stage:
                self._apply_camera(value)
            else:  # taken once the stage opens, over its first look
                self._camera_wanted = value

    # -- items -------------------------------------------------------------------------
    def reload(self) -> None:
        """(Re)read the collection and show one stage: the last one a pick added, else
        the one shown, else the one a selection or ``params.selected`` names, else the
        first."""
        shown, before = self.key, set(self.keys)
        self.items = [
            it for it in (self.collection.items if self.collection else []) if usd_part(it)
        ]
        self.keys = [it.key for it in self.items]
        added = [k for k in self.keys if k not in before] if before else []
        i = -1
        for key in (
            added[-1] if added else None,
            shown,
            self._wanted_key,
            self._params.get("selected"),
        ):
            if key is not None and str(key) in self.keys:
                i = self.keys.index(str(key))
                break
        if i < 0 and self.items:
            i = 0
        if not self.items:
            self.set_health("paused", "no stages" if self.collection is not None else "no data")
        self._show(i)

    def _show(self, index: int) -> None:
        self.index = index if 0 <= index < len(self.items) else -1
        path = usd_part(self.items[self.index]) if self.index >= 0 else None
        self.sync_step()
        if path == self.path and (path is None or self.source is not None or self.killed):
            return
        self.path = path
        self.stage = {}
        self.time = None
        self._set_failure(None)
        self._halt()
        self.tl.set_label("")
        self._reset_tree()
        self._clear_selection()
        self.bridge.stageChanged.emit()
        if path is None:
            self._uncover()
            self._close_source()
            return
        self._begin_cover(COVER_PHASES["starting" if self.source is None else "composing"])
        if self.host_paused and self.source is None:
            return  # resume relaunches it, and it loads the stage
        if self.source is None:
            self._spawn()
        elif self.ready is not None:
            self._load()

    @property
    def key(self) -> str | None:
        return self.keys[self.index] if 0 <= self.index < len(self.keys) else None

    # live: the document keeps the stage the agent selected
    def step_keys(self) -> list[str]:
        return self.keys

    def step_key(self) -> str | None:
        return self.key

    def show_step(self, key: str) -> bool:
        self._show(self.keys.index(key))
        return True

    @property
    def emits_selection(self) -> bool:
        return False  # the selection port carries prims (#140), not the stage stepped to

    # -- the source process ------------------------------------------------------------
    def _spawn(self) -> None:
        self.killed = False
        self.ready = None
        self.shown_seq = None  # a new ring counts from 1
        self._set_failure(None)
        self._begin_cover(COVER_PHASES["starting"])
        try:
            choice = self.source_command(
                self.view, lambda: self.ctx.engine.capabilities, *self.size
            )
            argv = choice.argv
            self._renderer_kind = argv[argv.index("--renderer") + 1] if "--renderer" in argv else ""
            self.fallback = choice.fallback_reason
            if self.fallback and not self._fallback_told:
                self._fallback_told = True
                self.ctx.inform(f"{self.view_id}: {self.fallback}")
            self.source = FrameSourceProcess.spawn(argv)
        except OSError as e:
            self.source = None
            self._uncover()
            self.set_health("error", f"cannot start the renderer: {e}")
            return
        self.set_health("loading", PHASES["starting"])
        self._poll.start()

    def _load(self) -> None:
        if self.source is None or self.path is None:
            return
        self.set_health("loading", f"{PHASES['composing']} {Path(self.path).name}")
        if self.cover is None:
            self._begin_cover(COVER_PHASES["composing"])
        self._cover_loaded, self.cover_frame = False, None
        self._send("load", path=self.path, **self._environment_params())

    def _send(self, method: str, **params: Any) -> None:
        if self.source is None:
            return
        try:
            self.source.send(method, **params)
        except SourceError as e:
            self.set_health("error", str(e))

    def _request(
        self,
        method: str,
        on_result: Callable[[dict], None],
        on_error: Callable[[], None] | None = None,
        **params: Any,
    ) -> None:
        """Ask the source off the GUI thread; ``on_result`` runs on the GUI thread with the
        result (an error goes to stderr, then ``on_error``: a stale query is not the
        view's health)."""
        src = self.source
        if src is None:
            return

        def ask() -> None:
            try:
                self._answered.emit(on_result, on_error, src.call(method, **params), None)
            except SourceError as e:
                self._answered.emit(on_result, on_error, None, e)

        threading.Thread(target=ask, name=f"usd-{method}", daemon=True).start()

    @Slot(object, object, object, object)
    def _on_answer(
        self,
        on_result: Callable[[dict], None],
        on_error: Callable[[], None] | None,
        result: Any,
        error: Any,
    ) -> None:
        if self.disposed:
            return
        if error is not None:
            print(f"[WARN] usd {self.view_id}: {error}", file=sys.stderr)
            if on_error is not None:
                on_error()
            return
        on_result(result)

    @Slot()
    def _take_events(self) -> None:
        src = self.source
        if src is None:
            self._poll.stop()
            return
        for name, params in src.poll():
            if src is not self.source:
                break  # an event closed or replaced the source
            self._on_event(name, params)

    def _on_event(self, name: str, params: dict[str, Any]) -> None:
        if name == "ready":
            self.ready = params
            if self.frame_item is not None:
                self.frame_item.setProperty("ring", params.get("ring", ""))
                self.frame_item.setProperty("wake", params.get("wake", ""))
            # the viewport reports its size between the panels while the renderer starts:
            # a source spawned at an older size takes the current one now (#180)
            if (params.get("width"), params.get("height")) != self.size:
                self._send("resize", width=self.size[0], height=self.size[1])
            if self._last_camera is not None:
                self._send("set_camera", **self._last_camera)
            self._load()
        elif name == "ring":
            if self.frame_item is not None:
                self.frame_item.setProperty("ring", params.get("name", ""))
        elif name == "health":
            if params.get("state") == "ok" and isinstance(params.get("timings"), dict):
                self.stage["load_timings"] = dict(params["timings"])  # snap (SPEC-27)
            seq = params.get("seq")
            self._on_phase(
                str(params.get("state", "")),
                str(params.get("message", "")),
                int(seq) if isinstance(seq, int) else None,
            )
        elif name == "warning":
            self._on_warning(str(params.get("message", "")))
        elif name == "view_mode":
            self._mode_shown(str(params.get("mode") or "lit"), str(params.get("note") or ""))
        elif name == "loaded":
            if params.get("path") != self.path:
                return  # a stage the user has stepped past
            self.stage = dict(params)
            self._cover_loaded = True
            self._stage_time(params)
            self._stage_modes()
            self._stage_environment(params)
            self.bridge.stageChanged.emit()
            self.camera.load(params.get("bounds"), str(params.get("up_axis", "Y")))
            if self._camera_wanted is not None:  # a shared viewpoint wins the first look
                self._apply_camera(self._camera_wanted)
                self._camera_wanted = None
            self._fetch("/", depth=2)
            if self._wanted_prims is not None:
                self._select_wanted()
            elif self.selected:  # a relaunched source: the outline and the anchor again
                self._send("select", paths=self.selected, color=COLORS["accent"])
                self._refresh_anchor()
        elif name == "exited":
            src = self.source
            self.source = None
            self.ready = None
            self._poll.stop()
            self._uncover()
            if src is not None and src.proc.pid not in self._closing:
                self._failed(src, params.get("code"))

    # -- view modes (#153) ---------------------------------------------------------------
    def offered_modes(self) -> list[str]:
        """The view modes this stage offers, by the source (lit until it has said)."""
        offered = {m.get("mode") for m in self.view_modes if m.get("available")}
        return [mode for mode in VIEW_MODES if mode == "lit" or mode in offered]

    def view_mode_rows(self) -> list[dict[str, Any]]:
        """The pill menu's rows: the modes on offer."""
        return [{"mode": m, "label": VIEW_MODES[m]} for m in self.offered_modes()]

    def set_view_mode(self, mode: str) -> None:
        """The user picked a view mode: ``params.view_mode``, one submit."""
        if mode not in VIEW_MODES or mode == self.view_mode:
            return
        self.view_mode = mode
        self._write_params({"view_mode": mode}, f"view mode {mode}")
        self._apply_view_mode()

    def _stage_modes(self) -> None:
        """A stage loaded: it shows lit; ask what it can show, then show the mode asked."""
        self.view_mode_shown, self.view_mode_note = "lit", ""
        self.view_modes = []
        self._mode_asked = "lit"
        self.view_mode_pending = None
        self.bridge.viewModeChanged.emit()
        path = self.path

        def take(result: dict[str, Any]) -> None:
            if path != self.path:
                return
            self.view_modes = list(result.get("modes") or [])
            self.bridge.viewModeChanged.emit()
            self._apply_view_mode()

        self._request("view_modes", take)

    def _apply_view_mode(self) -> None:
        """Ask the source for ``params.view_mode`` when the stage can show it, else lit."""
        if self.source is None or not self.stage or not self.view_modes:
            return
        want = self.view_mode if self.view_mode in self.offered_modes() else "lit"
        if want == self._mode_asked:
            return
        self._mode_asked = want
        self.view_mode_pending = want
        self.bridge.viewModeChanged.emit()  # the chip says the mode on its way
        path = self.path

        def take(result: dict[str, Any]) -> None:
            if path == self.path:
                self._mode_shown(str(result.get("mode") or want), str(result.get("note") or ""))

        def failed() -> None:
            # the picture stays in the mode it shows; should the source still switch
            # (it bounds the overlay's build below this wait), its ``view_mode`` event
            # brings the chip along (SPEC-33)
            if path == self.path and self._mode_asked == want:
                self._mode_asked = self.view_mode_shown
                self.view_mode_pending = None
                self.bridge.viewModeChanged.emit()

        self._request("set_view_mode", take, failed, mode=want, timeout=MODE_TIMEOUT_S)

    def _mode_shown(self, mode: str, note: str) -> None:
        """The source shows ``mode`` (an answer or its ``view_mode`` event): the chip, the
        note and snap follow the picture."""
        self.view_mode_shown, self.view_mode_note = mode, note
        if self.view_mode_pending == mode:
            self.view_mode_pending = None
        self.bridge.viewModeChanged.emit()

    # -- the environment light (SPEC-31) -----------------------------------------------------
    def set_environment(self, environment: str) -> None:
        """The user picked an environment: ``params.environment``, one submit."""
        if environment not in ENVIRONMENTS or environment == self.environment:
            return
        self.environment = environment
        self.bridge.environmentChanged.emit()
        self._write_params({"environment": environment}, f"environment {environment}")
        self._apply_environment()

    def set_sky(self, name: str) -> None:
        """The panel's Sky list: ``params.sky``, one submit; a sky not cached is fetched."""
        if name not in skies.SKIES or name == self.sky:
            return
        self.sky = name
        self.bridge.environmentChanged.emit()
        self._write_params({"sky": name}, f"sky {skies.label(name)}")
        self._apply_environment()

    def set_hdri(self, name: str) -> None:
        """The panel's HDRI list: ``params.hdri``, one submit; one not here is fetched."""
        if name not in HDRIS or name == self.hdri:
            return
        self.hdri = name
        self.bridge.environmentChanged.emit()
        self._write_params({"hdri": name}, f"HDRI {HDRIS[name]}")
        self._apply_environment()

    def set_ground(self, on: bool) -> None:
        """The panel's Ground toggle: ``params.ground``, one submit."""
        on = bool(on)
        if on == self.ground:
            return
        self.ground = on
        self.bridge.environmentChanged.emit()
        self._write_params({"ground": on}, "ground on" if on else "ground off")
        self._apply_environment()

    def _environment_params(self) -> dict[str, Any]:
        """What a ``load`` and a ``set_environment`` carry."""
        return {
            "environment": self.environment,
            "sky": self.sky,
            "hdri": self.hdri,
            "sky_controls": self.sky_controls.as_dict(),
            "ground": self.ground,
        }

    def _take_environment(self, result: dict[str, Any]) -> None:
        """The source's answer: what shows, why, and the controls in effect."""
        if "environment" in result:
            self.environment_shown = str(result.get("environment") or "stage")
            self.environment_note = str(result.get("environment_note") or "")
        controls = result.get("sky_controls")
        if isinstance(controls, dict):
            self.sky_shown = {k: float(v) for k, v in controls.items()}
        self.bridge.environmentChanged.emit()
        if "renderer_note" in result and self.stage:
            self.stage["renderer_note"] = str(result.get("renderer_note") or "")
            self.bridge.stageChanged.emit()

    def _stage_environment(self, params: dict[str, Any]) -> None:
        """A stage loaded with the choice sent along: the source says what shows."""
        self._take_environment(params)
        self._ensure_assets()
        if self.environment_shown != self.environment:  # a fallback, or an old source
            self._apply_environment()

    def _apply_environment(self) -> None:
        """Ask the source for the environment chosen on the stage shown; what shows is its
        answer (the choice, or the fallback with its note), and the readout's note follows.
        A sky or an HDRI not here yet is fetched meanwhile and applied when it lands."""
        self._ensure_assets()
        if self.source is None or not self.stage:
            return
        path, want = self.path, self._environment_params()

        def take(result: dict[str, Any]) -> None:
            if path == self.path and want == self._environment_params():
                self._take_environment(result)

        self._request("set_environment", take, **want, timeout=MODE_TIMEOUT_S)

    # the environment's assets: fetched on first use, off the GUI thread, said in the panel
    def _wanted_assets(self) -> list[str]:
        """The fetcher keys the choice needs: the sky (and the HDRI that stands in for it
        meanwhile, or under Storm), or the HDRI."""
        if self.environment == "sky":
            return [self.sky, skies.hdri_key(self.hdri)]
        if self.environment == "hdri":
            return [skies.hdri_key(self.hdri)]
        return []

    def _ensure_assets(self) -> None:
        for key in self._wanted_assets():
            if self._asset_cached(key):
                continue
            if self._fetcher is None:
                self._fetcher = self._fetcher_factory(self._fetch_changed.emit)
            self._fetching.add(key)
            self._fetcher.ask(key)
        self._refresh_fetch_note()

    def _asset_cached(self, key: str) -> bool:
        if self._fetcher is not None:
            return bool(self._fetcher.cached(key))
        if key.startswith(skies.HDRI_PREFIX):
            return skies.hdri_path(key[len(skies.HDRI_PREFIX) :]) is not None
        return skies.package(key) is not None

    @Slot()
    def _on_fetch_changed(self) -> None:
        """The fetcher moved (on the GUI thread): the note, and an asset the choice needs
        that landed applies."""
        if self.disposed or self._fetcher is None:
            return
        landed = {key for key in self._fetching if self._fetcher.cached(key)}
        self._fetching -= landed
        self._refresh_fetch_note()
        if landed & set(self._wanted_assets()):
            self._apply_environment()

    def _refresh_fetch_note(self) -> None:
        """The fetch's line: the sky's while it is on its way, else the HDRI's when the
        HDRI is what is asked for (or stands in for the sky)."""
        note = ""
        if self._fetcher is not None:
            for key in self._wanted_assets():
                note = self._fetcher.note(key)
                if note:
                    break
        if note != self.sky_fetch_note:
            self.sky_fetch_note = note
            self.bridge.environmentChanged.emit()

    @property
    def environment_words(self) -> str:
        """The panel's line under the control: an asset's fetch while it is on its way,
        else the source's note on what shows instead."""
        return self.sky_fetch_note or self.environment_note

    def _fetch_state(self, key: str) -> dict[str, Any]:
        if self._fetcher is None:
            return {"cached": self._asset_cached(key)}
        return self._fetcher.status(key).as_dict()

    def sky_fetch_state(self) -> dict[str, Any]:
        """``snap``: the sky chosen and its fetch (cached, fetching, done, total, error)."""
        return {"sky": self.sky, **self._fetch_state(self.sky)}

    def hdri_fetch_state(self) -> dict[str, Any]:
        """``snap``: the HDRI chosen and its fetch."""
        return {"hdri": self.hdri, **self._fetch_state(skies.hdri_key(self.hdri))}

    @property
    def hdri_stands_in(self) -> bool:
        """The HDRI shows for the sky chosen (Storm, or the sky not here yet)."""
        return self.environment == "sky" and self.environment_shown == "hdri"

    def sky_controls_offered(self) -> tuple[str, ...]:
        """The sky's controls that act now: those the sky takes, while it shows."""
        if self.environment_shown != "sky":
            return ()
        return controls_of(self.sky)

    # the sky's controls: live while a slider moves, one user submit on release
    def set_sky_control(self, name: str, value: float | None, *, final: bool = True) -> None:
        """One of :data:`SkyControls.NAMES`: the sky follows at once; the param is written
        on ``final`` (a slider's release, the agent's value)."""
        if name not in SkyControls.NAMES:
            return
        controls = self.sky_controls.with_value(name, value)
        if controls != self.sky_controls:
            self.sky_controls = controls
            self.bridge.environmentChanged.emit()
            self._controls_out.start()
        if final and self._params.get(name) != getattr(controls, name):
            self._write_params({name: getattr(controls, name)}, name.replace("_", " "))

    @Slot()
    def _send_sky_controls(self) -> None:
        if self.source is None or not self.stage:
            return
        path, want = self.path, self.sky_controls

        def take(result: dict[str, Any]) -> None:
            if path == self.path and want == self.sky_controls:
                self._take_environment({"sky_controls": result.get("sky_controls")})

        self._request("set_sky_controls", take, **want.as_dict(), timeout=MODE_TIMEOUT_S)

    def sky_values(self) -> dict[str, float]:
        """The controls as the sliders show them: the params, else the values in effect
        (the source's), else where a slider rests before a sky says."""
        shown = {**SkyControls.SLIDER_DEFAULTS, **self.sky_shown}
        for key, value in self.sky_controls.as_dict().items():
            if value is not None:
                shown[key] = value
        return shown

    def _on_phase(self, state: str, message: str, seq: int | None = None) -> None:
        if state == "ok":
            self.set_health("ok", self._ok_message())
            self._first_frame_out(seq)
        elif state == "error":
            self._uncover()
            self.set_health("error", message or "the renderer failed")
        else:
            words = PHASES.get(state, state)
            self.set_health("loading", f"{words} {message}".strip())
            if self.cover is not None:
                self._cover_phase(cover_words(state, message))

    def health_hint(self) -> str:
        """While the stub plays because no renderer is found here, why (SPEC-26): every
        health this view gets carries it, its own and the registry's alike."""
        return self.fallback

    def _ok_message(self) -> str:
        """Health's message while the stage renders: its file, then the last error the
        renderer drew past."""
        name = Path(self.path).name if self.path else ""
        warnings = self.stage.get("warnings") or []
        return f"{name}: {warnings[-1]}" if warnings and name else name

    def _on_warning(self, message: str) -> None:
        """An error the renderer drew past (``warning``, once each a stage): logged, kept
        in the snapshot, and said in health while the view is ok; the view goes on."""
        if not message:
            return
        print(f"[WARN] usd {self.view_id}: {message}", file=sys.stderr)
        self.stage.setdefault("warnings", []).append(message)
        if self.ctx.health_of(self.view_id).get("status") == "ok":
            self.set_health("ok", self._ok_message())

    # -- the loading label (#156) --------------------------------------------------------
    def _begin_cover(self, phase: str) -> None:
        """Cover the picture: a stage starts loading (an open, a step, a relaunch)."""
        self._cover_loaded, self.cover_frame = False, None
        cover = {"file": Path(self.path).name, "phase": phase} if self.path else None
        if cover != self.cover:
            self.cover = cover
            self.bridge.coverChanged.emit()

    def _cover_phase(self, phase: str) -> None:
        if self.cover is not None and self.cover["phase"] != phase:
            self.cover = {**self.cover, "phase": phase}
            self.bridge.coverChanged.emit()

    def _uncover(self) -> None:
        self._cover_loaded, self.cover_frame = False, None
        if self.cover is not None:
            self.cover = None
            self.bridge.coverChanged.emit()

    def _first_frame_out(self, seq: int | None) -> None:
        """The source's first frame of the covered stage is in its ring, and ``seq`` names
        the stage's first complete frame (#166). Headless (no viewport reports frames)
        the label lifts now; else once the viewport shows that frame or a later one,
        which may already be on screen: a settled renderer (Storm) draws a few frames
        and idles, so a frame counted from the one shown now might never come. A
        source that names none: the next frame shown."""
        if self.cover is None or not self._cover_loaded or self.cover_frame is not None:
            return  # an ``ok`` of the stage stepped past (a drain), or one already counted
        if (self.frame_item is None or self._no_frames) and self.shown_seq is None:
            self._uncover()
            return
        self.cover_frame = seq if seq is not None else (self.shown_seq or 0) + 1
        if self.shown_seq is not None and self.shown_seq >= self.cover_frame:
            self._uncover()
            return
        self._cover_phase(STREAMING)

    def frame_shown(self, seq: int) -> None:
        """The viewport shows frame ``seq``: the label lifts on the stage's first
        complete frame or a later one."""
        self.shown_seq = seq
        if self.cover_frame is not None and seq >= self.cover_frame:
            self._uncover()

    def frames_unavailable(self) -> None:
        """The viewport draws no frames (no OpenGL): the label lifts on the first frame's
        ``ok`` alone, as headless."""
        self._no_frames = True
        if self.cover_frame is not None and self.shown_seq is None:
            self._uncover()

    # -- a source that died (#155) -------------------------------------------------------
    def _failed(self, src: FrameSourceProcess, code: int | None) -> None:
        """The source exited on its own: why, in words, over the picture."""
        lines = src.stderr_tail()
        cause = GPU_LOST_WORDS if any(GPU_LOST.search(line) for line in lines) else ""
        log = Path(tempfile.gettempdir()) / f"specto-usd-source-{src.proc.pid}.log"
        failure = {
            "reason": exit_reason(code),
            "cause": cause,
            "errors": renderer_errors(lines),
            "log": str(log) if log.is_file() else "",
            "retry": self.path is not None,
            "storm": self.path is not None and self._renderer_kind != "storm" and self._has_storm(),
        }
        self._set_failure(failure)
        self.set_health("error", f"{failure['reason']}: {cause}" if cause else failure["reason"])

    def _has_storm(self) -> bool:
        try:
            storm = self.ctx.engine.capabilities.get("storm")
        except Exception:
            return False
        return storm is not None and bool(storm.present)

    def _set_failure(self, failure: dict[str, Any] | None) -> None:
        if failure != self.failure:
            self.failure = failure
            self.bridge.failureChanged.emit()

    def retry(self) -> None:
        """The failure's Retry: a new source with the same renderer, on the same stage."""
        if self.failure is None or self.source is not None or self.path is None:
            return
        self._spawn()

    def open_with_storm(self) -> None:
        """The failure's Open with Storm: ``params.renderer`` becomes ``storm`` (one user
        submit, so the view stays on Storm) and a Storm source loads the stage."""
        if self.failure is None or self.source is not None or self.path is None:
            return
        self._write_params({"renderer": "storm"}, "open with Storm")
        self._spawn()

    def _close_source(self) -> None:
        src, self.source = self.source, None
        self.ready = None
        self._poll.stop()
        self._idle.stop()
        if src is None:
            return
        self._closing.add(src.proc.pid)
        threading.Thread(target=src.close, name="usd-source-close", daemon=True).start()

    @property
    def renderer_label(self) -> str:
        name = str(self.stage.get("renderer", ""))
        return RENDERER_LABELS.get(name, name)

    # -- the camera ----------------------------------------------------------------------
    def _on_camera(self, params: dict[str, Any], cut: bool) -> None:
        self._last_camera = dict(params)
        if self.ready is not None:
            self._send("set_camera", **params, cut=cut)
        if self._camera_in:
            self._camera_out.stop()  # a received camera: shown, not republished
        elif self.stage and not self._camera_out.isActive():
            self._camera_out.start()

    def _apply_camera(self, value: CameraValue) -> None:
        """A ``camera`` port value (the 3d view's frame): the navigation goes there."""
        self._camera_in = True
        try:
            self.camera.apply_value(value.model_dump(mode="json"))
        finally:
            self._camera_in = False

    @Slot()
    def _emit_camera(self) -> None:
        """The user's navigation as the ``camera`` output, throttled (CAMERA_EMIT_MS)."""
        try:
            value = CameraValue.model_validate(self.camera.value())
        except ValueError:
            return  # a camera with no lens yet (no stage)
        self.emit(CAMERA, value)

    def set_camera_speed(self, speed: float, *, final: bool = True) -> None:
        """The VIEW panel's slider: live while it moves, one user submit on release."""
        speed = speed_of(speed)
        changed = speed != self.camera_speed
        self.camera_speed = speed
        self.camera.camera_speed = speed
        if changed:
            self.bridge.speedChanged.emit()
        if final and self._params.get("camera_speed") != speed:
            self._write_params({"camera_speed": speed}, "camera speed")

    # -- the camera mode (#39) -----------------------------------------------------------
    def set_camera_mode(self, mode: str) -> None:
        """The VIEW panel's Free / Orbit: ``params.camera_mode``, one submit."""
        mode = mode_of(mode)
        if mode == self.camera_mode:
            return
        self._apply_camera_mode(mode)
        self._write_params({"camera_mode": mode}, f"camera mode {mode}")

    def _apply_camera_mode(self, mode: str) -> None:
        """The mode changes: the camera stays put, the anchor is taken for orbit mode and
        the status bar's gesture line follows (the health said again refreshes it)."""
        self.camera_mode = mode
        self.camera.mode = mode
        self.bridge.cameraModeChanged.emit()
        if mode == "orbit":
            self._refresh_anchor()
        health = self.ctx.health_of(self.view_id)
        self.set_health(str(health.get("status", "ok")), str(health.get("message", "")))

    @property
    def nav_keys(self) -> str:
        """The status bar's gesture line for the mode (the canvas controller reads it;
        empty: the view type's own line, Unreal Editor's)."""
        return ORBIT_KEYS if self.camera_mode == "orbit" else ""

    def _refresh_anchor(self) -> None:
        """Orbit mode's pivot: the selection's centre (its subtree bounds), None without
        a selection that draws; asked of the source, dropped if the selection moves on."""
        if self.camera_mode != "orbit":
            return
        if not self.selected or self.source is None or not self.stage:
            self._set_anchor(None)
            return

        def take(bounds: list[list[float]] | None) -> None:
            if bounds:
                self._set_anchor([(a + b) / 2 for a, b in zip(*bounds, strict=True)])
            else:
                self._set_anchor(None)

        self._selection_bounds(take)

    def _set_anchor(self, point: list[float] | None) -> None:
        self._anchor = point
        self.camera.set_anchor(point)

    # -- viewport input (#151) ----------------------------------------------------------
    def press(self, button: str, alt: bool, x: float, y: float) -> None:
        """A button went down at NDC ``(x, y)``: a gesture that pivots gets its pivot. The
        free camera's orbit (Alt+LMB): the selection's picked point, else its bounds'
        centre, else the point picked under the cursor. Orbit mode (#39): the anchor (the
        selection's centre) stands; without one, the point picked under the cursor."""
        self.camera.press(button, alt)
        if not self.camera.pivoting or self.source is None or not self.stage:
            return
        if self.camera_mode == "orbit":
            if self._anchor is None:
                self._pick_pivot(x, y)
            return
        picked = self._picked_point()
        if picked is not None:
            self.camera.set_pivot(picked)
            return

        def centre(bounds: list[list[float]] | None) -> None:
            if bounds:
                self.camera.set_pivot([(a + b) / 2 for a, b in zip(*bounds, strict=True)])

        if self.selected:
            self._selection_bounds(centre)
            return
        self._pick_pivot(x, y)

    def _pick_pivot(self, x: float, y: float) -> None:
        """The point under the press (``pick``, selecting nothing) as the gesture's pivot."""
        stage = self.path

        def take(result: dict[str, Any]) -> None:
            if stage == self.path and result.get("hit") and result.get("position"):
                self.camera.set_pivot(list(result["position"]))

        self._request("pick", take, x=x, y=y)

    def release(self, button: str) -> None:
        """A button came up; the camera speed the RMB wheel set is kept (one submit)."""
        self.camera.release(button)
        kept = speed_of(self._params.get("camera_speed", 1.0))
        if button == "right" and kept != self.camera_speed:
            self.set_camera_speed(self.camera_speed, final=True)

    def wheel(self, angle_delta_y: float) -> None:
        self.camera.wheel(angle_delta_y)
        if self.camera.camera_speed != self.camera_speed:  # RMB held: the speed, live
            self.set_camera_speed(self.camera.camera_speed, final=False)

    def frame_selection(self) -> None:
        """F and the VIEW panel's Frame selected: frame the selected prims' bounds, the
        union over each one's whole subtree; a selection that draws nothing, the picked
        point; nothing selected, the whole stage."""
        if not self.selected:
            self.camera.frame_all()
            return

        def take(bounds: list[list[float]] | None) -> None:
            picked = self._picked_point()
            if bounds:
                self.camera.frame(bounds)
            elif picked is not None:
                self.camera.frame_point(picked)
            else:
                self.camera.frame_all()

        self._selection_bounds(take)

    def _picked_point(self) -> list[float] | None:
        """Where the viewport click that selected the last prim hit it (stage frame);
        None for a selection made otherwise."""
        hit = self.hit
        if hit and hit.get("position") and hit.get("path") in self.selected:
            return [float(c) for c in hit["position"]]
        return None

    def _selection_bounds(self, take: Callable[[list[list[float]] | None], None]) -> None:
        """The world bounds around every selected prim, each its whole subtree's (the
        source's ``bounds``; None: they draw nothing), handed to ``take`` once the source
        has answered for each; dropped if the stage or the selection changes meanwhile."""
        paths, stage = list(self.selected), self.path
        boxes: dict[str, list[list[float]] | None] = {}

        def one(path: str) -> Callable[[dict[str, Any]], None]:
            def got(result: dict[str, Any]) -> None:
                boxes[path] = result.get("bounds")
                if len(boxes) < len(paths) or stage != self.path or paths != self.selected:
                    return
                found = [b for b in boxes.values() if b]
                if not found:
                    take(None)
                    return
                lo = [min(b[0][i] for b in found) for i in range(3)]
                hi = [max(b[1][i] for b in found) for i in range(3)]
                take([lo, hi])

            return got

        for path in paths:
            self._request("bounds", one(path), path=path)

    def viewport_resized(self, width: float, height: float, dpr: float) -> None:
        max_pixels = int(self._params.get("max_pixels") or MAX_PIXELS)
        size = frame_size(width, height, dpr, max_pixels)
        if size == self.size:
            return
        self.size = size
        if self.ready is not None:
            self._send("resize", width=size[0], height=size[1])

    # -- time (#141) ---------------------------------------------------------------------
    def _stage_time(self, params: dict[str, Any]) -> None:
        """A stage loaded: its clock from the ``loaded`` event. A reload of the same stage
        (a relaunch) keeps the timecode; a new one starts at its start, or lands on a time
        already sitting on the input port."""
        same = self.time is not None
        self.time = t = StageTime.from_loaded(params)
        if not t.animated:
            self.tl.set_label("")
            return
        self.tl.set_range(t.seconds(t.start), t.seconds(t.end), USD_TIMELINE)
        if not same:
            self.timecode = self.time.start
            value = self.ctx.links.input_of(self.view_id, TIME)
            if isinstance(value, TimeValue) and value.timeline in (USD_TIMELINE, OWN_TIMELINE):
                self._follow_time(value)
                return
        self._set_timecode(self.time.clamp(self.timecode))

    def _follow_time(self, value: TimeValue) -> None:
        """A time arrived: the stage goes there (clamped to its range) and the landed time
        goes out once, as the video's does; another timeline's time is not this stage's."""
        if self.time is None or not self.time.animated:
            return
        if value.timeline not in (USD_TIMELINE, OWN_TIMELINE):
            return
        self._halt()
        self._set_timecode(self.time.clamp(self.time.timecode(value.t)))
        self.emit(TIME, self.time.value(self.timecode))

    def scrub_time(self, t: float, final: bool) -> None:
        """The scrubber was dragged to ``t`` seconds: the stage goes to the whole timecode
        there, which goes out while dragging and on the release."""
        if self.time is None or not self.time.animated:
            return
        self._halt()
        timecode = self.time.clamp(round(self.time.timecode(t)))
        if timecode != self.timecode or final:
            self._set_timecode(timecode)
            self.emit(TIME, self.time.value(timecode))
        self.tl.set_t(self.time.seconds(timecode))

    def step_time(self, delta: int) -> None:
        """Left / Right: one timecode back or on, clamped to the range."""
        if self.time is None or not self.time.animated:
            return
        self._halt()
        self._set_timecode(self.time.clamp(round(self.timecode) + delta))
        self.emit(TIME, self.time.value(self.timecode))

    def _on_time_hover(self, t: float | None) -> None:
        """The scrubber's hover tip: the whole timecode under the pointer, and its time."""
        if t is None or self.time is None:
            self.tl.set_hover_tip("", "")
            return
        timecode = self.time.clamp(round(self.time.timecode(t)))
        self.tl.set_hover_tip(f"tc {timecode:g}", f"{self.time.seconds(timecode):.2f} s")

    @property
    def playing(self) -> bool:
        return self._play.isActive()

    def toggle_play(self) -> None:
        """Play / pause: play moves the stage a whole timecode at a time at
        timeCodesPerSecond, each one out on ``time`` (so a root usd view drives the global
        clock); it stops on the last one, and starts over from there. A pause sends the
        landed time once more."""
        t = self.time
        if t is None or not t.animated:
            return
        if self.playing:
            self._halt()
            self.emit(TIME, t.value(self.timecode))
            return
        if self.timecode >= t.end:
            self._set_timecode(t.start)
            self.emit(TIME, t.value(t.start))
        self._play_from = (time.monotonic(), self.timecode)
        self._play.start()
        self.tl.set_playing(True)

    @Slot()
    def _play_tick(self) -> None:
        t = self.time
        if t is None or not t.animated:
            self._halt()
            return
        started, from_tc = self._play_from
        timecode = min(t.end, from_tc + math.floor((time.monotonic() - started) * t.tcps))
        if timecode != self.timecode:
            self._set_timecode(timecode)
            self.emit(TIME, t.value(timecode))
        if timecode >= t.end:
            self._halt()

    def _halt(self) -> None:
        """Stop playing, sending nothing."""
        self._play.stop()
        self.tl.set_playing(False)

    def _set_timecode(self, timecode: float) -> None:
        self.timecode = timecode
        if self.time is None:
            return
        self.tl.set_t(self.time.seconds(timecode))
        self.tl.set_label(self.time.readout(timecode))
        if self.ready is not None:
            self._send("set_time", t=self.time.seconds(timecode), timecode=timecode)

    def time_snapshot(self) -> dict[str, Any] | None:
        t = self.time
        if t is None:
            return None
        return {
            "animated": t.animated,
            "timeline": USD_TIMELINE,
            "range": [t.start, t.end],
            "tcps": t.tcps,
            "timecode": self.timecode,
            "t": t.seconds(self.timecode),
            "playing": self.playing,
        }

    # -- the PRIMS outliner --------------------------------------------------------------
    def _reset_tree(self) -> None:
        self.tree = {}
        self.expanded = set()
        self.matches = []
        self.match_total = 0
        self.bridge.rowsChanged.emit()

    def _fetch(self, path: str, *, depth: int = 1, offset: int = 0) -> None:
        stage = self.path

        def take(result: dict[str, Any]) -> None:
            if stage == self.path:
                self._merge(result, expand_default=path == "/")

        self._request("query_tree", take, path=path, depth=depth, offset=offset, limit=TREE_PAGE)

    def _merge(self, result: dict[str, Any], *, expand_default: bool = False) -> None:
        parent = str(result.get("path", "/"))
        node = self.tree.setdefault(parent, {"path": parent, "kids": []})
        if int(result.get("offset", 0)) == 0:
            node["kids"] = []
        node["count"] = int(result.get("total", 0))
        for child in result.get("children", []):
            node["kids"].append(self._add_node(child))
        if expand_default and node["kids"]:
            self.expanded.add(node["kids"][0])  # the root prim (the default prim first)
        self.bridge.rowsChanged.emit()

    def _add_node(self, child: dict[str, Any]) -> str:
        path = str(child["path"])
        node = self.tree.setdefault(path, {"path": path})
        node.update(name=child.get("name", ""), type=child.get("type", ""))
        node["count"] = int(child.get("count", 0))
        if "children" in child:
            node["kids"] = [self._add_node(c) for c in child["children"]]
        return path

    def outliner_rows(self) -> list[dict[str, Any]]:
        """The rows the outliner shows: the expanded tree, depth-first, with an ``n more``
        row under a node whose children are not all fetched; while a filter is set, the
        matches, flat."""
        if self.tree_filter:
            return [
                {"kind": "prim", "depth": 0, "expanded": False, **m, "count": 0}
                for m in self.matches
            ]
        rows: list[dict[str, Any]] = []

        def walk(path: str, depth: int) -> None:
            node = self.tree.get(path) or {}
            kids = node.get("kids") or []
            for kid in kids:
                k = self.tree[kid]
                open_ = kid in self.expanded
                rows.append(
                    {
                        "kind": "prim",
                        "path": kid,
                        "name": k.get("name", ""),
                        "type": k.get("type", ""),
                        "depth": depth,
                        "count": k.get("count", 0),
                        "expanded": open_,
                    }
                )
                if open_:
                    walk(kid, depth + 1)
            left = int(node.get("count", 0)) - len(kids)
            if kids and left > 0:
                rows.append({"kind": "more", "path": path, "depth": depth, "count": left})

        walk("/", 0)
        return rows

    def toggle_row(self, path: str) -> None:
        node = self.tree.get(path)
        if node is None or not node.get("count"):
            return
        if path in self.expanded:
            self.expanded.discard(path)
        else:
            self.expanded.add(path)
            if node.get("kids") is None:
                self._fetch(path)
        self.bridge.rowsChanged.emit()

    def more_rows(self, path: str) -> None:
        node = self.tree.get(path)
        if node is not None:
            self._fetch(path, offset=len(node.get("kids") or []))

    def set_tree_filter(self, text: str) -> None:
        text = text.strip()
        self.tree_filter = text
        self.matches, self.match_total = [], 0
        self.bridge.rowsChanged.emit()
        if not text:
            return
        stage = self.path

        def take(result: dict[str, Any]) -> None:
            if stage == self.path and text == self.tree_filter:
                self.matches = list(result.get("matches", []))
                self.match_total = int(result.get("total", 0))
                self.bridge.rowsChanged.emit()

        self._request("query_tree", take, filter=text, limit=TREE_PAGE)

    # -- selection (#140) ----------------------------------------------------------------
    def pick(self, x: float, y: float, *, add: bool = False) -> None:
        """A click on the picture at NDC ``(x, y)`` (-1..1, +y up, the ``pick`` message's):
        the prim under it is selected (``add``: added, or taken out when already
        selected); nothing under it clears, unless ``add``. Picks are answered one at a
        time, in order."""
        if self.source is None or not self.stage:
            return
        self._picks.append((x, y, add))
        if len(self._picks) == 1:
            self._next_pick()

    def _next_pick(self) -> None:
        if not self._picks:
            return
        x, y, add = self._picks[0]
        stage = self.path

        def take(result: dict[str, Any]) -> None:
            self._picks.popleft()
            if stage == self.path:
                self._picked(result, add)
            self._next_pick()

        if self.source is None:
            self._picks.clear()
            return
        self._request("pick", take, on_error=self._picks.clear, x=x, y=y)

    def _picked(self, result: dict[str, Any], add: bool) -> None:
        path = result.get("path") if result.get("hit") else None
        self.hit = {k: v for k, v in result.items() if k != "hit"} if path else None
        if not path:
            if not add:
                self.select([])
            return
        if not add:
            self.select([path])
        elif path in self.selected:
            self.select([p for p in self.selected if p != path], reveal=False)
        else:
            self.select([*self.selected, path])

    def select_row(self, path: str, *, add: bool = False) -> None:
        """A click on an outliner row (Ctrl: add or take out)."""
        self.hit = None
        if not add:
            self.select([path], reveal=False)
        elif path in self.selected:
            self.select([p for p in self.selected if p != path], reveal=False)
        else:
            self.select([*self.selected, path], reveal=False)

    def frame_prim(self, path: str) -> None:
        """An outliner double-click: select the prim and frame its world bounds."""
        if self.selected != [path]:
            self.select_row(path)
        stage = self.path

        def take(result: dict[str, Any]) -> None:
            if stage == self.path and result.get("bounds"):
                self.camera.frame(result["bounds"])

        self._request("bounds", take, path=path)

    def select(self, paths: list[str], *, emit: bool = True, reveal: bool = True) -> None:
        """Select ``paths`` on the stage shown: the source outlines them, the outliner
        reveals the last, ``content`` carries the last one's properties and, for a pick
        made here (``emit``), ``selection`` goes out; a selection received is shown, not
        republished."""
        self.selected = list(dict.fromkeys(paths))
        self.bridge.selectionChanged.emit()
        self._send("select", paths=self.selected, color=COLORS["accent"])
        self._refresh_anchor()
        if reveal and self.selected:
            self._reveal(self.selected[-1])
        if emit:
            keys = [f"{self.key}#{p}" for p in self.selected]
            self.emit(SELECTION, Selection(keys=keys, last=keys[-1] if keys else None))
        self._emit_content()

    def _select_wanted(self) -> None:
        key, prims = self._wanted_prims or (None, [])
        self._wanted_prims = None
        if key == self.key and prims != self.selected:
            self.hit = None
            self.select(prims, emit=False)

    def _clear_selection(self) -> None:
        self._picks.clear()
        self.hit = None
        self.revealed = None
        self._set_anchor(None)
        if self._wanted_prims is not None and self._wanted_prims[0] != self.key:
            self._wanted_prims = None
        if not self.selected:
            return
        self.selected = []
        self.bridge.selectionChanged.emit()
        self.emit(SELECTION, Selection(keys=[], last=None))
        self.emit(CONTENT, ContentValue(root=self._content_root()))

    def _content_root(self) -> str:
        return str(Path(self.path).parent) if self.path else ""

    def _emit_content(self) -> None:
        """The last selected prim's properties on ``content`` (none: an empty value)."""
        root = self._content_root()
        if not self.selected:
            self.emit(CONTENT, ContentValue(root=root))
            return
        path, key, name = self.selected[-1], self.key, Path(self.path or "").name

        def take(result: dict[str, Any]) -> None:
            if key != self.key or not self.selected or self.selected[-1] != path:
                return  # selected on since
            entry = ContentEntry(
                key=f"{key}#{path}", path=f"{name}#{path}", format="json", data=result
            )
            self.emit(CONTENT, ContentValue(root=root, entries=[entry]))

        self._request("properties", take, path=path)

    def _reveal(self, path: str) -> None:
        """Open the outliner's rows down to ``path`` and scroll to it."""
        stage = self.path

        def take(result: dict[str, Any]) -> None:
            if stage != self.path:
                return
            levels = result.get("levels") or []
            for level in levels:
                self._merge(level)
                if level.get("path") != "/":
                    self.expanded.add(str(level["path"]))
            self.revealed = path
            self.bridge.rowsChanged.emit()
            rows = self.outliner_rows()
            row = next(
                (i for i, r in enumerate(rows) if r["kind"] == "prim" and r["path"] == path), -1
            )
            if row >= 0:
                self.bridge.revealRequested.emit(row)

        self._request("reveal", take, path=path, limit=TREE_PAGE)

    # -- side panels and params ----------------------------------------------------------
    def set_panel(
        self, name: str, *, collapsed: bool | None = None, width: float | None = None
    ) -> None:
        """A panel's collapse or width changed by the user: ``params.panels``, one submit."""
        if name not in PANELS:
            return
        state = dict(self.panel_state(name))
        if collapsed is not None:
            state["collapsed"] = collapsed
        if width is not None:
            state["width"] = int(max(PANEL_MIN, min(PANEL_MAX, round(width))))
        if state == self.panel_state(name):
            return
        panels = {**self.panels, name: state}
        self.panels = panels
        self.bridge.panelsChanged.emit()
        self._write_params({"panels": panels}, f"{name} panel")

    def panel_state(self, name: str) -> dict[str, Any]:
        s = self.panels.get(name) or {}
        return {
            "collapsed": bool(s.get("collapsed", False)),
            "width": int(s.get("width", PANEL_WIDTH[name])),
        }

    def _write_params(self, patch: dict[str, Any], what: str) -> None:
        doc = self.ctx.live.document()
        view = doc["views"].get(self.view_id)
        if view is None:
            return
        view.setdefault("params", {}).update(patch)
        self._params = {**self._params, **patch}
        self.ctx.submit(doc, f"{view.get('label') or self.view_id}: {what}")

    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        prefix = f"/views/{self.view_id}"
        if any(
            op.get("path", "").startswith(f"{prefix}/params") or op.get("path") == prefix
            for op in patch
        ):
            self._apply_params()

    def _apply_params(self, *, initial: bool = False) -> None:
        try:
            p = dict(self.params)
        except SpectoError:
            return
        before, self._params = self._params, p
        speed = speed_of(p.get("camera_speed", 1.0))
        if speed != self.camera_speed:
            self.camera_speed = speed
            self.camera.camera_speed = speed
            self.bridge.speedChanged.emit()
        mode = mode_of(p.get("camera_mode", "free"))
        if mode != self.camera_mode:
            self._apply_camera_mode(mode)
        panels = p.get("panels") if isinstance(p.get("panels"), dict) else {}
        if panels != self.panels:
            self.panels = {k: dict(v) for k, v in panels.items() if isinstance(v, dict)}
            self.bridge.panelsChanged.emit()
        mode = str(p.get("view_mode") or "lit")
        if mode != self.view_mode and mode in VIEW_MODES:
            self.view_mode = mode
            self._apply_view_mode()
        environment = environment_of(p.get("environment", "stage"))
        sky, hdri = sky_of(p.get("sky")), hdri_of(p.get("hdri"))
        controls, ground = SkyControls.of(p), bool(p.get("ground", False))
        choice = (environment, sky, hdri, ground)
        if choice != (self.environment, self.sky, self.hdri, self.ground):
            self.environment, self.sky, self.hdri, self.ground = choice
            self.sky_controls = controls
            self.bridge.environmentChanged.emit()
            self._apply_environment()
        elif controls != self.sky_controls:
            self.sky_controls = controls
            self.bridge.environmentChanged.emit()
            self._send_sky_controls()
        if initial:
            return
        sel = p.get("selected")
        if sel != before.get("selected") and sel is not None and str(sel) in self.keys:
            self._show(self.keys.index(str(sel)))

    # -- pause / resume (culled views) ---------------------------------------------------
    def pause(self) -> None:
        """Off screen or collapsed: the source stops stepping, and is killed after
        ``idle_kill_s`` paused (its VRAM freed)."""
        super().pause()
        self.camera.stop()
        self._was_playing = self.playing
        self._halt()
        self._send("pause")
        if self.source is not None:
            self._idle.start(int(self.idle_kill_s * 1000))

    def resume(self) -> None:
        super().resume()
        self._idle.stop()
        if self._was_playing:
            self._was_playing = False
            self.toggle_play()
        if self.source is not None:
            self._send("resume")
        elif self.path is not None:
            # after the registry has put the old health back: the relaunch's phases show
            QTimer.singleShot(0, self._relaunch)

    def _relaunch(self) -> None:
        if self.disposed or self.host_paused or self.source is not None or self.path is None:
            return
        self._spawn()

    @Slot()
    def _idle_kill(self) -> None:
        if self.host_paused and self.source is not None:
            self._close_source()
            self.killed = True

    # -- snap ----------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        src = self.source
        source: dict[str, Any] = {"alive": src is not None, "killed": self.killed}
        if src is not None:
            source["pid"] = src.proc.pid
            if self.ready is not None:
                with contextlib.suppress(SourceError):
                    source.update(src.call("state", timeout=2.0))
        frame = self.frame_item
        return {
            **super().snapshot(),
            "count": len(self.items),
            "selected": {"index": self.index, "key": self.key},
            "path": self.path,
            "renderer": str(self.stage.get("renderer", "")),
            "renderer_note": str(self.stage.get("renderer_note", "")),
            "fallback_reason": self.fallback,
            "load_timings": dict(self.stage.get("load_timings") or {}),
            "renderer_warnings": list(self.stage.get("warnings") or []),
            "up_axis": self.stage.get("up_axis"),
            "bounds": self.stage.get("bounds"),
            "prims": self.stage.get("prims"),
            "camera_speed": self.camera_speed,
            "camera_mode": self.camera_mode,
            "panels": {name: self.panel_state(name) for name in PANELS},
            "outliner": {
                "rows": len(self.outliner_rows()),
                "filter": self.tree_filter,
                "revealed": self.revealed,
            },
            "selection": {"paths": list(self.selected), "hit": self.hit},
            "view_mode": self.view_mode_shown,
            "view_modes": self.offered_modes(),
            "view_mode_note": self.view_mode_note,
            "environment": self.environment_shown,
            "environment_note": self.environment_words,
            "sky": self.sky,
            "hdri": self.hdri,
            "sky_controls": self.sky_values(),
            "sky_controls_offered": list(self.sky_controls_offered()),
            "ground": self.ground,
            "sky_fetch": self.sky_fetch_state(),
            "hdri_fetch": self.hdri_fetch_state(),
            "time": self.time_snapshot(),
            "source": source,
            "failure": self.failure,
            "cover": self.cover,
            "frame": {
                "seq": int(frame.property("frameSeq")),
                "size": [int(frame.property("frameWidth")), int(frame.property("frameHeight"))],
                "backend": str(frame.property("backend")),
            }
            if frame is not None
            else None,
        }

    def dispose(self) -> None:
        self._poll.stop()
        self._idle.stop()
        self._play.stop()
        self._camera_out.stop()
        self._controls_out.stop()
        src, self.source = self.source, None
        if src is not None:
            self._closing.add(src.proc.pid)
            src.close()
        self.frame_item = None
        super().dispose()


class UsdFactory:
    type = "usd"
    plugin = None  # core declares the usd view type (#135)

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> UsdHost:
        from specto_app.usd.camera import NavCamera

        return UsdHost(
            ctx,
            view_id,
            manifest,
            camera_factory=NavCamera,
        )


def factory() -> UsdFactory:
    return UsdFactory()


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(UsdFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "CAMERA_MODES",
    "ENVIRONMENTS",
    "IDLE_KILL_S",
    "ORBIT_KEYS",
    "USD_MANIFEST",
    "USD_QML",
    "CameraControl",
    "PlaceholderCamera",
    "UsdBridge",
    "UsdFactory",
    "UsdHost",
    "cover_words",
    "exit_reason",
    "factory",
    "frame_size",
    "mode_of",
    "register",
    "rtx_launcher",
    "source_command",
    "stub_launcher",
    "usd_part",
]
