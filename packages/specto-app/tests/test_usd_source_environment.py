"""``specto-usd-source`` rtx flavour against the fake ovrtx (SPEC-31): the environment light.
``load`` takes the environment and the ``loaded`` event says what shows; the renderer
adds it as a reference under ``/__specto/Environment`` at runtime, hides the fallback
dome and turns auto exposure off while an environment lights a light-less stage;
``set_environment`` switches without a reload and answers what shows, with a note when
it is not what was asked (a sky not fetched); ``state`` reports it."""

from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from specto_app.frames import control
from specto_app.usd import skies
from specto_app.usd.environment import HDRI_MISSING, ROOT, SHADER, SUN, sky_missing

FAKE = Path(__file__).resolve().parent / "fake_ovrtx"
NO_LIGHTS_NOTE = "no lights in the stage: a default dome light"
FALLBACK = "/__specto/Light"
AUTO = "omni:rtx:autoExposure:enabled"

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
{lights}}}
"""
SUN_LIGHT = '    def DistantLight "Sun"\n    {\n    }\n'
#: the fake sky's stage, with the /World coordinates the real one authors
SKY_WORLD = """#usda 1.0
(
    defaultPrim = "World"
)

def Xform "World"
{
    double DayOfYear = 100 (
    )
    double Latitude = 51.426 (
    )
    double Longitude = -0.985 (
    )
    double TimeOfDay = 6.93 (
    )
}
"""


@pytest.fixture
def assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fetched ClearSky in a user cache (the fetcher's layout, fake bytes, the real
    pins in its manifest) and a built HDRI, found through the environment variables the
    source process inherits."""
    cache = tmp_path / "user" / "skies"
    (cache / "src" / "Dynamic").mkdir(parents=True)
    stage = cache / "src" / "Dynamic" / "ClearSky.usd"
    stage.write_text(SKY_WORLD, encoding="utf-8", newline="\n")
    with zipfile.ZipFile(cache / "ClearSky.usdz", "w") as zf:
        zf.write(stage, "Dynamic/ClearSky.usd")
    pins = {rel: skies.FILES[rel][0] for rel in skies.sky_files("ClearSky")}
    (cache / "ClearSky.json").write_text(json.dumps({"files": pins}), "utf-8")
    hdr = tmp_path / "sunny.hdr"
    hdr.write_bytes(b"#?RADIANCE\n")
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("SPECTO_USD_HDRI", str(hdr))
    package = (cache / "ClearSky.usdz").as_posix()
    return {"sky": f"{package}[Dynamic/ClearSky.usd]", "hdr": hdr.as_posix()}


def calls(log: Path, name: str | None = None) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if name is None or r["call"] == name]


def writes(log: Path, attr: str) -> list[dict]:
    return [w for w in calls(log, "write_attribute") if w["attr"] == attr]


def spawn(log: Path) -> control.FrameSourceProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(FAKE), env.get("PYTHONPATH", "")])
    env["FAKE_OVRTX_LOG"] = str(log)
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx"]
    return control.FrameSourceProcess.spawn([*argv, "--width", "64", "--height", "36"], env=env)


def wait_health(src: control.FrameSourceProcess, state: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == state:
            return params


def until(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.02)
    assert cond(), "timed out"


@pytest.fixture
def source(tmp_path: Path, assets: dict[str, str]):
    """A source spawned with the assets' variables in its environment."""
    log = tmp_path / "ovrtx.log"
    src = spawn(log)
    src.wait_event("ready")
    yield src, log
    src.close()


def stage_file(tmp_path: Path, up: str = "Z", lights: str = "") -> Path:
    path = tmp_path / "stage.usda"
    path.write_text(STAGE.format(up=up, lights=lights), encoding="utf-8", newline="\n")
    return path


def test_a_stage_loaded_with_the_sky_references_it_hides_the_dome_and_fixes_exposure(
    source, tmp_path, assets
):
    src, log = source
    src.send(
        "load",
        path=str(
            stage_file(tmp_path),
        ),
        environment="sky",
    )
    loaded = src.wait_event("loaded", timeout=30)
    assert (loaded["environment"], loaded["environment_note"]) == ("sky", "")
    assert "renderer_note" not in loaded  # the sky lights it: no default dome to mention
    wait_health(src, "ok")
    until(lambda: calls(log, "add_usd_reference_from_string"))
    (ref,) = calls(log, "add_usd_reference_from_string")
    assert ref["target"] == ROOT
    assert f"prepend references = @{assets['sky']}@" in ref["text"]
    assert "rotateX" not in ref["text"]  # a Z-up stage: the sky as authored
    assert calls(log, "apply_usd_changes")
    (hide,) = writes(log, "visibility")
    assert (hide["prims"], hide["value"]) == ([FALLBACK], ["invisible"])
    (exposure,) = writes(log, AUTO)
    assert exposure["value"] == [[False]]  # the sky is on Kit's scale: the fixed exposure
    assert src.call("state")["environment"] == "sky"


def test_switching_at_runtime_swaps_the_reference_and_answers_what_shows(source, tmp_path, assets):
    src, log = source
    src.send("load", path=str(stage_file(tmp_path, up="Y")), environment="sky")
    src.wait_event("loaded", timeout=30)
    wait_health(src, "ok")
    until(lambda: calls(log, "add_usd_reference_from_string"))
    answer = src.call("set_environment", environment="hdri", timeout=10)
    assert (answer["environment"], answer["note"], answer["renderer_note"]) == ("hdri", "", "")
    assert answer["sky_controls"]["time_of_day"] == 6.93  # ClearSky's own, as fetched
    assert calls(log, "remove_usd")
    _sky, dome = calls(log, "add_usd_reference_from_string")
    assert 'def DomeLight "Dome"' in dome["text"]
    assert f"@{assets['hdr']}@" in dome["text"]
    assert "float xformOp:rotateX = -90" in dome["text"]  # a Y-up stage
    assert len(calls(log, "open_usd_from_string")) == 1  # no reload
    assert src.call("state")["environment"] == "hdri"
    # the same again: nothing happens
    assert src.call("set_environment", environment="hdri", timeout=10)["environment"] == "hdri"
    assert len(calls(log, "add_usd_reference_from_string")) == 2
    # back to the stage's own light: the fallback dome and auto exposure return, and the note
    answer = src.call("set_environment", environment="stage", timeout=10)
    assert (answer["environment"], answer["note"]) == ("stage", "")
    assert answer["renderer_note"] == NO_LIGHTS_NOTE
    assert len(calls(log, "remove_usd")) == 2
    assert writes(log, "visibility")[-1]["value"] == ["inherited"]
    assert writes(log, AUTO)[-1]["value"] == [[True]]
    assert src.call("state")["environment"] == "stage"


def test_a_sky_not_fetched_shows_the_hdri_and_says_why(source, tmp_path, assets, monkeypatch):
    src, log = source
    # the source process read the variables at spawn: an empty folder stands for no fetch
    src.close()
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "nowhere"))
    src = spawn(log)
    try:
        src.wait_event("ready")
        src.send("load", path=str(stage_file(tmp_path)), environment="sky")
        loaded = src.wait_event("loaded", timeout=30)
        assert (loaded["environment"], loaded["environment_note"]) == (
            "hdri",
            sky_missing("ClearSky"),
        )
        wait_health(src, "ok")
        until(lambda: calls(log, "add_usd_reference_from_string"))
        assert 'def DomeLight "Dome"' in calls(log, "add_usd_reference_from_string")[0]["text"]
    finally:
        src.close()


def test_without_the_hdri_either_the_stage_lights_itself_and_both_notes_say_so(
    source, tmp_path, monkeypatch
):
    src, log = source
    src.close()
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("SPECTO_USD_HDRI", str(tmp_path / "nowhere.hdr"))
    src = spawn(log)
    try:
        src.wait_event("ready")
        src.send("load", path=str(stage_file(tmp_path)), environment="sky")
        loaded = src.wait_event("loaded", timeout=30)
        assert loaded["environment"] == "stage"
        assert loaded["environment_note"] == f"{sky_missing('ClearSky')}; {HDRI_MISSING}"
        assert loaded["renderer_note"] == NO_LIGHTS_NOTE
        wait_health(src, "ok")
        assert not calls(log, "add_usd_reference_from_string")
        assert not writes(log, "visibility")
    finally:
        src.close()


def test_a_stage_with_its_own_plain_light_keeps_auto_exposure_under_an_environment(
    source, tmp_path, assets
):
    src, log = source
    src.send("load", path=str(stage_file(tmp_path, lights=SUN_LIGHT)), environment="hdri")
    loaded = src.wait_event("loaded", timeout=30)
    assert loaded["environment"] == "hdri" and "renderer_note" not in loaded
    wait_health(src, "ok")
    until(lambda: calls(log, "add_usd_reference_from_string"))
    assert not writes(log, "visibility")  # no fallback dome to hide
    assert not writes(log, AUTO)  # the inline root's auto exposure stands: a plain light
    (root,) = calls(log, "open_usd_from_string")
    assert "bool omni:rtx:autoExposure:enabled = 1" in root["text"]


def test_the_sky_layer_sets_the_sun_from_the_time_of_day_and_the_controls_write_live(
    source, tmp_path, assets
):
    src, log = source
    controls = {"time_of_day": 17.5, "cloudiness": 0.5, "haze": None}
    src.send("load", path=str(stage_file(tmp_path)), environment="sky", sky_controls=controls)
    loaded = src.wait_event("loaded", timeout=30)
    # the fake cache has the sky's stage, not its MDL: haze reads 0 there
    assert loaded["sky_controls"] == {"time_of_day": 17.5, "cloudiness": 0.5, "haze": 0.0}
    wait_health(src, "ok")
    until(lambda: calls(log, "add_usd_reference_from_string"))
    (ref,) = calls(log, "add_usd_reference_from_string")
    text = ref["text"]
    assert "float inputs:TimeOfDay = 17.5" in text and "bool inputs:SunPositionFromTOD = 1" in text
    assert "float inputs:Latitude = 51.426" in text  # the sky's own coordinates
    assert "float inputs:CloudCoverage = 0.25" in text and "bool inputs:CumulusEnabled = 1" in text
    assert "inputs:haze" not in text  # left to the sky
    assert (
        'def DistantLight "Sun"' in text and "float intensity = 0" in text
    )  # ours in, the rig's off
    # live: the shader inputs and the sun's world transform, no reload
    answer = src.call("set_sky_controls", time_of_day=8.0, cloudiness=0.0, haze=0.5, timeout=10)
    assert answer["sky_controls"] == {"time_of_day": 8.0, "cloudiness": 0.0, "haze": 0.5}
    written = {w["attr"]: w for w in calls(log, "write_attribute") if w["prims"] == [SHADER]}
    assert written["inputs:TimeOfDay"]["value"] == [[8.0]]
    assert written["inputs:CumulusEnabled"]["value"] == [[False]]
    assert written["inputs:haze"]["value"] == [[10.0]]
    (sun,) = [w for w in calls(log, "write_attribute") if w["prims"] == [SUN]]
    assert sun["attr"] == "omni:xform" and len(sun["value"][0]) == 4
    assert len(calls(log, "add_usd_reference_from_string")) == 1
    state = src.call("state")
    assert state["sky"] == "ClearSky" and state["sky_controls"]["time_of_day"] == 8.0
    # another sky named: another reference (its package is not fetched here: the HDRI, said)
    answer = src.call("set_environment", sky="Overcast", timeout=10)
    assert answer["environment"] == "hdri" and answer["note"] == sky_missing("Overcast")
    assert len(calls(log, "add_usd_reference_from_string")) == 2
