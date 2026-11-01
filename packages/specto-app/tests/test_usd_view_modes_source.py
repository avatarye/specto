"""Seam 2 (#153): view modes in ``specto-usd-source``, rtx flavour, with the fake ovrtx
(``tests/fake_ovrtx``): ``set_view_mode`` writes the product attributes ovrtx's render
settings schema names (the albedo debug view, the wireframe mode, DLSS) in one commit
with a history reset; collision adds the overlay as an inline reference and hides the
stage's gprims, and going back puts their own visibility back; ``view_modes`` says what
the stage can show and why not."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from test_usd_source import STAGE, Fake, load, spawn, until, writes

from specto_app.frames import control

COLLIDERS = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Crate" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 2
    }

    def Sphere "Decor"
    {
        token visibility = "invisible"
    }
}
"""
PRODUCT = "/Render/__specto"
RESET = "omni:rtx:viewTile:renderHistoryReset:requestId"


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path / "ovrtx.log")


@pytest.fixture
def plain_stage(tmp_path: Path) -> Path:
    path = tmp_path / "box world.usda"
    path.write_text(STAGE, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def collider_stage(tmp_path: Path) -> Path:
    path = tmp_path / "crates.usda"
    path.write_text(COLLIDERS, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def source(fake: Fake):
    src = spawn(fake)
    yield src
    src.close()


def last(fake: Fake, attr: str) -> dict:
    return writes(fake, attr)[-1]


def test_the_inline_root_starts_lit_with_the_mode_attributes_authored(source, fake, plain_stage):
    load(source, plain_stage)
    (root,) = fake.calls("open_usd_from_string")
    assert 'token omni:rtx:wireframe:mode = "instance"' in root["text"]
    assert 'string omni:rtx:debug:view:target = ""' in root["text"]
    # wires 1 px wide: the default 0.3 px draws them dashed
    assert "float omni:rtx:wireframe:thickness = 1.0" in root["text"]
    assert source.call("state")["view_mode"] == "lit"


def test_view_modes_says_collision_needs_colliders(source, plain_stage):
    load(source, plain_stage)
    answer = source.call("view_modes")
    assert answer["mode"] == "lit"
    modes = {m["mode"]: m for m in answer["modes"]}
    assert list(modes) == ["lit", "unlit", "wireframe", "collision"]
    assert all(modes[m]["available"] for m in ("lit", "unlit", "wireframe"))
    assert modes["collision"] == {
        "mode": "collision",
        "available": False,
        "reason": "no prims with UsdPhysics CollisionAPI",
    }
    with pytest.raises(control.SourceError, match="CollisionAPI"):
        source.call("set_view_mode", mode="collision")
    assert source.call("state")["view_mode"] == "lit"


def test_unlit_is_the_albedo_debug_view_at_native_resolution_in_one_commit(
    source, fake, plain_stage
):
    load(source, plain_stage)
    assert source.call("set_view_mode", mode="unlit") == {"mode": "unlit", "note": ""}
    target = last(fake, "omni:rtx:debug:view:target")
    assert target["prims"] == [PRODUCT]
    assert target["semantic"] == "STRING" and target["value"] == ["diffuseReflectance"]
    reset = last(fake, RESET)
    assert reset["ordinal"] == target["ordinal"]
    # at the frame's own resolution (the debug view under DLSS performance is half),
    # no wireframe
    assert last(fake, "omni:rtx:post:dlss:execMode")["value"] == ["rtxaa"]
    assert writes(fake, "omni:rtx:wireframe:mode") == []
    until(lambda: [s for s in fake.calls("step") if s["ordinal"] == target["ordinal"]])
    assert source.call("state")["view_mode"] == "unlit"


def test_wireframe_draws_wires_at_native_resolution_and_lit_undoes_it(source, fake, plain_stage):
    load(source, plain_stage)
    source.call("set_view_mode", mode="wireframe")
    wire = last(fake, "omni:rtx:wireframe:mode")
    assert wire["semantic"] == "TOKEN_ID" and wire["value"] == ["shaded"]
    assert last(fake, "omni:rtx:debug:view:target")["value"] == ["diffuseReflectance"]
    assert last(fake, "omni:rtx:post:dlss:execMode")["value"] == ["rtxaa"]
    source.call("set_view_mode", mode="lit")
    assert last(fake, "omni:rtx:wireframe:mode")["value"] == ["instance"]
    assert last(fake, "omni:rtx:debug:view:target")["value"] == [""]
    assert last(fake, "omni:rtx:post:dlss:execMode")["value"] == ["performance"]


def test_collision_shows_the_overlay_hides_the_stage_and_lit_puts_it_back(
    source, fake, collider_stage
):
    load(source, collider_stage)
    answer = source.call("set_view_mode", mode="collision")
    assert answer == {"mode": "collision", "note": "1 collider"}
    (ref,) = fake.calls("add_usd_reference_from_string")
    assert ref["target"] == "/__specto/Collision"
    assert 'defaultPrim = "Collision"' in ref["text"] and "def Mesh" in ref["text"]
    hide = last(fake, "visibility")
    assert sorted(hide["prims"]) == ["/World/Crate", "/World/Decor"]
    assert hide["value"] == ["invisible", "invisible"]
    target = last(fake, "omni:rtx:debug:view:target")
    assert target["value"] == ["diffuseReflectance"] and target["ordinal"] == hide["ordinal"]
    assert last(fake, "omni:rtx:post:dlss:execMode")["value"] == ["rtxaa"]
    (apply,) = fake.calls("apply_usd_changes")
    assert apply["ordinal"] == hide["ordinal"]

    source.call("set_view_mode", mode="lit")
    (removed,) = fake.calls("remove_usd")
    assert removed["handle"] == ref["handle"]
    restored = {c["prims"][0]: c["value"][0] for c in writes(fake, "visibility")[1:]}
    assert restored == {"/World/Crate": "inherited", "/World/Decor": "invisible"}
    assert source.call("state")["view_mode"] == "lit"


def test_a_pick_in_collision_names_the_collider(fake, collider_stage):
    src = spawn(fake, FAKE_OVRTX_PICK="/__specto/Collision/C00000@0,0,1@0,0,1")
    try:
        load(src, collider_stage)
        src.call("set_view_mode", mode="collision")
        hit = src.call("pick", x=0.0, y=0.0)
        assert hit["hit"] is True and hit["path"] == "/World/Crate"
        # ovrtx picks nothing under the debug view: lifted for the pick, then back
        targets = [w["value"] for w in writes(fake, "omni:rtx:debug:view:target")]
        assert targets[-2:] == [[""], ["diffuseReflectance"]]
    finally:
        src.close()


def test_an_unknown_mode_is_an_error_and_a_new_load_starts_lit(source, fake, plain_stage):
    load(source, plain_stage)
    with pytest.raises(control.SourceError, match="unknown view mode 'xray'"):
        source.call("set_view_mode", mode="xray")
    source.call("set_view_mode", mode="unlit")
    source.send("load", path=str(plain_stage))
    until(lambda: len(fake.calls("open_usd_from_string")) == 2)
    until(lambda: source.call("state")["view_mode"] == "lit")
