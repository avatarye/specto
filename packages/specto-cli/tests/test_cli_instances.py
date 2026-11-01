"""#187: `specto instances` and `specto message <instance> <text>` over the registry."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from specto_cli.main import CliState, cli
from specto_core import Args, CommandContext, Engine, Project, Result
from specto_core.instances import Instance, register
from specto_core.registry import Registry
from specto_core.registry import registry as default_registry
from specto_sdk.testing import InProcessEndpoint


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    monkeypatch.delenv("NIMITZ_ID", raising=False)
    monkeypatch.delenv("NIMITZ_ROLE", raising=False)


class MsgArgs(Args):
    model_config = {"extra": "forbid", "populate_by_name": True}
    sender: str = ""
    text: str = ""
    reply_to: str | None = None
    labels: dict[str, str] | None = None

    def __init__(self, **kw):
        if "from" in kw:
            kw["sender"] = kw.pop("from")
        super().__init__(**kw)


class MsgRes(Result):
    id: int
    mode: str
    status: str
    instance: str
    verified: bool = False


def endpoint_with_message(received: list) -> InProcessEndpoint:
    """The built-ins plus a stand-in for the app's ``message`` command."""
    reg = Registry()
    for spec in default_registry.specs():
        if spec.meta.name != "message":
            reg.register(spec)

    @reg.command("message", doc="a peer request")
    def _message(ctx: CommandContext, args: MsgArgs) -> MsgRes:
        received.append(args.model_dump())
        return MsgRes(id=7, mode="auto", status="running", instance="rasel")

    return InProcessEndpoint(Engine(project=None, registry=reg))


def test_instances_lists_the_live_ones(tmp_path):
    proj = Project.init(tmp_path / "a", "alpha")
    register(
        Instance(
            name="rasel",
            project=str(proj.root),
            project_name="alpha",
            pid=os.getpid(),
            endpoint={"kind": "tcp", "address": "127.0.0.1:1"},
        )
    )
    runner = CliRunner()
    r = runner.invoke(cli, ["instances"], obj=CliState())
    assert r.exit_code == 0 and "rasel  alpha" in r.output
    r = runner.invoke(cli, ["--json", "instances"], obj=CliState())
    assert json.loads(r.output)[0]["name"] == "rasel"


def test_message_finds_the_instance_and_sends_from_the_fleet_member(tmp_path, monkeypatch):
    proj = Project.init(tmp_path / "a", "alpha")
    register(
        Instance(
            name="rasel",
            project=str(proj.root),
            pid=os.getpid(),
            endpoint={"kind": "tcp", "address": "127.0.0.1:9"},
        )
    )
    received: list = []
    ep = endpoint_with_message(received)
    seen: list = []

    def factory(st):
        seen.append((st.project, st.endpoint))
        return ep.client()

    monkeypatch.setenv("NIMITZ_ID", "FLEET/SQUAD/SHIP")
    monkeypatch.setenv("NIMITZ_ROLE", "ship")
    r = CliRunner().invoke(
        cli,
        ["message", "rasel", "show", "the", "rear", "run", "--reply-to", "m1"],
        obj=CliState(client_factory=factory),
    )
    assert r.exit_code == 0, r.output
    assert "[OK] rasel: running" in r.output
    assert seen == [(str(proj.root), "tcp://127.0.0.1:9")]
    # the member's labels go along: the app verifies the sender from them (#190)
    assert received == [
        {
            "sender": "FLEET/SQUAD/SHIP",
            "text": "show the rear run",
            "reply_to": "m1",
            "labels": {"NIMITZ_ID": "FLEET/SQUAD/SHIP", "NIMITZ_ROLE": "ship"},
        }
    ]


def test_message_to_an_unknown_instance_names_the_live_ones(tmp_path):
    r = CliRunner().invoke(cli, ["message", "bakov", "hi"], obj=CliState())
    assert r.exit_code == 1
    assert "no running Specto instance named 'bakov'" in r.output
    assert "none running" in r.output
