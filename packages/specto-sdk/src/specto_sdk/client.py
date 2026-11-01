"""The remote client: JSON-RPC 2.0 over a line transport to a running app.

    from specto_sdk import Client
    with Client.open() as c:                      # nearest .specto/, its endpoint.json
        doc = c.call("canvas.state", {})["document"]
        doc["views"]["fege"]["label"] = "Data"
        c.call("canvas.submit", {"document": doc, "label": "renamed the file tree"})
        c.api.canvas_undo()                       # typed methods from api-info.json

Requests are synchronous. Notifications (``event`` after ``watch``) arriving while a
response is awaited are queued and read with ``events()`` / ``next_event()``.
"""

from __future__ import annotations

import itertools
import json
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from specto_core import Project
from specto_core.registry import API_LEVEL
from specto_core.rpc import EndpointInfo

from .endpoint import find_endpoint
from .errors import RemoteError, TransportError
from .transport import Transport, open_transport


class Client:
    def __init__(
        self,
        transport: Transport,
        *,
        client: str = "specto-sdk",
        origin: str = "user",
        api_level: int = API_LEVEL,
        hello: bool = True,
        project: Project | None = None,
        endpoint: EndpointInfo | None = None,
        connection: str | None = None,
    ) -> None:
        self.transport = transport
        self.client_name = client
        self.origin = origin
        self.connection_name = connection
        self.project = project
        self.endpoint = endpoint
        self._ids = itertools.count(1)
        self._notifications: deque[dict[str, Any]] = deque()
        self._api_info: dict[str, Any] | None = None
        self.hello_result: dict[str, Any] | None = None
        self.connection: str | None = None
        if hello:
            self.hello(api_level=api_level)

    # -- construction ------------------------------------------------------------------
    @classmethod
    def open(
        cls,
        project: str | Path | Project | None = None,
        *,
        endpoint: str | None = None,
        origin: str = "user",
        client: str = "specto-sdk",
        connection: str | None = None,
        timeout: float = 5.0,
    ) -> Client:
        """Connect to the app serving ``project`` (default: nearest ``.specto/``).
        ``connection`` names the engine connection every process that presents the same
        name shares (the CLI uses one name per user and host)."""
        info, proj = find_endpoint(project, endpoint=endpoint)
        transport = open_transport(info, timeout=timeout)
        return cls(
            transport,
            client=client,
            origin=origin,
            project=proj,
            endpoint=info,
            connection=connection,
        )

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.transport.close()

    @property
    def closed(self) -> bool:
        return self.transport.closed

    # -- wire --------------------------------------------------------------------------
    def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        rid = next(self._ids)
        self.transport.send(
            json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        )
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            line = self.transport.recv(remaining)
            if line is None:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TransportError(f"{method}: no response within {timeout}s")
                if deadline is None:
                    raise TransportError(f"{method}: the endpoint produced no response")
                continue
            try:
                msg = json.loads(line)
            except ValueError as e:
                raise TransportError(f"bad JSON from the app: {line[:120]!r} ({e})") from e
            if "method" in msg and "id" not in msg:
                self._notifications.append(msg)
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RemoteError.from_rpc(msg["error"])
                return msg.get("result")
            if msg.get("id") is None and "error" in msg:
                # parse / invalid-request errors carry no id: they are about our line
                raise RemoteError.from_rpc(msg["error"])
            # a late reply to something else; drop it

    def hello(self, *, api_level: int = API_LEVEL) -> dict[str, Any]:
        params: dict[str, Any] = {
            "client": self.client_name,
            "origin": self.origin,
            "api_level": api_level,
        }
        if self.connection_name:
            params["connection"] = self.connection_name
        res = self.request("hello", params)
        self.hello_result = res
        self.connection = res.get("connection")
        if not res.get("api_compatible", True):
            raise TransportError(
                f"api level {api_level} is not compatible with the app's {res.get('api_level')}",
                fix="update specto-sdk or the app",
            )
        return res

    # -- commands ----------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("call", {"name": name, "args": args or {}})

    def list_commands(self, prefix: str | None = None) -> list[dict[str, Any]]:
        return self.request("list_commands", {"prefix": prefix})["commands"]

    def describe(self, target: str) -> dict[str, Any]:
        return self.request("describe", {"target": target})

    def ping(self) -> dict[str, Any]:
        return self.request("ping", {})

    def api_info(self, *, refresh: bool = False) -> dict[str, Any]:
        """The app's ``api-info.json`` (cached)."""
        if self._api_info is None or refresh:
            self._api_info = self.request("api.info", {})
        return self._api_info

    @property
    def api(self) -> Any:
        """Typed methods generated from ``api-info.json`` (``specto_sdk._api.Api``)."""
        from ._api import Api

        return Api(self)

    # -- jobs ----------------------------------------------------------------------------
    def job_status(self, job: str) -> dict[str, Any]:
        return self.call("job.status", {"job": job})["job"]

    def wait_job(
        self,
        job: str,
        *,
        timeout: float | None = None,
        poll_s: float = 0.1,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Poll ``job.status`` until the job is done, failed or cancelled."""
        deadline = None if timeout is None else time.monotonic() + timeout
        last = None
        while True:
            info = self.job_status(job)
            if on_progress and (info["progress"], info["message"]) != last:
                last = (info["progress"], info["message"])
                on_progress(info)
            if info["state"] in ("done", "failed", "cancelled"):
                return info
            if deadline is not None and time.monotonic() >= deadline:
                raise TransportError(f"job {job} still {info['state']} after {timeout}s")
            time.sleep(poll_s)

    def resolve_job(self, result: dict[str, Any], **wait_kw: Any) -> dict[str, Any]:
        """If ``result`` is ``{job: ...}`` wait for it and return the job info; else
        return ``result`` unchanged."""
        job = result.get("job") if isinstance(result, dict) else None
        if isinstance(job, str) and set(result) <= {"job"}:
            return self.wait_job(job, **wait_kw)
        return result

    # -- events --------------------------------------------------------------------------
    def watch(self, since_seq: int = 0) -> dict[str, Any]:
        """Subscribe to the running canvas's events (pushed as notifications)."""
        return self.request("watch", {"since_seq": since_seq})

    def unwatch(self) -> dict[str, Any]:
        return self.request("unwatch", {})

    def poll_events(self, since_seq: int = 0, limit: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"since_seq": since_seq}
        if limit is not None:
            args["limit"] = limit
        return self.call("events.poll", args)

    def next_event(self, timeout: float | None = 0.0) -> dict[str, Any] | None:
        """The next pushed event (``params`` of an ``event`` notification), reading the
        transport for up to ``timeout`` seconds (``None`` blocks)."""
        while True:
            while self._notifications:
                msg = self._notifications.popleft()
                if msg.get("method") == "event":
                    return msg.get("params")
            line = self.transport.recv(timeout)
            if line is None:
                return None
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" in msg:
                self._notifications.append(msg)

    def events(self, timeout: float | None = 0.0) -> list[dict[str, Any]]:
        """Drain every pushed event available now (waiting ``timeout`` for the first)."""
        out = []
        ev = self.next_event(timeout)
        while ev is not None:
            out.append(ev)
            ev = self.next_event(0.0)
        return out
