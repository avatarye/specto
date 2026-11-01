"""Snapping in the normal view (spec #35 story 65, glossary "Snapping"): a moved or
resized view's borders pull flush against a neighbouring view or the window-frame guide
within a few pixels; further away nothing happens. Qt-free; the worked example's layout
(file tree 280x900 at the origin, a 1600x900 window frame) gives the literals."""

from __future__ import annotations

from specto_app.canvas.snapping import snap_move, snap_resize, visible_ids

FRAME = (0.0, 0.0, 1600.0, 900.0)
TREE = {"files": (0.0, 0.0, 280.0, 900.0)}


def test_moving_a_view_snaps_its_left_edge_to_the_tree_and_its_top_to_the_frame():
    out = snap_move((286.0, 7.0, 640.0, 400.0), TREE, FRAME, threshold=10)
    assert out.rect == (280.0, 0.0, 640.0, 400.0)
    assert out.guides == (("x", 280.0), ("y", 0.0))


def test_moving_a_view_snaps_its_right_edge_to_the_frame():
    out = snap_move((955.0, 300.0, 640.0, 400.0), TREE, FRAME, threshold=10)
    assert out.rect == (960.0, 300.0, 640.0, 400.0)
    assert out.guides == (("x", 1600.0),)


def test_far_from_any_border_nothing_snaps():
    out = snap_move((300.0, 40.0, 640.0, 400.0), TREE, FRAME, threshold=10)
    assert out.rect == (300.0, 40.0, 640.0, 400.0) and out.guides == ()


def test_the_view_itself_is_never_a_neighbour():
    rects = {**TREE, "kobi": (300.0, 40.0, 640.0, 400.0)}
    out = snap_move((303.0, 40.0, 640.0, 400.0), rects, FRAME, threshold=10, exclude="kobi")
    assert out.rect == (303.0, 40.0, 640.0, 400.0)


def test_resizing_from_the_bottom_right_corner_snaps_to_the_frame_edges():
    out = snap_resize((280.0, 0.0, 1312.0, 893.0), {"right", "bottom"}, TREE, FRAME, threshold=10)
    assert out.rect == (280.0, 0.0, 1320.0, 900.0)
    assert out.guides == (("x", 1600.0), ("y", 900.0))


def test_resizing_from_the_left_edge_moves_the_origin_and_keeps_the_right_edge():
    out = snap_resize((286.0, 0.0, 634.0, 400.0), {"left"}, TREE, FRAME, threshold=10)
    assert out.rect == (280.0, 0.0, 640.0, 400.0)


def test_a_view_never_shrinks_below_the_minimum_size():
    out = snap_resize((300.0, 40.0, 50.0, 30.0), {"right", "bottom"}, {}, None, threshold=10)
    assert out.rect == (300.0, 40.0, 200.0, 120.0)


def test_visible_ids_are_the_views_that_overlap_the_viewport():
    rects = {
        "files": (0.0, 0.0, 280.0, 900.0),
        "kobi": (300.0, 0.0, 640.0, 400.0),
        "tamu": (3000.0, 0.0, 400.0, 300.0),  # far right: off screen
        "rena": (1590.0, 880.0, 400.0, 300.0),  # a corner peeks in
    }
    assert visible_ids((0.0, 0.0, 1600.0, 900.0), rects) == {"files", "kobi", "rena"}
    assert visible_ids((2800.0, 0.0, 1600.0, 900.0), rects) == {"tamu"}


def test_snapped_views_keep_flush_document_rects_but_their_drawn_frames_show_a_gap():
    """The frame is drawn inside a transparent gutter (GUTTER px): two views snapped edge
    to edge keep touching document rects and show a 2 * GUTTER gap between frames."""
    from specto_app.canvas.snapping import GUTTER, drawn_rect

    moved = snap_move((286.0, 7.0, 640.0, 400.0), TREE, FRAME, threshold=10).rect
    assert moved[0] == TREE["files"][0] + TREE["files"][2]  # flush in the document
    tree, view = drawn_rect(TREE["files"]), drawn_rect(moved)
    assert GUTTER == 2.0
    assert view[0] - (tree[0] + tree[2]) == 4.0
    assert view == (282.0, 2.0, 636.0, 396.0)
