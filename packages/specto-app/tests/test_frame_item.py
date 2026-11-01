"""Seam 2 of the frame path (#136): the stub frame source feeds the native FrameItem
through the shared-memory ring and the wake socket; the rendered item shows the stub's
pattern for the frame it reports, and follows size changes, including one that outgrows
the ring.

FrameItem draws through OpenGL, which Windows' offscreen platform lacks, so a child
process renders it with QQuickRenderControl into an FBO: no window is shown, nothing
takes the mouse or the keyboard, and no desktop lock is needed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from specto_app.views import gallery_native

HERE = Path(__file__).resolve().parent
CHILD = HERE / "frame_item_child.py"

pytestmark = [
    pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT),
]

RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
GREY, WHITE = (64, 64, 64), (255, 255, 255)


def run_child(tmp_path: Path, sizes: list[list[int]]) -> list[dict]:
    plan = {"sizes": sizes, "save": str(tmp_path / "grab")}
    r = subprocess.run(
        [sys.executable, str(CHILD), json.dumps(plan)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT ") :])["steps"]


def rgb(img: QImage, x: int, y: int) -> tuple[int, int, int]:
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def assert_pattern(step: dict) -> None:
    """The stub's pattern (see stub_source), checked at its own coordinates: the item is
    the frame's size, so one item pixel is one frame pixel."""
    w, h = step["size"]
    assert (step["frameWidth"], step["frameHeight"]) == (w, h), step
    img = QImage(step["grab"])
    assert (img.width(), img.height()) == (w, h)
    band = h // 8
    assert rgb(img, w // 4, (band + h // 2) // 2) == RED
    assert rgb(img, 3 * w // 4, (band + h // 2) // 2) == GREEN
    assert rgb(img, w // 4, 3 * h // 4) == BLUE
    assert rgb(img, 3 * w // 4, 3 * h // 4) == YELLOW
    # the moving bar sits where frame `frameSeq` puts it: the grab is that frame, whole
    left = (step["frameSeq"] * max(1, w // 64)) % w
    right = min(w, left + max(1, w // 16))
    assert rgb(img, (left + right - 1) // 2, band // 2) == WHITE, step
    assert rgb(img, (left + w // 2) % w, band // 2) == GREY, step  # half a frame away


def test_the_item_shows_the_frame_it_reports(tmp_path: Path):
    (step,) = run_child(tmp_path, [[160, 120]])
    assert step["backend"] == "opengl" and not step["error"], step
    assert step["connected"] and step["frames"] >= 2, step
    assert_pattern(step)


def test_the_item_follows_size_changes_in_and_beyond_the_ring(tmp_path: Path):
    steps = run_child(tmp_path, [[160, 120], [96, 64], [320, 240]])
    for step in steps:
        assert_pattern(step)
    first, smaller, bigger = steps
    assert smaller["ring"] == first["ring"]  # fits the slots: same ring
    assert bigger["ring"] != first["ring"]  # outgrew them: the item followed the successor
