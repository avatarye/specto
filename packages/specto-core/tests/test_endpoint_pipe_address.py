"""A pipe endpoint's address names a local pipe, nothing else (SPEC-74).

``.specto/endpoint.json`` is agent-writable, and on Windows opening ``\\\\host\\pipe\\x``
connects out over SMB (which can hand the host the user's NTLM hash). So the model that
every client reads it through refuses any pipe address but a bare name or
``\\\\.\\pipe\\<name>``, by its text, before anything is opened.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from specto_core import Project
from specto_core.errors import InvalidArgs, SpectoError
from specto_core.rpc import EndpointInfo, endpoint_path, local_pipe_path, read_endpoint_file

GOOD = {
    "specto-abc123": r"\\.\pipe\specto-abc123",
    "specto_x.y-1": r"\\.\pipe\specto_x.y-1",
    r"\\.\pipe\specto-abc123": r"\\.\pipe\specto-abc123",  # what QLocalServer reports
    r"\\.\PIPE\specto-abc123": r"\\.\PIPE\specto-abc123",
}

BAD = [
    r"\\atk\pipe\p",  # a remote host's pipe: SMB
    "//atk/pipe/p",
    r"\\atk\share\x",
    r"\\?\pipe\p",  # device namespaces other than \\.\pipe
    r"\\?\UNC\atk\pipe\p",
    r"\\.\C:\x",
    r"\\.\pipe\a\b",  # nested
    r"\\.\pipe\..",
    r"\\.\pipe\\",
    r"\\.\pipe\ ",
    "//./pipe/p",
    r"\??\UNC\atk\pipe\p",
    r"C:\pipe\p",
    "/tmp/specto-x",
    "a/b",
    r"a\b",
    "..",
    ".",
    "",
    "name with space",
    "specto:x",
    "specto-\u00e9",  # names are ASCII
]


@pytest.mark.parametrize("address,path", list(GOOD.items()))
def test_a_local_pipe_name_is_accepted(address, path):
    assert local_pipe_path(address) == path
    assert EndpointInfo(kind="pipe", address=address, pid=1).address == address


@pytest.mark.parametrize("address", BAD)
def test_anything_else_is_refused_as_a_pipe_address(address):
    assert local_pipe_path(address) is None
    with pytest.raises(ValidationError, match="local pipe"):
        EndpointInfo(kind="pipe", address=address, pid=1)


def test_other_kinds_keep_their_own_addresses():
    assert EndpointInfo(kind="tcp", address="127.0.0.1:5000", pid=1).address
    assert EndpointInfo(kind="unix", address="/tmp/specto-x", pid=1).address


def test_an_endpoint_url_naming_a_remote_pipe_is_bad_args():
    with pytest.raises(InvalidArgs, match="local pipe"):
        EndpointInfo.from_url(r"pipe://\\atk\pipe\p")
    assert EndpointInfo.from_url("pipe://specto-abc").address == "specto-abc"


def test_an_endpoint_file_naming_a_remote_pipe_is_refused(tmp_path):
    proj = Project.init(tmp_path / "proj", "p")
    endpoint_path(proj).write_text(
        json.dumps({"kind": "pipe", "address": r"\\atk\pipe\p", "pid": 1}), encoding="utf-8"
    )
    with pytest.raises(SpectoError, match="local pipe"):
        read_endpoint_file(proj)
