"""Where an endpoint read from a file may point (SPEC-88, after SPEC-74's pipes).

``.specto/endpoint.json`` and the instance files are agent-writable. A tcp address there
may name only this machine (loopback): a remote host would let an agent redirect every
CLI, SDK and MCP call to a server it controls. A remote host is the user's own choice
only through an explicit ``--endpoint`` / ``SPECTO_ENDPOINT`` url. A unix socket's path
is never a network or device path, wherever it comes from.
"""

from __future__ import annotations

import json
import os

import pytest
from pydantic import ValidationError

from specto_core import Project
from specto_core.errors import SpectoError
from specto_core.instances import Instance, list_instances, register
from specto_core.pathguard import endpoint_refusal
from specto_core.rpc import EndpointInfo, endpoint_path, read_endpoint_file

LOOPBACK = [
    "127.0.0.1:5000",
    "127.8.9.1:5000",
    "localhost:5000",
    "LOCALHOST:1",
    "[::1]:5000",
    ":5000",
]
REMOTE = [
    "10.0.0.5:5000",
    "203.0.113.7:80",
    "example.com:5000",
    "[2001:db8::1]:5000",
    "0.0.0.0:5000",
]
BAD_TCP = ["127.0.0.1", "127.0.0.1:", "127.0.0.1:x", "127.0.0.1:70000", "127.0.0.1:0"]
NETWORK_UNIX = [r"\\host\share\s", "//host/share/s", r"\\.\pipe\x", r"\\?\UNC\host\s", r"\??\x"]


def _write(proj: Project, kind: str, address: str) -> None:
    endpoint_path(proj).write_text(
        json.dumps({"kind": kind, "address": address, "pid": 1}), encoding="utf-8"
    )


@pytest.mark.parametrize("address", LOOPBACK)
def test_a_loopback_tcp_address_is_accepted_from_a_file(address):
    assert endpoint_refusal("tcp", address) is None


@pytest.mark.parametrize("address", REMOTE)
def test_a_remote_tcp_address_is_refused_from_a_file_and_taken_from_the_user(address):
    assert "loopback" in endpoint_refusal("tcp", address)
    assert endpoint_refusal("tcp", address, remote_ok=True) is None
    assert EndpointInfo.from_url(f"tcp://{address}").address == address  # --endpoint


@pytest.mark.parametrize("address", BAD_TCP)
def test_a_tcp_address_needs_a_port(address):
    assert endpoint_refusal("tcp", address, remote_ok=True)


@pytest.mark.parametrize("address", NETWORK_UNIX)
def test_a_unix_socket_is_never_a_network_or_device_path(address):
    assert endpoint_refusal("unix", address, remote_ok=True)
    with pytest.raises(ValidationError, match="unix"):
        EndpointInfo(kind="unix", address=address, pid=1)


def test_a_local_unix_socket_path_is_accepted():
    assert endpoint_refusal("unix", "specto-x") is None
    assert (
        endpoint_refusal("unix", "C:/Temp/specto-x" if os.name == "nt" else "/tmp/specto-x") is None
    )


@pytest.mark.parametrize("address", REMOTE[:2])
def test_an_endpoint_file_naming_a_remote_host_is_refused(tmp_path, address):
    proj = Project.init(tmp_path / "proj", "p")
    _write(proj, "tcp", address)
    with pytest.raises(SpectoError, match="loopback") as ei:
        read_endpoint_file(proj)
    assert "delete .specto/endpoint.json" in ei.value.fix


def test_an_endpoint_file_on_loopback_is_read(tmp_path):
    proj = Project.init(tmp_path / "proj", "p")
    _write(proj, "tcp", "127.0.0.1:5000")
    assert read_endpoint_file(proj).address == "127.0.0.1:5000"


def test_an_instance_file_naming_a_remote_host_is_not_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    good = Instance(
        name="lami",
        project=str(tmp_path),
        pid=os.getpid(),
        endpoint={"kind": "tcp", "address": "127.0.0.1:5000", "pid": os.getpid()},
    )
    register(good)
    path = register(good.model_copy(update={"name": "kobi"}))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["endpoint"]["address"] = "203.0.113.7:5000"  # an agent rewrites it
    path.write_text(json.dumps(data), encoding="utf-8")
    assert [i.name for i in list_instances(sweep=False)] == ["lami"]
    with pytest.raises(ValidationError, match="loopback"):
        Instance.model_validate(data)
