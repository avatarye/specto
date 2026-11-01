"""#187: the MCP server's instances and message tools, through a real MCP client."""

from __future__ import annotations

import json
import os

import anyio
import pytest
from mcp.client import Client as McpClient

from specto_core import Args, CommandContext, Engine, Result
from specto_core.instances import Instance, register
from specto_core.registry import Registry
from specto_core.registry import registry as default_registry
from specto_mcp import AgentSessions, build_server
from specto_sdk.testing import InProcessEndpoint


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("NIMITZ_ID", "FLEET/SQUAD/NIMITZ")
    monkeypatch.setenv("NIMITZ_ROLE", "ship")


class PeerArgs(Args):
    model_config = {"extra": "allow"}


class PeerRes(Result):
    id: int
    mode: str
    status: str
    instance: str


def target(received: list) -> InProcessEndpoint:
    reg = Registry()
    for spec in default_registry.specs():
        if spec.meta.name != "message":
            reg.register(spec)

    @reg.command("message", doc="a peer request")
    def _message(ctx: CommandContext, args: PeerArgs) -> PeerRes:
        received.append(args.model_dump())
        return PeerRes(id=3, mode="ask", status="waiting", instance="tomvi")

    return InProcessEndpoint(Engine(project=None, registry=reg))


def data(result):
    assert not result.is_error, result.content[0].text
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_instances_and_message(tmp_path):
    register(
        Instance(
            name="tomvi",
            project=str(tmp_path / "b"),
            project_name="beta",
            pid=os.getpid(),
            endpoint={"kind": "tcp", "address": "127.0.0.1:9"},
        )
    )
    received: list = []
    ep = target(received)
    connected: list = []

    def connect(inst, origin):
        connected.append((inst.name, inst.url(), origin))
        return ep.client(origin=origin)

    home = InProcessEndpoint(Engine(project=None))
    sessions = AgentSessions(lambda origin: home.client(origin=origin), default_session="s1")
    server = build_server(sessions, connect=connect)

    async def go():
        async with McpClient(server) as c:
            live = data(await c.call_tool("instances", {}))
            sent = data(await c.call_tool("message", {"instance": "tomvi", "text": "hi"}))
            missing = await c.call_tool("message", {"instance": "bakov", "text": "hi"})
            return live, sent, missing

    live, sent, missing = anyio.run(go)
    assert [i["name"] for i in live["instances"]] == ["tomvi"]
    assert sent["status"] == "waiting" and sent["instance"] == "tomvi"
    assert connected == [("tomvi", "tcp://127.0.0.1:9", "agent:mcp")]
    # the server runs in the member's session: its labels go along to verify it (#190)
    labels = {"NIMITZ_ID": "FLEET/SQUAD/NIMITZ", "NIMITZ_ROLE": "ship"}
    assert received == [{"from": "FLEET/SQUAD/NIMITZ", "text": "hi", "labels": labels}]
    assert missing.is_error and "no running Specto instance named 'bakov'" in (
        missing.content[0].text
    )
