"""The ``usd_info`` decoder (#135): the root layer's header, read without pxr, and an
honest list of what cannot be known without composing the stage."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from specto_core.kinds import DataRef, DecodeRequest
from specto_readers import DecodeError, default_readers

#: the head of a Kit-authored world (Z up, metres), in the shape of the spike's
KIT_WORLD = """#usda 1.0
(
    customLayerData = {
        dictionary cameraSettings = {
            dictionary Front = {
                double3 position = (5, 0, 0)
                double radius = 5
            }
            dictionary Perspective = {
                double3 position = (-12.5, 8.75, 4)
                double3 target = (23, -17.5, 1.875)
            }
            string boundCamera = "/OmniverseKit_Persp"
        }
        dictionary renderSettings = {
            bool "rtx:reflections:enabled" = 0
        }
    }
    defaultPrim = "World"
    endTimeCode = 0
    metersPerUnit = 1
    startTimeCode = -1
    upAxis = "Z"
)

def Xform "World" (
    customData = {
        string note = \'\'\'{"name": "example"}\'\'\'
    }
)
{
}
"""


def _info(path: Path) -> dict:
    ref = DataRef(kind="usd_stage_ref", uri=str(path))
    buf = default_readers().decode(ref, DecodeRequest(purpose="meta"))
    info = json.loads(buf.to_numpy().tobytes())
    assert buf.spec.meta == info  # the same facts ride on the buffer's spec
    return info


def test_a_kit_world_header_gives_axis_units_time_and_default_prim(tmp_path: Path):
    world = tmp_path / "world.usda"
    world.write_text(KIT_WORLD, encoding="utf-8")
    info = _info(world)
    assert info["format"] == "usda"
    assert info["default_prim"] == "World"
    assert info["up_axis"] == "Z"
    assert info["meters_per_unit"] == 1.0
    assert (info["start_time_code"], info["end_time_code"]) == (-1.0, 0.0)
    assert info["time_codes_per_second"] == 24.0  # not authored: USD's fallback
    assert info["kit_camera"] == {"position": [-12.5, 8.75, 4.0], "target": [23.0, -17.5, 1.875]}


def test_unauthored_fields_take_usds_fallbacks_and_say_so(tmp_path: Path):
    bare = tmp_path / "bare.usda"
    bare.write_text('#usda 1.0\n\ndef "A" {}\n', encoding="utf-8")
    info = _info(bare)
    assert info["default_prim"] is None
    assert (info["up_axis"], info["meters_per_unit"]) == ("Y", 0.01)
    assert (info["start_time_code"], info["end_time_code"]) == (None, None)
    assert (info["time_codes_per_second"], info["frames_per_second"]) == (24.0, 24.0)
    assert info["kit_camera"] is None and info["sublayers"] == []
    assert info["defaulted"] == [
        "up_axis",
        "meters_per_unit",
        "time_codes_per_second",
        "frames_per_second",
    ]


def test_time_codes_per_second_falls_back_to_frames_per_second(tmp_path: Path):
    anim = tmp_path / "anim.usda"
    anim.write_text(
        '#usda 1.0\n(\n    "a doc string"\n    framesPerSecond = 30\n'
        "    startTimeCode = 1\n    endTimeCode = 240\n)\n",
        encoding="utf-8",
    )
    info = _info(anim)
    assert (info["time_codes_per_second"], info["frames_per_second"]) == (30.0, 30.0)
    assert (info["start_time_code"], info["end_time_code"]) == (1.0, 240.0)
    assert info["defaulted"] == ["up_axis", "meters_per_unit"]


def test_sublayers_are_listed_as_authored(tmp_path: Path):
    root = tmp_path / "root.usda"
    root.write_text(
        '#usda 1.0\n(\n    doc = """two\nlines"""\n'
        "    subLayers = [\n        @./props.usda@ (offset = 10; scale = 2),\n"
        '        @Common/sky.usd@\n    ]\n    upAxis = "Z" # comment\n)\n',
        encoding="utf-8",
    )
    info = _info(root)
    assert info["sublayers"] == ["./props.usda", "Common/sky.usd"]
    assert info["up_axis"] == "Z"


def test_only_the_header_is_read(tmp_path: Path):
    big = tmp_path / "big.usda"
    with big.open("w", encoding="utf-8") as f:
        f.write('#usda 1.0\n(\n    defaultPrim = "World"\n)\n')
        f.write("this body is not usda at all ((( {{{ \n" * 200_000)  # ~8 MB
    assert _info(big)["default_prim"] == "World"


def test_what_needs_the_composed_stage_is_listed_as_unavailable(tmp_path: Path):
    world = tmp_path / "world.usda"
    world.write_text(KIT_WORLD, encoding="utf-8")
    unavailable = _info(world)["unavailable"]
    assert set(unavailable) == {"prim_count", "bounds", "cameras"}
    assert all("source process" in why for why in unavailable.values())


def test_a_crate_layer_says_its_header_needs_pxr(tmp_path: Path):
    crate = b"PXR-USDC" + bytes([0, 10, 0, 0, 0, 0, 0, 0]) + bytes(72)
    for name in ("world.usdc", "world.usd"):
        (tmp_path / name).write_bytes(crate)
        info = _info(tmp_path / name)
        assert (info["format"], info["root_format"]) == (name.rsplit(".")[-1], "usdc")
        assert info["up_axis"] is None and info["default_prim"] is None
        assert "pxr" in info["unavailable"]["up_axis"]
        assert "pxr" in info["unavailable"]["time_codes_per_second"]


def test_a_text_usd_is_read_like_usda(tmp_path: Path):
    usd = tmp_path / "world.usd"
    usd.write_text(KIT_WORLD, encoding="utf-8")
    info = _info(usd)
    assert (info["format"], info["root_format"]) == ("usd", "usda")
    assert info["up_axis"] == "Z"


def test_a_usdz_goes_by_its_first_entry(tmp_path: Path):
    text = tmp_path / "text.usdz"
    with zipfile.ZipFile(text, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("scene.usda", KIT_WORLD)
        zf.writestr("textures/a.png", b"\x89PNG")
    info = _info(text)
    assert (info["format"], info["root_format"], info["root_layer"]) == (
        "usdz",
        "usda",
        "scene.usda",
    )
    assert info["default_prim"] == "World"

    crate = tmp_path / "crate.usdz"
    with zipfile.ZipFile(crate, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("scene.usdc", b"PXR-USDC" + bytes(80))
    info = _info(crate)
    assert info["root_format"] == "usdc" and "pxr" in info["unavailable"]["default_prim"]


def test_a_file_that_is_not_usd_is_a_decode_error_with_a_fix(tmp_path: Path):
    bad = tmp_path / "fake.usda"
    bad.write_text("hello", encoding="utf-8")
    cut = tmp_path / "cut.usda"
    cut.write_text('#usda 1.0\n(\n    defaultPrim = "Wor', encoding="utf-8")
    for path in (bad, cut, tmp_path / "missing.usda"):
        with pytest.raises(DecodeError) as e:
            _info(path)
        assert path.name in str(e.value) and e.value.fix


#: the spike's world in the user's large USD depot (read only, never synced or edited):
#: ``SPECTO_P4_DEPOT`` is the depot root (else ``~/depot``), ``SPECTO_P4_WORLD`` the world
#: file in it, relative to the root. Nothing here names a real depot.
def perforce_depot() -> Path:
    return Path(os.environ.get("SPECTO_P4_DEPOT") or (Path.home() / "depot"))


DEPOT_WORLD = perforce_depot() / os.environ.get("SPECTO_P4_WORLD", "worlds/example_world.usda")


@pytest.mark.skipif(not DEPOT_WORLD.exists(), reason="no depot world file here")
def test_the_spike_world_reads_as_a_z_up_metre_stage():
    info = _info(DEPOT_WORLD)
    assert (info["default_prim"], info["up_axis"], info["meters_per_unit"]) == ("World", "Z", 1.0)
    assert (info["start_time_code"], info["end_time_code"]) == (-1.0, 0.0)
    assert info["kit_camera"] is not None
