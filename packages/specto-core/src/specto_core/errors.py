"""Error types. Every error carries a stable code and, where possible, a fix-it hint.

Principle 4 of the HLD: fail loud with a fix-it message; never drop silently. The
endpoint maps ``SpectoError`` to a JSON-RPC error whose ``data`` is ``to_dict()``.
"""

from __future__ import annotations

from typing import Any


class SpectoError(Exception):
    """Base error. ``code`` is machine-readable, ``fix`` says what call would succeed."""

    code = "error"

    def __init__(self, message: str, *, fix: str | None = None, **data: Any) -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fix:
            out["fix"] = self.fix
        if self.data:
            out["data"] = self.data
        return out

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f" Fix: {self.fix}" if self.fix else "")


class InvalidArgs(SpectoError):
    code = "invalid_args"


class NotFound(SpectoError):
    code = "not_found"


class Ambiguous(SpectoError):
    code = "ambiguous"


class AddressError(SpectoError):
    code = "bad_address"


class Locked(SpectoError):
    code = "locked"


class QuotaExceeded(SpectoError):
    code = "quota"


class TxnError(SpectoError):
    code = "txn"


class Irreversible(TxnError):
    code = "irreversible"


class SchemaError(SpectoError):
    code = "schema"


class UnknownCommand(SpectoError):
    code = "unknown_command"


class PatchError(SpectoError):
    code = "patch"


class BindError(SpectoError):
    """A view cannot accept a collection (no shared data kind)."""

    code = "bind"
