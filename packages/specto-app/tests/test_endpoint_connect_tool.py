"""The repo's GUI drivers connect only to a local endpoint (SPEC-88).

tools/gui_drive.py (in a bare pyautogui venv) and tools/gui_qtdrive.py read
``.specto/endpoint.json`` and opened its address as written; agents run these scripts.
Both now connect through tools/endpoint_connect.py, which judges the address with
specto_core.pathguard (loaded by file, no specto install needed) before any open.
Every open and connect is trapped here.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "tools"
_spec = importlib.util.spec_from_file_location("endpoint_connect", TOOLS / "endpoint_connect.py")
assert _spec is not None and _spec.loader is not None
endpoint_connect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(endpoint_connect)


@pytest.fixture
def trapped(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    def fake_open(path, *a, **k):
        calls.append(("open", str(path)))
        raise FileNotFoundError(2, "trapped", str(path))

    def fake_connect(address, *a, **k):
        calls.append(("connect", address))
        raise ConnectionRefusedError("trapped")

    class FakeSocket:
        def __init__(self, *a, **k) -> None:
            pass

        def connect(self, path):
            calls.append(("unix", path))
            raise ConnectionRefusedError("trapped")

    monkeypatch.setattr(endpoint_connect, "open", fake_open, raising=False)
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(sys, "platform", "win32")
    return calls


@pytest.mark.parametrize(
    "info",
    [
        {"kind": "pipe", "address": r"\\atk\pipe\p"},
        {"address": "//atk/pipe/p"},  # kind left out: a pipe
        {"kind": "pipe", "address": r"\\?\pipe\p"},
        {"kind": "tcp", "address": "203.0.113.7:5000"},
        {"kind": "tcp", "address": "example.com:5000"},
        {"kind": "unix", "address": r"\\atk\share\s"},
        {"kind": "inproc", "address": "x"},
    ],
)
def test_a_non_local_endpoint_is_refused_before_any_open(trapped, info):
    with pytest.raises(SystemExit, match="endpoint"):
        endpoint_connect.connect(info)
    assert trapped == []


def test_a_local_pipe_opens_its_local_path(trapped):
    with pytest.raises(FileNotFoundError):
        endpoint_connect.connect({"kind": "pipe", "address": "specto-abc"})
    assert trapped == [("open", r"\\.\pipe\specto-abc")]


def test_a_loopback_tcp_endpoint_connects(trapped):
    with pytest.raises(ConnectionRefusedError):
        endpoint_connect.connect({"kind": "tcp", "address": "127.0.0.1:5000"})
    assert trapped == [("connect", ("127.0.0.1", 5000))]


@pytest.mark.parametrize("tool", ["gui_drive.py", "gui_qtdrive.py"])
def test_both_drivers_connect_through_it(tool):
    src = (TOOLS / tool).read_text(encoding="utf-8")
    assert "from endpoint_connect import connect" in src
    assert "def connect(" not in src and "open(address" not in src


def test_a_bare_unix_name_connects_in_the_temp_dir(trapped, monkeypatch):
    """SPEC-98: QLocalServer's socket name off Windows; the AF_UNIX constant is faked
    where this Python has none (the socket itself is trapped)."""
    import tempfile

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
    with pytest.raises(ConnectionRefusedError):
        endpoint_connect.connect({"kind": "unix", "address": "specto-abc"})
    assert trapped == [("unix", str(Path(tempfile.gettempdir()) / "specto-abc"))]


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="this Python has no AF_UNIX")
def test_a_local_unix_socket_is_reached(tmp_path, monkeypatch):
    """A real round trip: a listener on a socket path, a line each way."""
    import threading

    monkeypatch.setattr(sys, "platform", "linux")
    path = str(tmp_path / "s")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def echo() -> None:
        conn, _ = server.accept()
        with conn:
            conn.sendall(conn.recv(64).upper())

    t = threading.Thread(target=echo, daemon=True)
    t.start()
    try:
        f = endpoint_connect.connect({"kind": "unix", "address": path})
        f.write(b"ping\n")
        assert f.read(5) == b"PING\n"
        f.close()
    finally:
        t.join(5)
        server.close()
