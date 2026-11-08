"""The RTX renderer of ``specto-usd-source`` (#137): NVIDIA ovrtx 0.6 over an ovstage 0.3
scene store, as the spike (#134) drove them.

The stage is never written. ovstage is populated from an **inline root layer** that
sublayers the file unchanged, copies its ``metersPerUnit`` and ``upAxis`` (stage metrics
come from the root layer; without them ovrtx assumes centimetres) and adds only the
viewer's ``/__specto/Camera`` and the ``/Render/__specto`` product with one
``LdrColor`` var, rendered by RT2 with DLSS performance (the setting that passes the
30 fps bar at 1080p; the defaults run at 17-19 fps).

At runtime the camera moves through ``omni:xform`` (a float64 row-vector matrix), the
lens through ``focalLength``, ``horizontalAperture`` and ``clippingRange``, the frame
size through the product's ``resolution``; each write takes the next ordinal, and the
step after it syncs through that ordinal. A camera cut bumps the product's history-reset
request id in the same commit: ``Renderer.reset`` would also do, but stalls about a
second per cut on the depot world.

Time (#141) is two clocks: USD time through ovstage's ``update_from_usd_time`` (seconds,
through the inline root's ``timeCodesPerSecond``, copied from the file) re-evaluates the
time-sampled attributes; the simulation clock is each step's ``delta_time``.

View modes (#153) are product attributes written at runtime, named as ovrtx's own render
settings schema names them (``bin/usd_plugins/rtx_settings``; the carb-style names are
ignored): unlit is the albedo debug view (``omni:rtx:debug:view:target =
"diffuseReflectance"``: base colour, no lights or shadows, black behind), wireframe the
``shaded`` wireframe mode under that debug view (wires in their base colour over black),
1 px wide (the default 0.3 px is narrower than a pixel and draws dashed). Every mode
but lit renders at native resolution (``rtxaa``): under DLSS performance the debug view
runs at half resolution, its edges jagged; lit goes back to DLSS. Collision adds
:mod:`.collision`'s overlay as an inline reference at its root, hides the stage's gprims
(a ``visibility`` write, put back after) and shows the overlay's baked tint through the
albedo view: it needs no lights. A switch is one commit, with a history reset; nothing
is reloaded.

Lighting (#148): RTX reads UsdLux intensities on its own photometric scale, under a fixed
camera exposure (f/5, ISO 100, 1 s): a DomeLight or SphereLight at USD's default
intensity 1 renders black, a DistantLight at 1 burns out. Lights authored in Kit are on
that scale (a Kit world, the worlds' sky): a stage lit by them alone keeps it. A stage
with any other light gets auto exposure on the viewer camera (:data:`AUTO_EXPOSURE`),
and a stage without lights a uniform dome light at Kit's default 1000 under
``/__specto`` (:class:`.stage.Lighting`). Nothing is authored over the stage's own prims.
A DistantLight at USD's default still burns out what it lights when the frame is mostly
background.

The environment light (SPEC-31, :mod:`.environment`) is a reference added at runtime under
``/__specto/Environment`` (``add_usd_reference_from_string``, as the collision overlay
is), swapped in one commit with a history reset; nothing is reloaded. While it lights a
stage without lights of its own, the fallback dome is hidden (a ``visibility`` write on
the viewer's own prim) and auto exposure is turned off (a bool write on the camera): the
sky and the HDRI dome are on Kit's scale. Both go back with the Stage choice.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import ovrtx
import ovstage

from .collision import ROOT as COLLISION_ROOT
from .environment import ROOT as ENVIRONMENT_ROOT
from .environment import SHADER as SKY_SHADER
from .environment import SUN as SKY_SUN
from .environment import Assets, SkyControls
from .environment import usda as environment_usda

if TYPE_CHECKING:
    from .stage import Lighting

CAMERA = "/__specto/Camera"
FALLBACK_PATH = "/__specto/Light"  # the fallback dome (#148), the viewer's own prim
AUTO_EXPOSURE_ATTR = "omni:rtx:autoExposure:enabled"
PRODUCT = "/Render/__specto"
LDR = f"{PRODUCT}/LdrColor"
DLSS_MODE = "performance"
#: the product attributes a view mode sets (#153), by ovrtx's render settings schema
DLSS = "omni:rtx:post:dlss:execMode"
WIREFRAME = "omni:rtx:wireframe:mode"
DEBUG_VIEW = "omni:rtx:debug:view:target"
ALBEDO = "diffuseReflectance"
WIRE_WIDTH = "omni:rtx:wireframe:thickness"
WIRE_PX = 1.0  # the default 0.3 px draws dashed lines
NATIVE = "rtxaa"  # no upscaling: the debug view at the frame's own resolution
MODE_SETTINGS = {
    "lit": {WIREFRAME: "instance", DEBUG_VIEW: "", DLSS: DLSS_MODE},
    "unlit": {WIREFRAME: "instance", DEBUG_VIEW: ALBEDO, DLSS: NATIVE},
    "wireframe": {WIREFRAME: "shaded", DEBUG_VIEW: ALBEDO, DLSS: NATIVE},
    "collision": {WIREFRAME: "instance", DEBUG_VIEW: ALBEDO, DLSS: NATIVE},
}
#: the product's one-shot render-history reset: bump the request id, every tile resets
HISTORY_RESET = "omni:rtx:viewTile:renderHistoryReset:requestId"
DELTA_TIME = 1 / 60
OUTLINE_GROUP = 1  # the selection's outline group (0: not outlined)
STAGE_NAME = "specto.usd.source"
#: ``--texture-streaming`` (#155): ovrtx's modes besides its asynchronous default
STREAMING = {"sync": "SYNCHRONOUS", "off": "DISABLE"}
#: a stage not lit for RTX (#148): auto exposure on the viewer camera. Unclamped: the
#: default 50-nit floor keeps a unit dome black. Averaged: the median of a frame mostly
#: background is black, and it burns out what is lit.
AUTO_EXPOSURE = (
    "        bool omni:rtx:autoExposure:enabled = 1\n"
    "        bool omni:rtx:autoExposure:histogramClamping:enabled = 0\n"
    '        token omni:rtx:autoExposure:histogramFilter = "average"\n'
)
#: a stage without lights (#148): a uniform white dome at Kit's default intensity
FALLBACK_LIGHT = """
    def DomeLight "Light"
    {
        float inputs:intensity = 1000
    }
"""

#: Camera values as :func:`specto_app.usd.nav.usd_camera` makes them
UsdCamera = dict[str, Any]


def _rows(matrix) -> str:
    return ", ".join("(" + ", ".join(repr(float(v)) for v in row) + ")" for row in matrix)


def inline_root(
    path: str,
    up_axis: str,
    meters_per_unit: float,
    camera: UsdCamera,
    width: int,
    height: int,
    time_codes: tuple[float, float, float] | None = None,
    lighting: Lighting | None = None,
) -> str:
    """The root layer ovstage opens: the file as a sublayer plus the viewer's camera and
    render product. ``time_codes`` (start, end, timeCodesPerSecond) are the file's: USD
    time goes to ovstage in seconds, through the root layer's timeCodesPerSecond.
    ``lighting`` (#148): auto exposure and a fallback dome as the stage needs them
    (None: neither)."""
    near, far = camera["clipping_range"]
    sublayer = Path(path).as_posix()
    timing = ""
    if time_codes is not None:
        start, end, tcps = (float(v) for v in time_codes)
        timing = (
            f"    startTimeCode = {start!r}\n    endTimeCode = {end!r}\n"
            f"    timeCodesPerSecond = {tcps!r}\n"
        )
    exposure = AUTO_EXPOSURE if lighting is not None and lighting.auto_exposure else ""
    light = FALLBACK_LIGHT if lighting is not None and lighting.fallback else ""
    return f"""#usda 1.0
(
    metersPerUnit = {float(meters_per_unit)!r}
    upAxis = "{up_axis}"
{timing}    subLayers = [@{sublayer}@]
)

def Scope "__specto"
{{
    def Camera "Camera"
    {{
{exposure}        float focalLength = {float(camera["focal_length"])!r}
        float horizontalAperture = {float(camera["horizontal_aperture"])!r}
        float verticalAperture = {float(camera["vertical_aperture"])!r}
        float2 clippingRange = ({float(near)!r}, {float(far)!r})
        matrix4d xformOp:transform = ({_rows(camera["transform"])})
        uniform token[] xformOpOrder = ["xformOp:transform"]
    }}
{light}}}

def "Render"
{{
    def RenderProduct "__specto"
    {{
        int2 resolution = ({int(width)}, {int(height)})
        uint[] deviceIds = [0]
        rel camera = <{CAMERA}>
        rel orderedVars = [<LdrColor>]
        token {DLSS} = "{DLSS_MODE}"
        token {WIREFRAME} = "instance"
        string {DEBUG_VIEW} = ""
        float {WIRE_WIDTH} = {WIRE_PX!r}
        float[] omni:rtx:viewTile:renderHistoryReset:tileIndices = [-1]
        int {HISTORY_RESET} = 0

        def RenderVar "LdrColor"
        {{
            string sourceName = "LdrColor"
        }}
    }}
}}
"""


class RtxRenderer:
    """One ovrtx renderer with one attached ovstage stage. Used from one thread."""

    name = "rtx"
    renders_mdl = True  # the sky's MDL compiles here (SPEC-31)

    def __init__(self, log_file: str, streaming: str = "async") -> None:
        kw = {}
        if streaming != "async":  # ovrtx's default (#155)
            kw["texture_streaming_mode"] = getattr(ovrtx.TextureStreamingMode, STREAMING[streaming])
        cfg = ovrtx.RendererConfig(log_file_path=log_file, enable_crash_reporter=False, **kw)
        self.renderer = ovrtx.Renderer(config=cfg)
        self.stage: Any = None
        self.ordinal = 0
        self._lens: dict[str, Any] = {}
        self._reset_id = 0
        self._size = (1, 1)
        self._meters_per_unit = 1.0
        self._outlined: list[str] = []
        self._outline_color: str | None = None
        self._settings: dict[str, str] = {}  # the view mode's product attributes, as written
        self._overlay: Any = None  # the collision overlay shown, and its reference handle
        self._overlay_handle: Any = None
        self._lighting: Lighting | None = None  # the loaded stage's (#148)
        self._environment = "stage"  # the environment shown (SPEC-31), and its reference
        self._environment_text = ""  # the reference's layer, as added
        self._environment_handle: Any = None
        self._auto_exposure = False  # the camera's auto exposure, as written

    # -- the stage -----------------------------------------------------------------------
    def load(
        self,
        path: str,
        up_axis: str,
        meters_per_unit: float,
        camera: UsdCamera,
        width: int,
        height: int,
        time_codes: tuple[float, float, float] | None = None,
        lighting: Lighting | None = None,
    ) -> None:
        had_stage = self.stage is not None
        self._drop_stage()
        if had_stage:
            self._clear_runtime_stage()  # a second stage in this renderer (#155, SPEC-64)
        self._size = (int(width), int(height))
        self._meters_per_unit = float(meters_per_unit) or 1.0
        self._outlined = []
        self._settings = dict(MODE_SETTINGS["lit"])
        self._overlay = self._overlay_handle = None
        self._lighting = lighting
        self._environment, self._environment_handle = "stage", None
        self._environment_text = ""
        self._auto_exposure = lighting is not None and lighting.auto_exposure
        self.stage = ovstage.Stage(STAGE_NAME)
        self.renderer.attach_ovstage(self.stage)
        self.ordinal = 1
        text = inline_root(
            path, up_axis, meters_per_unit, camera, width, height, time_codes, lighting
        )
        ovstage.population.open_usd_from_string(self.stage, text, ordinal=self.ordinal)
        self.stage.advance_write_floor(self.ordinal, ovstage.Scope.ALL).wait()
        paths = ovstage.PathDictionary(self.stage)
        self._paths = paths
        self._camera_q = self.stage.query_from_path_list(
            paths.create_path_list_from_strings([CAMERA])
        )
        self._product_q = self.stage.query_from_path_list(
            paths.create_path_list_from_strings([PRODUCT])
        )
        self._token = paths.intern_token
        self._reset_id = 0
        self._lens = {
            "focalLength": float(camera["focal_length"]),
            "horizontalAperture": float(camera["horizontal_aperture"]),
            "clippingRange": tuple(float(v) for v in camera["clipping_range"]),
        }

    def _drop_stage(self) -> None:
        if self.stage is None:
            return
        self.renderer.detach_ovstage()
        self.stage.destroy()
        self.stage = None

    def _clear_runtime_stage(self) -> None:
        """Clear the renderer's runtime stage between two stages (SPEC-64). A stage
        attached to a renderer that had already shown one crashed with GPU page faults
        a few seconds in (#155), which is why each stage got a fresh renderer, 1.0-1.4 s
        a switch; with the runtime stage cleared first, 59 switches of mixed stages ran
        clean (SPEC-64's soak). ovrtx 0.6 marks ``reset_stage`` deprecated in favour of
        the ovstage path, which is the path this is (detach, destroy, attach a new
        ovstage); the clear is what that path lacks, so the warning is not news here."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.renderer.reset_stage()

    @staticmethod
    def stage_note(lighting: Lighting, environment: str = "stage") -> str:
        """What the view says under the readout about how the stage is lit (#148); nothing
        while an environment light (SPEC-31) lights it."""
        return lighting.note_with(environment != "stage")

    # -- the environment light (SPEC-31) -----------------------------------------------------
    def set_environment(
        self,
        environment: str,
        up_axis: str,
        assets: Assets,
        controls: SkyControls | None = None,
        authored: Mapping[str, Any] | None = None,
        *,
        textured: bool = False,
        ground: bool = False,
    ) -> None:
        """Show ``environment`` (``stage``, ``sky`` or ``hdri``, as :func:`.environment.
        resolve` answered) from the next step: the reference under ``/__specto/Environment``
        swapped (another sky, or the ground toggled, is another reference), the fallback
        dome hidden or shown and the camera's auto exposure set for the stage's own lights
        under it (the textured sky is brighter than Kit's scale: auto exposure), in one
        commit with a history reset. ``controls`` and ``authored`` place the sky's sun and
        set its inputs as the reference is added; ``ground`` brings the preview floor."""
        text = environment_usda(
            environment,
            up_axis,
            assets,
            controls,
            authored,
            textured=textured,
            ground=ground,
            meters_per_unit=self._meters_per_unit,
        )
        if (environment, text) == (self._environment, self._environment_text):
            return
        writes: list[Callable[[], None]] = []
        if self._environment_handle is not None:
            writes.append(self._remove_environment)
        if text:
            writes.append(lambda: self._add_environment(text))
        self._environment_text = text
        lit = environment != "stage"
        lighting = self._lighting
        if lighting is not None:
            if lighting.fallback:
                value = "invisible" if lit else "inherited"
                writes.append(
                    lambda: self._write_tokens(self._query([FALLBACK_PATH]), "visibility", [value])
                )
            auto = lighting.auto_exposure_with(lit) or (environment == "sky" and textured)
            if auto != self._auto_exposure:
                self._auto_exposure = auto
                flag = np.asarray([[auto]], np.bool_)
                writes.append(lambda: self._write(self._camera_q, AUTO_EXPOSURE_ATTR, flag, 1))
        writes.append(self._history_reset())
        self._commit(writes)
        self._environment = environment

    def set_sky_controls(self, inputs: Mapping[str, Any], sun: list[list[float]]) -> None:
        """The sky's controls, live (SPEC-31): ``inputs`` (:func:`.environment.shader_inputs`)
        written on the sky's shader prim, floats and bools, and the viewer's sun turned
        to ``sun`` (a world transform, :func:`.environment.sun_matrix`), in one commit
        with a history reset; nothing while no sky shows."""
        if self._environment != "sky" or self._environment_handle is None:
            return
        shader = self._query([SKY_SHADER])
        writes: list[Callable[[], None]] = []
        for name, value in inputs.items():
            array = np.asarray([[value]], np.bool_ if isinstance(value, bool) else np.float32)
            writes.append(lambda a=array, n=name: self._write(shader, f"inputs:{n}", a, 1))
        matrix = np.asarray(sun, np.float64).reshape(1, 4, 4)
        writes.append(
            lambda: self._write(
                self._query([SKY_SUN]), "omni:xform", matrix, 16, ovstage.AttributeSemantic.MATRIX
            )
        )
        writes.append(self._history_reset())
        self._commit(writes)

    def _add_environment(self, text: str) -> None:
        self._environment_handle = ovstage.population.add_usd_reference_from_string(
            self.stage, text, ENVIRONMENT_ROOT
        )
        ovstage.population.apply_usd_changes(self.stage, self.ordinal)

    def _remove_environment(self) -> None:
        handle, self._environment_handle = self._environment_handle, None
        ovstage.population.remove_usd(self.stage, handle)
        ovstage.population.apply_usd_changes(self.stage, self.ordinal)

    # -- writes --------------------------------------------------------------------------
    def _write(self, query, attr: str, array: np.ndarray, lanes: int, semantic=None) -> None:
        kw = {"semantic": semantic} if semantic is not None else {}
        tensor = ovstage.make_dltensor(
            array, dtype=ovstage.numpy_to_dldatatype(array.dtype, lanes=lanes), shape=[1], ndim=1
        )
        self.stage.write_attribute(
            query, self._token(attr), ordinal=self.ordinal, tensors=tensor, is_array=False, **kw
        )

    def _query(self, prims: list[str]) -> Any:
        return self.stage.query_from_path_list(self._paths.create_path_list_from_strings(prims))

    def _write_tokens(self, query, attr: str, values: list[str]) -> None:
        """A token attribute, one value a prim of ``query``."""
        ids = np.asarray([self._token(v) for v in values], np.uint64)
        tensor = ovstage.make_dltensor(
            ids, dtype=ovstage.numpy_to_dldatatype(ids.dtype, lanes=1), shape=[len(ids)], ndim=1
        )
        self.stage.write_attribute(
            query, self._token(attr), ordinal=self.ordinal, tensors=tensor, is_array=False,
            semantic=ovstage.AttributeSemantic.TOKEN_ID,
        ).wait()  # fmt: skip

    def _write_string(self, query, attr: str, value: str) -> None:
        """A string attribute: its UTF-8 bytes as one ragged row."""
        data = np.frombuffer(value.encode("utf-8"), np.uint8).copy()
        self.stage.write_attribute(
            query, self._token(attr), ordinal=self.ordinal, tensors=[data], is_array=True,
            semantic=ovstage.AttributeSemantic.STRING,
        ).wait()  # fmt: skip

    def _commit(self, writes: list[Callable[[], None]]) -> None:
        if not writes:
            return
        self.ordinal += 1
        for write in writes:
            write()
        self.stage.advance_write_floor(self.ordinal, ovstage.Scope.ALL).wait()

    def set_camera(self, camera: UsdCamera, cut: bool = False) -> None:
        """Move the viewer's camera; the lens attributes are written only when they change.
        A ``cut`` (a jump) also drops the temporal history (TAA, DLSS) instead of ghosting."""
        matrix = np.asarray(camera["transform"], np.float64).reshape(1, 4, 4)
        writes = [
            lambda: self._write(
                self._camera_q, "omni:xform", matrix, 16, ovstage.AttributeSemantic.MATRIX
            )
        ]
        writes += self._lens_writes(camera)
        if cut:
            writes.append(self._history_reset())
        self._commit(writes)

    def set_time(self, seconds: float, cut: bool = False) -> None:
        """USD time (#141): every time-sampled attribute re-evaluated at ``seconds``
        (ovstage converts through the inline root's timeCodesPerSecond) at the next
        ordinal. A ``cut`` (a jump, a step back) drops the render history, as a camera
        cut does; the simulation clock is the step's ``delta_time``."""
        writes = [
            lambda: ovstage.population.update_from_usd_time_async(
                self.stage, self.ordinal, float(seconds)
            ).wait()
        ]
        if cut:
            writes.append(self._history_reset())
        self._commit(writes)

    def _history_reset(self) -> Callable[[], None]:
        self._reset_id += 1
        request = np.asarray([[self._reset_id]], np.int32)
        return lambda: self._write(self._product_q, HISTORY_RESET, request, 1)

    def _lens_writes(self, camera: UsdCamera) -> list[Callable[[], None]]:
        lens = {
            "focalLength": float(camera["focal_length"]),
            "horizontalAperture": float(camera["horizontal_aperture"]),
            "clippingRange": tuple(float(v) for v in camera["clipping_range"]),
        }
        writes = []
        for attr, value in lens.items():
            if self._lens.get(attr) == value:
                continue
            self._lens[attr] = value
            array = np.asarray([value], np.float32).reshape(1, -1)
            writes.append(lambda a=attr, v=array: self._write(self._camera_q, a, v, v.shape[1]))
        return writes

    # -- view modes (#153) ---------------------------------------------------------------
    def set_view_mode(self, mode: str, overlay: Any = None) -> str:
        """Show ``mode`` from the next step: its product attributes (only those that
        change) and, into or out of collision, the overlay. ``overlay``: the stage's
        :class:`.collision.CollisionOverlay`, for collision."""
        writes: list[Callable[[], None]] = []
        if mode == "collision" and self._overlay is None:
            writes.append(lambda: self._show_overlay(overlay))
        elif mode != "collision" and self._overlay is not None:
            writes.append(self._hide_overlay)
        for attr, value in MODE_SETTINGS[mode].items():
            if self._settings.get(attr) == value:
                continue
            self._settings[attr] = value
            if attr == DEBUG_VIEW:
                writes.append(lambda a=attr, v=value: self._write_string(self._product_q, a, v))
            else:
                writes.append(lambda a=attr, v=value: self._write_tokens(self._product_q, a, [v]))
        writes.append(self._history_reset())
        self._commit(writes)
        return ""

    def _show_overlay(self, overlay: Any) -> None:
        self._overlay_handle = ovstage.population.add_usd_reference_from_string(
            self.stage, overlay.usda(), COLLISION_ROOT
        )
        ovstage.population.apply_usd_changes(self.stage, self.ordinal)
        self._overlay = overlay
        hidden = list(overlay.hidden)
        if hidden:
            self._write_tokens(self._query(hidden), "visibility", ["invisible"] * len(hidden))

    def _hide_overlay(self) -> None:
        overlay, self._overlay = self._overlay, None
        ovstage.population.remove_usd(self.stage, self._overlay_handle)
        self._overlay_handle = None
        ovstage.population.apply_usd_changes(self.stage, self.ordinal)
        by_value: dict[str, list[str]] = {}
        for path, value in overlay.hidden.items():
            by_value.setdefault(value, []).append(path)
        for value, prims in by_value.items():  # each gprim's own visibility again
            self._write_tokens(self._query(prims), "visibility", [value] * len(prims))

    def resize(self, width: int, height: int, camera: UsdCamera) -> None:
        """New frame size (and the aperture for its aspect); the next step renders it."""
        self._size = (int(width), int(height))
        size = np.asarray([[width, height]], np.int32)
        writes = [lambda: self._write(self._product_q, "resolution", size, 2)]
        writes += self._lens_writes(camera)
        self._commit(writes)

    # -- frames --------------------------------------------------------------------------
    def submit(self, delta_time: float | None = None) -> Any:
        """Start one step that advances the simulation clock by ``delta_time`` seconds
        (None: ``DELTA_TIME``); :meth:`finish` waits for it."""
        op = self.renderer.step_async(
            render_products={PRODUCT},
            delta_time=DELTA_TIME if delta_time is None else delta_time,
            ordinal=self.ordinal,
        )
        return op

    def finish(self, op: Any, sink: Callable[[np.ndarray], None]) -> None:
        """Wait for a step and hand its ``LdrColor`` (H, W, 4 uint8) to ``sink`` while
        it is mapped."""
        products = op.wait().fetch()
        var = products[PRODUCT].frames[-1].render_vars[LDR].map(device=ovrtx.Device.CPU)
        try:
            sink(np.from_dlpack(var))
        finally:
            var.unmap()

    # -- selection (#140) ----------------------------------------------------------------
    def pick(self, x: float, y: float) -> dict[str, Any] | None:
        """The prim under NDC ``(x, y)`` (-1..1, +y up; the protocol's, #143): a one-pixel
        pick query (ovrtx's NDC is [0, 1] from the top left) rides on one extra step after
        those in flight. ``{path, position, normal}``, the position in the stage's units,
        or None over the background. Under the debug view or the wireframe (unlit,
        wireframe, collision, #153) ovrtx picks nothing: the pick lifts both for a step
        before it and its own, then puts them back with a render history reset."""
        debug, wire = self._settings.get(DEBUG_VIEW), self._settings.get(WIREFRAME)
        if not debug and wire == "instance":
            return self._pick(x, y)

        def view(target: str, mode: str) -> list[Callable[[], None]]:
            return [
                lambda: self._write_string(self._product_q, DEBUG_VIEW, target),
                lambda: self._write_tokens(self._product_q, WIREFRAME, [mode]),
            ]

        self._commit(view("", "instance"))
        try:
            self.submit().wait()  # the settings take a step to arrive
            return self._pick(x, y)
        finally:
            self._commit([*view(debug or "", wire or "instance"), self._history_reset()])

    def _pick(self, x: float, y: float) -> dict[str, Any] | None:
        w, h = self._size
        px = min(w - 1, max(0, int((x + 1) / 2 * w)))
        py = min(h - 1, max(0, int((1 - y) / 2 * h)))
        self.renderer.enqueue_pick_query(
            PRODUCT, px / w, py / h, (px + 1) / w, (py + 1) / h,
            flags=ovrtx.OVRTX_PICK_FLAG_INCLUDE_TRACKED_INFO,
        )  # fmt: skip
        products = self.submit().wait().fetch()  # held: its frames' outputs live with it
        frame = products[PRODUCT].frames[-1]
        hits = frame.render_vars[ovrtx.OVRTX_RENDER_VAR_PICK_HIT].map(device=ovrtx.Device.CPU)
        try:
            if int(np.from_dlpack(hits.params["hitCount"]).reshape(-1)[0]) < 1:
                return None
            path_id = int(np.from_dlpack(hits["primPath"]).reshape(-1)[0])
            position = np.from_dlpack(hits["worldPositionM"]).reshape(-1, 3)[0]
            normal = np.from_dlpack(hits["worldNormal"]).reshape(-1, 3)[0]
            return {
                "path": self.renderer.resolve_prim_path_id(path_id),
                "position": [float(v) / self._meters_per_unit for v in position],
                "normal": [float(v) for v in normal],
            }
        finally:
            hits.unmap()

    def outline(self, paths: list[str], color: str) -> None:
        """ovrtx's selection outline on exactly ``paths`` (gprims), in ``OUTLINE_GROUP``
        coloured ``color`` (``#rrggbb``); the prims outlined before and not now go back
        to group 0. Takes effect with the next step."""
        if color != self._outline_color:
            rgba = (*(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)), 1.0)
            style = ovrtx.SelectionGroupStyle(outline_color=rgba, fill_color=(0.0, 0.0, 0.0, 0.0))
            self.renderer.set_selection_group_styles({OUTLINE_GROUP: style})
            self._outline_color = color
        keep = set(paths)
        cleared = [p for p in self._outlined if p not in keep]
        if cleared:
            self.renderer.set_selection_outline_group_strings(cleared, 0)
        if paths:
            self.renderer.set_selection_outline_group_strings(list(paths), OUTLINE_GROUP)
        self._outlined = list(paths)

    def close(self) -> None:
        try:
            self._drop_stage()
        finally:
            self.renderer.destroy()
