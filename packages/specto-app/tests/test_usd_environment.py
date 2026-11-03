"""The usd view's environment light (SPEC-31), at the composition seam: which choice shows
(``resolve``: the renderer's MDL ability and the fetched assets decide the fallbacks and
their notes), what RTX's inline reference says (``usda``), and the session-layer
composition Storm draws (``author``), built on a tmp stage whose file is never written."""

from __future__ import annotations

from pathlib import Path

import pytest

from specto_app.usd import environment as env
from specto_app.usd.environment import Assets, resolve

SKY = "C:/build/nvidia-sky/clear-sky.usdz[Dynamic/ClearSky.usd]"
HDR = "C:/checkout/packages/specto-web/node_modules/.hdri/kloofendal_48d_partly_cloudy_1k.hdr"
ALL = Assets(sky=SKY, hdri=HDR)
NONE = Assets(sky=None, hdri=None)


def test_the_choices_and_their_labels_as_the_panel_shows_them():
    assert env.ENVIRONMENTS == ("stage", "sky", "hdri")
    assert env.LABELS == {"stage": "Stage", "sky": "Sky", "hdri": "HDRI"}
    assert env.environment_of("sky") == "sky"
    assert env.environment_of("night") == "stage" and env.environment_of(None) == "stage"
    assert env.sky_of("Overcast") == "Overcast" and env.sky_of("Mars") == "ClearSky"
    assert list(env.HDRIS) == ["studio", "overcast", "sunny", "indoor", "urban"]  # the 3d view's
    assert env.hdri_of("urban") == "urban" and env.hdri_of(None) == "sunny"


def test_rtx_shows_each_choice_as_asked_when_the_assets_are_there():
    assert resolve("stage", ALL, renders_mdl=True) == ("stage", "")
    assert resolve("sky", ALL, renders_mdl=True) == ("sky", "")
    assert resolve("hdri", ALL, renders_mdl=True) == ("hdri", "")


def test_storm_cannot_render_the_skys_mdl_so_the_sky_falls_back_to_the_hdri_and_says_so():
    assert resolve("sky", ALL, renders_mdl=False) == ("hdri", env.STORM_NOTE)
    assert env.STORM_NOTE == "Storm cannot render the sky's MDL: the HDRI instead"
    assert resolve("hdri", ALL, renders_mdl=False) == ("hdri", "")
    assert resolve("stage", ALL, renders_mdl=False) == ("stage", "")


def test_a_sky_not_fetched_falls_back_to_the_hdri_and_an_hdri_not_built_to_the_stage():
    no_sky, no_hdr = Assets(sky=None, hdri=HDR), Assets(sky=SKY, hdri=None)
    missing = env.sky_missing("Overcast")
    assert resolve("sky", no_sky, renders_mdl=True, sky="Overcast") == ("hdri", missing)
    assert missing == "Overcast is not fetched yet: the HDRI instead"
    assert resolve("hdri", no_hdr, renders_mdl=True) == ("stage", env.HDRI_MISSING)
    assert "packages/specto-web/build.py" in env.HDRI_MISSING
    shown, note = resolve("sky", NONE, renders_mdl=True)
    assert shown == "stage" and note == f"{env.sky_missing('ClearSky')}; {env.HDRI_MISSING}"
    shown, note = resolve("sky", NONE, renders_mdl=False)
    assert shown == "stage" and note == f"{env.STORM_NOTE}; {env.HDRI_MISSING}"


def test_the_assets_are_found_through_the_users_cache_and_the_web_build(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("SPECTO_USD_HDRI", str(tmp_path / "none.hdr"))
    assert env.find_assets() == NONE
    hdr = tmp_path / "sunny.hdr"
    hdr.write_bytes(b"#?RADIANCE\n")
    monkeypatch.setenv("SPECTO_USD_HDRI", str(hdr))
    assert env.find_assets("Overcast", "urban") == Assets(sky=None, hdri=hdr.as_posix())


# -- RTX: the reference text under /__specto/Environment --------------------------------
def test_the_rtx_sky_is_a_reference_to_the_fetched_package_turned_for_the_stages_up_axis():
    text = env.usda("sky", "Z", ALL)
    assert f"prepend references = @{SKY}@" in text
    assert 'def Xform "Environment"' in text and "rotateX" not in text
    text = env.usda("sky", "Y", ALL)
    assert "float xformOp:rotateX = -90" in text  # the sky is authored Z-up
    # the sky's own rotation stays in the order, after ours
    assert 'uniform token[] xformOpOrder = ["xformOp:rotateX", "xformOp:rotateZYX"]' in text


def test_the_rtx_sky_carries_the_controls_the_rig_light_off_and_the_viewers_sun():
    authored = {"time_of_day": 6.93, "latitude": 51.426, "longitude": -0.985, "day_of_year": 100.0}
    text = env.usda("sky", "Z", ALL, env.SkyControls(time_of_day=12.0, haze=0.1), authored)
    shader = text.split('over "Shader"', 1)[1].split("}", 1)[0]
    assert "bool inputs:SunPositionFromTOD = 1" in shader
    assert "float inputs:TimeOfDay = 12.0" in shader
    assert "float inputs:haze = 2.0" in shader and "CloudCoverage" not in shader
    rig = text.split('over "DistantLight"', 1)[1].split("}", 1)[0]
    assert "float intensity = 0" in rig and "float inputs:intensity = 0" in rig
    sun = text.split('def DistantLight "Sun"', 1)[1]
    assert "float inputs:intensity = 5000" in sun and "matrix4d xformOp:transform = (" in sun
    noon = env.sun_matrix(env.sun_direction(12.0, 51.426, 100))
    assert repr(float(noon[2][2])) in sun  # its +Z row is the sun's direction


# -- the sun and the controls, as the sky's material does them -------------------------------
def test_the_sun_follows_the_materials_own_maths():
    # ClearSky's place (lat 51.4, day 100): noon high in the south, morning east and low,
    # evening west and low, night below the horizon
    az, el = env.sun_angles(12.0, 51.426, 100)
    assert 45 < el < 50 and abs(abs(az) - 180) < 2  # the material's azimuth: 180 is south
    _, morning = env.sun_angles(8.0, 51.426, 100)
    _, evening = env.sun_angles(17.5, 51.426, 100)
    assert 20 < morning < 30 and 5 < evening < 15
    assert env.sun_angles(1.0, 51.426, 100)[1] < -20
    east = env.sun_direction(8.0, 51.426, 100)
    west = env.sun_direction(17.5, 51.426, 100)
    assert east[1] < -0.5 < 0.5 < west[1] and east[2] > 0 and west[2] > 0  # z up
    m = env.sun_matrix(west)  # its +Z row is the direction, a proper frame
    assert m[2][:3] == pytest.approx(west) and m[3] == [0, 0, 0, 1]
    dot = sum(a * b for a, b in zip(m[0][:3], m[2][:3], strict=True))
    assert dot == pytest.approx(0, abs=1e-9)
    my = env.sun_matrix(west, "Y")  # a Y-up stage: the direction turned by -90 about X
    assert my[2][:3] == pytest.approx([west[0], west[2], -west[1]])


def test_the_controls_map_to_the_materials_inputs_with_the_skys_own_values_as_defaults():
    authored = {
        "time_of_day": 6.93, "latitude": 51.426, "longitude": -0.985, "day_of_year": 100.0,
        "cloud_coverage": 0.743, "cumulus": True, "haze": 0.001,
    }  # fmt: skip
    none = env.SkyControls()
    shown = env.effective(none, authored)
    assert shown["time_of_day"] == 6.93
    assert shown["cloudiness"] == pytest.approx((0.743 + 0.5) / 1.5)
    assert shown["haze"] == pytest.approx(0.00005)
    assert env.effective(none, {**authored, "cumulus": False})["cloudiness"] == 0.0
    inputs = env.shader_inputs(none, authored)
    assert inputs["SunPositionFromTOD"] is True and inputs["TimeOfDay"] == 6.93
    assert inputs["Latitude"] == 51.426 and "CloudCoverage" not in inputs and "haze" not in inputs
    some = env.SkyControls.of({"time_of_day": 30, "cloudiness": 1.5, "haze": "0.25"})
    assert some == env.SkyControls(24.0, 1.0, 0.25)  # clamped
    inputs = env.shader_inputs(some, authored)
    assert inputs["CloudCoverage"] == 1.0 and inputs["CumulusEnabled"] is True
    assert inputs["haze"] == 5.0 and inputs["TimeOfDay"] == 24.0
    assert env.shader_inputs(env.SkyControls(cloudiness=0.0), authored)["CumulusEnabled"] is False
    assert env.cloud_coverage(0.0) == -0.5 and env.cloud_coverage(1.0) == 1.0
    assert env.SkyControls.of({"time_of_day": "noon"}) == none


def test_the_rtx_hdri_is_a_dome_light_with_the_3d_views_hdri_at_kits_intensity():
    text = env.usda("hdri", "Z", ALL)
    assert 'def DomeLight "Dome"' in text
    assert f"asset inputs:texture:file = @{HDR}@" in text
    assert 'token inputs:texture:format = "latlong"' in text
    assert "float inputs:intensity = 1000" in text
    assert "rotateX" not in text
    assert "rotateX = -90" in env.usda("hdri", "Y", ALL)


def test_the_stage_choice_authors_nothing():
    assert env.usda("stage", "Z", ALL) == ""


# -- Storm: a sublayer of the session layer ------------------------------------------------
pxr = pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")
from pxr import Sdf, Usd, UsdGeom, UsdLux  # noqa: E402

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "{up}"
)

def Xform "World"
{{
    def Cube "Box"
    {{
    }}

    def DistantLight "Sun"
    {{
        float inputs:intensity = 5000
    }}

    def Sphere "Bulb" (
        prepend apiSchemas = ["MeshLightAPI"]
    )
    {{
    }}
}}
"""


@pytest.fixture
def stage(tmp_path: Path):
    path = tmp_path / "stage.usda"
    path.write_text(STAGE.format(up="Z"), encoding="utf-8", newline="\n")
    st = Usd.Stage.Open(str(path), Usd.Stage.LoadAll)
    layer = Sdf.Layer.CreateAnonymous("specto-environment")
    st.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
    return st, layer, path


def test_the_hdri_composes_a_dome_over_the_stage_and_hides_its_own_lights(stage):
    st, layer, path = stage
    before = path.read_bytes()
    env.author(layer, "hdri", "Z", ALL, st)
    dome = st.GetPrimAtPath(f"{env.ROOT}/Dome")
    assert dome and dome.IsA(UsdLux.DomeLight)
    assert dome.GetAttribute("inputs:texture:file").Get().path == HDR
    assert dome.GetAttribute("inputs:intensity").Get() == 0.5  # Storm's scale, no tone mapping
    # Storm keeps a dome's pole along +Y: a Z-up stage turns it by +90 about X
    root = UsdGeom.Xformable(st.GetPrimAtPath(env.ROOT))
    ops = root.GetOrderedXformOps()
    assert [op.GetOpName() for op in ops] == ["xformOp:rotateX"] and ops[0].Get() == 90
    # the stage's own lights are out of the picture while the dome lights it
    assert UsdGeom.Imageable(st.GetPrimAtPath("/World/Sun")).ComputeVisibility() == "invisible"
    assert UsdGeom.Imageable(st.GetPrimAtPath("/World/Bulb")).ComputeVisibility() == "invisible"
    assert UsdGeom.Imageable(st.GetPrimAtPath("/World/Box")).ComputeVisibility() == "inherited"
    # in the session, never in the file
    assert not st.GetRootLayer().dirty and path.read_bytes() == before
    assert st.GetRootLayer().GetPrimAtPath("/__specto") is None


def test_back_to_the_stage_choice_clears_the_composition(stage):
    st, layer, _ = stage
    env.author(layer, "hdri", "Z", ALL, st)
    env.author(layer, "stage", "Z", ALL, st)
    assert not st.GetPrimAtPath(env.ROOT)
    assert UsdGeom.Imageable(st.GetPrimAtPath("/World/Sun")).ComputeVisibility() == "inherited"
    assert layer.ExportToString().strip().endswith("#usda 1.0") or not layer.rootPrims


def test_a_y_up_stage_leaves_storms_dome_as_it_is(tmp_path):
    path = tmp_path / "y.usda"
    path.write_text(STAGE.format(up="Y"), encoding="utf-8", newline="\n")
    st = Usd.Stage.Open(str(path))
    layer = Sdf.Layer.CreateAnonymous()
    st.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
    env.author(layer, "hdri", "Y", ALL, st)
    root = UsdGeom.Xformable(st.GetPrimAtPath(env.ROOT))
    assert root.GetOrderedXformOps() == []


def test_each_renderer_turns_the_environment_onto_the_stages_up_axis_from_its_own():
    # the sky is authored Z-up, ovrtx keeps a dome's pole along +Z, Storm along +Y
    assert env.rotation_x("Z") == 0 and env.rotation_x("Y") == -90
    assert env.rotation_x("Z", "Z") == 0 and env.rotation_x("Y", "Y") == 0
    assert env.rotation_x("Z", "Y") == 90 and env.rotation_x("Y", "Z") == -90
