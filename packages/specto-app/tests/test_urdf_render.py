"""SPEC-43.2, the urdf page's pixel checks: the real page in headless Qt WebEngine (no
window on screen; ``urdf_render_child.py``) loads the hand-written arm model of
``packages/specto-web/tests/fixtures/urdf/arm.json`` with an STL and an OBJ written
here, then renders it lit by the two lights, in the collision mode, in visual +
collision, and lit by the bundled studio HDRI. Checked: the meshes keep the robot's frame
(an STL standing on Z stands up, an OBJ along X points along X), the collision mode
draws the links in the usd view's tints with no visual grey left, visual + collision
mixes the two, and the HDRI lights the robot differently with the lights off. Needs the
vendored Babylon and HDRIs (``packages/specto-web/build.py``) and a WebGL2 context;
skipped without them. GPU tier: run under ``tools/gpu_guard.py``."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

import specto_web

CHILD = Path(__file__).with_name("urdf_render_child.py")
WEB = specto_web.find_web_root()
STUDIO = specto_web.environment_file("studio")
ARM = (
    Path(__file__).resolve().parents[2] / "specto-web" / "tests" / "fixtures" / "urdf" / "arm.json"
)
# the usd view's first two collision tints (specto_app/usd/collision.py TINTS): base_link,
# upper_arm, as 8-bit colours (StandardMaterial emissive, no lighting)
TINT_BASE = (51, 204, 115)
TINT_UPPER = (64, 166, 242)

pytestmark = [
    pytest.mark.skipif(
        WEB is None or not (WEB / "vendor" / "babylon" / "babylon.js").is_file(),
        reason="Babylon is not vendored: " + specto_web.BUILD_HINT,
    ),
    pytest.mark.skipif(STUDIO is None, reason="the HDRIs are not vendored: build.py --hdri-only"),
]

STEPS = [
    {"params": {"view_mode": "lit", "environment": "none"}},
    {"params": {"view_mode": "collision"}},
    {"params": {"view_mode": "visual_collision"}},
    {"params": {"view_mode": "lit", "environment": "studio"}},
]


def box_triangles(lo: tuple[float, float, float], hi: tuple[float, float, float]):
    """The 12 outward triangles of an axis-aligned box, counter-clockwise from outside."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
    tris = []
    for a, b, c, d in quads:
        tris += [(v[a], v[b], v[c]), (v[a], v[c], v[d])]
    return v, quads, tris


def write_stl(path: Path, lo, hi) -> Path:
    _, _, tris = box_triangles(lo, hi)
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0) for t in tris)
    path.write_bytes(bytes(80) + struct.pack("<I", len(tris)) + body)
    return path


def write_obj(path: Path, lo, hi) -> Path:
    v, quads, _ = box_triangles(lo, hi)
    lines = [f"v {x} {y} {z}" for x, y, z in v] + [
        f"f {a + 1} {b + 1} {c + 1} {d + 1}" for a, b, c, d in quads
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def model_for(tmp: Path) -> dict:
    """The arm with its meshes on disk: forearm an STL standing 0.3 m on its Z, wrist an
    OBJ reaching 0.3 m along its X, the collision STL a thinner copy."""
    m = json.loads(ARM.read_text(encoding="utf-8"))
    stl = write_stl(tmp / "segment.stl", (-0.03, -0.03, 0.0), (0.03, 0.03, 0.3)).as_uri()
    col = write_stl(
        tmp / "segment_collision.stl", (-0.025, -0.025, 0.0), (0.025, 0.025, 0.3)
    ).as_uri()
    obj = write_obj(tmp / "reach.obj", (0.0, -0.02, -0.02), (0.3, 0.02, 0.02)).as_uri()
    for link in m["links"]:
        for part in link["visuals"] + link["collisions"]:
            g = part["geometry"]
            if g["type"] == "mesh" and g["url"]:
                g["url"] = col if "collision" in g["url"] else stl
        if link["name"] == "wrist":
            link["visuals"][0]["geometry"] = {
                "type": "mesh",
                "url": obj,
                "ext": "obj",
                "scale": [1, 1, 1],
            }
    return m


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


def count_near(img: QImage, box, rgb, tol: int = 18) -> int:
    x0, y0, w, h = box
    n = 0
    for y in range(y0, y0 + h, 2):
        for x in range(x0, x0 + w, 2):
            c = img.pixelColor(x, y)
            if (
                abs(c.red() - rgb[0]) <= tol
                and abs(c.green() - rgb[1]) <= tol
                and abs(c.blue() - rgb[2]) <= tol
            ):
                n += 1
    return n


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("urdf-render")
    plan = {
        "model": model_for(tmp),
        "out": str(tmp / "out"),
        "envs": [{"name": "studio", "label": "Studio", "file": str(STUDIO)}],
        "params": {"show_grid": False, "up": "z"},
        "steps": STEPS,
    }
    res = subprocess.run(
        [sys.executable, str(CHILD), json.dumps(plan)],
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    line = next((x for x in res.stdout.splitlines() if x.startswith("RESULT ")), None)
    said = f"{res.stdout[-3000:]}\n{res.stderr[-3000:]}"
    assert line, f"no result from the child (rc {res.returncode})\n{said}"
    out = json.loads(line[len("RESULT ") :])
    if "WebGL" in str(out.get("error", "")):
        pytest.skip(f"no WebGL2 here: {out['error']}")
    assert "error" not in out, f"{out['error']}\n{said}"
    return out


def canvas_box(step: dict, frac: float = 1.0) -> tuple[int, int, int, int]:
    x, y, w, h = step["report"]["canvas"]
    mx, my = int(w * (1 - frac) / 2), int(h * (1 - frac) / 2)
    return (x + mx, y + my, w - 2 * mx, h - 2 * my)


def test_meshes_keep_the_robots_frame(renders):
    """STL and OBJ coordinates are the link's: the STL stands up the robot's Z (Babylon's
    +Y under up z), the OBJ reaches along +X; swapped axes would stick out along Babylon Z
    or flip X."""
    r = renders["loaded"]
    assert not r["meshes"]["failed"], r["meshes"]["failed"]
    assert r["meshes"]["placeholders"] == 1, "only gripper's missing mesh stands in"
    lo, hi = r["bounds"]
    assert abs(hi[0] - 0.3) < 0.02, f"the OBJ reaches +X: {r['bounds']}"
    assert abs(lo[0] + 0.15) < 0.02, f"nothing flipped to -X: {r['bounds']}"
    assert lo[2] > -0.16 and hi[2] < 0.16, f"nothing sticks out along Babylon Z: {r['bounds']}"
    assert abs(lo[1]) < 0.01 and 0.95 < hi[1] < 1.0, (
        f"the arm stands up from the ground: {r['bounds']}"
    )


def test_collision_draws_each_link_in_its_tint_and_no_visual(renders):
    lit, col = renders["steps"][0], renders["steps"][1]
    assert col["report"]["view_mode"] == "collision"
    img_lit, img_col = QImage(lit["png"]), QImage(col["png"])
    box = canvas_box(col, 0.9)
    assert count_near(img_col, box, TINT_BASE) > 50, "base_link in the first tint"
    assert count_near(img_col, box, TINT_UPPER) > 50, "upper_arm in the second tint"
    assert count_near(img_lit, box, TINT_BASE) < 5, "the tints only show in collision"
    # the robot's lit grey is gone: the visuals are hidden
    grey = mean_rgb(img_lit, box)
    assert sum(abs(a - b) for a, b in zip(grey, mean_rgb(img_col, box), strict=True)) > 3


def test_visual_collision_mixes_the_lit_visuals_and_the_shells(renders):
    lit, col, both = (renders["steps"][i] for i in range(3))
    img = {k: QImage(s["png"]) for k, s in (("lit", lit), ("col", col), ("both", both))}
    box = canvas_box(both, 0.9)
    tinted = count_near(img["both"], box, TINT_BASE, tol=40)
    assert tinted < count_near(img["col"], box, TINT_BASE, tol=40), "shells, not solid tints"
    m = {k: mean_rgb(v, box) for k, v in img.items()}
    assert m["both"] != m["lit"] and m["both"] != m["col"]


def test_the_studio_hdri_lights_the_robot_with_the_lights_off(renders):
    lit, hdri = renders["steps"][0], renders["steps"][3]
    env = hdri["report"]["environment"]
    assert env["name"] == "studio" and env["ready"] and env["lights"] is False
    assert lit["report"]["environment"]["lights"] is True
    # over the robot's own pixels (where the lit grab is not the background)
    img0, img1 = QImage(lit["png"]), QImage(hdri["png"])
    x0, y0, w, h = canvas_box(hdri, 0.9)
    bg = img0.pixelColor(x0 + 2, y0 + 2)
    total, n = 0.0, 0
    for y in range(y0, y0 + h, 2):
        for x in range(x0, x0 + w, 2):
            a, b = img0.pixelColor(x, y), img1.pixelColor(x, y)
            if (
                abs(a.red() - bg.red()) + abs(a.green() - bg.green()) + abs(a.blue() - bg.blue())
                < 30
            ):
                continue
            total += (
                abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
            ) / 3
            n += 1
    assert n > 500, f"the robot covers {n} sampled pixels"
    assert total / n > 4, f"the HDRI changes the robot by {total / n:.1f} levels on average"
