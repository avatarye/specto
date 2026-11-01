"""Journal (glossary): ``.specto/canvas.journal.jsonl``, append-only.

Each line: ``{seq, ts, kind, txn, txn_no, origin, schema_version, label, patch, inverse,
touched}``. ``kind`` is ``txn`` for a submit, ``undo`` / ``redo`` for history moves,
``squash`` for the submits of one prompt turn folded into one (``squashed`` names them;
the patch is the turn's whole difference, already applied by the squashed entries),
``navigate`` for the window frame following the user's pan (no undo step), ``rename``
and ``rewire`` for a saved canvas rewritten on load (#79 names, #83 links moved to the
project source; no undo step, and the history before it is dropped). The other fields
are kept so journals written before the M2 path was deleted still read.
Replay runs from a snapshot forward. Watch arrivals are events, not submits, so they
never appear.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import fsio
from .clock import now_iso
from .patch import apply_patch
from .schema import SCHEMA_VERSION


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    ts: str = Field(default_factory=now_iso)
    kind: Literal["txn", "undo", "redo", "squash", "navigate", "rename", "rewire"] = "txn"
    txn: str
    txn_no: int
    origin: str
    connection: str = "local"
    schema_version: int = SCHEMA_VERSION
    label: str = ""
    commands: list[dict[str, Any]] = Field(default_factory=list)
    patch: list[dict[str, Any]] = Field(default_factory=list)
    inverse: list[dict[str, Any]] = Field(default_factory=list)
    irreversible: bool = False
    replayable: bool = True
    undoable: bool = True
    effects: list[str] = Field(default_factory=list)
    touched: list[str] = Field(default_factory=list)
    #: ``squash`` only: the ids of the records this one replaced, oldest first
    squashed: list[str] = Field(default_factory=list)
    #: ``rename`` entries: legacy view id -> name (#79)
    renamed: dict[str, str] = Field(default_factory=dict)
    #: ``rewire`` (or ``rename``) entries: links moved to the project source on load (#83)
    rewired: list[str] = Field(default_factory=list)


class Journal:
    """Append-only writer/reader. ``path=None`` keeps entries in memory only."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._seq = 0
        self._mem: list[JournalEntry] = []
        #: entries not yet on disk (a sharing lock denied the append); the next append
        #: writes them first, in order. ``write_error`` is the lock still in the way.
        self.pending: list[JournalEntry] = []
        self.write_error: OSError | None = None
        #: told once per streak of denied appends (the canvas turns it into a notice)
        self.on_write_error: Callable[[OSError], None] | None = None
        if path is not None and path.exists():
            for e in self.read():
                self._seq = max(self._seq, e.seq)

    @property
    def last_seq(self) -> int:
        return self._seq

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def append(self, entry: JournalEntry) -> JournalEntry:
        if self.path is None:
            self._mem.append(entry)
            return entry
        self.pending.append(entry)
        self.flush()
        return entry

    def flush(self) -> bool:
        """Append the pending entries. A sharing lock (``PermissionError``) is retried
        briefly; one that persists keeps the entries pending and returns False."""
        if self.path is None or not self.pending:
            return True
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)

        def write() -> None:
            with open(path, "a", encoding="utf-8") as f:
                for e in self.pending:
                    f.write(json.dumps(e.model_dump(), separators=(",", ":"), ensure_ascii=False))
                    f.write("\n")

        try:
            fsio.retry_locked(
                write, attempts=fsio.QUICK_ATTEMPTS if self.write_error else fsio.ATTEMPTS
            )
        except PermissionError as e:
            first = self.write_error is None
            self.write_error = e
            if first and self.on_write_error is not None:
                self.on_write_error(e)
            return False
        self.pending.clear()
        self.write_error = None
        return True

    def read(self) -> list[JournalEntry]:
        if self.path is None:
            return list(self._mem)
        if not self.path.exists():
            return []
        out: list[JournalEntry] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(JournalEntry.model_validate(json.loads(line)))
        return out

    def tail(self, n: int) -> list[JournalEntry]:
        return self.read()[-n:]


def replay(
    snapshot: dict[str, Any], entries: list[JournalEntry], *, upto_seq: int | None = None
) -> tuple[dict[str, Any], list[int]]:
    """Apply the patches of replayable entries after ``snapshot`` in order.

    Returns the resulting document and the seqs that were skipped (not replayable).
    Undo/redo entries are applied as recorded, so the result matches the live history.
    """
    data = json.loads(json.dumps(snapshot))
    skipped: list[int] = []
    for e in entries:
        if upto_seq is not None and e.seq > upto_seq:
            break
        if not e.replayable:
            skipped.append(e.seq)
            continue
        if e.kind == "squash":
            continue  # its difference was applied by the entries it folded
        apply_patch(data, e.patch)
    return data, skipped
