"""The video draws the right way up (user test 1: front.mp4 played upside down while
the gallery showed the same frame right). A clip whose top half is red and bottom half
blue (ffmpeg) is shown paused in an MpvItem; the top-centre pixel must be red.

libmpv renders through OpenGL, which Windows' offscreen platform cannot give a context
for, so the item lives in a tiny focus-less, input-transparent real window in a child
process, under the desktop lock (other agents drive real mouse tests on this desktop).
Skipped without ffmpeg, the native plugin, a Windows desktop, or the lock within 60 s."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QColor

from specto_app.views import gallery_native

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CHILD = HERE / "video_orient_child.py"
LOCK = ROOT / "tools" / "desktop_lock.py"

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("SPECTO_GUI_TESTS"),
        reason="opens a desktop window; set SPECTO_GUI_TESTS=1 to run",
    ),
    pytest.mark.skipif(
        sys.platform != "win32" and not os.environ.get("DISPLAY"),
        reason="a desktop window (Windows or X11)",
    ),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg"),
    pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT),
    pytest.mark.skipif(
        not (
            gallery_native.NATIVE_MODULE_DIR
            / ("libmpv-2.dll" if sys.platform == "win32" else "libmpv.so.2")
        ).exists(),
        reason="no libmpv",
    ),
]
#: the desktop's own platform: the offscreen one this process runs on has no GL context
DESKTOP_QPA = "windows" if sys.platform == "win32" else "xcb"


def red_top_blue_bottom(path: Path) -> None:
    cmd = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:s=64x32:d=1:r=10",
        "-f", "lavfi", "-i", "color=blue:s=64x32:d=1:r=10",
        "-filter_complex", "[0:v][1:v]vstack",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-t", "1", str(path),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)


def hue(name: str) -> str:
    c = QColor(name)
    if c.red() > 180 and c.blue() < 80 and c.green() < 80:
        return "red"
    if c.blue() > 180 and c.red() < 80 and c.green() < 80:
        return "blue"
    return name


def test_the_first_frame_shows_its_top_at_the_top(tmp_path: Path):
    clip = tmp_path / "redblue.mp4"
    red_top_blue_bottom(clip)
    env = {**os.environ, "QT_QPA_PLATFORM": DESKTOP_QPA}
    env.pop("QT_QUICK_BACKEND", None)
    cmd = [
        sys.executable, str(LOCK), "--wait", "60", "run", "test-video-orientation", "--",
        sys.executable, str(CHILD), str(clip), str(tmp_path / "grab.png"),
    ]  # fmt: skip
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=ROOT, timeout=180)
    if r.returncode == 3:
        pytest.skip("the desktop lock was not free within 60 s")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))
    res = json.loads(line[len("RESULT ") :])
    assert res["backend"] == "opengl" and res["renderFrames"] >= 2, res
    assert (hue(res["top"]), hue(res["bottom"])) == ("red", "blue"), res
