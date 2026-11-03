"""The ``usd`` view type (#135, design usd.md rev 2 sections 2 and 4), at seam 1: what the
agent reads (describe, the canvas schema) and what a submit resolves to (quotas, the
root view). No host and no rendering here: those are later tickets."""

from __future__ import annotations

import sys

import pytest
from doc_helpers import pin

from specto_core import Engine
from specto_core.canvas_document import CanvasInvalid
from specto_core.machine import MachineCapability, usd_renderer
from specto_core.ports import PLAYER_TYPES, VIEW_ROLES
from specto_core.quotas import Quotas

AGENT = "agent:s1"


def _describe(engine: Engine, target: str) -> dict:
    return engine.call("describe", {"target": target})["info"]


def test_usd_is_a_player_so_it_can_be_the_root_view():
    assert VIEW_ROLES["usd"] == "player"
    assert "usd" in PLAYER_TYPES


def test_describe_view_usd_lists_the_designs_ports(engine: Engine):
    info = _describe(engine, "view:usd")
    assert info["role"] == "player"
    assert info["ports"] == {
        "inputs": {
            "data": "collection",
            "selection": "selection",
            "time": "time",
            "camera": "camera",
        },
        "outputs": {
            "selection": "selection",
            "time": "time",
            "camera": "camera",
            "content": "content",
        },
        # data, selection -> selection, content; time -> time; camera -> camera
        "propagates": {
            "data": ["selection", "content"],
            "selection": ["selection", "content"],
            "time": ["time"],
            "camera": ["camera"],
        },
    }


def test_describe_view_usd_is_a_frame_host_accepting_usd_stages(engine: Engine):
    info = _describe(engine, "view:usd")
    assert info["host"] == "frame"
    assert info["accepts"] == ["usd_stage_ref"]
    assert "usd" in _describe(engine, "kind:usd_stage_ref")["accepted_by"]


def test_the_agent_schema_names_each_param_with_its_choices(engine: Engine):
    info = _describe(engine, "view:usd")
    assert info["defaults"] == {
        "renderer": "auto",
        "mode": "rt",
        "look_through": None,
        "camera_speed": 1.0,
        "camera_mode": "free",
        "max_pixels": 1920 * 1080,
        "spp": 1,
        "view_mode": "lit",
        "environment": "stage",
        "sky": "ClearSky",
        "hdri": "sunny",
        "time_of_day": None,
        "cloudiness": None,
        "haze": None,
        "ground": False,
    }
    props = info["state_schema"]["properties"]
    assert props["renderer"]["enum"] == ["auto", "rtx", "storm"]
    assert props["mode"]["enum"] == ["rt", "pt", "minimal"]
    assert props["view_mode"]["enum"] == ["lit", "unlit", "wireframe", "collision"]  # #153
    assert props["camera_mode"]["enum"] == ["free", "orbit"]  # #39
    assert props["environment"]["enum"] == ["stage", "sky", "hdri"]  # SPEC-31
    assert props["environment"]["default"] == "stage"
    assert "Storm shows the HDRI" in props["environment"]["description"]
    assert props["sky"]["enum"][0] == "ClearSky" and len(props["sky"]["enum"]) == 7
    import specto_web  # the enum is the 3d view's manifest, in its order

    names = [e["name"] for e in specto_web.environments()]
    assert props["hdri"]["enum"] == names and "sunny" in names
    assert (props["ground"]["type"], props["ground"]["default"]) == ("boolean", False)
    for name, top in (("time_of_day", 24), ("cloudiness", 1), ("haze", 1)):
        assert props[name]["type"] == ["number", "null"] and props[name]["maximum"] == top
    assert (props["camera_speed"]["minimum"], props["camera_speed"]["maximum"]) == (0.125, 8)
    assert {"look_through", "selected", "max_pixels", "spp", "panels"} <= set(props)


def test_the_describe_example_submits_as_it_stands(engine: Engine):
    example = _describe(engine, "view:usd")["example"]
    live = engine.live
    doc = live.document()
    tree = next(v for v, view in doc["views"].items() if view["type"] == "file_tree")
    assert tree == "fege"  # the example names the fresh canvas's file tree
    doc["views"]["kobi"] = example["view"] | {"layout": {"position": [330, 0], "size": [640, 420]}}
    doc["views"]["tamu"] = {
        "type": "video",
        "layout": {"position": [1000, 0], "size": [480, 320]},
    }
    pin(doc, "tamu")
    for i, link in enumerate(example["links"]):
        doc["links"][f"new{i}"] = link
    result = live.submit(doc, origin=AGENT, label="a world with a video beside it")
    assert set(result.views["added"]) == {"kobi", "tamu"}
    links = live.data["links"].values()
    kinds = {link["kind"] for link in links if link["target"]["view"] == "kobi"}
    assert kinds == {"intaker", "time_map"}


def test_a_usd_view_may_be_the_root_view(engine: Engine):
    live = engine.live
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "usd",
        "layout": {"position": [330, 0], "size": [640, 420]},
    }
    doc["links"]["new1"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"screener": ["@usd"]},
        "summary": {"sentence": "kobi shows the stage picked in fege."},
    }
    doc["root_view"] = "kobi"
    live.submit(doc, origin=AGENT, label="the stage drives the clock")
    assert live.data["root_view"] == "kobi"


def test_usd_files_are_stages_by_extension_under_the_usd_alias(engine: Engine):
    for ext in (".usd", ".usda", ".usdc", ".usdz", ".USDA"):
        assert engine.plugins.kinds_for_extension(ext) == ["usd_stage_ref"]
    assert engine.plugins.aliases()["@usd"] == [".usd", ".usda", ".usdc", ".usdz"]
    assert "usd" in _describe(engine, "kind:usd_stage_ref")["produced_by"]


def _two_stages(engine: Engine, second_renderer: str) -> dict:
    doc = engine.live.document()
    for i, (name, x) in enumerate((("kobi", 330), ("tamu", 980))):
        params = {"renderer": second_renderer} if i else {}
        doc["views"][name] = {
            "type": "usd",
            "layout": {"position": [x, 0], "size": [600, 400]},
            "params": params,
        }
        pin(doc, name)
    return doc


def test_one_live_rtx_view_per_canvas(engine: Engine):
    assert Quotas().rtx_views == 1
    with pytest.raises(CanvasInvalid) as e:
        engine.live.submit(_two_stages(engine, "rtx"), origin=AGENT, label="two worlds")
    [problem] = e.value.problems
    assert "rtx_views=1" in problem["message"] and "kobi" in problem["message"]
    assert "renderer: storm" in problem["fix"]


def test_a_storm_view_costs_no_rtx_quota(engine: Engine):
    result = engine.live.submit(_two_stages(engine, "storm"), origin=AGENT, label="rtx and storm")
    assert set(result.views["added"]) == {"kobi", "tamu"}


def _engine_with_ovrtx(present: bool, storm: bool = False) -> Engine:
    cap = MachineCapability(name="ovrtx", present=present, reason="test machine")
    st = MachineCapability(name="storm", present=storm, reason="test machine")
    return Engine(project=None, capabilities=lambda: {"ovrtx": cap, "storm": st})


def test_describe_says_auto_renders_with_rtx_where_ovrtx_is_present(monkeypatch):
    """On Windows and Linux: rtx first, with or without a Storm build beside it."""
    monkeypatch.setattr(sys, "platform", "win32")
    info = _describe(_engine_with_ovrtx(True), "view:usd")
    assert info["uses"] == ["ovrtx", "storm"]
    assert info["machine"]["ovrtx"]["present"] is True
    assert info["machine"]["storm"]["present"] is False
    assert info["renderer_auto"] == "rtx"
    monkeypatch.setattr(sys, "platform", "linux")
    info = _describe(_engine_with_ovrtx(True, storm=True), "view:usd")
    assert info["machine"]["storm"]["present"] is True
    assert info["renderer_auto"] == "rtx"


def test_describe_says_auto_renders_with_storm_on_macos(monkeypatch):
    """On macOS auto is always Storm, whatever the machine reports for ovrtx."""
    monkeypatch.setattr(sys, "platform", "darwin")
    info = _describe(_engine_with_ovrtx(True, storm=True), "view:usd")
    assert info["renderer_auto"] == "storm"
    info = _describe(_engine_with_ovrtx(True), "view:usd")
    assert info["renderer_auto"] == "storm"


def test_describe_says_auto_falls_back_to_storm_and_why():
    info = _describe(_engine_with_ovrtx(False), "view:usd")
    assert info["machine"]["ovrtx"] == {
        "name": "ovrtx",
        "present": False,
        "version": None,
        "reason": "test machine",
    }
    assert info["renderer_auto"] == "storm"


def test_the_renderer_follows_the_param_and_the_capability():
    rtx = {"ovrtx": MachineCapability(name="ovrtx", present=True)}
    none = {"ovrtx": MachineCapability(name="ovrtx", present=False)}
    storm = {"storm": MachineCapability(name="storm", present=True)}
    for platform in ("win32", "linux"):
        assert usd_renderer({}, rtx, platform) == "rtx"
        assert usd_renderer({}, rtx | storm, platform) == "rtx"  # rtx first, Storm or not
        assert usd_renderer({"renderer": "auto"}, none, platform) == "storm"
        assert usd_renderer({}, none | storm, platform) == "storm"
        assert usd_renderer({"renderer": "storm"}, rtx, platform) == "storm"
        # asked for: the host says why not
        assert usd_renderer({"renderer": "rtx"}, none, platform) == "rtx"


def test_on_macos_auto_is_storm_and_the_explicit_renderers_still_win(monkeypatch):
    """ovrtx does not run on macOS: auto is Storm there even on a machine that reports
    ovrtx present; rtx and storm by name are honoured as everywhere. Without a platform
    argument the choice follows sys.platform."""
    from specto_core.machine import rtx_first

    rtx = {"ovrtx": MachineCapability(name="ovrtx", present=True)}
    storm = {"storm": MachineCapability(name="storm", present=True)}
    assert rtx_first("win32") and rtx_first("linux") and not rtx_first("darwin")
    assert usd_renderer({}, rtx | storm, "darwin") == "storm"
    assert usd_renderer({}, rtx, "darwin") == "storm"
    assert usd_renderer({"renderer": "rtx"}, rtx | storm, "darwin") == "rtx"
    assert usd_renderer({"renderer": "storm"}, rtx, "darwin") == "storm"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert not rtx_first() and usd_renderer({}, rtx) == "storm"
    monkeypatch.setattr(sys, "platform", "win32")
    assert rtx_first() and usd_renderer({}, rtx) == "rtx"
