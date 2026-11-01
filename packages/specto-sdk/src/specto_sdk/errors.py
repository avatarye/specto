"""Errors a remote client sees. ``RemoteError`` mirrors the server's ``SpectoError``
(same ``code``, ``message``, ``fix``, ``data``) so callers handle both alike."""

from __future__ import annotations

from typing import Any

from specto_core.errors import SpectoError


class RemoteError(SpectoError):
    """A ``SpectoError`` raised on the server and relayed over JSON-RPC."""

    def __init__(
        self, code: str, message: str, *, fix: str | None = None, data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, fix=fix, **(data or {}))
        self.code = code

    @classmethod
    def from_rpc(cls, error: dict[str, Any]) -> RemoteError:
        data = error.get("data")
        if isinstance(data, dict) and "code" in data:
            return cls(
                str(data["code"]),
                str(data.get("message", error.get("message", ""))),
                fix=data.get("fix"),
                data=data.get("data") or None,
            )
        return cls(
            f"rpc:{error.get('code', 'error')}",
            str(error.get("message", "unknown error")),
            data={"raw": data} if data is not None else None,
        )


class TransportError(SpectoError):
    """The app is not reachable, went away or spoke garbage."""

    code = "transport"


class EndpointNotFound(TransportError):
    """No running app for the project (no ``.specto/endpoint.json``)."""

    code = "no_endpoint"
