"""The usd view's environment light (SPEC-31): what lights a stage beyond its own
lights, ``params.environment``, with the sky and the HDRI it names, the sky's controls and
its ground.

- ``stage``: the stage's own lights, with rtx's fallback dome for a stage without any
  (#148): today's behaviour;
- ``sky``: one of NVIDIA's dynamic skies (:mod:`.skies`: ``params.sky``, ``ClearSky`` by
  default), an MDL dome light with a sun, fetched on first use into the user's folder
  and referenced from its ``.usdz``. Only RTX compiles MDL: Storm shows the HDRI instead
  and says so. The sky's controls (:class:`SkyControls`: ``params.time_of_day``,
  ``cloudiness``, ``haze``) drive it live: the time of day moves the sun (the dome's,
  through the material's inputs, and a ``DistantLight`` of the viewer's placed by the
  material's own maths, :func:`sun_direction`, in place of the sky's rig light),
  cloudiness sets the clouds' cover, haze the atmosphere's haze. The procedural skies
  take all three; the textured CloudySky takes the time of day (its material places the
  sun by ``Elevation`` and ``Azimuth``, which the viewer computes) and the cloudiness (its
  ``CloudBias``), and has no haze. ``params.ground`` brings in the floor of the preview
  scene every sky stage ships beside it (``Sky_Elements/PreviewScene.usda``: a 10 x 20 m
  floor whose top is at height 0, scaled to the stage's units, its spheres left inactive);
- ``hdri``: a ``DomeLight`` with one of the 3d view's HDRIs (``params.hdri``, ``sunny``
  by default; Poly Haven, CC0), the web build's ``.hdr`` in a source checkout, else
  fetched on first use like the skies (:func:`.skies.fetch_hdri`).

The environment is composed over the stage in the viewer's session and never written to
the file (ADR 0024): under RTX as a reference added to the inline root's ``/__specto``
scope at runtime (:meth:`.rtx.RtxRenderer.set_environment`), the sky's controls as
attribute writes on that reference's prims (:meth:`.rtx.RtxRenderer.set_sky_controls`);
under Storm as a sublayer of the session layer (:func:`author`), beside the stage's own
lights. Both put it at :data:`ROOT`, turned onto the stage's up axis
(:func:`rotation_x`): the sky is authored Z-up, and each renderer keeps a dome's pole
along its own axis whatever the stage's up axis (measured 2026-11-03: ovrtx +Z, Storm +Y),
so a Y-up stage turns the sky and ovrtx's dome by -90 degrees about X, and a Z-up stage
turns Storm's dome by +90. The HDRI dome's intensity is each renderer's own too: Kit's
default 1000 under RTX, the scale its fixed exposure is set for (a stage lit by an
environment alone keeps that exposure, :meth:`.stage.Lighting.auto_exposure_with`; the
textured sky is brighter and keeps auto exposure); 0.5 under Storm, which has no tone
mapping.

:func:`resolve` decides what shows for a choice: a sky without MDL (Storm) or not fetched
yet falls back to the HDRI, an HDRI not there yet to the stage's own light, each with a
note the panel and ``snap`` show. The GUI process imports this module too (the host reads
the names): nothing here imports pxr at module level.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import specto_web

from . import skies

ENVIRONMENTS = ("stage", "sky", "hdri")
LABELS = {"stage": "Stage", "sky": "Sky", "hdri": "HDRI"}
#: the 3d view's HDRIs (``web/3d/environments.json``), name -> label, and the default
HDRIS: dict[str, str] = {
    str(e["name"]): str(e.get("label") or e["name"]) for e in specto_web.environments()
}
DEFAULT_HDRI = "sunny" if "sunny" in HDRIS else next(iter(HDRIS), "")
#: where both renderers put the environment: a prim under the viewer's own scope
ROOT = "/__specto/Environment"
#: the sky's prims under :data:`ROOT` (the sky's ``/World`` becomes the root)
SHADER = f"{ROOT}/Looks/SkyMaterial/Shader"
RIG_LIGHT = f"{ROOT}/AxisNorth/AxisLatitude/AxisSHA/AxisDeclination/DistantLight"
SUN = f"{ROOT}/Sun"  # the viewer's own sun, placed by the time of day
GROUND = f"{ROOT}/Ground"  # the preview scene's floor, when params.ground
#: the preview scene's prims the ground leaves out (its spheres)
GROUND_OFF = ("MetalBalls", "PlasticBalls")
SKY_METERS_PER_UNIT = 0.01  # the skies and their preview scene are authored in cm
#: every sky's /World turns its dome rig by 90 degrees about X (``rotateZYX = (90, 0, 0)``);
#: the ground under it turns back
SKY_ROOT_ROTATE_X = 90.0
SUN_INTENSITY = 5000  # the rig light's, on Kit's scale
SUN_COLOR = (0.7423431, 0.62958664, 0.5119833)  # the rig light's
RTX_DOME_INTENSITY = 1000  # Kit's default dome intensity: RTX's fixed exposure is set for it
STORM_DOME_INTENSITY = 0.5  # Storm has no tone mapping: a mid-grey ground reads grey at this
SKY_UP = "Z"  # the sky is authored Z-up
RTX_DOME_POLE, STORM_DOME_POLE = "Z", "Y"  # where each renderer keeps a dome's pole
STORM_NOTE = "Storm cannot render the sky's MDL: the HDRI instead"
#: the sky's authored coordinates when its files say nothing (ClearSky's)
AUTHORED = {"time_of_day": 6.93, "latitude": 51.426, "longitude": -0.985, "day_of_year": 100.0}
#: the textured sky's CloudBias over the cloudiness slider (its soft range: 3 shows no
#: clouds, -2 the most; its own 0.5 is a cloudiness of 0.5)
CLOUD_BIAS_CLEAR, CLOUD_BIAS_CLOUDY = 3.0, -2.0


def environment_of(value: Any) -> str:
    """An environment from a param: one of :data:`ENVIRONMENTS`, anything else ``stage``."""
    return str(value) if value in ENVIRONMENTS else "stage"


def sky_of(value: Any) -> str:
    """A sky name from a param: one of :data:`.skies.SKIES`, else the default."""
    return str(value) if value in skies.SKIES else skies.DEFAULT_SKY


def hdri_of(value: Any) -> str:
    """An HDRI name from a param: one of :data:`HDRIS`, else the default."""
    return str(value) if value in HDRIS else DEFAULT_HDRI


def _unit(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, v)) if math.isfinite(v) else None


@dataclass(frozen=True)
class SkyControls:
    """The sky's controls, each None where the sky's own authored value stands:
    ``time_of_day`` in hours (0-24), ``cloudiness`` and ``haze`` 0-1. :data:`NAMES` are
    their names (the params, the sliders), :data:`RANGES` their ranges and
    :data:`SLIDER_DEFAULTS` where a slider rests before a sky says its own value."""

    time_of_day: float | None = None
    cloudiness: float | None = None
    haze: float | None = None

    NAMES: ClassVar[tuple[str, ...]] = ("time_of_day", "cloudiness", "haze")
    RANGES: ClassVar[dict[str, tuple[float, float]]] = {
        "time_of_day": (0.0, 24.0),
        "cloudiness": (0.0, 1.0),
        "haze": (0.0, 1.0),
    }
    SLIDER_DEFAULTS: ClassVar[dict[str, float]] = {
        "time_of_day": 12.0,
        "cloudiness": 0.0,
        "haze": 0.0,
    }

    @classmethod
    def of(cls, params: Mapping[str, Any]) -> SkyControls:
        tod = params.get("time_of_day")
        low, high = cls.RANGES["time_of_day"]
        try:
            tod = None if tod is None else max(low, min(high, float(tod)))
        except (TypeError, ValueError):
            tod = None
        return cls(tod, _unit(params.get("cloudiness")), _unit(params.get("haze")))

    def with_value(self, name: str, value: float | None) -> SkyControls:
        """These controls with ``name`` set to ``value`` (rounded past a slider's step
        arithmetic; None: the sky's own), clamped."""
        if name not in self.NAMES:
            raise KeyError(name)
        if value is not None:
            value = round(float(value), 4)
        return SkyControls.of({**self.as_dict(), name: value})

    def as_dict(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in self.NAMES}


def controls_of(sky: str) -> tuple[str, ...]:
    """The controls sky ``sky`` takes: all three for a procedural sky; the textured one
    has no haze."""
    info = skies.SKIES.get(sky)
    if info is not None and info.textured:
        return ("time_of_day", "cloudiness")
    return SkyControls.NAMES


@dataclass(frozen=True)
class Assets:
    """Where the chosen environments' files are on this machine; None: not fetched yet.
    ``sky``: the sky stage's reference path (``<package>[<entry>]``); ``hdri``: the
    ``.hdr`` path, posix; ``ground``: the preview scene's reference path in the sky's
    package."""

    sky: str | None
    hdri: str | None
    ground: str | None = None


def hdri_file(name: str) -> str | None:
    """HDRI ``name``'s ``.hdr``: ``SPECTO_USD_HDRI`` when set (a file, for tests), else
    the web build's cache or the user's fetched copy (:func:`.skies.hdri_path`)."""
    own = os.environ.get("SPECTO_USD_HDRI")
    if own:
        return Path(own).as_posix() if Path(own).is_file() else None
    found = skies.hdri_path(name)
    return found.as_posix() if found is not None else None


def find_assets(sky: str = skies.DEFAULT_SKY, hdri: str = DEFAULT_HDRI) -> Assets:
    sky = sky_of(sky)
    return Assets(
        sky=skies.reference(sky),
        hdri=hdri_file(hdri_of(hdri)),
        ground=skies.preview_reference(sky),
    )


def sky_missing(sky: str) -> str:
    return f"{skies.label(sky_of(sky))} is not fetched yet: the HDRI instead"


def hdri_missing(hdri: str) -> str:
    return f"the {HDRIS.get(hdri, hdri)} HDRI is not fetched yet: the stage's own light"


def resolve(
    environment: str,
    assets: Assets,
    *,
    renders_mdl: bool,
    sky: str = skies.DEFAULT_SKY,
    hdri: str = DEFAULT_HDRI,
) -> tuple[str, str]:
    """What shows for ``environment`` on a renderer that compiles MDL or not, with the
    assets at hand: ``(shown, note)``, the note saying why it is not what was asked."""
    shown, notes = environment_of(environment), []
    if shown == "sky":
        if not renders_mdl:
            notes.append(STORM_NOTE)
            shown = "hdri"
        elif assets.sky is None:
            notes.append(sky_missing(sky))
            shown = "hdri"
    if shown == "hdri" and assets.hdri is None:
        notes.append(hdri_missing(hdri))
        shown = "stage"
    return shown, "; ".join(notes)


def rotation_x(up_axis: str, authored_up: str = SKY_UP) -> float:
    """The turn about X (degrees) that takes something authored with ``authored_up`` as
    its up axis (the sky, a renderer's dome pole) onto the stage's ``up_axis``."""
    have, want = str(authored_up).upper(), str(up_axis).upper()
    if have == want:
        return 0.0
    return -90.0 if (have, want) == ("Z", "Y") else 90.0


# -- the sun, as the sky's material places it ------------------------------------------------
def _r2d(x: float, y: float, turn: float) -> tuple[float, float]:
    a = turn * 2 * math.pi
    return math.cos(a) * x + math.sin(a) * y, math.cos(a) * y - math.sin(a) * x


def sun_angles(time_of_day: float, latitude: float, day_of_year: float) -> tuple[float, float]:
    """``(azimuth, elevation)`` in degrees of the sun at ``time_of_day`` (local hours) on
    ``day_of_year`` at ``latitude``: the sky material's own ``suncalc`` (ProceduralSky.mdl,
    declination from the day of the year), so the viewer's sun and the dome's agree."""
    sha = (time_of_day - 12) * 15
    t = math.radians((360 / 365.25) * (math.floor(day_of_year) + time_of_day / 24))
    decl = -23.45 * math.cos(t + math.radians(10))
    x, y, z = 0.0, -1.0, 0.0
    x, y = _r2d(x, y, decl / 360)
    y, z = _r2d(y, z, (180 - sha) / 360)
    x, y = _r2d(x, y, latitude / 360)
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, y))))
    azimuth = math.degrees(math.atan2(z, -x))
    return azimuth, elevation


def sun_direction(time_of_day: float, latitude: float, day_of_year: float) -> list[float]:
    """The unit vector toward the sun in the sky's own Z-up frame (the material's
    ``SunDirection``, its Y-up frame turned back: x, -z, y)."""
    az, el = sun_angles(time_of_day, latitude, day_of_year)
    x, y, z = 1.0, 0.0, 0.0
    x, y = _r2d(x, y, -el / 360)
    x, z = _r2d(x, z, az / 360)
    return [x, -z, y]


def sun_matrix(direction: list[float], up_axis: str = SKY_UP) -> list[list[float]]:
    """A ``DistantLight``'s transform (row vectors) shining along ``-direction``: its
    local +Z along ``direction``, given in the sky's Z-up frame; ``up_axis`` turns it
    into the stage's frame (a Y-up stage: -90 about X), for a world-space write."""
    b = _normalized(direction)
    if up_axis.upper() == "Y" and SKY_UP == "Z":
        b = [b[0], b[2], -b[1]]  # R_x(-90)
    up = [0.0, 0.0, 1.0] if abs(b[2]) < 0.99 else [0.0, 1.0, 0.0]
    r = _normalized(_cross(up, b))
    u = _cross(b, r)
    return [[*r, 0.0], [*u, 0.0], [*b, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _normalized(v: list[float]) -> list[float]:
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


# -- the sky's controls as the material's inputs -------------------------------------------
def cloud_coverage(cloudiness: float) -> float:
    """``CloudCoverage`` for a 0-1 cloudiness: the material thresholds its noise at
    ``0.5 - CloudCoverage``, so -0.5 is no cloud and 1.0 solid cover (the skies author
    -0.24 light cumulus, 0.01 heavy, 0.74 overcast)."""
    return -0.5 + 1.5 * cloudiness


def cloud_bias(cloudiness: float) -> float:
    """The textured sky's ``CloudBias`` for a 0-1 cloudiness (higher is fewer clouds)."""
    return CLOUD_BIAS_CLEAR + (CLOUD_BIAS_CLOUDY - CLOUD_BIAS_CLEAR) * cloudiness


def haze_value(haze: float) -> float:
    """The material's ``haze`` for a 0-1 haziness: it adds 2 to it and saturates its
    effects by about 17, so 0-20 spans clear to milky (the skies author 0.001)."""
    return 20.0 * haze


def effective(
    controls: SkyControls, authored: Mapping[str, Any], textured: bool = False
) -> dict[str, float]:
    """The controls as shown: each param, or the sky's own authored value (``authored``:
    :func:`.skies.authored`), as 0-1 cloudiness and haziness and hours. The textured sky's
    own cloudiness is its ``CloudBias`` of 0.5; it has no haze (0)."""
    if controls.cloudiness is not None:
        cloudiness = controls.cloudiness
    elif textured:
        cloudiness = (CLOUD_BIAS_CLEAR - 0.5) / (CLOUD_BIAS_CLEAR - CLOUD_BIAS_CLOUDY)
    elif not authored.get("cumulus", True):
        cloudiness = 0.0
    else:
        coverage = float(authored.get("cloud_coverage", 0.3))
        cloudiness = max(0.0, min(1.0, (coverage + 0.5) / 1.5))
    if textured:
        haze = 0.0
    elif controls.haze is not None:
        haze = controls.haze
    else:
        haze = float(authored.get("haze", 0.0)) / 20
    tod = controls.time_of_day
    if tod is None:
        tod = float(authored.get("time_of_day", AUTHORED["time_of_day"]))
    return {"time_of_day": tod, "cloudiness": cloudiness, "haze": max(0.0, min(1.0, haze))}


def _coords(authored: Mapping[str, Any]) -> dict[str, float]:
    keys = ("latitude", "longitude", "day_of_year")
    return {k: float(authored.get(k, AUTHORED[k])) for k in keys}


def shader_inputs(
    controls: SkyControls, authored: Mapping[str, Any], textured: bool = False
) -> dict[str, Any]:
    """The material inputs the controls set on the sky's shader. A procedural sky: the
    sun from the time of day through the material's own maths (``SunPositionFromTOD``,
    the sky's latitude, longitude and day), the coverage and the haze when set. The
    textured sky's material has no sun maths: its ``Elevation`` and ``Azimuth`` are
    computed here by the same maths, and the cloudiness is its ``CloudBias``."""
    coords = _coords(authored)
    shown = effective(controls, authored, textured)
    if textured:
        az, el = sun_angles(shown["time_of_day"], coords["latitude"], coords["day_of_year"])
        out: dict[str, Any] = {"Elevation": el, "Azimuth": az}
        if controls.cloudiness is not None:
            out["CloudsEnabled"] = controls.cloudiness > 0.0
            out["CloudBias"] = cloud_bias(controls.cloudiness)
        return out
    out = {
        "SunPositionFromTOD": True,
        "DeclinationFromDayOfYear": True,
        "LocalTimeOfDay": True,
        "Latitude": coords["latitude"],
        "Longitude": coords["longitude"],
        "DayOfYear": coords["day_of_year"],
        "TimeOfDay": shown["time_of_day"],
    }
    if controls.cloudiness is not None:
        out["CumulusEnabled"] = controls.cloudiness > 0.0
        out["CloudCoverage"] = cloud_coverage(controls.cloudiness)
    if controls.haze is not None:
        out["haze"] = haze_value(controls.haze)
    return out


def sun_for(controls: SkyControls, authored: Mapping[str, Any]) -> list[float]:
    """The sun's direction (the sky's Z-up frame) for the controls."""
    shown = effective(controls, authored)
    coords = _coords(authored)
    return sun_direction(shown["time_of_day"], coords["latitude"], coords["day_of_year"])


def ground_scale(meters_per_unit: float) -> float:
    """The preview floor's scale in a stage of ``meters_per_unit``: its centimetres in
    the stage's units."""
    return SKY_METERS_PER_UNIT / (float(meters_per_unit) or 1.0)


def _usda_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return repr(float(value))


def _xform(up_axis: str, indent: str, authored_up: str = SKY_UP, keep: str = "") -> str:
    deg = rotation_x(up_axis, authored_up)
    if not deg:
        return ""
    order = '"xformOp:rotateX"' + (f", {keep}" if keep else "")
    return (
        f"{indent}float xformOp:rotateX = {deg:g}\n"
        f"{indent}uniform token[] xformOpOrder = [{order}]\n"
    )


def _rows(matrix: list[list[float]]) -> str:
    return ", ".join("(" + ", ".join(repr(float(v)) for v in row) + ")" for row in matrix)


def _ground(reference: str, meters_per_unit: float) -> str:
    """The preview scene's floor under :data:`GROUND`: referenced from the sky's package,
    turned back from the sky root's own rotation (it sits under it, flat on the stage's
    ground plane), scaled to the stage's units, its spheres inactive."""
    s = ground_scale(meters_per_unit)
    offs = "".join(
        f'        over "{name}" (\n            active = false\n        )\n        {{\n        }}\n'
        for name in GROUND_OFF
    )
    return (
        f'    def Xform "Ground" (\n        prepend references = @{reference}@\n    )\n    {{\n'
        f"        float xformOp:rotateX = {-SKY_ROOT_ROTATE_X:g}\n"
        f"        float3 xformOp:scale = ({s!r}, {s!r}, {s!r})\n"
        '        uniform token[] xformOpOrder = ["xformOp:rotateX", "xformOp:scale"]\n'
        f"{offs}    }}\n"
    )


def usda(
    environment: str,
    up_axis: str,
    assets: Assets,
    controls: SkyControls | None = None,
    authored: Mapping[str, Any] | None = None,
    *,
    textured: bool = False,
    ground: bool = False,
    meters_per_unit: float = 1.0,
) -> str:
    """The environment as the layer RTX references at :data:`ROOT` (ovstage's
    ``add_usd_reference_from_string``): the sky as a reference to its package with the
    controls' inputs over its shader, its rig light turned off, the viewer's sun placed
    and, with ``ground``, the preview scene's floor; the HDRI as a dome light; empty for
    ``stage``. ``environment`` is what :func:`resolve` answered, so its asset is there. A
    Y-up stage turns the sky's root after the sky's own rotation (its
    ``xformOp:rotateZYX`` stays in the order)."""
    head = '#usda 1.0\n(\n    defaultPrim = "Environment"\n)\n\n'
    controls = controls or SkyControls()
    if environment == "sky":
        authored = authored or {}
        inputs = shader_inputs(controls, authored, textured)
        pad = " " * 16
        lines = "".join(
            f"{pad}{'bool' if isinstance(v, bool) else 'float'} inputs:{k} = {_usda_value(v)}\n"
            for k, v in inputs.items()
        )
        sun = _rows(sun_matrix(sun_for(controls, authored)))
        keep = '"xformOp:rotateZYX"'
        floor = _ground(assets.ground, meters_per_unit) if ground and assets.ground else ""
        return (
            f'{head}def Xform "Environment" (\n    prepend references = @{assets.sky}@\n)\n{{\n'
            f"{_xform(up_axis, '    ', keep=keep)}"
            '    over "Looks"\n    {\n        over "SkyMaterial"\n        {\n'
            f'            over "Shader"\n            {{\n{lines}            }}\n'
            "        }\n    }\n"
            '    over "AxisNorth"\n    {\n        over "AxisLatitude"\n        {\n'
            '            over "AxisSHA"\n            {\n                over "AxisDeclination"\n'
            '                {\n                    over "DistantLight"\n                    {\n'
            "                        float intensity = 0\n"
            "                        float inputs:intensity = 0\n"
            "                    }\n                }\n            }\n        }\n    }\n"
            '    def DistantLight "Sun"\n    {\n'
            f"        float inputs:intensity = {SUN_INTENSITY}\n"
            f"        color3f inputs:color = {tuple(SUN_COLOR)}\n"
            "        float inputs:angle = 1\n"
            f"        matrix4d xformOp:transform = ({sun})\n"
            '        uniform token[] xformOpOrder = ["xformOp:transform"]\n'
            f"    }}\n{floor}}}\n"
        )
    if environment == "hdri":
        return (
            f'{head}def Xform "Environment"\n{{\n{_xform(up_axis, "    ", RTX_DOME_POLE)}'
            '    def DomeLight "Dome"\n    {\n'
            f"        float inputs:intensity = {RTX_DOME_INTENSITY}\n"
            f"        asset inputs:texture:file = @{assets.hdri}@\n"
            '        token inputs:texture:format = "latlong"\n'
            "    }\n}\n"
        )
    return ""


def author(layer: Any, environment: str, up_axis: str, assets: Assets) -> None:
    """Storm's composition: ``layer`` (a sublayer of the stage's session layer, Storm's
    own) holds the environment: cleared, then for ``hdri`` the dome light under
    :data:`ROOT` turned for the up axis (Storm's pole is +Y), at Storm's intensity. The
    stage's own lights stay as they are: the dome adds to them. ``sky`` never reaches
    here (:func:`resolve` routes it to the HDRI on Storm)."""
    from pxr import Sdf

    layer.Clear()
    if environment != "hdri":
        return
    with Sdf.ChangeBlock():
        root = Sdf.CreatePrimInLayer(layer, ROOT)
        root.specifier = Sdf.SpecifierDef
        root.typeName = "Xform"
        root.nameParent.specifier = Sdf.SpecifierDef
        root.nameParent.typeName = "Scope"
        deg = rotation_x(up_axis, STORM_DOME_POLE)
        if deg:
            rot = Sdf.AttributeSpec(root, "xformOp:rotateX", Sdf.ValueTypeNames.Float)
            rot.default = deg
            order = Sdf.AttributeSpec(
                root, "xformOpOrder", Sdf.ValueTypeNames.TokenArray, Sdf.VariabilityUniform
            )
            order.default = ["xformOp:rotateX"]
        dome = Sdf.CreatePrimInLayer(layer, f"{ROOT}/Dome")
        dome.specifier = Sdf.SpecifierDef
        dome.typeName = "DomeLight"
        Sdf.AttributeSpec(dome, "inputs:intensity", Sdf.ValueTypeNames.Float).default = float(
            STORM_DOME_INTENSITY
        )
        Sdf.AttributeSpec(
            dome, "inputs:texture:file", Sdf.ValueTypeNames.Asset
        ).default = Sdf.AssetPath(assets.hdri or "")
        Sdf.AttributeSpec(
            dome, "inputs:texture:format", Sdf.ValueTypeNames.Token
        ).default = "latlong"


__all__ = [
    "AUTHORED",
    "DEFAULT_HDRI",
    "ENVIRONMENTS",
    "GROUND",
    "HDRIS",
    "LABELS",
    "RIG_LIGHT",
    "ROOT",
    "RTX_DOME_INTENSITY",
    "SHADER",
    "STORM_DOME_INTENSITY",
    "STORM_NOTE",
    "SUN",
    "Assets",
    "SkyControls",
    "author",
    "cloud_bias",
    "cloud_coverage",
    "controls_of",
    "effective",
    "environment_of",
    "find_assets",
    "ground_scale",
    "haze_value",
    "hdri_file",
    "hdri_missing",
    "hdri_of",
    "resolve",
    "rotation_x",
    "shader_inputs",
    "sky_missing",
    "sky_of",
    "sun_angles",
    "sun_direction",
    "sun_for",
    "sun_matrix",
    "usda",
]
