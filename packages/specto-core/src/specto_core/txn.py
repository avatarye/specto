"""Undo-stack records (ADR 0013): one ``TxnRecord`` per submit, rebuilt from the journal.
``LiveCanvas`` drives them; this module is the data."""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import PatchError
from .journal import JournalEntry
from .patch import apply_patch

_OBJ_RE = re.compile(r"^/(?:views|links)/([^/]+)")


def touched_objects(patch: list[dict[str, Any]]) -> list[str]:
    """View and link ids a patch touches (``/views/<id>``, ``/links/<id>``), in order."""
    seen: dict[str, None] = {}
    for op in patch:
        for p in (op.get("path"), op.get("from")):
            if not p:
                continue
            m = _OBJ_RE.match(p)
            if m:
                seen[m.group(1)] = None
    return list(seen)


class TxnRecord(BaseModel):
    """One submit on the undo stack: its patch and inverse (glossary: patch)."""

    model_config = ConfigDict(extra="forbid")
    id: str
    no: int
    canvas: str
    label: str
    origin: str
    ts: str = ""
    seq: int = 0  # journal seq
    patch: list[dict[str, Any]] = Field(default_factory=list)
    inverse: list[dict[str, Any]] = Field(default_factory=list)
    touched: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """What ``state`` and the reply strip show (``canvas.history`` adds the patch)."""
        return {
            "txn": self.id,
            "no": self.no,
            "label": self.label,
            "origin": self.origin,
            "ts": self.ts,
            "ops": len(self.patch),
            "touched": list(self.touched),
        }


def rebuild_history(
    canvas_id: str, entries: list[JournalEntry]
) -> tuple[list[TxnRecord], list[TxnRecord], int]:
    """Replay journal entries into ``(history, log, cursor)`` the way they were built
    live. Raises ``ValueError`` when an undo/redo entry does not fit the stack."""
    history: list[TxnRecord] = []
    log: list[TxnRecord] = []
    cursor = 0
    for e in sorted(entries, key=lambda x: x.seq):
        if e.kind == "txn":
            rec = TxnRecord(
                id=e.txn,
                no=e.txn_no,
                canvas=canvas_id,
                label=e.label,
                origin=e.origin,
                ts=e.ts,
                seq=e.seq,
                patch=e.patch,
                inverse=e.inverse,
                touched=e.touched or touched_objects(e.patch),
            )
            dropped = {r.id for r in history[cursor:]}
            del history[cursor:]
            log = [r for r in log if r.id not in dropped]
            history.append(rec)
            cursor = len(history)
            log.append(rec)
        elif e.kind == "undo":
            if cursor == 0 or history[cursor - 1].id != e.txn:
                raise ValueError(f"journal entry {e.seq} undoes {e.txn}, which is not on top")
            cursor -= 1
        elif e.kind == "redo":
            if cursor >= len(history) or history[cursor].id != e.txn:
                raise ValueError(f"journal entry {e.seq} redoes {e.txn}, which is not next")
            cursor += 1
        elif e.kind in ("rename", "rewire"):
            # the canvas was rewritten on load (#79 names, #83 links moved to the project
            # source): the older patches name the old ids and ports
            history, log, cursor = [], [], 0
        elif e.kind == "squash":
            n = len(e.squashed)
            top = [r.id for r in history[cursor - n : cursor]] if n <= cursor else []
            if cursor != len(history) or top != list(e.squashed):
                raise ValueError(f"journal entry {e.seq} squashes {e.squashed}, not the top")
            rec = TxnRecord(
                id=e.txn,
                no=e.txn_no,
                canvas=canvas_id,
                label=e.label,
                origin=e.origin,
                ts=e.ts,
                seq=e.seq,
                patch=e.patch,
                inverse=e.inverse,
                touched=e.touched or touched_objects(e.patch),
            )
            gone = set(e.squashed)
            del history[cursor - n :]
            history.append(rec)
            cursor = len(history)
            log = [r for r in log if r.id not in gone] + [rec]
    return history, log, cursor


def stack_applies(data: dict[str, Any], history: list[TxnRecord], cursor: int) -> bool:
    """The next undo (and redo) patch of a rebuilt stack must apply to the document."""
    try:
        if cursor > 0:
            apply_patch(copy.deepcopy(data), history[cursor - 1].inverse)
        if cursor < len(history):
            apply_patch(copy.deepcopy(data), history[cursor].patch)
    except PatchError:
        return False
    return True


class UndoResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    txn: str
    label: str = ""
    patch: list[dict[str, Any]] = Field(default_factory=list)
    touched: list[str] = Field(default_factory=list)
    cursor: int = 0
    depth: int = 0
