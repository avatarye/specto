"""``specto-usd-source`` with the real Storm (SPEC-31): the environment light. The HDRI is a
dome light composed in the session layer (another picture, lit, the stage file untouched);
the sky falls back to the HDRI with the note, without a reload, since Storm cannot render
MDL; the stage's own light comes back. Skipped where this machine has no Storm or the
HDRI is not built."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

from specto_app.frames import control
from specto_app.frames.ring import RingReader
from specto_app.usd import environment as env
from specto_core.machine import detect_storm

STORM = detect_storm()
ASSETS = env.find_assets()
pytestmark = [
    pytest.mark.skipif(not STORM.present, reason=STORM.reason),
    pytest.mark.skipif(
        ASSETS.hdri is None, reason="the HDRI is not built: packages/specto-web/build.py"
    ),
]

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

    def DistantLight "Sun"
    {
        float inputs:intensity = 5000
    }
}
"""


def wait_health(src, state: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == "error":
            raise AssertionError(f"the source failed: {params}")
        if params.get("state") == state:
            return params


def frame_after(reader: RingReader, after: int, timeout: float = 30.0):
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


def test_the_hdri_lights_the_stage_the_sky_falls_back_to_it_and_the_file_is_untouched(
    tmp_path: Path,
):
    path = tmp_path / "sun.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    before = path.read_bytes()
    src = control.FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.usd.source", "--renderer", "storm",
         "--width", "320", "--height", "180"]
    )  # fmt: skip
    try:
        ready = src.wait_event("ready", timeout=60)
        reader = RingReader.open(ready["ring"])
        try:
            src.send("load", path=str(path), environment="stage")
            loaded = src.wait_event("loaded", timeout=120)
            assert (loaded["environment"], loaded["environment_note"]) == ("stage", "")
            ok = wait_health(src, "ok")
            plain = rgb(frame_after(reader, ok["seq"] + 2))

            seq = reader.latest().seq
            answer = src.call("set_environment", environment="hdri", timeout=60)
            assert (answer["environment"], answer["environment_note"]) == ("hdri", "")
            hdri = rgb(frame_after(reader, seq + 2))
            assert np.abs(hdri - plain).mean() > 10, "the dome is another picture"
            top = hdri[: hdri.shape[0] // 6]
            assert top.std() > 5, "the HDRI is drawn behind the stage, not a flat clear colour"
            assert float((hdri.min(axis=-1) == 255).mean()) < 0.3, "not burnt out"

            # the sky: Storm cannot render MDL, the HDRI stays and the note says why
            answer = src.call("set_environment", environment="sky", timeout=60)
            assert (answer["environment"], answer["environment_note"]) == ("hdri", env.STORM_NOTE)
            assert answer["renderer_note"] == "MDL materials show grey; no sky"
            state = src.call("state")
            assert state["environment"] == "hdri" and state["loaded"] == str(path)

            seq = reader.latest().seq
            answer = src.call("set_environment", environment="stage", timeout=60)
            assert answer["environment"] == "stage"
            again = rgb(frame_after(reader, seq + 2))
            assert np.abs(again - plain).mean() < 2, "the headlight look again"
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0
    assert path.read_bytes() == before
