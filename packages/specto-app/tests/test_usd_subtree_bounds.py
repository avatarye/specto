"""Seam 1 (#39): the source's ``bounds`` of a prim are its whole subtree's world bounds
(``StageInfo.bounds_of`` through usd-core): a parent Xform with no geometry of its own
frames all of its children, an invisible child is left out, a prim under which nothing
draws has none, and the bounds follow the stage's time."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from pxr import Usd, UsdGeom

from specto_app.usd.stage import open_stage

CUBE = 2.0  # the cubes' size: each spans +-1 about its centre


def build(path: Path) -> None:
    """A Y-up stage: ``/World/Group`` (an Xform at x 10, no geometry) holding cubes ``A``
    at x -3 and ``B`` at x +3 (so the group spans x 6 .. 14), an invisible cube
    ``Hidden`` high above, an empty scope ``Nothing``, and ``Mover``, a cube that
    slides from x 0 at time 0 to x 100 at time 10."""
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    group = UsdGeom.Xform.Define(stage, "/World/Group")
    group.AddTranslateOp().Set((10.0, 0.0, 0.0))
    for name, x in (("A", -3.0), ("B", 3.0)):
        cube = UsdGeom.Cube.Define(stage, f"/World/Group/{name}")
        cube.AddTranslateOp().Set((x, 0.0, 0.0))
        cube.GetSizeAttr().Set(CUBE)
    hidden = UsdGeom.Cube.Define(stage, "/World/Group/Hidden")
    hidden.AddTranslateOp().Set((0.0, 50.0, 0.0))
    hidden.GetSizeAttr().Set(CUBE)
    hidden.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    UsdGeom.Scope.Define(stage, "/World/Nothing")
    mover = UsdGeom.Cube.Define(stage, "/World/Mover")
    mover.GetSizeAttr().Set(CUBE)
    slide = mover.AddTranslateOp()
    slide.Set((0.0, 0.0, 0.0), Usd.TimeCode(0))
    slide.Set((100.0, 0.0, 0.0), Usd.TimeCode(10))
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(10)
    stage.GetRootLayer().Save()


@pytest.fixture
def info(tmp_path: Path):
    build(tmp_path / "subtree.usda")
    return open_stage(str(tmp_path / "subtree.usda"))


def test_a_parent_with_no_geometry_frames_its_children_together(info):
    # the group's own box is empty; A and B, 2 wide each at x 7 and 13, span 6 .. 14
    assert info.bounds_of("/World/Group") == {
        "path": "/World/Group",
        "bounds": [[6.0, -1.0, -1.0], [14.0, 1.0, 1.0]],
    }
    assert info.bounds_of("/World/Group/A")["bounds"] == [[6.0, -1.0, -1.0], [8.0, 1.0, 1.0]]
    assert info.bounds_of("/World/Group/B")["bounds"] == [[12.0, -1.0, -1.0], [14.0, 1.0, 1.0]]


def test_an_invisible_child_is_left_out_of_its_parents_bounds(info):
    # Hidden sits at y 50: the group's bounds stop at y 1 without it
    assert info.bounds_of("/World/Group")["bounds"][1][1] == 1.0
    assert info.bounds_of("/World")["bounds"][1][1] == 1.0


def test_a_prim_under_which_nothing_draws_has_no_bounds(info):
    assert info.bounds_of("/World/Nothing") == {"path": "/World/Nothing", "bounds": None}


def test_the_bounds_follow_the_stages_time(info):
    at_start = info.bounds_of("/World/Mover", timecode=0.0)["bounds"]
    assert at_start == [[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]]
    at_end = info.bounds_of("/World/Mover", timecode=10.0)["bounds"]
    assert at_end == [[99.0, -1.0, -1.0], [101.0, 1.0, 1.0]]
    # the default time: no time samples apply, the op's default value (none authored:
    # the identity), as the stage's own bounds are read
    assert (
        info.bounds_of("/World/Mover")["bounds"] == info.bounds_of("/World/Mover", None)["bounds"]
    )
    # the whole world moves with the mover: its subtree bounds at the end time
    assert info.bounds_of("/World", timecode=10.0)["bounds"][1][0] == 101.0
