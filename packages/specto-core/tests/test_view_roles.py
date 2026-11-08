"""View roles (ADR 0020, spec #103): every view type declares exactly one role, by what it
does (browser, viewer, player, scene), and the agent reads it in describe and the schema."""

from __future__ import annotations

import pytest

from specto_core import Engine
from specto_core.kinds import PluginManifest, PluginRegistry, ViewManifest
from specto_core.ports import PLAYER_TYPES, VIEW_PORTS, VIEW_ROLES, VIEW_TYPES

#: spec #103 decision 1 and its "View type" record
SPEC_ROLES = {
    "file_tree": "browser",
    "gallery": "browser",
    "image": "viewer",
    "markdown": "viewer",
    "json": "viewer",
    "html": "viewer",
    "pdf": "viewer",
    "preview": "viewer",
    "video": "player",
    "usd": "player",  # #132 decision usd-role
    "scratch": "browser",  # #184: many items, a selection out
    "3d": "scene",
    "map": "scene",
    "text": "viewer",  # SPEC-32: the "Not supported" view, one short text in its native form
    "urdf": "viewer",  # SPEC-43: one robot, never the root view
}


def test_every_view_type_declares_the_role_the_spec_gives_it():
    assert VIEW_ROLES == SPEC_ROLES


def test_every_registered_view_type_declares_exactly_one_known_role(engine: Engine):
    registered = set(VIEW_TYPES) | set(VIEW_PORTS) | {m.type for m in engine.plugins.views()}
    assert registered <= set(VIEW_ROLES)
    assert set(VIEW_ROLES.values()) <= {"browser", "viewer", "player", "scene"}


def test_the_players_are_the_views_with_the_player_role():
    assert PLAYER_TYPES == {t for t, r in VIEW_ROLES.items() if r == "player"}


def test_a_plugin_view_type_without_a_role_is_refused():
    reg = PluginRegistry()
    with pytest.raises(ValueError, match="role"):
        reg.register(
            PluginManifest(name="ext", views=[ViewManifest(type="spectrogram", host="quick")])
        )
    assert reg.view("spectrogram") is None and "ext" not in reg.plugins


def test_describe_shows_each_view_types_role(engine: Engine):
    for view_type, role in SPEC_ROLES.items():
        info = engine.call("describe", {"target": f"view:{view_type}"})["info"]
        assert info["role"] == role, view_type


def test_api_info_lists_the_role_of_each_view_type(engine: Engine):
    view_types = engine.call("api.info", {})["view_types"]
    assert {v["type"]: v["role"] for v in view_types} == {
        "gallery": "browser",
        "json": "viewer",
        "usd": "player",
        "scratch": "browser",
    }


def test_the_canvas_schema_names_each_view_types_role(engine: Engine):
    schema = engine.call("describe", {"target": "canvas"})["info"]["schema"]  # agent: schema
    doc = schema["$defs"]["View"]["properties"]["type"]["description"]
    for view_type, role in SPEC_ROLES.items():
        assert f"{view_type} ({role})" in doc
    assert "browser" in doc and "feeds" in doc
