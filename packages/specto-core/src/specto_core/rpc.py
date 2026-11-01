"""JSON-RPC 2.0 endpoint logic, transport-agnostic (contracts section 2).

One ``RpcConnection`` per client socket. The transport (``specto_app.endpoint`` over
``QLocalServer``, or a plain socket / in-process fake in tests) owns the bytes and calls:

    conn = handler.connect(send=push)          # socket opened; ``push(dict)`` writes a line
    out = handler.handle_line(conn, line)      # one request line -> response text (or None)
    handler.disconnect(conn)                   # socket closed: drops its watch

Messages are newline-delimited JSON-RPC 2.0 objects (batches allowed). ``watch`` turns
the connection into a subscriber of the running canvas's events: the server pushes
``{"method": "event", "params": {seq, ts, kind, payload}}`` notifications.
Nothing here touches Qt or sockets; the engine runs on whatever thread calls in.
"""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .engine import CallContext, Engine
from .errors import InvalidArgs, NotFound, SpectoError
from .events import Event
from .project import SPECTO_DIR, Project
from .registry import API_COMPATIBLE, API_LEVEL

SERVER_NAME = "specto-app"
ENDPOINT_FILE = "endpoint.json"

#: JSON-RPC error codes. ``SPECTO_ERROR`` carries ``SpectoError.to_dict()`` in ``data``.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SPECTO_ERROR = -32000

EndpointKind = Literal["pipe", "unix", "tcp", "inproc"]


# -- endpoint discovery (.specto/endpoint.json) ------------------------------------------
class EndpointInfo(BaseModel):
    """What the app writes to ``.specto/endpoint.json`` so clients find it."""

    model_config = ConfigDict(extra="forbid")
    kind: EndpointKind
    address: str = Field(description="pipe/unix: the local server name or path; tcp: host:port")
    pid: int
    project_id: str = ""
    server: str = SERVER_NAME
    api_level: int = API_LEVEL
    api_compatible: int = API_COMPATIBLE

    def url(self) -> str:
        return f"{self.kind}://{self.address}"

    @classmethod
    def from_url(cls, url: str, *, pid: int = 0, project_id: str = "") -> EndpointInfo:
        """``pipe://name``, ``unix:///path``, ``tcp://host:port`` (``SPECTO_ENDPOINT``)."""
        kind, sep, addr = url.partition("://")
        if not sep or kind not in ("pipe", "unix", "tcp", "inproc"):
            raise InvalidArgs(
                f"bad endpoint url {url!r}",
                fix="use pipe://<name>, unix://<path> or tcp://<host>:<port>",
            )
        return cls(kind=kind, address=addr, pid=pid, project_id=project_id)  # type: ignore[arg-type]


def endpoint_name(project_id: str) -> str:
    """The ``QLocalServer`` name for a project: ``specto-<project id>``."""
    return f"specto-{project_id}"


def endpoint_path(project: Project | Path) -> Path:
    root = project.specto_dir if isinstance(project, Project) else Path(project) / SPECTO_DIR
    return root / ENDPOINT_FILE


def write_endpoint_file(project: Project | Path, info: EndpointInfo) -> Path:
    path = endpoint_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info.model_dump(), indent=1) + "\n", encoding="utf-8")
    return path


def read_endpoint_file(project: Project | Path) -> EndpointInfo | None:
    path = endpoint_path(project)
    if not path.exists():
        return None
    try:
        return EndpointInfo.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as e:
        raise SpectoError(f"{path} is not a valid endpoint file: {e}", fix="restart the app") from e


def remove_endpoint_file(project: Project | Path) -> None:
    path = endpoint_path(project)
    if path.exists():
        path.unlink()


# -- connections -------------------------------------------------------------------------
Sender = Callable[[dict[str, Any]], None]


class RpcConnection:
    """One client socket. ``origin`` comes from ``hello``; the engine sees ``id`` as
    ``CallContext.connection``.

    ``key`` is unique per socket. ``id`` equals it unless ``hello`` named a
    ``connection``: then several sockets (CLI invocations, one per process) present one
    name."""

    def __init__(self, key: str, send: Sender | None = None) -> None:
        self.key = key
        self.id = key
        self.send = send
        self.origin = "user"
        self.client = ""
        self.api_level: int | None = None
        self.hello_done = False
        self.closed = False
        self._unwatch: Callable[[], None] | None = None
        #: notifications waiting for a transport that cannot push (returned with the
        #: next response by ``handle_line``)
        self.pending: list[dict[str, Any]] = []

    @property
    def watching(self) -> bool:
        return self._unwatch is not None

    def notify(self, method: str, params: dict[str, Any]) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if self.closed:
            return
        if self.send is None:
            self.pending.append(msg)
        else:
            self.send(msg)

    def _unwatch_all(self) -> None:
        if self._unwatch is not None:
            self._unwatch()
            self._unwatch = None


def _error(code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


class RpcHandler:
    """Maps JSON-RPC methods to the engine. Single-threaded by design: the app calls it
    on the GUI thread; a socket server serialises calls with a lock."""

    def __init__(self, engine: Engine, *, server_name: str = SERVER_NAME) -> None:
        self.engine = engine
        self.server_name = server_name
        self._n = 0
        self.connections: dict[str, RpcConnection] = {}

    # -- lifecycle ---------------------------------------------------------------------
    def connect(self, send: Sender | None = None, *, conn_id: str | None = None) -> RpcConnection:
        self._n += 1
        conn = RpcConnection(conn_id or f"rpc:{self._n}", send)
        self.connections[conn.key] = conn
        return conn

    def disconnect(self, conn: RpcConnection) -> None:
        """Socket closed: drop its watch. A submit is atomic, so nothing is left open."""
        conn._unwatch_all()
        conn.closed = True
        self.connections.pop(conn.key, None)

    def close(self) -> None:
        for conn in list(self.connections.values()):
            self.disconnect(conn)

    # -- wire --------------------------------------------------------------------------
    def handle_line(self, conn: RpcConnection, line: str) -> str | None:
        """One line of text in, zero or more lines of text out (the response, followed by
        any notifications that could not be pushed)."""
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except ValueError as e:
            resp: Any = {
                "jsonrpc": "2.0",
                "id": None,
                "error": _error(PARSE_ERROR, f"parse error: {e}"),
            }
        else:
            resp = self.handle(conn, msg)
        out = [] if resp is None else [json.dumps(resp, ensure_ascii=False)]
        if conn.pending:
            out.extend(json.dumps(m, ensure_ascii=False) for m in conn.pending)
            conn.pending.clear()
        return "\n".join(out) + "\n" if out else None

    def handle(self, conn: RpcConnection, msg: Any) -> dict[str, Any] | list[Any] | None:
        """One parsed JSON-RPC message (or a batch). Returns the response object, or
        ``None`` for a notification."""
        if isinstance(msg, list):
            if not msg:
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": _error(INVALID_REQUEST, "empty batch"),
                }
            replies = [r for r in (self._handle_one(conn, m) for m in msg) if r is not None]
            return replies or None
        return self._handle_one(conn, msg)

    def _handle_one(self, conn: RpcConnection, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": _error(INVALID_REQUEST, "expected an object with a string 'method'"),
            }
        rid = msg.get("id")
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _reply(rid, error=_error(INVALID_PARAMS, "params must be an object"))
        try:
            result = self.dispatch(conn, msg["method"], params)
        except SpectoError as e:
            err = _error(SPECTO_ERROR, e.message, e.to_dict())
        except _MethodNotFound as e:
            err = _error(METHOD_NOT_FOUND, str(e))
        except _BadParams as e:
            err = _error(INVALID_PARAMS, str(e))
        except Exception as e:  # the endpoint never dies on a handler bug
            err = _error(INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        else:
            return None if rid is None else _reply(rid, result=result)
        return None if rid is None else _reply(rid, error=err)

    # -- methods -----------------------------------------------------------------------
    def dispatch(self, conn: RpcConnection, method: str, params: dict[str, Any]) -> Any:
        fn = getattr(self, f"rpc_{method.replace('.', '_')}", None)
        if fn is None or not callable(fn):
            raise _MethodNotFound(
                f"unknown method {method!r}; use hello, call, list_commands, describe, "
                "watch, unwatch, ping"
            )
        try:
            bound = inspect.signature(fn).bind(conn, **params)
        except TypeError as e:
            raise _BadParams(f"{method}: bad params: {e}") from e
        return fn(*bound.args, **bound.kwargs)

    def rpc_hello(
        self,
        conn: RpcConnection,
        client: str = "",
        origin: str = "user",
        api_level: int = API_LEVEL,
        connection: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(origin, str) or not origin:
            raise InvalidArgs("hello: origin must be a non-empty string")
        if connection is not None:
            if not isinstance(connection, str) or not connection.strip():
                raise InvalidArgs("hello: connection must be a non-empty string when given")
            conn.id = f"client:{connection.strip()}"
        conn.client = str(client)
        conn.origin = origin
        conn.api_level = int(api_level)
        conn.hello_done = True
        project = self.engine.project
        return {
            "server": self.server_name,
            "api_level": API_LEVEL,
            "api_compatible": API_COMPATIBLE <= conn.api_level <= API_LEVEL,
            "project": (
                {"id": project.id, "name": project.name, "slug": project.slug} if project else None
            ),
            "connection": conn.id,
        }

    def rpc_call(
        self, conn: RpcConnection, name: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if args is not None and not isinstance(args, dict):
            raise InvalidArgs(f"call {name}: args must be an object")
        ctx = CallContext(connection=conn.id, origin=conn.origin)
        return self.engine.call(name, args or {}, ctx)

    def rpc_list_commands(self, conn: RpcConnection, prefix: str | None = None) -> dict[str, Any]:
        return self.rpc_call(conn, "list_commands", {"prefix": prefix})

    def rpc_describe(self, conn: RpcConnection, target: str) -> dict[str, Any]:
        return self.rpc_call(conn, "describe", {"target": target})

    def rpc_ping(self, conn: RpcConnection) -> dict[str, Any]:
        return {"seq": self.engine.live.events.last_seq, "connection": conn.id}

    def rpc_watch(self, conn: RpcConnection, since_seq: int = 0) -> dict[str, Any]:
        """Subscribe to the running canvas's events, replaying those after ``since_seq``."""
        events = self.engine.live.events
        conn._unwatch_all()
        for ev in events.since(int(since_seq)):
            conn.notify("event", _event_params(ev))

        def push(ev: Event) -> None:
            conn.notify("event", _event_params(ev))

        conn._unwatch = events.subscribe(push)
        return {"seq": events.last_seq}

    def rpc_unwatch(self, conn: RpcConnection) -> dict[str, Any]:
        if conn._unwatch is None:
            raise NotFound("not watching the canvas", fix="watch it first")
        conn._unwatch_all()
        return {}

    def rpc_api_info(self, conn: RpcConnection) -> dict[str, Any]:
        return self.engine.api_info()


class _MethodNotFound(Exception):
    pass


class _BadParams(Exception):
    pass


def _reply(rid: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def _event_params(ev: Event) -> dict[str, Any]:
    return {"seq": ev.seq, "ts": ev.ts, "kind": ev.kind, "payload": ev.payload}


def default_endpoint_info(project: Project | None, *, kind: EndpointKind = "pipe") -> EndpointInfo:
    """The endpoint the app advertises for a project: ``specto-<id>`` on a pipe
    (Windows) or a socket (elsewhere)."""
    pid = os.getpid()
    name = endpoint_name(project.id if project else "default")
    return EndpointInfo(kind=kind, address=name, pid=pid, project_id=project.id if project else "")
