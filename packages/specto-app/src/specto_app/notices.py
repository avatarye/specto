"""Notices: fix-it messages for the user (HLD principle 4).

Every ``SpectoError`` a GUI gesture hits (a quota, a lock, a bad binding) becomes a card
with the message and the fix. Info notices (command results) expire on their own; errors
stay until dismissed.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

LEVELS = ("info", "warn", "error")


class Notices(QObject):
    changed = Signal()
    posted = Signal(str)  # level

    def __init__(self, parent: QObject | None = None, *, limit: int = 8) -> None:
        super().__init__(parent)
        self._items: list[dict[str, Any]] = []
        self._n = 0
        self._limit = limit
        self.log: list[dict[str, Any]] = []  # everything ever posted (tests, diagnostics)

    def post(self, level: str, message: str, *, fix: str | None = None, **detail: Any) -> int:
        if level not in LEVELS:
            level = "info"
        self._n += 1
        item = {
            "id": self._n,
            "level": level,
            "message": str(message),
            "fix": fix or "",
            "ts": time.time(),
            **{k: v for k, v in detail.items() if v is not None},
        }
        self._items.append(item)
        self.log.append(item)
        del self._items[: max(0, len(self._items) - self._limit)]
        tag = {"info": "[OK]", "warn": "[WARN]", "error": "[ERROR]"}[level]
        print(f"{tag} {message}" + (f"  -> {fix}" if fix else ""))
        self.changed.emit()
        self.posted.emit(level)
        return self._n

    def info(self, message: str, **kw: Any) -> int:
        return self.post("info", message, **kw)

    def warn(self, message: str, **kw: Any) -> int:
        return self.post("warn", message, **kw)

    def error(self, message: str, **kw: Any) -> int:
        return self.post("error", message, **kw)

    @Slot(int)
    def dismiss(self, notice_id: int) -> None:
        before = len(self._items)
        self._items = [n for n in self._items if n["id"] != notice_id]
        if len(self._items) != before:
            self.changed.emit()

    @Slot()
    def clear(self) -> None:
        if self._items:
            self._items.clear()
            self.changed.emit()

    @Property("QVariantList", notify=changed)
    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._items)


__all__ = ["LEVELS", "Notices"]
