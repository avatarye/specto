"""The view frame's rounded corners hold for every view type (user hand test of
51e9e34: "square corners under the bottom rounded corners"). The content is clipped to
the rounded shape on the scene graph (RoundedClip), so whatever lies behind the view, the
canvas and its shadow, another view, shows through the corners.

Diagnosis (fix-corners): the frame covered the corners with opaque canvas-coloured
caps; over the card's translucent shadow (and over anything behind the view) each cap
read as a lighter square under the rounded arc. The offscreen software renderer draws
neither the shadow nor a curved clip, so the pixel check runs the shell in a child
process on the real platform (frame_corners_child.py: a tool window parked off every
monitor, never activated) and skips where no GPU path exists."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pdf_helpers import make_pdf
from PySide6.QtGui import QColor, QImage

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.theme import COLORS, METRICS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
R = METRICS["radius_card"]
BG = QColor(COLORS["bg"]).name()
RECT = {"position": [400, 40], "size": [640, 400]}
WEB_TYPES = ["json", "markdown", "map", "html", "3d"]
TYPES = ["file_tree", "gallery", "image", "video", "pdf", *WEB_TYPES]
CHILD = Path(__file__).with_name("frame_corners_child.py")


def compose(app, spin, vtype: str) -> str:
    root = app.project.root
    (root / "pics").mkdir(exist_ok=True)
    img = QImage(16, 16, QImage.Format.Format_RGB32)
    img.fill(QColor("#4080c0"))
    img.save(str(root / "pics" / "a.png"))
    make_pdf(root / "pics" / "a.pdf", 2)
    doc = app.live.document()
    vid = "fege" if vtype == "file_tree" else "kobi"
    if vtype == "file_tree":  # off the window-frame guide's edges
        doc["views"]["fege"]["layout"] = dict(RECT)
        app.engine.call("canvas.submit", {"document": doc, "label": vtype}, AGENT)
    else:
        doc["views"][vid] = {"type": vtype, "label": vtype, "layout": dict(RECT)}
        doc["links"]["l2"] = {
            "kind": "intaker",
            "scope": "pinned",
            "label": "files",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": vid, "port": "data"},
            "params": {"sources": ["pics"]},
            "summary": {"sentence": "The view shows the pictures."},
        }
        app.engine.call("canvas.submit", {"document": doc, "label": vtype}, AGENT)
        spin(until=lambda: vid in app.hosts.hosts)
    spin(300)
    return vid


def frame_rect(app, vid: str) -> tuple[float, float, float, float]:
    item = find_item(app.window.contentItem(), f"frame:{vid}")
    p = item.mapToScene(item.boundingRect().topLeft())
    return (p.x(), p.y(), item.width(), item.height())


def pixel(image, x: float, y: float) -> str:
    return QColor(image.pixel(int(x), int(y))).name()


def reddish(name: str) -> bool:
    c = QColor(name)
    return c.red() > 150 and c.green() < 110 and c.blue() < 110


@pytest.mark.parametrize("vtype", ["file_tree", "gallery", "image", "video", "pdf"])
def test_the_gutter_outside_the_frame_stays_bare_for_every_view_type(app, spin, vtype):
    """Offscreen (software): no content reaches past the frame into the gutter."""
    vid = compose(app, spin, vtype)
    fx, fy, fw, fh = frame_rect(app, vid)
    image = app.window.grabWindow()
    assert pixel(image, fx + R + 6, fy + fh - R - 6) != BG  # the probe sees content
    gutter = {
        "left": pixel(image, fx - 1, fy + fh / 2),
        "right": pixel(image, fx + fw + 1, fy + fh / 2),
        "bottom": pixel(image, fx + fw / 2, fy + fh + 1),
        "below-left": pixel(image, fx - 1, fy + fh + 1),
    }
    assert gutter == dict.fromkeys(gutter, BG), gutter


def test_whatever_lies_behind_shows_through_the_rounded_corners_on_the_gpu(tmp_path):
    """On the real render path, for every view type: a red view behind the corners
    shows through the arcs, the content never reaches past them."""
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        pytest.skip("the parked-window child needs a display (windows or X11)")
    # the child picks its own platform and RHI (conftest pins this process to offscreen);
    # one child per type, so a host that crashes its process only fails its own row
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QT_", "QSG_"))}
    env["FRAME_CORNERS_SAVE"] = str(tmp_path)
    got = {}
    for t in TYPES:
        res = subprocess.run(
            [sys.executable, "-I", str(CHILD), t],
            cwd=CHILD.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        rows = [json.loads(ln) for ln in res.stdout.splitlines() if ln.startswith("{")]
        if rows:
            got[t] = rows[0]
        else:
            got[t] = {"type": t, "error": f"no row (exit {res.returncode}): {res.stderr[-600:]}"}
    rows = list(got.values())
    if any(r.get("api") == "Software" for r in rows):
        pytest.skip("no GPU render path in this environment")
    problems = []
    for t in TYPES:
        r = got[t]
        if "error" in r:
            problems.append(f"{t}: {r['error']}")
            continue
        if not reddish(r["behind"]):
            problems.append(f"{t}: the swatch is not behind the view ({r['behind']})")
        if reddish(r["inside"]):
            problems.append(f"{t}: no content inside ({r['inside']}, host {r['host']})")
        for corner, px in r["outside"].items():
            if not reddish(px):
                problems.append(f"{t}: {corner} shows {px}, not what lies behind")
    assert not problems, "\n".join(problems)
