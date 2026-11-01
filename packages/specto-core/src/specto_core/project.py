"""Project (ADR 0012): a data folder with exactly one canvas, kept under ``.specto/``.

    .specto/project.toml          name, id (UUID written once), defaults
    .specto/canvas.json           the canvas document (canvas_document.py)
    .specto/canvas.journal.jsonl
    .specto/links/*.py            Python link code (ADR 0016)

A v1 (M2) project (``.specto/canvases/<id>.json``, no ``canvas.json``) is refused on
open with a clear message; there is no migration (ADR 0017). One project per app
process. Caches live in the platform cache dir keyed by project id; nothing here writes
into the project folder except ``.specto/``.
"""

from __future__ import annotations

import json
import os
import tomllib
import uuid
from pathlib import Path
from typing import Any

from . import fsio
from .canvas_document import (
    TREE_WATCH,
    CanvasDocument,
    fit_project_source,
    infer_intaker_scopes,
    new_canvas_document,
    validate_canvas,
    watch_the_file_tree,
)
from .errors import NotFound, SchemaError, SpectoError
from .names import rename_legacy_ids
from .schema import V1_FIX, migrate

SPECTO_DIR = ".specto"
PROJECT_FILE = "project.toml"
CANVAS_FILE = "canvas.json"
JOURNAL_FILE = "canvas.journal.jsonl"
LINKS_DIR = "links"


def _toml_dumps(table: dict[str, Any]) -> str:
    """Minimal TOML writer for the flat project table (strings, ints, bools, floats)."""
    lines = []
    nested = {}
    for k, v in table.items():
        if isinstance(v, dict):
            nested[k] = v
            continue
        lines.append(f"{k} = {_toml_value(v)}")
    for name, sub in nested.items():
        lines.append("")
        lines.append(f"[{name}]")
        for k, v in sub.items():
            lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to project.toml")


class Project:
    def __init__(self, root: Path, meta: dict[str, Any]) -> None:
        self.root = Path(root).resolve()
        self.meta = meta

    # -- discovery / lifecycle ------------------------------------------------------
    @property
    def specto_dir(self) -> Path:
        return self.root / SPECTO_DIR

    @property
    def canvas_file(self) -> Path:
        return self.specto_dir / CANVAS_FILE

    @property
    def journal_file(self) -> Path:
        return self.specto_dir / JOURNAL_FILE

    @property
    def links_dir(self) -> Path:
        return self.specto_dir / LINKS_DIR

    @property
    def id(self) -> str:
        return str(self.meta["id"])

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.root.name)

    @property
    def slug(self) -> str:
        """Address slug for ``proj:<slug>``."""
        return "".join(c if c.isalnum() or c in "_-." else "_" for c in self.name)

    @classmethod
    def init(cls, root: Path, name: str | None = None) -> Project:
        root = Path(root).resolve()
        if (root / SPECTO_DIR / PROJECT_FILE).exists():
            raise SpectoError(f"{root} is already a project", fix="use Project.open")
        meta = {
            "name": name or root.name,
            "id": str(uuid.uuid4()),
            # load rewrites a fresh canvas never needs (``rewrote``)
            "rewrites": [TREE_WATCH],
            "defaults": {},
        }
        proj = cls(root, meta)
        proj.links_dir.mkdir(parents=True, exist_ok=True)
        proj._write_meta()
        proj.save_canvas_document(new_canvas_document(proj.name))
        return proj

    @classmethod
    def open(cls, root: Path) -> Project:
        root = Path(root).resolve()
        pf = root / SPECTO_DIR / PROJECT_FILE
        if not pf.exists():
            raise NotFound(
                f"no {SPECTO_DIR}/{PROJECT_FILE} under {root}",
                fix="run `specto init <dir>` or pass --project",
            )
        if not (root / SPECTO_DIR / CANVAS_FILE).exists():
            raise SchemaError(
                f"{root} is a Specto v1 (M2) project: it has no {SPECTO_DIR}/{CANVAS_FILE}",
                fix=V1_FIX,
            )
        with open(pf, "rb") as f:
            meta = tomllib.load(f)
        if "id" not in meta:
            raise SpectoError(f"{pf} has no id")
        return cls(root, meta)

    @classmethod
    def find(cls, start: Path | None = None) -> Project | None:
        """Nearest ``.specto/`` at or above ``start`` (default cwd), like git."""
        cur = Path(start or os.getcwd()).resolve()
        for p in (cur, *cur.parents):
            if (p / SPECTO_DIR / PROJECT_FILE).exists():
                return cls.open(p)
        return None

    @classmethod
    def open_or_init(cls, root: Path, name: str | None = None) -> Project:
        try:
            return cls.open(root)
        except NotFound:
            return cls.init(root, name)

    def rewrote(self, name: str) -> bool:
        """Whether the canvas went through the one-time load rewrite ``name``."""
        return name in (self.meta.get("rewrites") or [])

    def mark_rewritten(self, name: str) -> None:
        """Record the one-time load rewrite ``name`` in ``project.toml``."""
        if not self.rewrote(name):
            self.meta["rewrites"] = [*(self.meta.get("rewrites") or []), name]
            self._write_meta()

    def _write_meta(self) -> None:
        self.specto_dir.mkdir(parents=True, exist_ok=True)
        (self.specto_dir / PROJECT_FILE).write_text(_toml_dumps(self.meta), encoding="utf-8")

    # -- the one canvas --------------------------------------------------------------
    def canvas_document(self) -> CanvasDocument:
        """Load and validate the project's canvas document."""
        return validate_canvas(self.load_canvas()[0])

    def load_canvas(
        self, *, rename: bool = True, rewire: bool = True
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """The canvas document's data as saved, made loadable: migrated, (``rewire``)
        made to fit the project source (#83: links off the file tree's ``all_files`` or
        ``project.folder`` moved to ``project.all_files``, the layout moved right when a
        view sits on the project node; #115: the file tree's auto-update on), intaker
        scopes inferred, and (``rename``) legacy ``v<n>`` view ids renamed to names (#79).
        Returns the data and the rename mapping (old id -> name; empty when the saved
        canvas already uses names).
        ``LiveCanvas.open`` loads with ``rewire=False``, applies the rewrite itself and
        journals it once."""
        with open(self.canvas_file, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise SchemaError(f"{self.canvas_file} is not a JSON object")
        data, _ = migrate(data)  # refuses v1 and newer documents with both versions
        if rewire:
            fit_project_source(data)
            watch_the_file_tree(data, saved_before=not self.rewrote(TREE_WATCH))
        # a canvas saved before intaker scopes still opens: the scope is read off the
        # source and written back with the next save
        infer_intaker_scopes(data)
        renamed = rename_legacy_ids(data) if rename else {}
        return data, renamed

    def save_canvas_document(
        self, doc: CanvasDocument | dict[str, Any], *, quick: bool = False
    ) -> None:
        """Write the canvas atomically (temp file, then replace). A sharing lock on the
        target (a sync client, a scanner) is retried with a short backoff; ``quick``
        (the lock persisted last time) tries only briefly. A lock that outlasts the
        retries raises the ``PermissionError``: the caller keeps its state."""
        data = doc.to_data() if isinstance(doc, CanvasDocument) else doc
        self.specto_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.canvas_file.with_suffix(".json.tmp")

        def write() -> None:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
                f.write("\n")
            tmp.replace(self.canvas_file)

        fsio.retry_locked(write, attempts=fsio.QUICK_ATTEMPTS if quick else fsio.ATTEMPTS)
