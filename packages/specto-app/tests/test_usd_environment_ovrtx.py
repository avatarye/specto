"""``specto-usd-source`` with the real ovrtx (SPEC-31): the environment light on a stage
without lights. Loaded with the Clear sky, the frame is lit by the sky (blue above the
horizon, not the fallback dome's flat white); ``set_environment`` switches to the HDRI
and back to the stage's own light without a reload, each a different, lit frame. Skipped
without the rtx extra, the fetched sky (``tools/fetch_nvidia_sky.py``) or the built HDRI
(``packages/specto-web/build.py``); a warm shader cache takes about 30 s."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
import pytest

# find_spec, not importorskip: the test process itself never loads ovrtx (#135)
if importlib.util.find_spec("ovrtx") is None:
    pytest.skip("needs the rtx extra: uv sync --extra rtx", allow_module_level=True)

from specto_app.frames import control
from specto_app.frames.ring import RingReader
from specto_app.usd import environment as env

pytestmark = pytest.mark.ovrtx

ASSETS = env.find_assets()
FIRST_FRAME_S = 400  # a cold shader cache compiles for about 2.5 minutes, then the scene

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "World"
{
    def Mesh "Ground"
    {
        int[] faceVertexCounts = [4]
        int[] faceVertexIndices = [0, 1, 2, 3]
        normal3f[] normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1)]
        point3f[] points = [(-10, -10, 0), (10, -10, 0), (10, 10, 0), (-10, 10, 0)]
        color3f[] primvars:displayColor = [(0.5, 0.5, 0.5)]
    }

    def Cube "Box"
    {
        double size = 2
        color3f[] primvars:displayColor = [(1, 0.1, 0.1)]
        double3 xformOp:translate = (0, 0, 1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""


def wait_health(src, state: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == "error":
            raise AssertionError(f"the source failed: {params}")
        if params.get("state") == state:
            return params


def settled_frame(reader: RingReader, after: int, timeout: float = 60.0):
    """A frame past ``after``: the settle steps after a change have converged by then."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = reader.latest()
        if f is not None and f.seq > after:
            return f
        time.sleep(0.01)
    raise AssertionError("no frame")


def rgb(frame) -> np.ndarray:
    px = np.frombuffer(bytes(frame.pixels), np.uint8).reshape(frame.height, frame.stride // 4, 4)
    return px[:, : frame.width, :3].astype(float)


def lit_share(image: np.ndarray) -> float:
    return float((image.max(axis=-1) > 40).mean())


@pytest.mark.skipif(ASSETS.sky is None, reason="the sky is not fetched: tools/fetch_nvidia_sky.py")
@pytest.mark.skipif(
    ASSETS.hdri is None, reason="the HDRI is not built: packages/specto-web/build.py"
)
def test_the_sky_and_the_hdri_light_a_light_less_stage_and_switch_without_a_reload(
    tmp_path: Path,
):
    path = tmp_path / "plain.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    src = control.FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx",
         "--width", "320", "--height", "180"]
    )  # fmt: skip
    try:
        ready = src.wait_event("ready", timeout=30)
        reader = RingReader.open(ready["ring"])
        try:
            src.send("load", path=str(path), environment="sky")
            loaded = src.wait_event("loaded", timeout=FIRST_FRAME_S)
            assert (loaded["environment"], loaded["environment_note"]) == ("sky", "")
            assert "renderer_note" not in loaded  # no default dome to mention
            ok = wait_health(src, "ok", FIRST_FRAME_S)
            sky = rgb(settled_frame(reader, after=ok["seq"] + 40))
            top = sky[: sky.shape[0] // 6].mean(axis=(0, 1))  # the sky above the horizon
            assert lit_share(sky) > 0.9, "the sky lights everything"
            assert top[2] >= top[0], f"a sky behind, bluer than red: {top}"

            answer = src.call("set_environment", environment="hdri", timeout=30)
            assert answer == {"environment": "hdri", "note": "", "renderer_note": ""}
            state = src.call("state")
            assert state["environment"] == "hdri" and state["loaded"] == str(path)
            hdri = rgb(settled_frame(reader, after=reader.latest().seq + 40))
            assert lit_share(hdri) > 0.9
            assert np.abs(hdri - sky).mean() > 10, "the HDRI is another picture"

            answer = src.call("set_environment", environment="stage", timeout=30)
            assert answer["environment"] == "stage"
            assert answer["renderer_note"] == "no lights in the stage: a default dome light"
            plain = rgb(settled_frame(reader, after=reader.latest().seq + 40))
            assert lit_share(plain) > 0.9
            assert np.abs(plain - hdri).mean() > 10 and np.abs(plain - sky).mean() > 10
            flat = plain[: plain.shape[0] // 6].mean(axis=(0, 1))
            assert abs(flat[2] - flat[0]) < 12, f"the uniform dome behind again: {flat}"
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0
