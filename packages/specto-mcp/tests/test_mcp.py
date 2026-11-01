"""The MCP server over the in-process fake endpoint, through a real MCP client: the six
agent tools (ADR 0017), the same ones the prompt bar's agent gets in-process."""

from __future__ import annotations

import json

import anyio
import pytest
from mcp.client import Client as McpClient

from specto_core import Engine
from specto_core.agent_tools import AGENT_TOOL_NAMES
from specto_core.canvas_example import worked_example
from specto_mcp import AgentSessions, build_server
from specto_sdk.testing import InProcessEndpoint


class FakeClock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def ep(clock) -> InProcessEndpoint:
    return InProcessEndpoint(Engine(project=None, clock=clock))


@pytest.fixture
def sessions(ep, clock) -> AgentSessions:
    return AgentSessions(
        lambda origin: ep.client(origin=origin, client="mcp"), default_session="s1", clock=clock
    )


@pytest.fixture
def server(sessions):
    return build_server(sessions)


def run(coro_fn):
    return anyio.run(coro_fn)


def data(result):
    assert not result.is_error, result.content[0].text
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_the_server_exposes_the_six_tools_and_the_fleet_two(server):
    async def go():
        async with McpClient(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}

    tools = run(go)
    assert set(AGENT_TOOL_NAMES) == {
        "state", "describe", "schema", "submit_canvas", "snap", "undo",
    }  # fmt: skip
    assert set(tools) == set(AGENT_TOOL_NAMES) | {"instances", "message"}  # #187
    assert tools["instances"].annotations.read_only_hint is True
    assert tools["state"].annotations.read_only_hint is True
    assert tools["schema"].annotations.read_only_hint is True
    assert tools["undo"].annotations.destructive_hint is True
    assert tools["submit_canvas"].annotations.destructive_hint is False
    assert tools["submit_canvas"].input_schema.get("required", []) == []  # edits or document
    assert tools["state"].input_schema.get("required", []) == []


def test_an_external_client_composes_and_undoes(server, ep):
    async def go():
        async with McpClient(server) as c:
            st = data(await c.call_tool("state", {}))
            assert [v["type"] for v in st["document"]["views"].values()] == ["file_tree"]
            schema = data(await c.call_tool("schema", {}))
            assert schema["schema"]["properties"]["schema_version"]["const"] == 2
            assert schema["example"]["root_view"] == "rena"
            d = data(await c.call_tool("describe", {"target": "link:intaker"}))
            assert d["info"]["maps"] == {"source": "files", "target": "collection"}

            res = data(
                await c.call_tool(
                    "submit_canvas",
                    {"document": worked_example(), "label": "added the run-001 review"},
                )
            )
            assert res["views"]["added"] == ["kobi", "tamu", "rena", "pola", "mihu", "vesa"]
            # the submit is the agent session's, on the one canvas's history
            assert ep.engine.live.record(res["txn"]).origin == "agent:s1"
            snap = data(await c.call_tool("snap", {"target": "rena"}))
            assert snap["kind"] == "view" and snap["meta"]["type"] == "video"
            assert [ln["id"] for ln in snap["meta"]["links"]["in"]] == ["l4", "l9"]
            whole = data(await c.call_tool("snap", {}))
            assert set(whole["meta"]["views"]) == {
                "fege",
                "kobi",
                "tamu",
                "rena",
                "pola",
                "mihu",
                "vesa",
            }

            u = data(await c.call_tool("undo", {}))
            assert u["txn"] == res["txn"] and u["label"] == "added the run-001 review"
            st = data(await c.call_tool("state", {}))
            assert [v["type"] for v in st["document"]["views"].values()] == ["file_tree"]
            assert st["undo"]["can_redo"] is True
            return True

    assert run(go)


def test_an_invalid_submit_returns_every_fixit_and_changes_nothing(server, ep):
    bad = worked_example()
    del bad["links"]["l9"]["summary"]
    bad["links"]["l8"]["source"] = {"view": "rena", "port": "time"}
    bad["root_view"] = "kobi"
    before = ep.engine.live.document()

    async def go():
        async with McpClient(server) as c:
            r = await c.call_tool("submit_canvas", {"document": bad})
            assert r.is_error
            text = r.content[0].text
            assert "[invalid_document]" in text and "3 problems" in text
            for path in ("/links/l9/summary", "/links/l8", "/root_view"):
                assert path in text
            assert text.count("(fix:") == 3
            r = await c.call_tool("undo", {})
            assert r.is_error and "nothing to undo" in r.content[0].text
            r = await c.call_tool("snap", {"target": "lito"})
            assert r.is_error and "[not_found]" in r.content[0].text
            return True

    assert run(go)
    assert ep.engine.live.document() == before


def test_sessions_are_one_connection_each_and_expire(sessions, clock):
    st = sessions.get("a")
    assert st.origin == "agent:a" and len(sessions) == 1
    st_b = sessions.get("b")
    assert st_b.client.connection != st.client.connection
    clock.t += 4000
    assert set(sessions.expire()) == {"a", "b"} and len(sessions) == 0
    st2 = sessions.get("a")
    assert st2 is not st and not st2.client.closed
    sessions.close_all()
    assert st2.client.closed


def test_default_session_when_transport_has_none(sessions):
    assert sessions.key(None) == "s1" and sessions.key("http-7") == "http-7"
    assert sessions.get(None).id == "s1"
    assert sessions.info()[0]["origin"] == "agent:s1"


def test_an_external_client_can_send_edits(server, ep):
    """#98 story 8: an MCP client keeps sending whole documents, or sends edits."""
    tree = next(iter(ep.engine.live.data["views"]))

    async def go():
        async with McpClient(server) as c:
            edits = {"views": {tree: {"label": "all files"}}}
            res = data(await c.call_tool("submit_canvas", {"edits": edits, "label": "relabel"}))
            assert res["views"]["changed"] == [tree]
            r = await c.call_tool("submit_canvas", {"label": "nothing"})
            assert r.is_error and "edits" in r.content[0].text
            return True

    assert run(go)
    assert ep.engine.live.data["views"][tree]["label"] == "all files"


def test_the_instructions_say_how_to_use_a_usd_view_and_that_it_is_read_only():
    from specto_mcp.server import INSTRUCTIONS

    usd = next(p for p in INSTRUCTIONS.split("\n\n") if "usd" in p)
    assert "one stage" in usd and "@usd" in usd and "global clock" in usd
    assert "auto|rtx|storm" in usd and "look_through" in usd and "max_pixels" in usd
    assert "camera port" in usd and "never by running code" in usd
    assert "never edit USD files" in usd
