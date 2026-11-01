"""Selection (#140) in ``specto-usd-source`` with the real ovrtx on the depot's world
(read only): a pick at the picture's centre answers a prim of the stage with a
world position inside its bounds, ``select`` draws ovrtx's outline in the colour asked
for, and ``properties`` reads the picked prim. Skipped without the rtx extra or the file."""

from __future__ import annotations

import importlib.util
import sys

import pytest

if importlib.util.find_spec("ovrtx") is None:
    pytest.skip("needs the rtx extra: uv sync --extra rtx", allow_module_level=True)

from test_usd_source_ovrtx import FIRST_FRAME_S, WORLD, mean_rgb, wait_frame, wait_health

from specto_app.frames import control
from specto_app.frames.ring import RingReader

pytestmark = pytest.mark.ovrtx

MAGENTA = (255, 0, 255)


def magenta_pixels(frame) -> int:
    """Pixels near pure magenta: the outline's colour, which the world never shows."""
    px, n = frame.pixels, 0
    for i in range(0, len(px), 4):
        if px[i] > 200 and px[i + 1] < 80 and px[i + 2] > 200:
            n += 1
    return n


@pytest.mark.skipif(not WORLD.is_file(), reason="no depot world here (SPECTO_P4_WORLD)")
def test_a_pick_on_the_depot_world_selects_and_outlines_a_prim():
    src = control.FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx",
         "--width", "640", "--height", "360"]
    )  # fmt: skip
    try:
        ready = src.wait_event("ready", timeout=30)
        src.send("load", path=str(WORLD))
        loaded = src.wait_event("loaded", timeout=FIRST_FRAME_S)
        wait_health(src, "ok", FIRST_FRAME_S)
        reader = RingReader.open(ready["ring"])
        try:
            f = wait_frame(reader, 60, width=640, height=360)
            assert mean_rgb(f) > 20
            hit = src.call("pick", x=0.0, y=-0.1, timeout=30)  # NDC, +y up
            assert hit["hit"] and hit["path"].startswith("/"), hit
            lo, hi = loaded["bounds"]
            for v, a, b in zip(hit["position"], lo, hi, strict=True):
                assert a - 1 <= v <= b + 1, hit
            assert abs(sum(n * n for n in hit["normal"]) - 1) < 0.05
            print("[OK] picked", hit)

            before = magenta_pixels(wait_frame(reader, 60, after=f.seq))
            src.call("select", paths=[hit["path"]], color="#ff00ff")
            assert src.call("state")["selected"] == [hit["path"]]
            f = wait_frame(reader, 60, after=reader.latest().seq + 3)
            after = magenta_pixels(f)
            print(f"[OK] outline pixels {before} -> {after}")
            assert after > before + 20

            props = src.call("properties", path=hit["path"])
            assert props["path"] == hit["path"] and props["type"]
            bounds = src.call("bounds", path=hit["path"])["bounds"]
            assert bounds is not None
            src.call("select", paths=[])
            f = wait_frame(reader, 60, after=reader.latest().seq + 3)
            assert magenta_pixels(f) <= before + 5
        finally:
            reader.close()
    finally:
        assert src.close(timeout=60) == 0
