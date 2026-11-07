"""``specto-usd-source``, rtx flavour (#137), driven over the frame-source protocol with a
fake ovrtx/ovstage (``tests/fake_ovrtx``) in place of NVIDIA's: what the source hands
the renderer (the inline root, camera and resolution writes, history resets) and what
the host sees (events, health, frames in the ring). usd-core is real: it reads the
stage's bounds and prims."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from specto_app.frames import control
from specto_app.frames.ring import RingReader

FAKE = Path(__file__).resolve().parent / "fake_ovrtx"
FAKE_COLOR = (40, 80, 120, 255)

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 0.01
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Box"
    {
        double size = 2
        double3 xformOp:translate = (10, 0, 1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }

    def Scope "Props"
    {
        def Sphere "Ball"
        {
        }
    }
}
"""


@pytest.fixture
def stage_file(tmp_path: Path) -> Path:
    path = tmp_path / "box world.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    return path


class Fake:
    """The fake renderer's call log."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def calls(self, name: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if name is None or r["call"] == name]


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path / "ovrtx.log")


def spawn(fake: Fake, *args: str, **env: str) -> control.FrameSourceProcess:
    environ = dict(os.environ)
    environ["PYTHONPATH"] = os.pathsep.join([str(FAKE), environ.get("PYTHONPATH", "")])
    environ["FAKE_OVRTX_LOG"] = str(fake.path)
    environ.update(env)
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx"]
    argv += ["--width", "64", "--height", "36", *args]
    return control.FrameSourceProcess.spawn(argv, env=environ)


@pytest.fixture
def source(fake: Fake):
    src = spawn(fake)
    yield src
    src.close()


def wait_health(src: control.FrameSourceProcess, state: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == state:
            return params


def wait_frame(reader: RingReader, timeout: float = 10.0, after: int = 0, **want):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = reader.latest()
        if f is not None and f.seq > after and all(getattr(f, k) == v for k, v in want.items()):
            return f
        time.sleep(0.005)
    raise AssertionError(f"no frame with {want}")


def pixel(frame, x: int, y: int) -> tuple[int, ...]:
    at = y * frame.stride + x * 4
    return tuple(frame.pixels[at : at + 4])


def test_a_loaded_stage_reports_itself_and_its_frames_reach_the_ring(source, stage_file):
    ready = source.wait_event("ready")
    source.send("load", path=str(stage_file))
    loaded = source.wait_event("loaded", timeout=30)
    assert loaded["path"] == str(stage_file)
    assert loaded["name"] == "box world.usda"
    assert loaded["renderer"] == "rtx"
    assert loaded["up_axis"] == "Z"
    assert loaded["prims"] == 4  # World, Box, Props, Ball
    lo, hi = loaded["bounds"]
    assert lo == pytest.approx([-1, -1, -1]) and hi == pytest.approx([11, 1, 2])  # ball + box
    wait_health(source, "ok")
    reader = RingReader.open(ready["ring"])
    try:
        f = wait_frame(reader, width=64, height=36)
        assert pixel(f, 0, 0) == FAKE_COLOR and pixel(f, 63, 35) == FAKE_COLOR
    finally:
        reader.close()


def load(src: control.FrameSourceProcess, path: Path) -> dict:
    """Load ``path`` and wait for its first frame; the ``ready`` event's params."""
    ready = src.wait_event("ready")
    src.send("load", path=str(path))
    wait_health(src, "ok")
    return ready


def test_the_inline_root_sublayers_the_file_with_its_stage_metrics(source, fake, stage_file):
    load(source, stage_file)
    (root,) = fake.calls("open_usd_from_string")
    text = root["text"]
    # the file's own metrics, not ovrtx's centimetre default
    assert "metersPerUnit = 0.01" in text
    assert 'upAxis = "Z"' in text
    assert f"subLayers = [@{stage_file.as_posix()}@]" in text
    # the viewer's camera and product: RT2 with DLSS performance at the frame size
    assert 'def Camera "Camera"' in text
    assert "int2 resolution = (64, 36)" in text
    assert "rel camera = </__specto/Camera>" in text
    assert 'token omni:rtx:post:dlss:execMode = "performance"' in text
    assert 'def RenderVar "LdrColor"' in text
    # nothing else is authored over the file's prims
    assert "/World" not in text


def until(what, timeout: float = 10.0):
    """``what()`` once it is truthy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = what()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def writes(fake: Fake, attr: str) -> list[dict]:
    return [c for c in fake.calls("write_attribute") if c["attr"] == attr]


# looking down -X from (10, 0, 2) with Z up, 2 * atan(1/2) vertically
SIDE_CAMERA = {
    "position": [10.0, 0.0, 2.0],
    "target": [0.0, 0.0, 2.0],
    "up": [0.0, 0.0, 1.0],
    "fov_y": 2 * 0.4636476090008061,
    "near": 0.5,
    "far": 500.0,
}


def test_set_camera_moves_the_viewer_camera_through_omni_xform(source, fake, stage_file):
    load(source, stage_file)
    source.send("set_camera", **SIDE_CAMERA, cut=False)
    (xform,) = until(lambda: writes(fake, "omni:xform"))
    assert xform["prims"] == ["/__specto/Camera"]
    assert xform["semantic"] == "MATRIX"
    # USD's row vectors: camera +X (right) is world +Y, camera +Y (up) is world +Z, the
    # camera looks down its -Z, so its +Z is world +X; the eye is the last row
    assert xform["value"] == [
        [[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [1.0, 0.0, 0.0, 0.0], [10.0, 0.0, 2.0, 1.0]]
    ]
    # the lens: tan(fov_y / 2) = 1/2 over UsdGeomCamera's 15.2908 mm vertical aperture
    (focal,) = until(lambda: writes(fake, "focalLength"))
    assert focal["value"][0][0] == pytest.approx(15.2908, abs=1e-4)
    (clip,) = writes(fake, "clippingRange")
    assert clip["value"][0] == pytest.approx([0.5, 500.0])
    # the writes share one new ordinal, and the next step renders through it
    assert xform["ordinal"] == focal["ordinal"] == clip["ordinal"] > 1
    until(lambda: [s for s in fake.calls("step") if s["ordinal"] == xform["ordinal"]])
    # a move is no cut: the render history stays
    assert writes(fake, "omni:rtx:viewTile:renderHistoryReset:requestId") == []


def test_a_cut_resets_the_render_history(source, fake, stage_file):
    load(source, stage_file)
    source.send("set_camera", **SIDE_CAMERA, cut=True)
    # the product's one-shot history reset (every tile: the root authors [-1]), not
    # renderer.reset, which costs a second a cut
    (reset,) = until(lambda: writes(fake, "omni:rtx:viewTile:renderHistoryReset:requestId"))
    assert reset["prims"] == ["/Render/__specto"]
    assert reset["value"] == [[1]]
    (root,) = fake.calls("open_usd_from_string")
    assert "float[] omni:rtx:viewTile:renderHistoryReset:tileIndices = [-1]" in root["text"]
    (xform,) = writes(fake, "omni:xform")
    assert reset["ordinal"] == xform["ordinal"]  # with the camera, in one commit
    assert fake.calls("reset") == []
    source.send("set_camera", **{**SIDE_CAMERA, "position": [9.0, 0.0, 2.0]}, cut=True)
    until(lambda: len(writes(fake, "omni:rtx:viewTile:renderHistoryReset:requestId")) == 2)
    assert writes(fake, "omni:rtx:viewTile:renderHistoryReset:requestId")[1]["value"] == [[2]]


def test_resize_writes_the_product_resolution_and_frames_follow(source, fake, stage_file):
    ready = load(source, stage_file)
    assert source.call("resize", width=48, height=48) == {"width": 48, "height": 48}
    (res,) = until(lambda: writes(fake, "resolution"))
    assert res["prims"] == ["/Render/__specto"]
    assert res["value"] == [[48, 48]]
    # a square frame: the horizontal aperture follows the aspect, the vertical stays
    (aperture,) = writes(fake, "horizontalAperture")
    assert aperture["value"][0][0] == pytest.approx(15.2908, abs=1e-4)
    reader = RingReader.open(ready["ring"])
    try:
        f = wait_frame(reader, width=48, height=48)
        assert pixel(f, 47, 47) == FAKE_COLOR
    finally:
        reader.close()


def test_a_resize_before_the_first_stage_is_the_size_it_loads_at(source, fake, stage_file):
    """#180: the host sends the viewport's size on `ready`, before any stage is loaded;
    the load renders at that size, not the spawn's."""
    ready = source.wait_event("ready")
    assert source.call("resize", width=50, height=40) == {"width": 50, "height": 40}
    time.sleep(0.3)  # the render thread takes the resize in a turn of its own
    source.send("load", path=str(stage_file))
    wait_health(source, "ok")
    reader = RingReader.open(ready["ring"])
    try:
        wait_frame(reader, width=50, height=40)
    finally:
        reader.close()
    assert source.call("state")["width"] == 50


def test_a_resize_is_capped_at_max_pixels_keeping_the_aspect(fake, stage_file):
    src = spawn(fake, "--max-pixels", "2400")
    try:
        load(src, stage_file)
        # 160 x 90 is 14400 px: scaled by sqrt(2400 / 14400) to 65 x 36
        assert src.call("resize", width=160, height=90) == {"width": 65, "height": 36}
    finally:
        src.close()


def test_stepping_stops_once_the_view_settles_and_resumes_on_a_move(fake, stage_file):
    src = spawn(fake, "--settle-steps", "5")
    try:
        load(src, stage_file)
        until(lambda: not src.call("state")["stepping"])
        idle = src.call("state")["steps"]
        assert idle == 5
        time.sleep(0.3)
        assert src.call("state")["steps"] == idle  # idle: no GPU work
        src.send("set_camera", **SIDE_CAMERA, cut=False)
        until(lambda: src.call("state")["steps"] == idle + 5)
    finally:
        src.close()


def test_a_paused_view_does_not_step(fake, stage_file):
    src = spawn(fake, "--settle-steps", "1000000")
    try:
        load(src, stage_file)
        src.call("pause")
        steps = src.call("state")["steps"]
        time.sleep(0.3)
        assert src.call("state")["steps"] <= steps + 2  # at most the steps in flight
        paused = src.call("state")["steps"]
        src.call("resume")
        until(lambda: src.call("state")["steps"] > paused + 10)
    finally:
        src.close()


def test_a_failed_step_is_a_restartable_error_and_restart_recovers(fake, stage_file):
    src = spawn(fake, "--settle-steps", "20", FAKE_OVRTX_FAIL_STEPS="3")
    try:
        ready = src.wait_event("ready")
        src.send("load", path=str(stage_file))
        error = wait_health(src, "error")
        assert error["restartable"] is True
        assert "step" in error["message"]
        assert src.call("state")["failed"] is True
        reader = RingReader.open(ready["ring"])
        try:
            last = until(reader.latest)  # the frames before the failure stay
            src.call("restart")
            assert wait_health(src, "ok")["message"] == "box world.usda"
            wait_frame(reader, after=last.seq)
        finally:
            reader.close()
        assert len(fake.calls("Renderer")) == 2
        assert len(fake.calls("open_usd_from_string")) == 2
        assert src.call("state")["failed"] is False
    finally:
        src.close()


def test_the_source_tears_down_and_exits_when_the_host_closes_stdin(fake, stage_file):
    src = spawn(fake)
    load(src, stage_file)
    src.proc.stdin.close()
    assert src.proc.wait(10) == 0
    tail = [c["call"] for c in fake.calls()][-3:]
    assert tail == ["detach_ovstage", "Stage.destroy", "Renderer.destroy"]


def test_query_tree_pages_the_stage_and_filters_by_name(source, stage_file):
    load(source, stage_file)
    top = source.call("query_tree", path="/", depth=2)
    assert top["total"] == 1
    (world,) = top["children"]
    assert (world["path"], world["type"], world["count"]) == ("/World", "Xform", 2)
    assert [c["name"] for c in world["children"]] == ["Box", "Props"]
    page = source.call("query_tree", path="/World", offset=1, limit=1)
    assert [c["path"] for c in page["children"]] == ["/World/Props"]
    assert page["total"] == 2
    found = source.call("query_tree", filter="bal")
    assert [m["path"] for m in found["matches"]] == ["/World/Props/Ball"]
    with pytest.raises(control.SourceError, match="no prim"):
        source.call("query_tree", path="/Nope")


def test_the_renderer_uploads_no_crash_dumps_and_logs_to_the_temp_folder(source, fake, stage_file):
    load(source, stage_file)
    (renderer,) = fake.calls("Renderer")
    assert renderer["config"]["enable_crash_reporter"] is False
    log = Path(renderer["config"]["log_file_path"])
    assert log.parent == Path(tempfile.gettempdir())


def test_without_ovrtx_the_health_says_how_to_install_it(tmp_path, fake):
    missing = tmp_path / "no_ovrtx"
    missing.mkdir()
    (missing / "ovrtx.py").write_text("raise ModuleNotFoundError(name='ovrtx')\n")
    src = spawn(fake, PYTHONPATH=str(missing))
    try:
        error = wait_health(src, "error")
        assert error["message"] == (
            "ovrtx is not installed: install the specto-app[rtx] extra (needs pypi.nvidia.com)"
        )
    finally:
        src.close()


def test_compiling_shaders_says_first_run_only_on_a_cold_shader_cache(tmp_path, fake):
    cold = tmp_path / "cold"
    cold.mkdir()
    src = spawn(fake, LOCALAPPDATA=str(cold), XDG_CACHE_HOME=str(cold))
    try:
        phase = wait_health(src, "compiling shaders")
        assert phase["message"] == "(first run, about 3 min)"
    finally:
        src.close()
    warm = tmp_path / "warm"
    (warm / "ovrtx" / "0.6.0").mkdir(parents=True)
    (warm / "ovrtx" / "0.6.0" / "shaders.bin").write_bytes(b"x")
    src = spawn(fake, LOCALAPPDATA=str(warm), XDG_CACHE_HOME=str(warm))
    try:
        assert wait_health(src, "compiling shaders")["message"] == ""
    finally:
        src.close()


def test_a_load_reports_where_its_time_went_once_its_named_frame_is_out(fake, stage_file):
    """SPEC-27: once the frame its ``ok`` names is in the ring, a load reports
    ``load_timings`` (seconds): the stage opened with usd-core, the renderer's stage
    populated, the environment set up, from there to that frame, all of it; with
    ``--fresh-renderer`` a load after the first also says what the renderer swap cost."""
    src = spawn(fake, "--fresh-renderer")
    try:
        ready = src.wait_event("ready")
        src.send("load", path=str(stage_file))
        ok = wait_health(src, "ok")
        first = src.wait_event("load_timings", timeout=20)
        assert first.pop("path") == str(stage_file)
        keys = {"open_s", "populate_s", "environment_s", "first_frame_s", "total_s"}
        assert set(first) == keys
        assert all(isinstance(v, float) and v >= 0 for v in first.values())
        parts = sum(first[k] for k in keys - {"total_s"})
        assert first["total_s"] >= parts - 0.01
        reader = RingReader.open(ready["ring"])
        try:
            assert reader.latest().seq >= ok["seq"]  # the named frame is out by now
        finally:
            reader.close()
        src.send("load", path=str(stage_file))
        wait_health(src, "ok")
        again = src.wait_event("load_timings", timeout=20)
        again.pop("path")
        assert set(again) == keys | {"renderer_s"} and again["total_s"] >= again["renderer_s"]
    finally:
        src.close()
