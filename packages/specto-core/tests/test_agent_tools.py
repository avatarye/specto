"""The agent tool set (ADR 0017): state, describe, schema, submit_canvas, snap, undo.
One table of tools, driven in-process over ``engine.call`` here and over MCP by
specto-mcp; the prompt bar's agent (#44) gets the same six."""

from __future__ import annotations

import pytest

from specto_core import CallContext, Engine
from specto_core.agent_tools import AGENT_TOOLS, AgentTools
from specto_core.canvas_document import validate_canvas
from specto_core.canvas_example import worked_example
from specto_core.errors import SpectoError


@pytest.fixture
def tools(engine: Engine) -> AgentTools:
    ctx = CallContext(connection="prompt-bar", origin="agent:s1")
    return AgentTools(lambda name, args: engine.call(name, args, ctx))


def test_the_tool_set_is_exactly_the_six():
    assert list(AGENT_TOOLS) == ["state", "describe", "schema", "submit_canvas", "snap", "undo"]
    assert AGENT_TOOLS["state"].read_only and AGENT_TOOLS["schema"].read_only
    assert AGENT_TOOLS["undo"].destructive and not AGENT_TOOLS["submit_canvas"].destructive
    assert all(t.doc for t in AGENT_TOOLS.values())


def test_an_agent_composes_and_undoes_through_the_tools(tools: AgentTools, engine: Engine):
    st = tools.state()
    assert [v["type"] for v in st["document"]["views"].values()] == ["file_tree"]
    assert st["undo"]["can_undo"] is False
    schema = tools.schema()
    assert schema["schema"]["properties"]["schema_version"]["const"] == 2
    assert validate_canvas(schema["example"])
    assert tools.describe("link:intaker")["info"]["maps"]["target"] == "collection"

    res = tools.submit_canvas(worked_example(), label="added the run-001 review")

    assert res["txn"] and res["views"]["added"] == ["kobi", "tamu", "rena", "pola", "mihu", "vesa"]
    assert engine.live.document()["views"]["rena"]["type"] == "video"
    assert engine.live.record(res["txn"]).origin == "agent:s1"
    snap = tools.snap("rena")
    assert snap["kind"] == "view" and snap["meta"]["type"] == "video"
    assert [ln["id"] for ln in snap["meta"]["links"]["in"]] == ["l4", "l9"]
    assert [ln["id"] for ln in snap["meta"]["links"]["out"]] == ["l10", "l11", "l12"]
    assert tools.state()["undo"] == {
        **tools.state()["undo"],
        "cursor": 1,
        "depth": 1,
        "can_undo": True,
    }

    undo = tools.undo()

    assert undo["txn"] == res["txn"] and undo["label"] == "added the run-001 review"
    assert [v["type"] for v in tools.state()["document"]["views"].values()] == ["file_tree"]
    assert tools.state()["undo"]["history"][-1]["undone"] is True


def test_an_invalid_submit_over_the_tools_names_every_problem(tools: AgentTools, engine: Engine):
    bad = worked_example()
    del bad["links"]["l9"]["summary"]
    bad["root_view"] = "kobi"
    before = engine.live.document()

    with pytest.raises(SpectoError) as e:
        tools.submit_canvas(bad)

    assert e.value.code == "invalid_document"
    assert {p["path"] for p in e.value.data["problems"]} == {"/links/l9/summary", "/root_view"}
    assert engine.live.document() == before


def test_tools_dispatch_by_name_for_adapters(tools: AgentTools):
    """MCP and the Agent SDK call ``run(name, args)`` with the tool's JSON arguments."""
    doc = tools.run("state", {})["document"]
    doc["views"]["fege"]["label"] = "Data"
    assert tools.run("submit_canvas", {"document": doc, "label": "rename"})["views"] == {
        "added": [],
        "removed": [],
        "changed": ["fege"],
    }
    assert tools.run("undo", {})["label"] == "rename"
    with pytest.raises(KeyError):
        tools.run("txn_begin", {})


def test_snap_goes_through_the_apps_snap_command_when_the_app_registered_it(engine: Engine):
    """Story 28: image plus structured state through the same six tools, in-process and over
    MCP. Headless, the image is reported unavailable; the structure is the app's snap."""
    import specto_app.commands  # noqa: F401  (registers snap, shell.locate)

    ctx = CallContext(connection="prompt-bar", origin="agent:s1")
    tools = AgentTools(lambda name, args: engine.call(name, args, ctx))
    tools.submit_canvas(worked_example(), label="compose")
    snap = tools.snap("rena", image=True)
    assert snap["kind"] == "view" and snap["target"] == "rena"
    assert snap["meta"]["type"] == "video" and [ln["id"] for ln in snap["meta"]["links"]["in"]]
    assert snap["image"] is None  # no window in this process
    assert tools.run("snap", {"target": "canvas"})["kind"] == "canvas"
    assert AGENT_TOOLS["snap"].arg("image")["type"] == "boolean"
    assert AGENT_TOOLS["submit_canvas"].required() == []  # edits or document (#98)


def test_the_submit_tool_names_the_default_scope_for_views_off_the_file_tree():
    from specto_core.agent_tools import AGENT_TOOLS

    doc = AGENT_TOOLS["submit_canvas"].doc
    assert "scope 'selection'" in doc and "by default" in doc and "selected_files" in doc
    assert "scope 'pinned'" in doc and "names specific data" in doc
    assert "selection/pinned" in AGENT_TOOLS["describe"].doc


def test_the_agent_is_told_how_the_float_layer_works(tools: AgentTools):
    """#112: the submit tool, describe canvas and the schema say what floating means and
    that a view the user floated is the user's to move."""
    doc = AGENT_TOOLS["submit_canvas"].doc
    assert "layout.floating" in doc and "floated" in doc
    info = tools.describe("canvas")["info"]
    assert "corner" in info["float"] and "by: user" in info["float"]
    assert "unfloat" in info["float"] and "window_frame" in info["float"]
    layout = info["schema"]["$defs"]["Layout"]["properties"]
    assert "floating" in layout and "Float" in info["schema"]["$defs"]


def test_the_agent_submits_edits_naming_only_what_changes(tools: AgentTools, engine: Engine):
    """#98: submit_canvas takes edits (a JSON Merge Patch over the live document) or a
    document, exactly one; the tool doc says how, and either/or comes back as a fix-it."""
    spec = AGENT_TOOLS["submit_canvas"]
    assert set(spec.params["properties"]) == {"edits", "document", "label"}
    assert "required" not in spec.params or not spec.params["required"]
    for words in ("edits", "null", "placeholder", "kept"):
        assert words in spec.doc
    tree = next(iter(engine.live.data["views"]))

    res = tools.run(
        "submit_canvas",
        {"edits": {"views": {tree: {"label": "files"}}}, "label": "relabel the tree"},
    )

    assert res["views"]["changed"] == [tree]
    assert engine.live.data["views"][tree]["label"] == "files"
    for args in ({"label": "nothing"}, {"edits": {}, "document": engine.live.document()}):
        with pytest.raises(SpectoError) as e:
            tools.run("submit_canvas", args)
        assert "edits" in str(e.value) and "document" in str(e.value)


def test_the_agent_is_told_how_collapsing_works(tools: AgentTools):
    """#114: the submit tool and the schema say a collapsed view shows its title bar,
    keeps its links and costs no quota."""
    doc = AGENT_TOOLS["submit_canvas"].doc
    assert "layout.collapsed" in doc
    layout = tools.describe("canvas")["info"]["schema"]["$defs"]["Layout"]["properties"]
    text = layout["collapsed"]["description"]
    assert "title bar" in text and "quota" in text and "links" in text
