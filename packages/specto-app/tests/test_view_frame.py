"""The view frame in the normal view (user feedback on v2): one chain-coloured rounded
rectangle wraps the whole view, header and body, rounded at all four corners, drawn
inside a 2 px transparent gutter so two snapped views show a 4 px gap; the content clips
to the rounded shape. Offscreen Qt."""

from __future__ import annotations

import pytest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.theme import METRICS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


def add_view(app, spin, vid: str, position: list, size=(640, 400)) -> None:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": vid,
        "layout": {"position": list(position), "size": list(size)},
    }
    pin(doc, vid)
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {vid}"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)


def scene_rect(item) -> tuple[float, float, float, float]:
    """The item's rect in canvas-area px (the area sits under the top bar)."""
    area = item
    while area is not None and area.objectName() != "canvasArea":
        area = area.parentItem()
    p = item.mapToItem(area, item.boundingRect().topLeft())
    return (p.x(), p.y(), item.width(), item.height())


def test_the_frame_wraps_the_whole_view_inside_the_gutter_and_rounds_every_corner(app, spin):
    add_view(app, spin, "kobi", [300, 40])
    root = app.window.contentItem()
    frame = find_item(root, "frame:kobi")
    assert frame is not None and frame.isVisible()
    # the view's rect is at pad + position; the frame is that rect inset by the gutter
    assert scene_rect(frame) == (12 + 300 + 2, 12 + 40 + 2, 640 - 4, 400 - 4)
    assert frame.property("gutter") == 2
    radius = METRICS["radius_card"]
    stroke = find_item(root, "frameStroke:kobi")
    assert stroke is not None and scene_rect(stroke) == scene_rect(frame)
    for corner in ("topLeftRadius", "topRightRadius", "bottomLeftRadius", "bottomRightRadius"):
        assert stroke.property(corner) == radius, corner
    # the stroke lies above the content: header and body are both inside it
    header = find_item(root, "header:kobi")
    slot = find_item(root, "slot:kobi")
    fx, fy, fw, fh = scene_rect(frame)
    hx, hy, hw, _hh = scene_rect(header)
    sx, sy, sw, sh = scene_rect(slot)
    assert fx <= hx and hy >= fy and hx + hw <= fx + fw
    assert fx <= sx and sx + sw <= fx + fw and sy + sh <= fy + fh
    assert sy + sh > fy + fh - 4  # the body reaches the frame's bottom edge
    assert stroke.z() > slot.z()
    # the content clips to the rounded shape on the scene graph: a RoundedClip one pixel
    # inside the frame, its curve concentric with the stroke; no opaque corner caps
    clip = find_item(root, "frameClip:kobi")
    assert clip is not None and clip.isVisible() and clip.property("clip") is True
    assert clip.property("radius") == radius - 1
    cx, cy, cw, ch = scene_rect(clip)
    assert (cx, cy, cw, ch) == (fx + 1, fy + 1, fw - 2, fh - 2)
    assert find_item(root, "frameCaps:kobi") is None


def test_two_snapped_views_show_a_four_pixel_gap_between_their_frames(app, spin):
    add_view(app, spin, "kobi", [360, 0])  # flush against the file tree's right edge
    root = app.window.contentItem()
    tree = scene_rect(find_item(root, "frame:fege"))
    view = scene_rect(find_item(root, "frame:kobi"))
    assert view[0] - (tree[0] + tree[2]) == 4
    # the document rects still touch: snapping removed the gap, the gutter draws it
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [360, 0]
