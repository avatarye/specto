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
import re
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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

from specto_readers import dae, scene3d
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
    # a converted mesh left by an earlier session (no cache entry) is reused while it
    # matches, with the same numbers as the first conversion (instances counted)
    fresh_cache = prepare_scene(src, tmp_path / "other-cache")
    assert fresh_cache["files"]["glb"] == str(side) and side.stat().st_mtime_ns == stamp
    assert r5(fresh_cache["bounds"]) == DAE_BOUNDS_M_YUP
    assert fresh_cache["count"] == again["count"] == 48
    assert fresh_cache["version"] == again["version"]
    # the DAE changes (in metres now): the GLB is written again, with the new size
    write_dae(src, unit_meter=1.0)
    later = stamp + 5_000_000_000
    os.utime(src, ns=(later, later))
    changed = prepare_scene(src, tmp_path / "cache")
    assert side.stat().st_mtime_ns != stamp
    assert r5(changed["bounds"]) == [[-20, 0, -20], [20, 120, 20]]
    assert changed["version"] != again["version"]  # an open view loads the new GLB


def test_a_dae_swapped_for_an_older_copy_is_converted_again(tmp_path: Path):
    """The converted mesh records the DAE it came from (size, mtime): a checkout or a
    restore that puts back an older DAE, older than the GLB, does not keep the GLB."""
    src = write_dae(tmp_path / "robot.dae")
    side = dae.converted_path(src)
    prepare_scene(src, tmp_path / "cache")
    assert dae.glb_record(side)["source"] == dae.source_stamp(src)
    # an older copy comes back: smaller numbers (metres), an mtime before the GLB's
    write_dae(src, unit_meter=1.0)
    old = side.stat().st_mtime_ns - 60_000_000_000
    os.utime(src, ns=(old, old))
    assert side.stat().st_mtime_ns > src.stat().st_mtime_ns
    scene = prepare_scene(src, tmp_path / "cache")
    assert r5(scene["bounds"]) == [[-20, 0, -20], [20, 120, 20]]
    assert dae.glb_record(side)["source"] == dae.source_stamp(src)
    # a GLB Specto did not write (no record) next to a DAE is replaced, never trusted
    side.write_bytes(read_glb_bytes_without_record(side))
    assert dae.glb_record(side) is None
    again = prepare_scene(src, tmp_path / "cache-2")
    assert dae.glb_record(side)["source"] == dae.source_stamp(src)
    assert again["count"] == 48


def read_glb_bytes_without_record(path: Path) -> bytes:
    gltf, blob = read_glb(path)
    gltf["asset"].pop("extras", None)
    text = json.dumps(gltf).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text
    body += struct.pack("<I4s", len(blob), b"BIN\0") + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def test_a_folder_that_cannot_take_the_glb_falls_back_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "ro").mkdir()
    src = write_dae(tmp_path / "ro" / "robot.dae")
    real = dae.write_atomic

    def read_only(data: bytes, dst: Path) -> None:
        if dst.parent == src.parent:
            raise PermissionError(13, "read-only folder", str(dst))
        real(data, dst)

    monkeypatch.setattr(dae, "write_atomic", read_only)
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


def test_a_dae_too_big_or_too_slow_is_refused_with_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src = write_dae(tmp_path / "robot.dae")
    monkeypatch.setattr(dae, "DAE_MAX_BYTES", 1000)
    with pytest.raises(dae.DaeError, match=r"robot\.dae: \d+ MB; the 3d view converts DAE"):
        prepare_scene(src, tmp_path / "cache")
    monkeypatch.setattr(dae, "DAE_MAX_BYTES", 256 * 2**20)
    monkeypatch.setattr(dae, "CONVERT_TIMEOUT_S", 0.01)
    with pytest.raises(dae.DaeError, match=r"conversion took longer than 0\.01 s, stopped"):
        prepare_scene(src, tmp_path / "cache")
    assert not dae.converted_path(src).exists()
    # the killed child leaves nothing behind: no GLB, no temporary, no log
    assert list((tmp_path / "cache" / "scene3d").iterdir()) == []
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_lxml_is_not_in_the_lock():
    """lxml is not in the runtime (the lock, not just this environment): the licence notice
    says so, and pycollada's ElementTree does not fetch external entities a COLLADA file
    might name."""
    lock = Path(__file__).parents[3] / "uv.lock"
    names = re.findall(r'^name = "([^"]+)"', lock.read_text(encoding="utf-8"), re.M)
    assert "pycollada" in names and "trimesh" in names
    assert "lxml" not in names
    from collada import xmlutil

    assert xmlutil.HAVE_LXML is False


def test_the_converter_imports_nothing_from_the_data_folder_or_the_current_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The child runs ``python -I`` from the cache folder: a ``trimesh.py`` or ``numpy.py``
    planted next to the DAE, or in the folder Specto was started from, never runs."""
    data = tmp_path / "data"
    data.mkdir()
    marker = tmp_path / "planted-ran"
    # the child's own working folder too: ``-m`` would put it first on sys.path
    work = tmp_path / "cache" / "scene3d"
    work.mkdir(parents=True)
    for name, folder in (
        (n, f) for n in ("trimesh.py", "numpy.py", "collada.py") for f in (data, work)
    ):
        (folder / name).write_text(
            f"open({str(marker)!r}, 'w').write('ran')\nraise SystemExit(9)\n", encoding="utf-8"
        )
    src = write_dae(data / "robot.dae")
    monkeypatch.chdir(data)
    scene = prepare_scene(src, tmp_path / "cache")
    assert scene["count"] == 48 and not marker.exists()


def test_two_threads_converting_one_dae_do_not_spoil_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """SPEC-58: two resolve threads on one DAE. The second used to read the converted mesh
    while the first renamed its own over it; on Windows the read met a sharing violation,
    glb_record said None, and that thread raised "wrote no readable GLB" (4 of 30 calls in a
    loop at a 1 us switch interval). Now one conversion runs per DAE at a time in a process:
    the second thread waits and reads what the first wrote."""
    src = write_dae(tmp_path / "robot.dae")
    runs: list[int] = []
    real = dae._convert_bounded

    def counted(*args):
        runs.append(1)
        return real(*args)

    monkeypatch.setattr(dae, "_convert_bounded", counted)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            scenes = list(pool.map(lambda _i: prepare_scene(src, tmp_path / "cache"), range(3)))
    finally:
        sys.setswitchinterval(old)
    assert runs == [1]
    assert [s["count"] for s in scenes] == [48, 48, 48]
    side = dae.converted_path(src)
    assert {s["files"]["glb"] for s in scenes} == {str(side)}
    assert dae.glb_record(side)["source"] == dae.source_stamp(src)
    key = scene3d.cache_key(src)
    assert [p.name for p in (tmp_path / "cache" / "scene3d").iterdir()] == [f"{key}.dae.json"]
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_a_passing_sharing_violation_is_tried_again(monkeypatch: pytest.MonkeyPatch):
    """Another process reading the converted mesh holds it for a moment (Windows' sharing
    or lock violation, winerror 32 or 33): a rename onto it, or a read of it, waits that
    out. A real access-denied (winerror 5), or any other error, is raised at once."""
    from specto_readers import scenecache

    monkeypatch.setattr(scenecache, "SHARING_WAIT_S", 0.0)

    def failing(winerror: int, times: int):
        calls: list[int] = []

        def fn() -> str:
            calls.append(1)
            if len(calls) <= times:
                e = PermissionError(13, "in use")
                e.winerror = winerror  # what Windows sets; settable on any platform
                raise e
            return "done"

        return fn, calls

    for code in (32, 33):
        fn, calls = failing(code, 2)
        assert scenecache.retry_sharing(fn) == "done" and len(calls) == 3
    fn, calls = failing(32, scenecache.SHARING_TRIES)
    with pytest.raises(PermissionError):
        scenecache.retry_sharing(fn)
    assert len(calls) == scenecache.SHARING_TRIES
    fn, calls = failing(5, 1)  # access denied: final
    with pytest.raises(PermissionError):
        scenecache.retry_sharing(fn)
    assert len(calls) == 1


def test_a_killed_conversion_takes_its_process_tree_and_leaves_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A venv launcher runs the real Python as its own child: the whole tree goes."""
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=sys.platform != "win32")
    for _ in range(200):
        if pid_file.is_file() and pid_file.read_text():
            break
        time.sleep(0.05)
    grandchild = int(pid_file.read_text())
    dae._kill_tree(proc)
    for _ in range(100):
        if not _alive(grandchild):
            break
        time.sleep(0.05)
    assert not _alive(grandchild)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_dead_runs_temporaries_are_swept_from_the_cache(tmp_path: Path):
    """SPEC-58: a crashed process leaves its temporaries (``unique_temp``'s names: write_atomic's
    ``.tmp``, the converter's ``.convert.glb`` and its ``.convert.glb.log``); a conversion sweeps
    those older than an hour, never a live one, a record, a converted mesh, or a file of
    the same ending that is not one of Specto's (another tool's ``old.log``)."""
    from specto_readers import scenecache

    cache = tmp_path / "cache" / "scene3d"
    cache.mkdir(parents=True)
    stale = [".k.1.2.convert.glb", ".k.1.2.convert.glb.log", ".x.json.1.2.tmp"]
    keep = [".k.3.4.convert.glb", "k.dae.json", "k.dae.glb", "old.log", ".notes.log", "a.tmp"]
    old = time.time() - 2 * scenecache.STALE_AFTER_S
    for name in stale + keep:
        (cache / name).write_bytes(b"x")
    for name in stale + keep[1:]:
        os.utime(cache / name, (old, old))
    assert [scenecache.is_temp(n) for n in stale] == [True] * 3
    assert scenecache.sweep_stale(cache) == len(stale)
    assert sorted(p.name for p in cache.iterdir()) == sorted(keep)
    # the names it sweeps are the names the writers make
    made = scenecache.unique_temp(cache, "k", scenecache.CONVERT_TEMP_SUFFIX)
    log = made.name.removesuffix(scenecache.CONVERT_TEMP_SUFFIX) + scenecache.CONVERT_LOG_SUFFIX
    assert scenecache.is_temp(made.name) and scenecache.is_temp(log)
    # a conversion sweeps on its way
    (cache / ".dead.9.9.convert.glb").write_bytes(b"x")
    os.utime(cache / ".dead.9.9.convert.glb", (old, old))
    prepare_scene(write_dae(tmp_path / "robot.dae"), tmp_path / "cache")
    assert not (cache / ".dead.9.9.convert.glb").exists()


def test_the_sweep_leaves_a_linked_cache_folder_alone(tmp_path: Path):
    """A scene3d folder that is a junction or link points at files that are not this
    cache's: nothing is swept there."""
    from specto_readers import scenecache

    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / ".k.1.2.tmp").write_bytes(b"x")
    old = time.time() - 2 * scenecache.STALE_AFTER_S
    os.utime(real / ".k.1.2.tmp", (old, old))
    link = tmp_path / "scene3d"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(real), str(link))
    else:
        link.symlink_to(real, target_is_directory=True)
    try:
        assert scenecache.sweep_stale(link) == 0
        assert (real / ".k.1.2.tmp").exists()
    finally:
        os.rmdir(link) if sys.platform == "win32" else link.unlink()


def test_the_page_and_resolve_strip_the_same_map_options():
    """The 3d page strips an MTL texture map's options before Babylon builds the URL;
    resolve strips them before it checks the file. The two tables must agree."""
    main_js = Path(__file__).parents[2] / "specto-web" / "web" / "3d" / "main.js"
    text = main_js.read_text(encoding="utf-8")
    m = re.search(r"const MAP_OPTIONS = (\{[^}]*\});", text)
    assert m, "MAP_OPTIONS not found in main.js"
    page = json.loads(m.group(1))
    assert page == scene3d._MAP_OPTIONS


def test_only_the_tail_of_the_childs_output_is_read_and_bad_bytes_pass(tmp_path: Path):
    log = tmp_path / "child.log"
    log.write_bytes(b"x" * 50_000 + b"\n\xff\xfe broken\n" + b'{"error": "robot.dae: bad"}\n')
    lines = dae._tail(log)
    assert sum(len(line) for line in lines) <= dae.OUTPUT_TAIL_BYTES
    assert lines[-1] == '{"error": "robot.dae: bad"}' and "\ufffd" in lines[-2]


def test_a_record_that_cannot_be_written_is_still_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """On Windows a reader holding the old record makes the rename fail: the scene stands."""
    from specto_readers import scenecache

    def busy(path: Path, record: dict) -> None:
        raise PermissionError(13, "in use", str(path))

    monkeypatch.setattr(scenecache, "write_record", busy)
    scene = prepare_scene(write_stl(tmp_path / "link.stl"), tmp_path / "cache")
    assert scene["count"] == 36


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


def test_stl_and_obj_are_read_in_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The resolve-time scan reads big text files block by block (bounded memory): a
    block size smaller than a line still gives the whole file's numbers."""
    from specto_readers import scene3d

    monkeypatch.setattr(scene3d, "SCAN_CHUNK", 7)
    for path in (write_stl(tmp_path / "a.stl", binary=False), write_obj(tmp_path / "b.obj")):
        scene = prepare_scene(path, tmp_path / "cache")
        assert r5(scene["bounds"]) == [list(BOX_LO), list(BOX_HI)], path.name
    monkeypatch.setattr(scene3d, "SCAN_CHUNK", 60)  # a binary STL: facets in steps of one
    scene = prepare_scene(write_stl(tmp_path / "c.stl"), tmp_path / "cache")
    assert scene["count"] == 36 and r5(scene["bounds"]) == [list(BOX_LO), list(BOX_HI)]


def test_an_stl_or_obj_over_the_limit_is_refused_with_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from specto_readers import scene3d

    monkeypatch.setattr(scene3d, "LOADER_MAX_BYTES", 100)
    with pytest.raises(scene3d.MeshError, match=r"link\.stl: 0 MB; the 3d view opens STL files"):
        prepare_scene(write_stl(tmp_path / "link.stl"), tmp_path / "cache")


def test_an_obj_names_its_textures_relative_to_itself_within_the_root(tmp_path: Path):
    """Robot packages keep textures beside the meshes folder (``../textures``): those
    resolve while they stay inside the root; one outside it, or missing, is named."""
    pkg = tmp_path / "pkg"
    (pkg / "meshes").mkdir(parents=True)
    obj = write_obj(pkg / "meshes" / "panel.obj", texture_ref="../textures/skin.png")
    scene = prepare_scene(obj, tmp_path / "cache", root=pkg)
    assert "missing" not in scene
    # outside the root: refused (the items host would not serve it either)
    out = write_obj(pkg / "meshes" / "lid.obj", texture_ref="../../elsewhere/lid.png")
    assert (tmp_path / "elsewhere" / "lid.png").is_file()
    scene = prepare_scene(out, tmp_path / "cache", root=pkg)
    assert scene["missing"] == ["../../elsewhere/lid.png"]
    # missing files: the MTL's texture, and an MTL that is not there
    (pkg / "textures" / "skin.png").unlink()
    assert prepare_scene(obj, tmp_path / "cache", root=pkg)["missing"] == ["../textures/skin.png"]
    (pkg / "meshes" / "panel.mtl").unlink()
    assert prepare_scene(obj, tmp_path / "cache", root=pkg)["missing"] == ["panel.mtl"]
    # a texture map's options are not part of its file name (-s, -o, -clamp, -bm, ...)
    (pkg / "textures" / "skin.png").parent.mkdir(exist_ok=True)
    write_obj(pkg / "meshes" / "opt.obj", texture_ref="../textures/opt.png")
    mtl = pkg / "meshes" / "opt.mtl"
    mtl.write_text(
        mtl.read_text(encoding="ascii").replace(
            "map_Kd ../textures/opt.png", "map_Kd -s 1 1 1 -o 0 0 -clamp on ../textures/opt.png"
        ),
        encoding="ascii",
    )
    assert "missing" not in prepare_scene(pkg / "meshes" / "opt.obj", tmp_path / "cache", root=pkg)
    # no root (a Python link's collection): only that the files are there
    assert "missing" not in prepare_scene(out, tmp_path / "cache")


def test_an_empty_stl_or_obj_still_opens(tmp_path: Path):
    (tmp_path / "e.obj").write_text("# nothing\n", encoding="ascii")
    (tmp_path / "e.stl").write_bytes(b" " * 80 + struct.pack("<I", 0))
    for name in ("e.obj", "e.stl"):
        scene = prepare_scene(tmp_path / name, tmp_path / "cache")
        assert scene["kind"] == "mesh" and scene["count"] == 0 and "bounds" not in scene
