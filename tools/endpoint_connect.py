"""Connect a GUI driver to the app's endpoint (``.specto/endpoint.json``), local only.

tools/gui_drive.py runs in a bare pyautogui venv (no specto packages) and
tools/gui_qtdrive.py in the workspace venv; both connect here. The address is read from
a file an agent can write, so it is judged first by ``specto_core.pathguard`` (loaded by
its file: it imports only the standard library): a local pipe, a local unix socket or a
loopback tcp host, nothing else (SPEC-74, SPEC-88). A refused address never reaches an
open or a connect.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

_PATHGUARD = (
    Path(__file__).resolve().parents[1] / "packages/specto-core/src/specto_core/pathguard.py"
)


def _load_pathguard() -> Any:
    spec = importlib.util.spec_from_file_location("_specto_pathguard", _PATHGUARD)
    if spec is None or spec.loader is None:
        raise SystemExit(f"[ERROR] cannot load {_PATHGUARD}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass/enum machinery looks the module up
    spec.loader.exec_module(module)
    return module


pathguard = _load_pathguard()


def connect(info: dict) -> Any:
    """A binary stream to the endpoint ``info`` (``endpoint.json``'s dict): the named pipe
    on Windows; elsewhere QLocalServer's unix socket, a path or a name in the temp dir; or
    a loopback tcp port (the SDK's ``open_transport`` rules). ``SystemExit`` when the
    address is not local."""
    kind = str(info.get("kind", "pipe"))
    address = str(info.get("address", ""))
    why = pathguard.endpoint_refusal(kind, address)
    if why is not None:
        raise SystemExit(f"[ERROR] refused endpoint {kind}:{address!r}: {why}")
    if kind == "pipe" and sys.platform == "win32":
        return open(pathguard.local_pipe_path(address), "r+b", buffering=0)
    if kind == "tcp":
        host, _, port = address.rpartition(":")
        sock = socket.create_connection((host.strip("[]") or "127.0.0.1", int(port)))
    else:
        path = address if os.sep in address else os.path.join(tempfile.gettempdir(), address)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(path)
    return sock.makefile("rwb", buffering=0)
