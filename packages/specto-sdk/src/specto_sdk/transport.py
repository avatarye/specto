"""Line transports for the JSON-RPC client.

- ``PipeTransport``: Windows named pipe (``\\\\.\\pipe\\specto-<id>``, what ``QLocalServer``
  creates on Windows), opened as a plain file.
- ``SocketTransport``: Unix domain socket (``QLocalServer`` elsewhere) or TCP.
- ``InProcessTransport``: feeds lines straight to an ``RpcHandler`` (tests, headless).

Each transport sends one line and receives one line; framing is newline-delimited UTF-8.
"""

from __future__ import annotations

import contextlib
import os
import select
import socket
import sys
from collections import deque
from typing import TYPE_CHECKING, Any, Protocol

from specto_core.rpc import EndpointInfo, RpcConnection, RpcHandler

if TYPE_CHECKING:
    from specto_core.live_canvas import SerialLock

from .errors import TransportError


class Transport(Protocol):
    def send(self, line: str) -> None: ...
    def recv(self, timeout: float | None = None) -> str | None:
        """Next line without its newline, or ``None`` on timeout."""
        ...

    def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...


class _LineReader:
    """Buffers raw chunks into lines."""

    def __init__(self) -> None:
        self._buf = b""
        self._lines: deque[str] = deque()

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line, self._buf = self._buf[:nl], self._buf[nl + 1 :]
            self._lines.append(line.decode("utf-8").rstrip("\r"))

    def pop(self) -> str | None:
        return self._lines.popleft() if self._lines else None


class SocketTransport:
    """A connected stream socket (AF_UNIX or AF_INET)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._reader = _LineReader()
        self._closed = False

    @classmethod
    def connect_unix(cls, path: str, *, timeout: float = 5.0) -> SocketTransport:
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            raise TransportError("unix sockets are not available on this platform")
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(path)
        except OSError as e:
            sock.close()
            raise TransportError(
                f"cannot connect to {path}: {e}", fix="is the Specto app running?"
            ) from e
        sock.settimeout(None)
        return cls(sock)

    @classmethod
    def connect_tcp(cls, address: str, *, timeout: float = 5.0) -> SocketTransport:
        host, _, port = address.rpartition(":")
        try:
            sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout)
        except (OSError, ValueError) as e:
            raise TransportError(
                f"cannot connect to tcp://{address}: {e}", fix="is the Specto app running?"
            ) from e
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        return cls(sock)

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, line: str) -> None:
        if self._closed:
            raise TransportError("transport is closed")
        try:
            self._sock.sendall((line.rstrip("\n") + "\n").encode("utf-8"))
        except OSError as e:
            self.close()
            raise TransportError(f"send failed: {e}", fix="reconnect") from e

    def recv(self, timeout: float | None = None) -> str | None:
        line = self._reader.pop()
        if line is not None:
            return line
        if self._closed:
            raise TransportError("transport is closed")
        while True:
            if timeout is not None:
                ready, _, _ = select.select([self._sock], [], [], timeout)
                if not ready:
                    return None
            try:
                chunk = self._sock.recv(65536)
            except OSError as e:
                self.close()
                raise TransportError(f"recv failed: {e}", fix="reconnect") from e
            if not chunk:
                self.close()
                raise TransportError("the app closed the connection", fix="reconnect")
            self._reader.feed(chunk)
            line = self._reader.pop()
            if line is not None:
                return line

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                self._sock.close()


class PipeTransport:
    """Windows named pipe client. ``QLocalServer`` creates byte-mode pipes, which the C
    runtime opens like a file; reads block until data arrives (no timeout)."""

    def __init__(self, name: str) -> None:
        path = name if name.startswith("\\\\") else rf"\\.\pipe\{name}"
        try:
            self._f = open(path, "r+b", buffering=0)  # noqa: SIM115 - closed in close()
        except OSError as e:
            raise TransportError(
                f"cannot open pipe {path}: {e}", fix="is the Specto app running?"
            ) from e
        self._reader = _LineReader()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, line: str) -> None:
        if self._closed:
            raise TransportError("transport is closed")
        try:
            self._f.write((line.rstrip("\n") + "\n").encode("utf-8"))
            self._f.flush()
        except OSError as e:
            self.close()
            raise TransportError(f"send failed: {e}", fix="reconnect") from e

    def recv(self, timeout: float | None = None) -> str | None:
        line = self._reader.pop()
        if line is not None:
            return line
        if self._closed:
            raise TransportError("transport is closed")
        while True:
            try:
                chunk = self._f.read(65536)
            except OSError as e:
                self.close()
                raise TransportError(f"recv failed: {e}", fix="reconnect") from e
            if not chunk:
                self.close()
                raise TransportError("the app closed the pipe", fix="reconnect")
            self._reader.feed(chunk)
            line = self._reader.pop()
            if line is not None:
                return line

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                self._f.close()


class InProcessTransport:
    """Talks to an ``RpcHandler`` directly: ``send`` runs the request synchronously and
    queues the response plus any pushed notifications for ``recv``."""

    def __init__(self, handler: RpcHandler, lock: SerialLock | None = None) -> None:
        self.handler = handler
        #: the canvas's serialise lock, held around each request (``InProcessEndpoint``
        #: shares it among its clients); None for a handler with no canvas behind it
        self._lock: SerialLock | contextlib.AbstractContextManager[Any] = (
            lock if lock is not None else contextlib.nullcontext()
        )
        self._lines: deque[str] = deque()
        self.conn: RpcConnection = handler.connect(send=self._push)
        self._closed = False

    def _push(self, msg: dict[str, Any]) -> None:
        import json

        self._lines.append(json.dumps(msg))

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, line: str) -> None:
        if self._closed:
            raise TransportError("transport is closed")
        with self._lock:
            out = self.handler.handle_line(self.conn, line)
        if out:
            self._lines.extend(ln for ln in out.split("\n") if ln)

    def recv(self, timeout: float | None = None) -> str | None:
        if self._lines:
            return self._lines.popleft()
        if self._closed:
            raise TransportError("transport is closed")
        return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.handler.disconnect(self.conn)


def open_transport(info: EndpointInfo, *, timeout: float = 5.0) -> Transport:
    """Connect to the endpoint described by ``.specto/endpoint.json``."""
    if info.kind == "tcp":
        return SocketTransport.connect_tcp(info.address, timeout=timeout)
    if info.kind == "unix":
        return SocketTransport.connect_unix(info.address, timeout=timeout)
    if info.kind == "pipe":
        if sys.platform == "win32":
            return PipeTransport(info.address)
        # QLocalServer on Unix puts a socket named <name> in the temp dir
        path = info.address if os.sep in info.address else os.path.join(_tmp_dir(), info.address)
        return SocketTransport.connect_unix(path, timeout=timeout)
    raise TransportError(f"cannot open an endpoint of kind {info.kind!r} from a process")


def _tmp_dir() -> str:
    import tempfile

    return tempfile.gettempdir()
