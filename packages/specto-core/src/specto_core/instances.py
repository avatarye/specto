"""Running Specto instances on this machine (#187): each has a readable 5-letter name
(``rasel``, ``tomvi``) so a fleet agent or the user can address it, and a registry entry
in the user dir so the CLI, the MCP server and other tools can find it.

A name is CVCVC from the view names' alphabet (``names.CONSONANTS``, ``names.VOWELS``);
view names are 4 letters, so the two never collide. The name is stable per project on
this machine: ``<user dir>/instance-names.json`` keeps it, keyed by the project path, so
a restart keeps the address. When the kept name is held by another live instance a new
one is drawn.

The registry is one file per running instance, ``<user dir>/instances/<name>.json``
({name, project, project_name, pid, endpoint, started}), written at start and removed
on exit; readers sweep entries whose process is gone (a crash leaves one behind).

    name = instance_name(project.root)
    register(Instance(name=name, project=str(project.root), pid=os.getpid(), ...))
    for inst in list_instances(): ...
    unregister(name, pid=os.getpid())

Qt-free; ``SPECTO_USER_DIR`` overrides the user dir (tests).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import NotFound
from .names import CONSONANTS, VOWELS
from .pathguard import endpoint_refusal

NAMES_FILE = "instance-names.json"
INSTANCES_DIR = "instances"
#: the instance name grammar: consonant, vowel, consonant, vowel, consonant
INSTANCE_PATTERN = rf"^[{CONSONANTS}][{VOWELS}][{CONSONANTS}][{VOWELS}][{CONSONANTS}]$"
_INSTANCE = re.compile(INSTANCE_PATTERN)

#: words in the grammar that read badly or would confuse as an address
BLOCKED_INSTANCE: frozenset[str] = frozenset(
    {
        "nazis", "penis", "kikes", "homos", "pedos", "rapes", "dikes", "fagot", "vomit",
        "tumor", "satan", "devil", "fetus", "nudes", "hates", "sodom", "bigot", "pukes",
        "gonad", "puked", "raped", "nuked", "penes", "kanak", "debug", "rules", "files",
        "nodes", "timer", "pipes", "token", "model",
    }
)  # fmt: skip


def user_dir() -> Path:
    """The user data dir the app keeps its settings and recents in (``SPECTO_USER_DIR``
    overrides it)."""
    override = os.environ.get("SPECTO_USER_DIR")
    if override:
        return Path(override).expanduser()
    try:
        import platformdirs
    except ImportError:  # pragma: no cover - the app installs platformdirs
        return Path.home() / ".specto"
    return Path(platformdirs.user_data_dir("specto", "specto"))


def is_instance_name(value: str) -> bool:
    return bool(_INSTANCE.fullmatch(value)) and value not in BLOCKED_INSTANCE


def _pool() -> list[str]:
    out = []
    for c1 in CONSONANTS:
        for v1 in VOWELS:
            for c2 in CONSONANTS:
                for v2 in VOWELS:
                    for c3 in CONSONANTS:
                        n = c1 + v1 + c2 + v2 + c3
                        if n not in BLOCKED_INSTANCE:
                            out.append(n)
    return out


_POOL: list[str] = _pool()


def draw_name(taken: set[str], salt: str) -> str:
    """A name not in ``taken``: ``salt`` (the project path) hashed into the pool, then
    forward past taken names. Deterministic for equal inputs."""
    digest = hashlib.sha256(salt.encode("utf-8")).digest()
    start = int.from_bytes(digest[:4], "big") % len(_POOL)
    for step in range(len(_POOL)):
        name = _POOL[(start + step) % len(_POOL)]
        if name not in taken:
            return name
    raise ValueError("the instance name pool is exhausted")


def project_key(root: str | Path) -> str:
    """A project path as the names file keys it (absolute, case-folded where the OS is)."""
    return os.path.normcase(os.path.abspath(str(root)))


# -- processes ------------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    """Whether a process with ``pid`` runs now (a reused pid reads as alive)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# -- the registry ---------------------------------------------------------------------------
class Instance(BaseModel):
    """One running Specto, as ``<user dir>/instances/<name>.json`` holds it."""

    model_config = ConfigDict(extra="ignore")
    name: str
    project: str = Field(description="The project's root folder.")
    project_name: str = ""
    pid: int
    endpoint: dict[str, Any] = Field(
        default_factory=dict, description="The app's .specto/endpoint.json (kind, address, ...)."
    )
    started: float = 0.0

    @model_validator(mode="after")
    def _endpoint_is_local(self) -> Instance:
        """The file is in the user dir, which an agent can write, and ``url()`` reaches
        ``Client.open`` as if the user had typed it: the endpoint is judged as a file's
        (a local pipe or socket, a loopback tcp host; SPEC-88). A refused one makes the
        entry unreadable, so it is never listed."""
        kind = str(self.endpoint.get("kind", ""))
        address = str(self.endpoint.get("address", ""))
        if kind and address:
            why = endpoint_refusal(kind, address)
            if why is not None:
                raise ValueError(f"{why}, not {address!r}")
        return self

    def url(self) -> str:
        """The endpoint as a url (``pipe://specto-<id>``) for ``Client.open(endpoint=)``."""
        kind = self.endpoint.get("kind", "")
        address = self.endpoint.get("address", "")
        return f"{kind}://{address}" if kind and address else ""


def instances_dir() -> Path:
    return user_dir() / INSTANCES_DIR


def _entry_path(name: str) -> Path:
    return instances_dir() / f"{name}.json"


def _read(path: Path) -> Instance | None:
    try:
        return Instance.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def register(instance: Instance) -> Path:
    """Write the instance's registry entry (``started`` filled in when 0)."""
    if not instance.started:
        instance = instance.model_copy(update={"started": time.time()})
    path = _entry_path(instance.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(instance.model_dump(), indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def unregister(name: str, *, pid: int | None = None) -> bool:
    """Remove ``name``'s entry; with ``pid``, only when the entry is that process's (a
    newer instance may hold the name by now). True when a file was removed."""
    path = _entry_path(name)
    if pid is not None:
        entry = _read(path)
        if entry is not None and entry.pid != pid:
            return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def list_instances(*, sweep: bool = True) -> list[Instance]:
    """The live instances, by name. An entry whose process is gone, or that cannot be
    read, is not listed; ``sweep`` deletes it."""
    folder = instances_dir()
    out: list[Instance] = []
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        entry = _read(path)
        if entry is not None and pid_alive(entry.pid):
            out.append(entry)
        elif sweep:
            with contextlib.suppress(OSError):
                path.unlink()
    return sorted(out, key=lambda i: i.name)


def default_sender() -> str:
    """Who a message comes from when the caller does not say: the fleet member this
    process runs in (``NIMITZ_ID``, e.g. ``FLEET/SQUAD/SHIP``), else ``<user>@<host>``."""
    member = os.environ.get("NIMITZ_ID", "").strip()
    if member:
        return member
    import getpass
    import socket

    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"{user}@{socket.gethostname()}"


#: the nimitz member labels a fleet member's processes carry (they reach its Bash tool's
#: and MCP servers' environments)
LABEL_VARS = ("NIMITZ_ID", "NIMITZ_ROLE")
_MEMBER_ID = re.compile(r"[A-Z0-9_]+(?:/[A-Z0-9_]+){0,2}")
_ROLE_OF_DEPTH = {1: "admiral", 2: "commodore", 3: "ship"}


def caller_labels(environ: dict[str, str] | None = None) -> dict[str, str]:
    """This process's nimitz member labels (``LABEL_VARS``), the ones that are set."""
    env = os.environ if environ is None else environ
    return {k: str(env[k]).strip() for k in LABEL_VARS if str(env.get(k, "")).strip()}


def verified_member(labels: dict[str, Any] | None) -> str | None:
    """The fleet member that ``labels`` name, by nimitz's own rule for a caller's
    labels: ``NIMITZ_ID`` a well-formed id and ``NIMITZ_ROLE`` the role its shape gives
    (``FLEET`` admiral, ``FLEET/SQUAD`` commodore, ``FLEET/SQUAD/SHIP`` ship); partial or
    disagreeing labels name nobody (``None``)."""
    labels = labels or {}
    mid = str(labels.get("NIMITZ_ID") or "").strip()
    role = str(labels.get("NIMITZ_ROLE") or "").strip()
    if not _MEMBER_ID.fullmatch(mid):
        return None
    return mid if _ROLE_OF_DEPTH.get(mid.count("/") + 1) == role else None


def sender_verified(sender: str, labels: dict[str, Any] | None) -> bool:
    """Whether a message's sender is verified: the caller's member labels name exactly
    that member. Anything else (no labels, other labels, a free-text sender) is a claim."""
    return bool(sender) and verified_member(labels) == str(sender).strip()


def find_instance(name: str) -> Instance:
    """The live instance called ``name``."""
    name = str(name).strip().lower()
    live = list_instances()
    for inst in live:
        if inst.name == name:
            return inst
    names = ", ".join(i.name for i in live) or "none running"
    raise NotFound(
        f"no running Specto instance named {name!r}",
        fix=f"live instances: {names} (`specto instances` lists them)",
    )


# -- the names file -------------------------------------------------------------------------
def _names_path() -> Path:
    return user_dir() / NAMES_FILE


def _load_names() -> dict[str, str]:
    try:
        data = json.loads(_names_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return {}
    return {str(k): str(v) for k, v in projects.items() if is_instance_name(str(v))}


def _save_names(names: dict[str, str]) -> None:
    path = _names_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"projects": names}, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def instance_name(project_root: str | Path) -> str:
    """This project's instance name on this machine: the kept one, unless another live
    instance holds it (another project's, or a second window on this one, ``specto open
    --new``); then, and on first use, a fresh one, kept."""
    key = project_key(project_root)
    names = _load_names()
    held = {i.name: project_key(i.project) for i in list_instances()}
    kept = names.get(key)
    if kept and kept not in held:
        return kept
    taken = set(held) | {n for k, n in names.items() if k != key}
    name = draw_name(taken, key + ("#" + kept if kept else ""))
    names[key] = name
    try:
        _save_names(names)
    except OSError as e:
        print(f"[WARN] instance name not kept: {e}")
    return name


__all__ = [
    "BLOCKED_INSTANCE",
    "INSTANCE_PATTERN",
    "LABEL_VARS",
    "Instance",
    "caller_labels",
    "default_sender",
    "draw_name",
    "find_instance",
    "instance_name",
    "instances_dir",
    "is_instance_name",
    "list_instances",
    "pid_alive",
    "project_key",
    "register",
    "sender_verified",
    "unregister",
    "user_dir",
    "verified_member",
]
