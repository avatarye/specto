"""Judge a path by its text, before any filesystem call (SPEC-61; ``docs/contracts.md``
sections 6 and 10).

One guard for every place that turns text from a file into a path: the items host's
relative paths (``specto_app.web.scheme.resolve_relative``: a note's image, an OBJ's
material map), the 3d reader's OBJ and MTL references (``specto_readers.scene3d.ref_refusal``,
SPEC-68) and xacro's includes (``specto_readers.xacro_expand``); a reader of robot
descriptions joins it next. It never touches the disk or the network, and it is an
allowlist: a path is accepted only when it is

- relative (no root, no drive), or
- on Windows, an absolute drive path, ``C:\\...`` or ``C:/...`` (a long local
  ``\\\\?\\C:\\...`` prefix is stripped first), or
- on POSIX, a path with a single leading ``/`` (``/dev``, ``/proc`` and ``/sys`` refused).

Every other rooted form is refused: a network (UNC) path (``\\\\host``, ``//host``; asking
Windows whether such a file exists connects to the host over SMB and can hand it the
user's NTLM hash), an NT object path (``\\??\\``, ``/??/``), a device path (``\\\\.\\``,
``\\\\?\\`` other than a drive), a drive-relative ``C:x`` and a drive-less ``\\x``. Inside an
accepted path a reserved device name (``NUL``, ``COM1``, ...) and a ``:`` stream name
(``secret.txt:x.png``) are refused too. ``file://`` URIs naming a host are network paths.
A caller that judges network paths itself (xacro, inside its allowed folders) may let a
plain ``\\\\host\\share`` through with ``unc_ok``; never anything else.
"""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote, urlparse

DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{p}{d}" for p in ("COM", "LPT") for d in "0123456789\u00b9\u00b2\u00b3"}
)
_DRIVE_ABS = re.compile(r"^[A-Za-z]:[\\/]")
#: \\host\share... where host is a plain name (not ?, . or an NT object prefix)
_PLAIN_UNC = re.compile(r"^[\\/]{2}[^\\/?.][^\\/]*[\\/][^\\/]+")


class Refusal(StrEnum):
    """Why a path's text is refused; the value is the words a cause or an error shows."""

    NETWORK = "a network path"
    DEVICE = "a device path"
    ROOTED = "a rooted path that is not a drive path"
    DEVICE_NAME = "a device name"
    STREAM = "a stream name"


def _windows() -> bool:
    return os.name == "nt"


def strip_local_long(text: str) -> str:
    """``\\\\?\\C:\\x`` (or ``//?/C:/x``) as the plain drive path it names; anything else
    unchanged. Text only."""
    if _windows() and text[:4] in ("\\\\?\\", "//?/") and _DRIVE_ABS.match(text[4:]):
        return text[4:]
    return text


def path_refusal(text: str, *, unc_ok: bool = False) -> Refusal | None:
    """Why ``text`` may not be used as a path, or None (module docstring: an allowlist).
    Text only: nothing is opened, resolved or stat'ed."""
    text = strip_local_long(text.strip())
    if not text:
        return None
    if _windows():
        rooted = text[0] in "\\/" or (len(text) >= 2 and text[1] == ":")
        if rooted and not _DRIVE_ABS.match(text):
            if _PLAIN_UNC.match(text):
                if not unc_ok:
                    return Refusal.NETWORK
            elif text[:2] in ("\\\\", "//"):
                return Refusal.DEVICE if text[2:3] in ("?", ".") else Refusal.NETWORK
            elif text[:4] in ("\\??\\", "/??/"):
                return Refusal.DEVICE
            else:
                return Refusal.ROOTED
        name = ntpath.basename(text.replace("/", "\\"))
        if name.split(".")[0].rstrip(" .").upper() in DEVICE_NAMES:
            return Refusal.DEVICE_NAME
        drive = ntpath.splitdrive(text)[0]
        if ":" in text[len(drive) :]:
            return Refusal.STREAM
        return None
    if text.startswith("//"):
        return Refusal.NETWORK
    if text.startswith(("/dev/", "/proc/", "/sys/")) or text in ("/dev", "/proc", "/sys"):
        return Refusal.DEVICE
    return None


def file_uri_path(uri: str) -> tuple[str | None, Refusal | None]:
    """A ``file://`` URI's local path, or ``(None, why)``: a URI naming a host other than
    ``localhost`` is a network path; its path goes through ``path_refusal``. Text only."""
    parsed = urlparse(uri.strip())
    host = unquote(parsed.netloc or "")
    if host and host.lower() != "localhost":
        return None, Refusal.NETWORK
    raw = unquote(parsed.path)
    if _windows() and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]  # file:///C:/x -> C:/x
    why = path_refusal(raw)
    return (None, why) if why else (raw, None)


def under(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """``path`` lies in one of ``roots`` (both already resolved): a text comparison."""
    p = Path(path)
    return any(p == Path(r) or p.is_relative_to(Path(r)) for r in roots)


__all__ = [
    "DEVICE_NAMES",
    "Refusal",
    "file_uri_path",
    "path_refusal",
    "strip_local_long",
    "under",
]
