"""Judge a path by its text, before any filesystem call (SPEC-61; ``docs/contracts.md``
sections 6 and 10).

One guard for every place that turns text from a file into a path: the items host's
relative paths (``specto_app.web.scheme.resolve_relative``: a note's image, an OBJ's
material map), the 3d reader's references out of a file (``reference_refusal``: an OBJ's
MTL and texture names, SPEC-68; a DAE's image names on their way to trimesh's resolver,
SPEC-69), xacro's includes (``specto_readers.xacro_expand``), an intaker's ``sources``
(``canvas_document.IntakerParams`` and the unknown-files check, SPEC-72), every intake
spec's source roots (``specto_intake.spec.SourceSpec`` and the backstop
``specto_intake.sources.resolve_root``, SPEC-72), the scratch pad's paths
(``specto_core.scratch``: link targets, drops, legacy cards; SPEC-67, SPEC-71), the
prompt agent's ``scratch_append`` paths (SPEC-73), and through ``output_refusal``
(``unc_ok`` for a project on a share) the roots and paths a Python link's output names
(``specto_core.python_links``) and each item part the items host is asked for
(``specto_app.web.scheme.locate_item``), SPEC-75, and a launcher's command, cwd and the
paths it is asked to open (``specto_app.launchers.book``), SPEC-76. It never touches the
disk or the network, and it is an allowlist: a path is accepted only when it is

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
A caller that judges network paths itself (xacro, inside its allowed folders;
``output_refusal``, inside a project on a share) may let a plain ``\\\\host\\share``
through with ``unc_ok``; never anything else.

The endpoint addresses clients read from ``.specto/endpoint.json`` and the instance
files are judged here too, by ``local_pipe_path`` and ``endpoint_refusal`` (SPEC-74,
SPEC-88; ``specto_core.rpc`` re-exports ``local_pipe_path``). This module imports only
the standard library, so a tool outside the workspace venv (``tools/endpoint_connect.py``)
loads it by its file.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
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


def reference_refusal(ref: str) -> Refusal | None:
    """Why a reference read out of a file (an OBJ's ``mtllib``, an MTL's ``map_Kd``, a DAE's
    ``<init_from>``) may not be looked for on disk: ``path_refusal`` of the text as the file
    wrote it and as a page's URL would carry it, backslashes as slashes and percent-escapes
    decoded, so every spelling of a network or device path is caught (``\\\\host\\x``,
    ``//host/x``, ``\\??\\UNC\\host\\x``, ``%5C%5Chost%5Cx``). Text only."""
    for text in {ref, unquote(ref)}:
        why = path_refusal(text) or path_refusal(text.replace("\\", "/"))
        if why is not None:
            return why
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


def output_refusal(text: str, root: str | None = None) -> Refusal | None:
    """Why a path an output names may not reach a filesystem call (SPEC-75): a Python
    link's roots and paths (its code is approved, its output is agent-written text) and
    an item part the items host is asked for. ``path_refusal`` of the text as written and
    with its backslashes as slashes; a single leading slash means a path below ``root``
    (an output's paths are relative POSIX paths), so that form is judged without it.

    A network path is accepted only when ``root`` is itself a plain network path
    (``\\\\host\\share\\...``, never ``//``, ``//host`` or a device form) and the
    path, normalised, is ``root`` or lies under it; under such a root a relative path is
    normalised against it the same way, so ``..`` never leaves it. Text only."""
    share = _share_root(root)
    for t in {text, text.replace("\\", "/")}:
        why = path_refusal(t, unc_ok=share is not None)
        below = below_root(t) if why is Refusal.ROOTED else None
        if below is not None:
            why = path_refusal(below, unc_ok=share is not None)
        if why is None and share is not None and not _in_share(t, share):
            why = Refusal.NETWORK
        if why is not None:
            return why
    return None


def below_root(text: str) -> str | None:
    """The path below a root that ``text`` names with a single leading slash or backslash
    (``/a/b`` and ``\\a\\b`` are ``a/b``, POSIX slashes), or None when it has none (a
    relative path, ``//host``, a drive path). The one leading-slash rule (SPEC-91): an
    output's files and content paths always mean a path below the root; a collection part
    and ``output_refusal`` only where it is no absolute path (Windows, where ``/x`` is the
    current drive's root); the items host refuses such a part. Text only."""
    t = text.replace("\\", "/")
    return t[1:] if t[:1] == "/" and t[1:2] != "/" else None


def _share_root(root: str | None) -> str | None:
    """``root`` as a normalised ``//host/share/...`` when it is a plain network path on
    Windows (the only root that trusts network paths); None otherwise."""
    if not root or not _windows():
        return None
    r = root.strip().replace("\\", "/")
    if not _PLAIN_UNC.match(r) or path_refusal(r, unc_ok=True) is not None:
        return None
    r = posixpath.normpath(r)  # keeps the leading //
    return r if _PLAIN_UNC.match(r) else None


def _in_share(text: str, share: str) -> bool:
    """``text`` (accepted by ``path_refusal`` with ``unc_ok``) stays in ``share``: a
    network path or a path relative to the share root, normalised, is the root or lies
    under it, compared as text without case; a local drive path is not the share's."""
    t = text.replace("\\", "/")
    if _DRIVE_ABS.match(t):
        return True
    full = t if t.startswith("//") else share + "/" + t.lstrip("/")
    low, base = posixpath.normpath(full).lower(), share.lower().rstrip("/")
    return low == base or low.startswith(base + "/")


def under(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """``path`` lies in one of ``roots`` (both already resolved): a text comparison."""
    p = Path(path)
    return any(p == Path(r) or p.is_relative_to(Path(r)) for r in roots)


# -- endpoint addresses (SPEC-74, SPEC-88) ---------------------------------------------------
#: a local pipe's name: what ``rpc.endpoint_name`` makes (``specto-<project id>``), ASCII only
_PIPE_NAME = re.compile(r"[A-Za-z0-9_.-]+", re.ASCII)
_LOCAL_PIPE_PREFIX = re.compile(r"\\\\\.\\pipe\\", re.ASCII | re.IGNORECASE)
PIPE_REFUSED = r"a pipe endpoint must name a local pipe (a bare name or \\.\pipe\<name>)"
TCP_REFUSED = "a tcp endpoint read from a file must be loopback (127.0.0.0/8, ::1 or localhost)"
UNIX_REFUSED = "a unix endpoint must be a local socket path, not a network or device path"


def local_pipe_path(address: str) -> str | None:
    r"""The ``\\.\pipe\<name>`` path a pipe endpoint's ``address`` names, or None when it
    is not a local pipe (SPEC-74). Accepted: a bare name (``specto-<id>``) or that full
    path, the name in ``[A-Za-z0-9_.-]``, not only dots. Everything else is refused by its
    text, before any open: on Windows opening ``\\host\pipe\x`` connects to the host
    over SMB and can hand it the user's NTLM hash, and ``endpoint.json`` is agent-writable."""
    prefix = _LOCAL_PIPE_PREFIX.match(address)
    name = address[prefix.end() :] if prefix else address
    if not _PIPE_NAME.fullmatch(name) or not name.strip("."):
        return None
    return address if prefix else rf"\\.\pipe\{name}"


def _loopback(host: str) -> bool:
    import ipaddress

    if host == "" or host.lower() == "localhost":
        return True  # an empty host is 127.0.0.1 to every client
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False  # a name other than localhost resolves who knows where


def endpoint_refusal(kind: str, address: str, *, remote_ok: bool = False) -> str | None:
    r"""Why an endpoint (``kind``, ``address``) may not be connected to, or None. Text
    only: nothing is opened, resolved or connected.

    - ``pipe``: a local pipe only (``local_pipe_path``).
    - ``unix``: a local socket path: never one starting with ``\`` or ``//`` (no network
      or device path, even where a backslash is a plain character), nor one
      ``path_refusal`` refuses for anything but being rooted without a drive.
    - ``tcp``: ``host:port`` with a port in 1-65535; the host loopback unless
      ``remote_ok``, which only the user's explicit ``--endpoint`` / ``SPECTO_ENDPOINT``
      grants: an address read from a file (``endpoint.json``, an instance file) is
      agent-writable, and a remote host there would hand every call to a server the
      agent controls (SPEC-88).
    - ``inproc``: never from another process.
    """
    if kind == "pipe":
        return None if local_pipe_path(address) is not None else PIPE_REFUSED
    if kind == "unix":
        if not address or address[:1] == "\\" or address[:2] == "//":
            return UNIX_REFUSED
        # a single leading "/" is a local path on Windows too (the current drive's root):
        # only what reaches out or names a device is refused
        why = path_refusal(address)
        return UNIX_REFUSED if why is not None and why is not Refusal.ROOTED else None
    if kind == "tcp":
        host, sep, port = address.rpartition(":")
        if not sep or not port.isdigit() or not 0 < int(port) < 65536:
            return "a tcp endpoint is host:port, the port 1-65535"
        return None if remote_ok or _loopback(host) else TCP_REFUSED
    return f"an endpoint of kind {kind!r} cannot be connected to from a process"


__all__ = [
    "DEVICE_NAMES",
    "PIPE_REFUSED",
    "Refusal",
    "below_root",
    "endpoint_refusal",
    "file_uri_path",
    "local_pipe_path",
    "output_refusal",
    "path_refusal",
    "reference_refusal",
    "strip_local_long",
    "under",
]
