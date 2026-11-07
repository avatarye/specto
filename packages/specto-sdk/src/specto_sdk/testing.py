"""Fake endpoints for tests: an in-process handler and a threaded TCP server speaking
the same newline-delimited JSON-RPC as the app. No Qt.

    ep = InProcessEndpoint()              # Engine(project=None) + RpcHandler
    c = ep.client(origin="agent:s1")      # a Client over an InProcessTransport
    with TcpEndpoint(ep.handler) as srv:  # real sockets, same handler
        c2 = Client(SocketTransport.connect_tcp(srv.address))
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from typing import Any

from specto_core import Engine, Project
from specto_core.rpc import EndpointInfo, RpcHandler, write_endpoint_file

from .client import Client
from .transport import InProcessTransport, SocketTransport


class InProcessEndpoint:
    """The engine behind direct calls. Its clients may run on any thread (an MCP server
    calls tools from worker threads): one lock serialises their requests, as the app's
    GUI thread does, so the canvas's thread check stands down."""

    def __init__(self, engine: Engine | None = None, *, project: Project | None = None) -> None:
        self.engine = engine or Engine(project=project)
        self.handler = RpcHandler(self.engine)
        self._lock = threading.RLock()
        self.engine.live.serialised_elsewhere()

    def transport(self) -> InProcessTransport:
        return InProcessTransport(self.handler, self._lock)

    def client(
        self,
        *,
        origin: str = "user",
        client: str = "test",
        hello: bool = True,
        connection: str | None = None,
    ) -> Client:
        return Client(
            self.transport(), origin=origin, client=client, hello=hello, connection=connection
        )


class TcpEndpoint:
    """Serves an ``RpcHandler`` on ``127.0.0.1:<port>`` from a background thread; one
    reader thread per client; handler calls are serialised with a lock (the app does
    the same by running everything on the GUI thread)."""

    def __init__(self, handler: RpcHandler, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.handler = handler
        self._lock = threading.Lock()
        # every handler call holds the lock: the canvas's thread check stands down
        live = getattr(getattr(handler, "engine", None), "live", None)
        if live is not None:
            live.serialised_elsewhere()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(16)
        self.host, self.port = self._srv.getsockname()[:2]
        self._threads: list[threading.Thread] = []
        self._socks: list[socket.socket] = []
        self._stop = threading.Event()
        self._acceptor = threading.Thread(target=self._accept_loop, daemon=True)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def info(self, project_id: str = "") -> EndpointInfo:
        import os

        return EndpointInfo(
            kind="tcp", address=self.address, pid=os.getpid(), project_id=project_id
        )

    def advertise(self, project: Project) -> None:
        """Write ``.specto/endpoint.json`` so ``Client.open(project)`` finds this server."""
        write_endpoint_file(project, self.info(project.id))

    def start(self) -> TcpEndpoint:
        self._acceptor.start()
        return self

    def __enter__(self) -> TcpEndpoint:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._srv.close()
        for s in self._socks:
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                s.close()
        for t in self._threads:
            t.join(timeout=2)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, _ = self._srv.accept()
            except OSError:
                return
            self._socks.append(sock)
            t = threading.Thread(target=self._serve, args=(sock,), daemon=True)
            self._threads.append(t)
            t.start()

    def _serve(self, sock: socket.socket) -> None:
        wlock = threading.Lock()

        def push(msg: dict[str, Any]) -> None:
            data = (json.dumps(msg) + "\n").encode("utf-8")
            with wlock, contextlib.suppress(OSError):
                sock.sendall(data)

        with self._lock:
            conn = self.handler.connect(send=push)
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    with self._lock:
                        out = self.handler.handle_line(conn, line.decode("utf-8"))
                    if out:
                        with wlock:
                            try:
                                sock.sendall(out.encode("utf-8"))
                            except OSError:
                                break
        finally:
            with self._lock:
                self.handler.disconnect(conn)
            with contextlib.suppress(OSError):
                sock.close()


def tcp_client(server: TcpEndpoint, **kw: Any) -> Client:
    return Client(SocketTransport.connect_tcp(server.address), **kw)
