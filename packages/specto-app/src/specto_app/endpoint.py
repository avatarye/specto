"""The app's command endpoint: ``RpcHandler`` served over ``QLocalServer`` (contracts
section 2). A named pipe on Windows, a Unix socket elsewhere, named ``specto-<project id>``.

Import-guarded: without PySide6 this module still imports (``HAVE_QT`` is False) so the
rest of the stack and its tests never need Qt. Everything runs on the GUI thread: each
request is handled in order as its bytes arrive, so nothing blocks longer than a command.

    ep = LocalEndpoint(engine, project=project)
    info = ep.start()          # listens, writes .specto/endpoint.json
    ...
    ep.stop()                  # closes clients, removes the file
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

from specto_core import Engine, Project
from specto_core.errors import SpectoError
from specto_core.rpc import (
    EndpointInfo,
    RpcConnection,
    RpcHandler,
    endpoint_name,
    remove_endpoint_file,
    write_endpoint_file,
)

try:
    from PySide6.QtCore import QObject, Slot
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    HAVE_QT = True
except ImportError:  # pragma: no cover - exercised where PySide6 is absent
    HAVE_QT = False


def endpoint_kind() -> str:
    return "pipe" if sys.platform == "win32" else "unix"


if HAVE_QT:

    class LocalEndpoint(QObject):
        """One ``QLocalServer``; one ``RpcConnection`` per ``QLocalSocket``."""

        def __init__(
            self,
            engine: Engine,
            *,
            project: Project | None = None,
            name: str | None = None,
            parent: QObject | None = None,
        ) -> None:
            super().__init__(parent)
            self.engine = engine
            self.project = project if project is not None else engine.project
            self.handler = RpcHandler(engine)
            self.name = name or endpoint_name(self.project.id if self.project else "default")
            self._server = QLocalServer(self)
            self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self._server.newConnection.connect(self._on_new_connection)
            self._clients: dict[int, tuple[QLocalSocket, RpcConnection, bytearray]] = {}
            self._info: EndpointInfo | None = None

        # -- lifecycle -----------------------------------------------------------------
        def start(self) -> EndpointInfo:
            QLocalServer.removeServer(self.name)  # a stale socket file from a crash
            if not self._server.listen(self.name):
                raise SpectoError(
                    f"cannot listen on local server {self.name!r}: {self._server.errorString()}",
                    fix="is another Specto app serving this project?",
                )
            import os

            self._info = EndpointInfo(
                kind=endpoint_kind(),  # type: ignore[arg-type]
                address=self._server.fullServerName(),
                pid=os.getpid(),
                project_id=self.project.id if self.project else "",
            )
            if self.project is not None:
                write_endpoint_file(self.project, self._info)
            return self._info

        @property
        def info(self) -> EndpointInfo | None:
            return self._info

        @property
        def listening(self) -> bool:
            return self._server.isListening()

        def stop(self) -> None:
            for key in list(self._clients):
                sock, conn, _ = self._clients.pop(key)
                self.handler.disconnect(conn)
                self._release(sock)
                sock.disconnectFromServer()
                sock.deleteLater()
            self._server.close()
            if self.project is not None:
                remove_endpoint_file(self.project)
            self._info = None

        def connection_count(self) -> int:
            return len(self._clients)

        # -- sockets ------------------------------------------------------------------
        @Slot()
        def _on_new_connection(self) -> None:
            while self._server.hasPendingConnections():
                sock = self._server.nextPendingConnection()
                if sock is None:
                    break
                key = id(sock)
                conn = self.handler.connect(send=lambda msg, s=sock: self._push(s, msg))
                self._clients[key] = (sock, conn, bytearray())
                sock.readyRead.connect(lambda k=key: self._on_ready_read(k))
                sock.disconnected.connect(lambda k=key: self._on_disconnected(k))

        def _on_ready_read(self, key: int) -> None:
            entry = self._clients.get(key)
            if entry is None:
                return
            sock, conn, buf = entry
            buf += bytes(sock.readAll().data())
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(buf[:nl]).decode("utf-8", errors="replace")
                del buf[: nl + 1]
                out = self.handler.handle_line(conn, line)
                if out:
                    self._write(sock, out)

        def _on_disconnected(self, key: int) -> None:
            entry = self._clients.pop(key, None)
            if entry is None:
                return
            sock, conn, _ = entry
            self.handler.disconnect(conn)
            self._release(sock)
            sock.deleteLater()

        @staticmethod
        def _release(sock: QLocalSocket) -> None:
            """Drop the Python slots before the socket is deleted. ``~QLocalSocket``
            aborts the socket, so signals can fire mid-destruction; on Linux a deferred
            delete with these lambdas still connected segfaulted in the destructor."""
            for signal in (sock.readyRead, sock.disconnected):
                with contextlib.suppress(RuntimeError, TypeError):
                    signal.disconnect()

        def _push(self, sock: QLocalSocket, msg: dict[str, Any]) -> None:
            self._write(sock, json.dumps(msg, ensure_ascii=False) + "\n")

        @staticmethod
        def _write(sock: QLocalSocket, text: str) -> None:
            if sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
                sock.write(text.encode("utf-8"))
                sock.flush()

else:  # pragma: no cover

    class LocalEndpoint:  # type: ignore[no-redef]
        """Placeholder when PySide6 is missing: constructing it raises with a fix."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise SpectoError(
                "PySide6 is not installed; the QLocalServer endpoint needs it",
                fix="uv sync (specto-app depends on PySide6), or use specto_sdk.testing",
            )


__all__ = ["HAVE_QT", "LocalEndpoint", "endpoint_kind"]
