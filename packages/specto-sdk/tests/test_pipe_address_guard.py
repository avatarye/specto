"""A client never opens a pipe address that is not a local pipe (SPEC-74).

The pipe transport opens its address like a file; on Windows ``\\\\host\\pipe\\x`` there
connects out over SMB. Every open is trapped here, so the tests also pass (and prove the
same thing) off Windows.
"""

from __future__ import annotations

import json
import sys

import pytest

from specto_core import Project
from specto_core.errors import SpectoError
from specto_core.rpc import EndpointInfo, endpoint_path
from specto_sdk import Client, TransportError
from specto_sdk import transport as transport_mod
from specto_sdk.transport import PipeTransport, open_transport


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path the pipe transport tries to open (each open then fails)."""
    calls: list[str] = []

    def fake_open(path, *a, **k):
        calls.append(str(path))
        raise FileNotFoundError(2, "trapped", str(path))

    monkeypatch.setattr(transport_mod, "open", fake_open, raising=False)
    return calls


@pytest.mark.parametrize("address", [r"\\atk\pipe\p", "//atk/pipe/p", r"\\?\pipe\p", "a\\b"])
def test_the_pipe_transport_refuses_a_remote_address_before_any_open(opened, address):
    with pytest.raises(TransportError, match="local pipe"):
        PipeTransport(address)
    assert opened == []


@pytest.mark.parametrize("address", ["specto-abc", r"\\.\pipe\specto-abc"])
def test_the_pipe_transport_opens_a_local_pipe(opened, address):
    with pytest.raises(TransportError, match="cannot open pipe"):
        PipeTransport(address)
    assert opened == [r"\\.\pipe\specto-abc"]


def test_a_client_refuses_an_endpoint_file_naming_a_remote_pipe(
    tmp_path, opened, monkeypatch: pytest.MonkeyPatch
):
    """The repro: an agent writes a remote pipe into endpoint.json, then any CLI, MCP or
    SDK connect would open it."""
    monkeypatch.setattr(sys, "platform", "win32")  # the pipe branch, on any OS
    proj = Project.init(tmp_path / "proj", "p")
    endpoint_path(proj).write_text(
        json.dumps({"kind": "pipe", "address": r"\\atk\pipe\p", "pid": 1}), encoding="utf-8"
    )
    with pytest.raises(SpectoError, match="local pipe"):
        Client.open(proj)
    assert opened == []


def test_open_transport_refuses_a_remote_pipe_built_past_the_validator(opened, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    info = EndpointInfo.model_construct(kind="pipe", address=r"\\atk\pipe\p", pid=1)
    with pytest.raises(TransportError, match="local pipe"):
        open_transport(info)
    assert opened == []


def test_a_bracketed_ipv6_loopback_connects_without_its_brackets(monkeypatch):
    """SPEC-98: ``[::1]:5000`` passes the loopback check; create_connection wants ``::1``."""
    calls: list[tuple[str, int]] = []

    def fake_connect(address, *a, **k):
        calls.append(address)
        raise ConnectionRefusedError("trapped")

    monkeypatch.setattr(transport_mod.socket, "create_connection", fake_connect)
    with pytest.raises(TransportError, match="cannot connect"):
        open_transport(EndpointInfo(kind="tcp", address="[::1]:5000", pid=1))
    assert calls == [("::1", 5000)]
