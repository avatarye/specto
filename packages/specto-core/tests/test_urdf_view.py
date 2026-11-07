"""The urdf view's vocabulary in core (SPEC-43.1, ADR 0028): the ``urdf`` data kind and
its ``@urdf`` screener alias, the view type (role viewer, never the root view), its ports,
and the dedicated view a double-click on a .urdf opens (ADR 0023). Its manifest is the
app's, as the 3d view's is."""

from __future__ import annotations

import pytest
from doc_helpers import app_plugins

from specto_core import Engine, gestures
from specto_core.canvas_document import CanvasInvalid, file_tree_of
from specto_core.kinds import DATA_KINDS, default_plugins
from specto_core.live_canvas import LiveCanvas
from specto_core.ports import PLAYER_TYPES, VIEW_PORTS, VIEW_ROLES, VIEW_TYPES


def test_a_urdf_file_is_of_the_urdf_kind_behind_the_urdf_alias():
    plugins = default_plugins()
    assert "urdf" in DATA_KINDS
    assert plugins.kinds_for_path("robots/arm.urdf") == ["urdf"]
    assert plugins.kinds_for_path("robots/ARM.URDF") == ["urdf"]
    assert plugins.aliases()["@urdf"] == [".urdf"]


def test_the_urdf_view_is_a_viewer_never_the_root_view():
    assert "urdf" in VIEW_TYPES and VIEW_ROLES["urdf"] == "viewer"
    assert "urdf" not in PLAYER_TYPES  # design decision 2


def test_the_urdf_views_ports():
    decl = VIEW_PORTS["urdf"]
    assert decl.inputs == {
        "data": "collection",
        "joints": "collection",
        "time": "time",
        "camera": "camera",
        "selection": "selection",
    }
    assert decl.outputs == {"camera": "camera", "selection": "selection", "content": "content"}


def test_describe_gives_the_urdf_views_role_and_ports_without_the_app(engine: Engine):
    # its manifest (params, cost) is the app's, as the 3d view's is (specto-app
    # test_urdf_host); core knows the type, its role and its ports
    info = engine.call("describe", {"target": "view:urdf"})["info"]
    assert info["role"] == "viewer"
    assert info["ports"]["outputs"] == {
        "camera": "camera",
        "selection": "selection",
        "content": "content",
    }


def test_a_double_click_on_a_urdf_opens_its_dedicated_urdf_view():
    live = LiveCanvas.headless("demo")
    tree = file_tree_of(live.data["views"])
    plugins = app_plugins()
    assert plugins.typed_view_for("robots/arm.urdf") == "urdf"
    opened = gestures.open_file(live, tree, "robots/arm.urdf", mode="dedicated", plugins=plugins)
    doc = live.document()
    view = doc["views"][opened.view]
    assert view["type"] == "urdf" and view["label"] == "arm.urdf · robots"
    (feed,) = [ln for ln in doc["links"].values() if ln["target"]["view"] == opened.view]
    assert (feed["kind"], feed["scope"]) == ("intaker", "pinned")
    assert feed["target"]["port"] == "data" and feed["params"]["sources"] == ["robots/arm.urdf"]
    # the preview has no urdf renderer yet: Ctrl+double-click opens the dedicated view
    other = gestures.open_file(live, tree, "robots/cart.urdf", mode="contextual", plugins=plugins)
    assert other.mode == "dedicated"
    assert live.document()["views"][other.view]["type"] == "urdf"


def test_a_urdf_view_may_not_be_the_root_view():
    live = LiveCanvas.headless("demo")
    doc = live.document()
    doc["views"]["robo"] = {"type": "urdf", "label": "arm", "layout": {"size": [600, 400]}}
    doc["root_view"] = "robo"
    with pytest.raises(CanvasInvalid, match="player"):
        live.submit(doc, origin="agent:t", label="root")
