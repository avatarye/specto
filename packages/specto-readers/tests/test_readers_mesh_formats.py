"""SPEC-42: STL, OBJ and DAE in the 3D view's scene preparation (seam 1, no Qt).

STL and OBJ pass through to Babylon's own loaders; resolve only measures their bounds
(for the first camera). A DAE is converted to a GLB (trimesh + pycollada) written next to
it as ``<name>.dae.glb``, with its ``<unit>`` and ``<up_axis>`` applied (glTF is metres,
Y-up), regenerated when the DAE is newer, and written to the cache folder instead when its
own folder cannot take it. The synthetic files and their expected numbers are in
``mesh_samples.py``."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest
from mesh_samples import (
    BOX_HI,
    BOX_LO,
    DAE_BLUE,
    DAE_BOUNDS_M_YUP,
    write_dae,
    write_obj,
    write_stl,
)

from specto_readers import dae
from specto_readers.scene3d import prepare_scene


def r5(bounds) -> list[list[float]]:
    """Bounds rounded to five places (float32 positions, metres)."""
    return (np.round(np.asarray(bounds, dtype=float), 5) + 0.0).tolist()


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2 and length == len(data)
    n, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    gltf = json.loads(data[20 : 20 + n])
    rest = data[20 + n :]
    blob = b""
    if rest:
        m, kind2 = struct.unpack_from("<I4s", rest, 0)
        assert kind2 == b"BIN\0"
        blob = rest[8 : 8 + m]
    return gltf, blob


def _local(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T  # column-major
    m = np.eye(4)
    t = node.get("translation", [0, 0, 0])
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    r = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    m[:3, :3] = r * np.array(s)
    m[:3, 3] = t
    return m


def world_bounds(gltf: dict) -> list[list[float]]:
    """The scene's world-space box: every mesh's POSITION min/max corners through the node
    chain (a reading of the file, independent of the converter)."""
    pts = []

    def walk(i: int, parent: np.ndarray) -> None:
        node = gltf["nodes"][i]
        m = parent @ _local(node)
        if "mesh" in node:
            for prim in gltf["meshes"][node["mesh"]]["primitives"]:
                acc = gltf["accessors"][prim["attributes"]["POSITION"]]
                lo, hi = acc["min"], acc["max"]
                for x in (lo[0], hi[0]):
                    for y in (lo[1], hi[1]):
                        for z in (lo[2], hi[2]):
                            pts.append((m @ [x, y, z, 1.0])[:3])
        for c in node.get("children", []):
            walk(c, m)

    for i in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
        walk(i, np.eye(4))
    a = np.array(pts)
    return [a.min(0).round(6).tolist(), a.max(0).round(6).tolist()]


# -- the asset header ------------------------------------------------------------------------


def test_the_asset_header_gives_unit_and_up_axis(tmp_path: Path):
    a = dae.read_asset(write_dae(tmp_path / "robot.dae"))
    assert (a.unit_meter, a.up_axis) == (0.01, "Z_UP")
    # COLLADA's defaults when the asset says nothing: one metre, Y up
    bare = tmp_path / "bare.dae"
    bare.write_text(
        '<?xml version="1.0"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema"'
        ' version="1.4.1"><asset/></COLLADA>',
        encoding="utf-8",
    )
    assert (dae.read_asset(bare).unit_meter, dae.read_asset(bare).up_axis) == (1.0, "Y_UP")


@pytest.mark.parametrize(
    ("up", "point", "expected"),
    [
        # (x, y, z) in the DAE's frame -> glTF's (x, y, z), Y up
        ("Y_UP", (1, 2, 3), (1, 2, 3)),
        ("Z_UP", (1, 2, 3), (1, 3, -2)),  # right +X, up +Z, in -Y
        ("X_UP", (1, 2, 3), (-2, 1, 3)),  # right -Y, up +X, in +Z
    ],
)
def test_the_up_axis_turns_to_y_up_without_mirroring(up, point, expected):
    m = dae.to_gltf_frame(dae.DaeAsset(unit_meter=1.0, up_axis=up))
    assert (m @ [*point, 1.0])[:3].tolist() == list(expected)
    assert np.linalg.det(m[:3, :3]) == pytest.approx(1.0)


# -- the conversion ----------------------------------------------------------------------------


def test_convert_keeps_transforms_materials_and_textures_in_metres_y_up(tmp_path: Path):
    src = write_dae(tmp_path / "robot.dae")
    out = tmp_path / "robot.glb"
    info = dae.convert(src, out)
    gltf, blob = read_glb(out)
    # the nested node translations survive, in metres and turned to Y up
    assert world_bounds(gltf) == DAE_BOUNDS_M_YUP
    assert r5(info["bounds"]) == DAE_BOUNDS_M_YUP
    assert info["count"] == 48  # two instances of a 24-vertex box
    # one material keeps its colour, the other its texture (the PNG, embedded)
    mats = gltf["materials"]
    colours = [m["pbrMetallicRoughness"].get("baseColorFactor") for m in mats]
    assert any(c and c == pytest.approx(list(DAE_BLUE), abs=0.01) for c in colours)
    textured = [m for m in mats if "baseColorTexture" in m["pbrMetallicRoughness"]]
    assert len(textured) == 1
    img = gltf["images"][gltf["textures"][0]["source"]]
    view = gltf["bufferViews"][img["bufferView"]]
    png = blob[view.get("byteOffset", 0) : view.get("byteOffset", 0) + view["byteLength"]]
    assert png.startswith(b"\x89PNG")


def test_a_y_up_metre_dae_is_left_as_it_is(tmp_path: Path):
    src = write_dae(tmp_path / "plain.dae", unit_meter=1.0, up_axis="Y_UP")
    info = dae.convert(src, tmp_path / "plain.glb")
    # the box in its own numbers: x, y in [-20, 20], z up to 120 (Y up: z stays z)
    assert r5(info["bounds"]) == [[-20, -20, 0], [20, 20, 120]]


def test_a_dae_without_geometry_says_so(tmp_path: Path):
    empty = tmp_path / "empty.dae"
    empty.write_text(
        '<?xml version="1.0"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema"'
        ' version="1.4.1"><asset/><library_visual_scenes><visual_scene id="s"/>'
        '</library_visual_scenes><scene><instance_visual_scene url="#s"/></scene></COLLADA>',
        encoding="utf-8",
    )
    with pytest.raises(dae.DaeError, match="no geometry"):
        dae.convert(empty, tmp_path / "empty.glb")
    assert not (tmp_path / "empty.glb").exists()


# -- where the GLB goes: next to the DAE, else the cache ------------------------------------


def test_the_glb_is_written_next_to_the_dae(tmp_path: Path):
    (tmp_path / "parts").mkdir()
    src = write_dae(tmp_path / "parts" / "robot.dae")
    scene = prepare_scene(src, tmp_path / "cache")
    side = tmp_path / "parts" / "robot.dae.glb"
    assert scene["kind"] == "mesh" and scene["role"] == ".dae"
    assert scene["files"] == {"glb": str(side)}
    assert r5(scene["bounds"]) == DAE_BOUNDS_M_YUP
    assert scene["up"] == "y"  # declared by the file: the view's up param leaves it alone
    assert world_bounds(read_glb(side)[0]) == DAE_BOUNDS_M_YUP
    assert dae.converted_path(src) == side and dae.is_converted(side)
    assert not dae.is_converted(src) and not dae.is_converted(tmp_path / "robot.glb")


def test_an_unchanged_dae_reuses_its_glb_and_a_newer_one_regenerates_it(tmp_path: Path):
    src = write_dae(tmp_path / "robot.dae")
    side = dae.converted_path(src)
    prepare_scene(src, tmp_path / "cache")
    stamp = side.stat().st_mtime_ns
    again = prepare_scene(src, tmp_path / "cache")
    assert again["files"]["glb"] == str(side) and side.stat().st_mtime_ns == stamp
    # a converted mesh left by an earlier session (no cache entry) is reused while it is fresh
    fresh_cache = prepare_scene(src, tmp_path / "other-cache")
    assert fresh_cache["files"]["glb"] == str(side) and side.stat().st_mtime_ns == stamp
    assert r5(fresh_cache["bounds"]) == DAE_BOUNDS_M_YUP
    # the DAE changes (in metres now): the GLB is written again, with the new size
    write_dae(src, unit_meter=1.0)
    later = stamp + 5_000_000_000
    os.utime(src, ns=(later, later))
    changed = prepare_scene(src, tmp_path / "cache")
    assert side.stat().st_mtime_ns != stamp
    assert r5(changed["bounds"]) == [[-20, 0, -20], [20, 120, 20]]


def test_a_folder_that_cannot_take_the_glb_falls_back_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "ro").mkdir()
    src = write_dae(tmp_path / "ro" / "robot.dae")
    real = dae._write_atomic

    def read_only(data: bytes, dst: Path) -> None:
        if dst.parent == src.parent:
            raise PermissionError(13, "read-only folder", str(dst))
        real(data, dst)

    monkeypatch.setattr(dae, "_write_atomic", read_only)
    scene = prepare_scene(src, tmp_path / "cache")
    glb = Path(scene["files"]["glb"])
    assert not dae.converted_path(src).exists()
    assert glb.parent == tmp_path / "cache" / "scene3d" and glb.name.endswith(".dae.glb")
    assert world_bounds(read_glb(glb)[0]) == DAE_BOUNDS_M_YUP
    # a stale converted mesh in a read-only folder is not used either
    dae.converted_path(src).write_bytes(b"stale")
    old = src.stat().st_mtime_ns - 10_000_000_000
    os.utime(dae.converted_path(src), ns=(old, old))
    again = prepare_scene(src, tmp_path / "cache2")
    assert Path(again["files"]["glb"]).parent == tmp_path / "cache2" / "scene3d"


def test_a_broken_dae_raises_and_writes_nothing(tmp_path: Path):
    bad = tmp_path / "bad.dae"
    bad.write_text("<COLLADA><library_geometries>", encoding="utf-8")
    with pytest.raises(dae.DaeError):
        prepare_scene(bad, tmp_path / "cache")
    assert not dae.converted_path(bad).exists()


# -- STL and OBJ: Babylon's loaders, bounds measured at resolve ------------------------------


@pytest.mark.parametrize("binary", [True, False])
def test_an_stl_passes_through_with_its_bounds(tmp_path: Path, binary: bool):
    stl = write_stl(tmp_path / "link.stl", binary=binary)
    scene = prepare_scene(stl, tmp_path / "cache")
    assert r5(scene.pop("bounds")) == [list(BOX_LO), list(BOX_HI)]
    assert scene == {
        "kind": "mesh",
        "role": ".stl",
        "files": {},
        "count": 36,
    }


def test_an_obj_passes_through_with_its_bounds(tmp_path: Path):
    obj = write_obj(tmp_path / "panel.obj")
    scene = prepare_scene(obj, tmp_path / "cache")
    assert r5(scene.pop("bounds")) == [list(BOX_LO), list(BOX_HI)]
    assert scene == {
        "kind": "mesh",
        "role": ".obj",
        "files": {},
        "count": 24,
    }
    assert (tmp_path / "panel.mtl").is_file() and (tmp_path / "textures" / "panel.png").is_file()


def test_an_empty_stl_or_obj_still_opens(tmp_path: Path):
    (tmp_path / "e.obj").write_text("# nothing\n", encoding="ascii")
    (tmp_path / "e.stl").write_bytes(b" " * 80 + struct.pack("<I", 0))
    for name in ("e.obj", "e.stl"):
        scene = prepare_scene(tmp_path / name, tmp_path / "cache")
        assert scene["kind"] == "mesh" and scene["count"] == 0 and "bounds" not in scene
