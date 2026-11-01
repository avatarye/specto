"""Seam 2 (#140): selection in ``specto-usd-source``, rtx flavour, with the fake ovrtx
(``tests/fake_ovrtx``): a ``pick`` in NDC (-1..1, +y up) answers the hit prim with its
world position (in the stage's units) and normal; ``select`` outlines the prims;
``properties`` and ``bounds`` read a prim through usd-core; ``reveal`` pages the outliner
down to a prim."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from test_usd_source import STAGE, Fake, load, spawn, until

from specto_app.frames import control


@pytest.fixture
def stage_file(tmp_path: Path) -> Path:
    path = tmp_path / "box world.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path / "ovrtx.log")


@pytest.fixture
def source(fake: Fake):
    src = spawn(fake)
    yield src
    src.close()


def test_a_pick_answers_the_hit_prim_with_its_world_position_in_stage_units(fake, stage_file):
    # the fake hit: the box's near face, in metres; the stage is in centimetres
    src = spawn(fake, FAKE_OVRTX_PICK="/World/Box@0.09,0,0.01@1,0,0")
    try:
        load(src, stage_file)
        hit = src.call("pick", x=-0.5, y=0.0)  # NDC, +y up
        assert hit["hit"] is True and hit["path"] == "/World/Box"
        assert hit["position"] == pytest.approx([9.0, 0.0, 1.0])
        assert hit["normal"] == pytest.approx([1.0, 0.0, 0.0])
        # one pixel of the 64 x 36 frame in ovrtx's [0, 1] from the top left, asking for
        # the tracked info
        (query,) = fake.calls("enqueue_pick_query")
        assert query["product"] == "/Render/__specto"
        assert query["rect"] == pytest.approx([16 / 64, 18 / 36, 17 / 64, 19 / 36])
        assert query["flags"] == 2  # OVRTX_PICK_FLAG_INCLUDE_TRACKED_INFO
    finally:
        src.close()


def test_a_pick_on_nothing_answers_no_hit(fake, stage_file):
    src = spawn(fake, FAKE_OVRTX_PICK="")
    try:
        load(src, stage_file)
        assert src.call("pick", x=0.0, y=0.8) == {"hit": False}
    finally:
        src.close()


def test_a_pick_before_a_stage_is_loaded_is_an_error(fake):
    src = spawn(fake)
    try:
        src.wait_event("ready")
        with pytest.raises(control.SourceError, match="no stage is rendering"):
            src.call("pick", x=0.0, y=0.0)
    finally:
        src.close()


def outlines(fake) -> list[tuple[list[str], int]]:
    return [(c["paths"], c["groups"]) for c in fake.calls("set_selection_outline_group_strings")]


def test_select_outlines_the_geometry_under_the_prims_and_clears_the_last(source, fake, stage_file):
    load(source, stage_file)
    assert source.call("select", paths=["/World/Props"], color="#3d7eff") == {}
    # a Scope has no geometry of its own: its gprims carry the outline, in group 1
    until(lambda: outlines(fake))
    assert outlines(fake) == [(["/World/Props/Ball"], 1)]
    (styles,) = fake.calls("set_selection_group_styles")
    assert styles["styles"]["1"]["outline"] == pytest.approx([0x3D / 255, 0x7E / 255, 1.0, 1.0])

    source.call("select", paths=["/World/Box"], color="#3d7eff")
    until(lambda: len(outlines(fake)) == 3)
    assert outlines(fake)[1:] == [(["/World/Props/Ball"], 0), (["/World/Box"], 1)]
    assert source.call("state")["selected"] == ["/World/Box"]

    source.call("select", paths=[])
    until(lambda: len(outlines(fake)) == 4)
    assert outlines(fake)[-1] == (["/World/Box"], 0)
    assert len(fake.calls("set_selection_group_styles")) == 1  # the colour did not change


MESH_STAGE = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)

def Xform "World" (
    kind = "assembly"
)
{
    def Mesh "Fence" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        point3f[] points = [%s]
        rel material:binding = </World/Looks/Wood>
        custom string specto:note = "north side"
    }
}
"""


def test_properties_reads_a_prims_resolved_values_as_json(source, stage_file):
    load(source, stage_file)
    box = source.call("properties", path="/World/Box")
    assert (box["path"], box["name"], box["type"]) == ("/World/Box", "Box", "Cube")
    props = box["properties"]
    assert props["size"] == 2.0
    assert props["xformOp:translate"] == [10.0, 0.0, 1.0]
    assert props["xformOpOrder"] == ["xformOp:translate"]
    assert props["visibility"] == "inherited"  # a schema fallback is a value too
    with pytest.raises(control.SourceError, match="no prim /World/Nope"):
        source.call("properties", path="/World/Nope")


def test_long_arrays_are_cut_to_their_head_and_length(source, tmp_path):
    points = ", ".join(f"({i}, 0, 0)" for i in range(40))
    path = tmp_path / "fence.usda"
    path.write_text(MESH_STAGE % points, encoding="utf-8", newline="\n")
    load(source, path)
    fence = source.call("properties", path="/World/Fence")
    assert fence["properties"]["points"] == {
        "length": 40,
        "head": [[float(i), 0.0, 0.0] for i in range(16)],
    }
    assert fence["properties"]["material:binding"] == ["/World/Looks/Wood"]
    assert fence["properties"]["specto:note"] == "north side"
    assert fence["schemas"] == ["MaterialBindingAPI"]
    assert source.call("properties", path="/World")["kind"] == "assembly"


def test_bounds_answers_a_prims_world_bounds(source, stage_file):
    load(source, stage_file)
    assert source.call("bounds", path="/World/Box") == {
        "path": "/World/Box",
        "bounds": [[9.0, -1.0, 0.0], [11.0, 1.0, 2.0]],
    }


def test_reveal_pages_each_ancestor_down_to_the_prim(source, stage_file):
    load(source, stage_file)
    # a page of `limit` children from the start, longer when the way down is further on
    out = source.call("reveal", path="/World/Props/Ball", limit=1)
    assert out["path"] == "/World/Props/Ball"
    assert [(lv["path"], lv["offset"], lv["total"]) for lv in out["levels"]] == [
        ("/", 0, 1), ("/World", 0, 2), ("/World/Props", 0, 1),
    ]  # fmt: skip
    assert [[c["path"] for c in lv["children"]] for lv in out["levels"]] == [
        ["/World"], ["/World/Box", "/World/Props"], ["/World/Props/Ball"],
    ]  # fmt: skip
    assert out["levels"][1]["children"][1] == {
        "path": "/World/Props", "name": "Props", "type": "Scope", "count": 1,
    }  # fmt: skip
