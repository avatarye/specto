"""Python links (ADRs 0006, 0016): code under the project's ``.specto/links/`` that
resolves to output data the link runtime delivers.

The link's entry point and what it returns depend on ``resolves_to`` (JSON only):

- ``table``: ``entry(items, params, root)`` returns ``{source key: output value}``; a
  selection on the source looks up its last picked key.
- ``samples``: ``entry(items, params, root)`` returns ``[[number, output value], ...]``;
  a numeric source value (time) picks the nearest sample.
- ``value`` (per pick, #168): ``entry(value, items, params, root)`` returns the output
  value for the source's current value (``value_json``: files ``{root, paths}``,
  selection ``{keys, last}``, time a number of seconds, any other port its JSON). It
  runs again whenever that value changes; ``None`` delivers nothing.

``items`` are the source view's collection items as plain dicts (``key``, ``parts``,
``meta``; empty when the source has no collection), ``params`` the link's params,
``root`` the project folder. Every output value is converted to the declared output
port type (``as_port_value``, ``SHAPES``); a shape that does not fit fails the link
with a fix-it.

Trust (ADR 0006): a file runs only after the user approved its exact content, recorded
as a sha256 in a plain hash list in the user data dir (``SPECTO_USER_DIR`` overrides
it). A changed file needs approval again; unapproved code never runs. The code runs in
its own interpreter (``python -I``) from the worker that resolves the link, so a bad
script can neither block nor crash the app; the runner re-hashes the file before
executing it, so what runs is what was approved.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .clock import now_iso
from .contents import ContentEntry, ContentValue, format_for, read_entry
from .kinds import Item
from .links import CameraValue, Collection, FilesValue, Selection
from .timebase import OWN_TIMELINE, TimeValue

TRUST_FILE = "trusted-links.json"
RUNNER = Path(__file__).with_name("_python_link_runner.py")
RUN_TIMEOUT_S = 120.0


def user_data_dir() -> Path:
    override = os.environ.get("SPECTO_USER_DIR")
    if override:
        return Path(override).expanduser()
    try:
        import platformdirs

        return Path(platformdirs.user_data_dir("Specto", "Specto"))
    except ImportError:  # pragma: no cover - the app installs platformdirs
        return Path.home() / ".specto"


class TrustStore:
    """The user's approvals: one sha256 per approved file content (glossary: trust)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else user_data_dir() / TRUST_FILE

    @classmethod
    def default(cls) -> TrustStore:
        return cls()

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict) and "sha256" in e]

    def approved(self, sha256: str) -> bool:
        return any(e["sha256"] == sha256 for e in self._read())

    def approve(self, sha256: str, *, path: str = "") -> None:
        entries = self._read()
        if any(e["sha256"] == sha256 for e in entries):
            return
        entries.append({"sha256": sha256, "path": path, "approved": now_iso()})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=1), encoding="utf-8")

    def revoke(self, sha256: str) -> None:
        entries = [e for e in self._read() if e["sha256"] != sha256]
        if self.path.exists():
            self.path.write_text(json.dumps(entries, indent=1), encoding="utf-8")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_hash(root: str, rel_path: str) -> str | None:
    """The sha256 of the link file's current content, None when it is missing."""
    try:
        return sha256_of((Path(root) / rel_path).read_bytes())
    except OSError:
        return None


def value_json(value: Any) -> Any:
    """A port value as a per-pick script sees it: files ``{root, paths}``, selection
    ``{keys, last}``, time a number of seconds, any other port its JSON (without the
    runtime's own stamps)."""
    if isinstance(value, FilesValue):
        return {"root": value.root, "paths": value.paths}
    if isinstance(value, Selection):
        return {"keys": list(value.keys), "last": value.last}
    if isinstance(value, TimeValue):
        return value.t
    if isinstance(value, Collection):
        return {"root": value.root, "items": [it.model_dump(mode="json") for it in value.items]}
    if isinstance(value, ContentValue):
        return value.model_dump(mode="json", exclude={"link"})
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class ScriptFailed(BaseModel):
    """What a link's code did wrong, as a resolve job's result (it crosses the worker
    pool as data): the one-line ``message`` and the script's own ``trace`` (empty when
    the result, not the code, was the problem). The link status carries both."""

    message: str
    trace: str = ""


class _Failed(Exception):
    def __init__(self, message: str, trace: str = "") -> None:
        super().__init__(message)
        self.failed = ScriptFailed(message=message, trace=trace)


#: on Windows the interpreter gets no console window of its own (the app has none)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def kill_tree(proc: subprocess.Popen) -> None:
    """End the script and whatever it started (a child holding the pipes would keep the
    run from ever returning)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=NO_WINDOW,
        )
    else:  # pragma: no cover - the session was started for this
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, 9)
    with contextlib.suppress(OSError):
        proc.kill()


def _run(request: dict[str, Any], where: str, root: str, timeout: float) -> Any:
    """Run one request in its own interpreter (``-I``, UTF-8 mode): the script's JSON
    result, or raise ``_Failed`` with a one-line message and the script's traceback."""
    proc = subprocess.Popen(
        [sys.executable, "-I", "-X", "utf8", str(RUNNER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=root or None,
        creationflags=NO_WINDOW,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = proc.communicate(
            json.dumps(request, default=str).encode("utf-8"), timeout=timeout
        )
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
            proc.communicate(timeout=2.0)
        raise _Failed(f"{where} did not finish in {timeout:g} s; fix it to return sooner") from None
    out = stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        reply = json.loads(out[-1]) if out else {}
    except ValueError:
        reply = {}
    if not isinstance(reply, dict) or not reply.get("ok"):
        reply = reply if isinstance(reply, dict) else {}
        err = reply.get("error") or stderr.decode("utf-8", "replace").strip().splitlines()
        last = (err[-1] if isinstance(err, list) and err else err) or f"exit code {proc.returncode}"
        sep = " " if reply.get("kind") == "result" else ": "
        raise _Failed(f"{where}{sep}{last}", str(reply.get("trace") or ""))
    return reply["result"]


def _request(link: dict[str, Any], sha256: str, items: list, root: str) -> dict[str, Any]:
    py = link["python"]
    return {
        "path": str(Path(root) / py["path"]),
        "sha256": sha256,
        "entry": py["entry"],
        "items": items,
        "params": link.get("params") or {},
        "root": root,
    }


def build_python_table(
    link: dict[str, Any],
    sha256: str,
    items: list[dict[str, Any]],
    *,
    root: str,
    progress: Any = None,
    timeout: float = RUN_TIMEOUT_S,
    prepare: Any = None,
) -> Any:
    """The resolve job of a table or samples link: run the link's entry in its own
    interpreter and return the table (or samples) it produced, each value converted to
    the output port type (a collection then ``prepare``d for its view, as an intaker's
    is). What the code got wrong comes back as ``ScriptFailed`` (a one-line, fix-it style
    message and its traceback)."""
    py = link["python"]
    where = f"{py['path']}:{py['entry']}"
    if progress:
        progress(0.1, f"running {where}")
    try:
        result = _run(_request(link, sha256, items, root), where, root, timeout)
        out = check_output(
            py["resolves_to"],
            result,
            where,
            output=py["output"],
            root=root,
            target_type=link.get("target_type"),
        )
    except _Failed as e:
        return e.failed
    except TypeError as e:  # the result's shape, with its fix-it (check_output)
        return ScriptFailed(message=str(e))
    values = out.values() if isinstance(out, dict) else [s[1] for s in out]
    for v in values:
        _prepare(v, prepare, progress)
    return out


def _prepare(value: Any, prepare: Any, progress: Any) -> None:
    if prepare is not None and isinstance(value, Collection):
        prepare(value.items, progress=progress)


def build_python_value(
    link: dict[str, Any],
    sha256: str,
    value: Any,
    items: list[dict[str, Any]],
    *,
    root: str,
    progress: Any = None,
    timeout: float = RUN_TIMEOUT_S,
    prepare: Any = None,
) -> Any:
    """The resolve job of a per-pick link (``resolves_to: value``): run
    ``entry(value, items, params, root)`` for this one source value (``value_json``)
    and return its result as the output port's value (None: nothing to deliver; a
    collection ``prepare``d for its view), or ``ScriptFailed``."""
    py = link["python"]
    where = f"{py['path']}:{py['entry']}"
    if progress:
        progress(0.1, f"running {where}")
    request = {**_request(link, sha256, items, root), "mode": "value", "value": value}
    try:
        result = _run(request, where, root, timeout)
        out = as_port_value(
            py["output"], result, root=root, where=where, target_type=link.get("target_type")
        )
    except _Failed as e:
        return e.failed
    except TypeError as e:  # a shape the output port does not take, with its fix-it
        return ScriptFailed(message=str(e))
    _prepare(out, prepare, progress)
    return out


def check_output(
    resolves_to: str,
    result: Any,
    where: str,
    *,
    output: str = "item",
    root: str = "",
    target_type: str | None = None,
) -> Any:
    """A table or samples result, checked, with every value as its output port value."""

    def port(v: Any) -> Any:
        return as_port_value(output, v, root=root, where=where, target_type=target_type)

    if resolves_to == "table":
        if not isinstance(result, dict):
            raise TypeError(f"{where} must return a table (a dict keyed by source key)")
        return {str(k): port(v) for k, v in result.items()}
    if not isinstance(result, list) or not all(
        isinstance(s, list) and len(s) == 2 and isinstance(s[0], (int, float)) for s in result
    ):
        raise TypeError(f"{where} must return samples ([[number, value], ...])")
    return [[s[0], port(s[1])] for s in sorted(result, key=lambda s: s[0])]


def lookup(resolves_to: str, table: Any, value: Any) -> tuple[bool, Any]:
    """``(found, output value)`` for one source value against the resolved data, whose
    values are port values already."""
    if resolves_to == "table":
        key = value.last if isinstance(value, Selection) else value
        if key is None or table.get(str(key)) is None:
            return False, None
        return True, table[str(key)]
    if isinstance(value, Selection):
        value = value.last
    elif isinstance(value, TimeValue):
        value = value.t
    try:
        t = float(value)
    except (TypeError, ValueError):
        return False, None
    if not table:
        return False, None
    nearest = min(table, key=lambda s: abs(s[0] - t))
    return nearest[1] is not None, nearest[1]


#: what each output port type takes, as the fix-it names it
SHAPES: dict[str, str] = {
    "files": 'a list of paths or {"root"?: ..., "paths": [...]}',
    "selection": 'a key, a list of keys or {"keys": [...], "last"?: ...}',
    "content": 'the files to read as {"paths": [...]} or a list of paths, or {"entries": [...]}',
    "time": 'a number of seconds or {"t": ..., "timeline"?: ...}',
    "camera": '{"position": [x, y, z], "target": [x, y, z], ...}',
    "collection": '{"root"?: ..., "items": [...]} or a list of items; an item is a path or '
    '{"key"?: ..., "parts": {"<role>": "<path>"} or ["<path>", ...], "keys"?: {...}}',
}


class _Shape(Exception):
    """The value does not have any shape the port takes."""


def _scalar(v: Any) -> bool:
    return isinstance(v, str | int | float) and not isinstance(v, bool)


def _root_of(given: Any, root: str) -> str:
    if not given:
        return root
    p = Path(str(given))
    return str(p if p.is_absolute() or not root else Path(root) / p)


def _paths_of(value: Any) -> tuple[Any, list[str]] | None:
    """``(root, paths)`` from ``{root?, paths}`` or a list of paths; None otherwise."""
    if isinstance(value, list) and all(isinstance(p, str) for p in value):
        return None, [p.replace("\\", "/") for p in value]
    if isinstance(value, dict) and "paths" in value and set(value) <= {"root", "paths"}:
        paths = value["paths"]
        if isinstance(paths, list) and all(isinstance(p, str) for p in paths):
            return value.get("root"), [p.replace("\\", "/") for p in paths]
    return None


def read_contents(root: str, paths: list[str], formats: set[str] | None = None) -> ContentValue:
    """The files at ``paths`` (relative to ``root``) parsed into a content value; a
    folder stands for the readable files under it (of ``formats`` only, when given: a
    map's folder is its GeoJSON files). A file named outright that is not readable
    contents is an error."""
    base = Path(root)
    entries: list[ContentEntry] = []
    for p in paths:
        rel = p.strip("/")
        full = base / rel
        if full.is_dir():
            for f in sorted(full.rglob("*"), key=lambda f: f.as_posix().lower()):
                fmt = format_for(f.name)
                if fmt is not None and (formats is None or fmt in formats) and f.is_file():
                    entries.append(read_entry(f, f.relative_to(base).as_posix(), fmt))
            continue
        fmt = format_for(rel)
        if fmt is None:
            raise ValueError(f"{rel} is not readable contents (geojson, json or text)")
        entries.append(read_entry(full, rel, fmt))
    return ContentValue(root=root, entries=entries)


def as_port_value(
    output: str,
    value: Any,
    *,
    root: str = "",
    where: str = "the link",
    target_type: str | None = None,
) -> Any:
    """One output value of a Python link as the ``output`` port's value; None stays None
    (nothing to deliver). Raises ``TypeError`` with a fix-it naming the shapes the port
    takes when the value does not fit."""
    if value is None or output not in SHAPES:
        return value
    try:
        out = _convert(output, value, root, target_type)
    except (_Shape, ValidationError, ValueError, TypeError) as e:
        detail = "" if isinstance(e, _Shape | ValidationError) else str(e).splitlines()[0]
        hint = f" ({detail})" if detail else ""
        got = json.dumps(value, default=str)[:80]
        raise TypeError(
            f"{where} returned {got} for a {output} output{hint}; return {SHAPES[output]}"
        ) from None
    if output == "content" and target_type == "map":
        for e in out.entries:
            if e.format != "geojson":
                raise TypeError(
                    f"{where}: the map shows GeoJSON files, but {e.path} is {e.format}; "
                    'return the .geojson paths or their folders, e.g. {"paths": [".../gis"]}'
                )
    return out


#: the formats a folder stands for in a content output, by target view type
FOLDER_FORMATS: dict[str, set[str]] = {"map": {"geojson"}}


def _convert(output: str, value: Any, root: str, target_type: str | None = None) -> Any:
    if output == "selection":
        sel = _selection(value)
        if target_type == "file_tree":  # a tree's keys are its relative POSIX paths
            return Selection(
                keys=[_tree_key(k, root) for k in sel.keys],
                last=_tree_key(sel.last, root) if sel.last is not None else None,
            )
        return sel
    if output in ("files", "content"):
        found = _paths_of(value)
        if found is not None:
            given, paths = found
            if output == "files":
                return FilesValue(root=_root_of(given, root), paths=paths)
            return read_contents(
                _root_of(given, root), paths, FOLDER_FORMATS.get(target_type or "")
            )
        if (
            output == "content"
            and isinstance(value, dict)
            and isinstance(value.get("entries"), list)
        ):
            return ContentValue.model_validate(
                {"root": _root_of(value.get("root"), root), "entries": value["entries"]}
            )
        raise _Shape
    if output == "time":
        # seconds naming no timeline are the target's own (a video's, a usd stage's)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return TimeValue(t=float(value), timeline=OWN_TIMELINE)
        if isinstance(value, dict):
            return TimeValue.model_validate({"timeline": OWN_TIMELINE, **value})
        raise _Shape
    if output == "camera":
        return CameraValue.model_validate(value)
    return collection_of(value, root)


def _selection(value: Any) -> Selection:
    if _scalar(value):
        return Selection(keys=[str(value)], last=str(value))
    if isinstance(value, list) and all(_scalar(k) for k in value):
        keys = [str(k) for k in value]
        return Selection(keys=keys, last=keys[-1] if keys else None)
    if isinstance(value, dict) and isinstance(value.get("keys"), list):
        keys = [str(k) for k in value["keys"] if _scalar(k)]
        last = value.get("last")
        return Selection(keys=keys, last=str(last) if last is not None else (keys or [None])[-1])
    raise _Shape


def _tree_key(key: str, root: str) -> str:
    """A file tree's key as a script may write it (``os.path.join``: backslashes, or an
    absolute path under the project) as the tree's relative POSIX path."""
    k = key.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/")
    if base and k.lower().startswith(base.lower() + "/"):
        k = k[len(base) + 1 :]
    return k.rstrip("/") or k


def _stem(rel: str) -> str:
    """The path without its extension: an item's key when the script names none (the
    intaker's key without a keys regex)."""
    name = rel.rsplit("/", 1)[-1]
    return rel[: len(rel) - len(name) + name.rindex(".", 1)] if "." in name[1:] else rel


def _role(parts: dict[str, str], rel: str) -> str:
    """A part's role as the intaker names it: its extension (or its name), made unique
    by its folder, then by a count."""
    name = rel.rsplit("/", 1)[-1]
    role = name[name.rindex(".", 1) :].lower() if "." in name[1:] else name
    if role not in parts:
        return role
    head = rel.rsplit("/", 1)[0] if "/" in rel else ""
    cand, n = f"{head}/{role}", 1
    while cand in parts:
        n += 1
        cand = f"{head}/{role}#{n}"
    return cand


def collection_of(value: Any, root: str) -> Collection:
    """A script's collection, built the way an intaker builds one: ``{root?, items}`` or
    a bare list of items; an item is a path, or ``{key?, parts, keys?, meta?}`` whose
    parts are ``{role: path}``, a list of paths (roles by extension) or one path. Paths
    are relative to ``root`` (the project folder unless given) or absolute; each part
    must exist, and is recorded with its relative path and stat (``meta.rel``,
    ``meta.stat``), so the views read it as an intaker's (a file tree lists it, an
    unchanged result is not delivered again). Keys default to the first part's path
    without extension, key fields (``meta.keys``) to ``{stem: key}``."""
    if isinstance(value, list):
        value = {"items": value}
    if not isinstance(value, dict) or not isinstance(value.get("items", []), list):
        raise _Shape
    base = _root_of(value.get("root"), root)
    base_posix = base.replace("\\", "/").rstrip("/")
    items: list[Item] = []
    seen: set[str] = set()
    for i, raw in enumerate(value.get("items") or []):
        if isinstance(raw, str):
            raw = {"parts": [raw]}
        if not isinstance(raw, dict):
            raise _Shape
        given = raw.get("parts")
        if not given:
            raise ValueError(f"item {raw.get('key', i)!r} has no parts; give it its files")
        if isinstance(given, str):
            given = [given]
        listed = (
            [(None, p) for p in given]
            if isinstance(given, list)
            else list(given.items())
            if isinstance(given, dict)
            else None
        )
        if listed is None or not all(isinstance(p, str) for _, p in listed):
            raise _Shape
        parts: dict[str, str] = {}
        rel: dict[str, str] = {}
        stat: dict[str, list[int]] = {}
        for role, path in listed:
            full = Path(path) if Path(path).is_absolute() else Path(base) / path
            try:
                st = full.stat()
            except OSError:
                raise ValueError(
                    f"{path} does not exist; parts are paths relative to {base_posix} or absolute"
                ) from None
            full_posix = str(full).replace("\\", "/")
            r = (
                full_posix[len(base_posix) + 1 :]
                if full_posix.lower().startswith(base_posix.lower() + "/")
                else None
            )
            role = str(role) if role is not None else _role(parts, r or full_posix)
            parts[role] = str(full)
            stat[role] = [st.st_size, st.st_mtime_ns]
            if r is not None:
                rel[role] = r
        first = next(iter(rel.values()), None) or next(iter(parts.values())).replace("\\", "/")
        key = str(raw["key"]) if raw.get("key") is not None else _stem(first)
        if key in seen:
            raise ValueError(
                f"key {key!r} is given twice; give each item its own key, or put the files "
                "in one item's parts"
            )
        seen.add(key)
        keys = raw.get("keys") or (raw.get("meta") or {}).get("keys") or {"stem": key}
        meta = {
            **(raw.get("meta") or {}),
            "keys": {str(k): v for k, v in keys.items()} if isinstance(keys, dict) else keys,
            "source": 0,
            "root": base_posix,
            "rel": rel,
            "stat": stat,
            "size": sum(s[0] for s in stat.values()),
            "mtime_ns": max(s[1] for s in stat.values()),
        }
        items.append(Item(key=key, parts=parts, index=i, meta=meta))
    return Collection(root=base, items=items)


__all__ = [
    "NO_WINDOW",
    "RUNNER",
    "SHAPES",
    "ScriptFailed",
    "TrustStore",
    "as_port_value",
    "build_python_table",
    "build_python_value",
    "check_output",
    "code_hash",
    "collection_of",
    "kill_tree",
    "lookup",
    "read_contents",
    "sha256_of",
    "user_data_dir",
    "value_json",
]
