"""Named data the prompt-bar agent keeps for its own project (#219): ``data_write``,
``data_read``, ``data_list`` and ``data_delete`` over ``<project>/.specto/<folder>/<file>``
(json, md, txt or csv; ``.specto/layouts/review.json``), the primitives a self-made
skill needs to store and bring back what it composed (a layout of views and links, a
list of the user's favourite runs). The agent has no file tools: this is the one scoped
write it gets outside the canvas, the skills, the commands and the link code.

The scope is the rule, not the agent's care: a path is folders and a file name of
letters, digits, ``_`` and ``-`` under ``.specto/``, at most ``MAX_DEPTH`` folders, never
one of Specto's own (``RESERVED``: skills, commands, links, scratch, the plugin
manifest) nor a dot folder, never ``..`` or absolute, and whatever it resolves to stays
under ``.specto/`` (a junction out of it is refused). A ``.json`` file must parse.
Reading and writing data never approves anything: a layout that brings a python link
back keeps the user's approval state of that code (ADR 0006, #203). Each write and
delete returns a ``DataChange`` with the file before and after, so the thread's data row
undoes a turn's data changes in one step. No Qt here.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import InvalidArgs, NotFound

DATA_DIR = ".specto"
#: folders of ``.specto/`` that are Specto's own, never the agent's data
RESERVED = frozenset({"skills", "commands", "links", "scratch", "canvases", ".claude-plugin"})
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
EXTS = (".json", ".md", ".txt", ".csv")
MAX_DEPTH = 3
MAX_BYTES = 1_000_000
MAX_LIST = 500

_PATH = {
    "type": "string",
    "description": "The file, relative to .specto/: <folder>/<name>.json (or .md, .txt, "
    ".csv); folders and names of letters, digits, _ and -; never skills, commands, links "
    "or scratch: layouts/review.json",
}

DATA_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "data_write",
            "Write a data file of your own under .specto/<folder>/ (json, md, txt or csv; "
            "a .json must parse; at most 1 MB), replacing it if it exists: what a skill of "
            "yours needs to keep for this project, such as a named layout of views and "
            "links as .specto/layouts/<name>.json. The panel shows the user the file with "
            "Open and Undo. Nothing else outside the canvas is yours to write.",
            params={
                "type": "object",
                "properties": {
                    "path": _PATH,
                    "text": {"type": "string", "description": "The whole file's text."},
                },
                "required": ["path", "text"],
            },
        ),
        ToolSpec(
            "data_read",
            "Read one of your data files under .specto/<folder>/: its text, size and "
            "modification time.",
            read_only=True,
            params={"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        ),
        ToolSpec(
            "data_list",
            "List your data files: with no folder, the data folders under .specto/; with "
            "one (layouts), its files with size and modification time.",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "A data folder under .specto/ (layouts); all of them "
                        "when left out.",
                    }
                },
            },
        ),
        ToolSpec(
            "data_delete",
            "Delete one of your data files under .specto/<folder>/ (the user can undo it).",
            destructive=True,
            params={"type": "object", "properties": {"path": _PATH}, "required": ["path"]},
        ),
    )
}


@dataclass(frozen=True)
class DataChange:
    """One data file written or removed: its text before (``None``: it was new) and
    after (``None``: deleted)."""

    rel: str  # layouts/review.json
    path: Path
    before: str | None
    after: str | None

    @property
    def qualified(self) -> str:
        return f"{DATA_DIR}/{self.rel}"

    @property
    def action(self) -> str:
        return "deleted" if self.after is None else "wrote"

    @property
    def note(self) -> str:
        return f"{self.action} {self.qualified}"


class DataStore:
    """The agent's data folders under the project's ``.specto``: scoped, validated
    writes, reads, listings, deletes and their undo."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / DATA_DIR

    # -- the scope ---------------------------------------------------------------------------
    def rel(self, value: Any) -> str:
        """``layouts/review.json`` for a path the agent gave (``.specto/`` and ``./``
        prefixes dropped, backslashes read as slashes); ``InvalidArgs`` outside the scope."""
        text = str(value or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        text = text.removeprefix(f"{DATA_DIR}/")
        if not text:
            raise InvalidArgs("the data file needs a path", fix="layouts/review.json")
        if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
            raise InvalidArgs(
                f"{text!r} is not under .specto/", fix="a relative path: layouts/review.json"
            )
        parts = text.split("/")
        if len(parts) < 2:
            raise InvalidArgs(
                f"{text!r} has no folder: data files live in a folder of .specto/",
                fix="layouts/review.json",
            )
        if len(parts) - 1 > MAX_DEPTH:
            raise InvalidArgs(f"{text!r} is deeper than {MAX_DEPTH} folders")
        *folders, name = parts
        stem, dot, ext = name.rpartition(".")
        if not dot or f".{ext}".lower() not in EXTS:
            raise InvalidArgs(
                f"{name!r} is not a data file", fix=f"one of {', '.join(EXTS)}: review.json"
            )
        for seg in (*folders, stem):
            if seg in ("..", "."):
                raise InvalidArgs(f"{text!r} leaves its folder", fix="no . or .. segments")
            if not SEGMENT_RE.match(seg):
                raise InvalidArgs(
                    f"{seg!r} in {text!r} is not a plain name",
                    fix="letters, digits, _ and -, no leading dot: layouts/review.json",
                )
        if folders[0] in RESERVED:
            raise InvalidArgs(
                f".specto/{folders[0]} is Specto's own, not a data folder",
                fix="use another folder: layouts, notes, runs",
            )
        rel = "/".join([*folders, f"{stem}.{ext.lower()}"])
        self._inside(self.dir / rel)
        return rel

    def _inside(self, path: Path) -> None:
        base = self.dir.resolve()
        try:
            resolved = path.resolve()
        except OSError as e:
            raise InvalidArgs(f"{path} cannot be resolved: {e}") from None
        if resolved != base and base not in resolved.parents:
            raise InvalidArgs(f"{path} resolves outside .specto/", fix="no links out of it")

    def _folder(self, value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/").strip("/")
        text = text.removeprefix(f"{DATA_DIR}/").strip("/")
        if not text:
            return ""
        parts = text.split("/")
        if len(parts) > MAX_DEPTH:
            raise InvalidArgs(f"{text!r} is deeper than {MAX_DEPTH} folders")
        for seg in parts:
            if not SEGMENT_RE.match(seg):
                raise InvalidArgs(f"{seg!r} in {text!r} is not a plain folder name")
        if parts[0] in RESERVED:
            raise InvalidArgs(f".specto/{parts[0]} is Specto's own, not a data folder")
        self._inside(self.dir / text)
        return text

    def path(self, rel: str) -> Path:
        return self.dir / rel

    def _read(self, rel: str) -> str | None:
        try:
            return self.path(rel).read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            raise InvalidArgs(f".specto/{rel} is not a text file") from None

    def _entry(self, rel: str) -> dict[str, Any]:
        st = self.path(rel).stat()
        return {
            "path": f"{DATA_DIR}/{rel}",
            "bytes": st.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        }

    # -- the tools ---------------------------------------------------------------------------
    def write(self, path: Any, text: Any) -> DataChange:
        rel = self.rel(path)
        body = str(text if text is not None else "").replace("\r\n", "\n")
        if not body.strip():
            raise InvalidArgs("the file's text is empty", fix="data_delete removes a file")
        data = body.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise InvalidArgs(
                f"the text is {len(data)} bytes, more than {MAX_BYTES}",
                fix="keep data files small; a layout is views and links, not file lists",
            )
        if rel.endswith(".json"):
            try:
                json.loads(body)
            except ValueError as e:
                raise InvalidArgs(f"the text is not JSON: {e}", fix="fix it or use .txt") from None
        before = self._read(rel)
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return DataChange(rel, target, before, body)

    def read(self, path: Any) -> dict[str, Any]:
        rel = self.rel(path)
        text = self._read(rel)
        if text is None:
            folder = rel.rpartition("/")[0]
            have = ", ".join(e["path"] for e in self._files(folder)) or "none"
            raise NotFound(f"no data file .specto/{rel}", fix=f"files in {folder}: {have}")
        return {**self._entry(rel), "text": text}

    def _folders(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self.dir.iterdir()
            if p.is_dir() and p.name not in RESERVED and not p.name.startswith(".")
        )

    def _files(self, folder: str) -> list[dict[str, Any]]:
        base = self.dir / folder if folder else self.dir
        if not base.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in EXTS:
                continue
            rel = p.relative_to(self.dir).as_posix()
            try:
                self.rel(rel)
            except InvalidArgs:
                continue  # a file outside the scope (a reserved folder, a dot name)
            out.append(self._entry(rel))
            if len(out) >= MAX_LIST:
                break
        return out

    def list(self, folder: Any = None) -> dict[str, Any]:
        name = self._folder(folder)
        if not name:
            return {"folders": self._folders()}
        files = self._files(name)
        return {"folder": f"{DATA_DIR}/{name}", "files": files, "count": len(files)}

    def delete(self, path: Any) -> DataChange:
        rel = self.rel(path)
        before = self._read(rel)
        if before is None:
            raise NotFound(f"no data file .specto/{rel}", fix="data_list shows the files")
        self.path(rel).unlink()
        return DataChange(rel, self.path(rel), before, None)

    def undo(self, changes: list[DataChange]) -> bool:
        """Put every file of ``changes`` (one turn's) back as it was before them.
        Refused, changing nothing, when a file no longer holds what the turn left."""
        last: dict[str, DataChange] = {}
        first: dict[str, DataChange] = {}
        for c in changes:
            first.setdefault(c.rel, c)
            last[c.rel] = c
        if any(self._read(rel) != c.after for rel, c in last.items()):
            return False
        for rel, c in first.items():
            if c.before is None:
                self.path(rel).unlink(missing_ok=True)
            else:
                self.path(rel).parent.mkdir(parents=True, exist_ok=True)
                self.path(rel).write_bytes(c.before.encode("utf-8"))
        return True

    def run(self, tool: str, args: dict[str, Any]) -> tuple[DataChange | None, dict[str, Any]]:
        """One data tool call: the change (``None`` for a read or a listing) and the
        result the agent reads."""
        if tool == "data_write":
            change = self.write(args.get("path"), args.get("text"))
            return change, {
                "wrote": change.qualified,
                "replaced": change.before is not None,
                "bytes": len(change.after.encode("utf-8")) if change.after else 0,
            }
        if tool == "data_read":
            return None, self.read(args.get("path"))
        if tool == "data_list":
            return None, self.list(args.get("folder"))
        if tool == "data_delete":
            change = self.delete(args.get("path"))
            return change, {"deleted": change.qualified}
        raise KeyError(tool)


__all__ = ["DATA_DIR", "DATA_TOOLS", "EXTS", "MAX_BYTES", "RESERVED", "DataChange", "DataStore"]
