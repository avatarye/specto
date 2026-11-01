"""Drags out of views (#185). ``payload`` (Qt-free): what a drag carries and its mime
data, built from the view's target (the context menu's, #154); ``qt``: the one place a
drag starts (``start_drag``: QMimeData, the drag picture, ``QDrag.exec``). How a view
contributes: ``docs/contracts.md`` section 11."""

from __future__ import annotations

from .payload import (
    HTML_MIME,
    KINDS,
    PNG_MIME,
    SPECTO_MIME,
    TEXT_MIME,
    URI_MIME,
    DragItem,
    DragPayload,
    payload_for,
)

__all__ = [
    "HTML_MIME",
    "KINDS",
    "PNG_MIME",
    "SPECTO_MIME",
    "TEXT_MIME",
    "URI_MIME",
    "DragItem",
    "DragPayload",
    "payload_for",
]
