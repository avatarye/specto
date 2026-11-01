"""A fresh canvas's file tree fills the visible canvas area (#125): the window frame,
which is the canvas area above the bottom bar inside its padding. Its footer stays in
the window at any window height, on load too, and the tree keeps beside the project
node at the canvas origin (#83). Offscreen Qt; the real window is the scripted flow
tools/gui_flows/v2-125-fresh-tree.txt."""

from __future__ import annotations

import json

import pytest

from specto_core.canvas_document import PROJECT_NODE_RECT, file_tree_of

pytest.importorskip("PySide6.QtQuick")


def locate(app, name: str) -> list[float]:
    found = app.engine.call("shell.locate", {"name": name})
    assert found is not None, name
    return found["rect"]


def assert_tree_fills_the_canvas_area(app) -> None:
    tree = file_tree_of(app.live.data["views"])
    frame = app.live.data["window_frame"]["rect"]
    assert app.live.data["views"][tree]["layout"] == {
        "position": [80.0, 0.0],
        "size": [280.0, frame["h"]],
    }
    ax, ay, _, ah = locate(app, "canvasArea")
    assert frame["h"] == ah - 24
    _, fy, _, fh = locate(app, f"footer:{tree}")
    assert ay < fy and fy + fh <= ay + ah - 12  # the footer sits inside the padding
    px, py, _, _ = locate(app, "projectNode")
    nx, ny, _, _ = PROJECT_NODE_RECT  # the project node at the canvas origin
    assert (px, py) == (ax + 12 + nx, ay + 12 + ny)


@pytest.mark.parametrize("height", [560, 720, 1000])
def test_a_fresh_canvas_file_tree_fits_the_visible_canvas_at_any_window_height(app, spin, height):
    app.window.resize(1300, height)
    area = lambda: locate(app, "canvasArea")  # noqa: E731
    spin(until=lambda: app.live.data["window_frame"]["rect"]["h"] == area()[3] - 24, timeout=3)
    spin(30)
    assert_tree_fills_the_canvas_area(app)
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "resize window") and app.controller.undoDepth == 1


def test_a_fresh_canvas_saved_with_a_tree_taller_than_its_frame_opens_inside_the_window(
    qapp, project, spin
):
    """A canvas made before #125 whose window came out shorter than the default: the
    frame was written back, the tree stayed 900 px. On load the tree fits the frame."""
    from specto_app.app import SpectoApp

    data = json.loads(project.canvas_file.read_text(encoding="utf-8"))
    data["window_frame"]["rect"]["h"] = 700
    project.canvas_file.write_text(json.dumps(data), encoding="utf-8")
    tree = file_tree_of(data["views"])
    assert data["views"][tree]["layout"]["size"] == [280, 900]

    app = SpectoApp(project, jobs="inline", web=False, offscreen=True, serve=False)
    try:
        app.start()
        spin(until=lambda: app.live.data["views"][tree]["layout"]["size"][1] == 700, timeout=3)
        spin(30)
        assert_tree_fills_the_canvas_area(app)
    finally:
        app.close()
        spin(20)
