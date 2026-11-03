"""The keyboard modifiers the views read off a tap (as ``int(Qt.KeyboardModifier)``), and
what they mean for the open-file gesture (SPEC-32): Ctrl turns a double-click into the
contextual open."""

from __future__ import annotations

from PySide6.QtCore import Qt

from specto_core.ports import OpenMode

CTRL = int(Qt.KeyboardModifier.ControlModifier.value)
SHIFT = int(Qt.KeyboardModifier.ShiftModifier.value)


def open_mode_for(modifiers: int) -> OpenMode:
    """The way a double-click (or Enter) opens a file: contextual with Ctrl, dedicated
    otherwise."""
    return "contextual" if modifiers & CTRL else "dedicated"


__all__ = ["CTRL", "SHIFT", "open_mode_for"]
