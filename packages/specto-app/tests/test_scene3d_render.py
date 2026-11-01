"""#175, the web pixel check: the real 3D page in headless Qt WebEngine (no window on
screen; ``scene3d_render_child.py``) loads a GLB of PBR spheres and renders it with no
environment (the two lights), then with the bundled studio HDRI: the spheres light
differently, a lower intensity darkens them, and the background shows the HDRI only
when asked. Needs the vendored Babylon and HDRIs (``packages/specto-web/build.py``) and a
WebGL2 context; skipped without them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from scene3d_helpers import write_pbr_spheres_glb

import specto_web

CHILD = Path(__file__).with_name("scene3d_render_child.py")
WEB = specto_web.find_web_root()
STUDIO = specto_web.environment_file("studio")

pytestmark = [
    pytest.mark.skipif(
        WEB is None or not (WEB / "vendor" / "babylon" / "babylon.js").is_file(),
        reason="Babylon is not vendored: " + specto_web.BUILD_HINT,
    ),
    pytest.mark.skipif(STUDIO is None, reason="the HDRIs are not vendored: build.py --hdri-only"),
]

STEPS = [
    {"environment": "none"},
    {"environment": "studio"},
    {"environment": "studio", "environment_background": True},
    {"environment": "studio", "environment_background": False, "environment_intensity": 0.25},
]


def mean_rgb(img: QImage, box: tuple[int, int, int, int]) -> list[float]:
    x0, y0, w, h = box
    sums, n = [0.0, 0.0, 0.0], 0
    for y in range(y0, y0 + h, 2):
        for x in range(x0, x0 + w, 2):
            c = img.pixelColor(x, y)
            sums[0] += c.red()
            sums[1] += c.green()
            sums[2] += c.blue()
            n += 1
    return [s / n for s in sums]


def diff(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / 3


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("render3d")
    glb = write_pbr_spheres_glb(tmp / "spheres.glb")
    plan = {
        "glb": str(glb),
        "out": str(tmp / "out"),
        "envs": [{"name": "studio", "label": "Studio", "file": str(STUDIO)}],
        "steps": STEPS,
    }
    res = subprocess.run(
        [sys.executable, str(CHILD), json.dumps(plan)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    line = next((x for x in res.stdout.splitlines() if x.startswith("RESULT ")), None)
    said = f"{res.stdout[-3000:]}\n{res.stderr[-3000:]}"
    assert line, f"no result from the child (rc {res.returncode})\n{said}"
    out = json.loads(line[len("RESULT ") :])
    if "WebGL" in str(out.get("error", "")):
        pytest.skip(f"no WebGL2 here: {out['error']}")
    assert "error" not in out, f"{out['error']}\n{res.stdout[-3000:]}"
    return out


def regions(step: dict) -> tuple[QImage, tuple[int, int, int, int], tuple[int, int, int, int]]:
    """The grab, the spheres' band across the canvas middle, and the canvas's top band
    (background only)."""
    img = QImage(step["png"])
    x, y, w, h = step["report"]["canvas"]
    band = (x + int(w * 0.12), y + int(h * 0.42), int(w * 0.76), int(h * 0.16))
    sky = (x + 4, y + 4, w - 8, int(h * 0.12))
    return img, band, sky


def test_the_studio_hdri_lights_the_pbr_spheres_differently_from_the_two_lights(renders):
    none, studio = renders["steps"][0], renders["steps"][1]
    assert none["report"]["environment"]["lights"] is True
    env = studio["report"]["environment"]
    assert env["name"] == "studio" and env["ready"] and env["lights"] is False
    img0, band, sky = regions(none)
    img1, _, _ = regions(studio)
    d = diff(mean_rgb(img0, band), mean_rgb(img1, band))
    assert d > 6, f"the spheres look the same lit by the HDRI ({d:.1f} levels apart)"
    assert diff(mean_rgb(img0, sky), mean_rgb(img1, sky)) < 2, "no background unless asked"
    assert env["load_ms"] is not None and env["load_ms"] < 5000, env


def test_a_lower_intensity_darkens_the_spheres(renders):
    full, dim = renders["steps"][1], renders["steps"][3]
    img1, band, _ = regions(full)
    img3, _, _ = regions(dim)
    assert dim["report"]["environment"]["intensity"] == 0.25
    assert sum(mean_rgb(img3, band)) < sum(mean_rgb(img1, band)) - 15


def test_the_background_shows_the_hdri_only_when_asked(renders):
    plain, sky_on = renders["steps"][1], renders["steps"][2]
    assert sky_on["report"]["environment"]["background"] is True
    img1, _, sky = regions(plain)
    img2, _, _ = regions(sky_on)
    assert diff(mean_rgb(img1, sky), mean_rgb(img2, sky)) > 6, "the studio behind the scene"
