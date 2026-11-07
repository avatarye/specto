"""Snapshot images of views for the overworld cards (HLD 4.4, 5.3).

A paused view keeps the last image it showed; the overworld card draws it with its age.
The shell grabs every on-screen view's item on its own (``capture``, through
``QQuickItem.grabToImage``: the item subtree rendered alone, so nothing painted over the
view, another view, a popup, the prompt bar, lands in its card, and the image comes in
the item's own device pixels); a host that renders where a grab cannot see returns its
own image from ``snapshot_image()`` (contracts section 11). Grabs land asynchronously, a
frame later; ``capture`` reports each through ``on_done``. QML reads the store through
the ``image://snaps/<view>?<rev>`` provider; the ``rev`` in the URL defeats Qt's image
cache.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider, QQuickItem

PROVIDER_ID = "snaps"
MAX_EDGE = 640  # stored snapshots are scaled down to this edge: a card never needs more
KEEP = Qt.AspectRatioMode.KeepAspectRatio
SMOOTH = Qt.TransformationMode.SmoothTransformation


class SnapshotStore:
    """view id -> (image, taken_at, rev). Thread-safe for the image provider."""

    def __init__(self, clock=time.monotonic) -> None:
        self._images: dict[str, tuple[QImage, float, int]] = {}
        self._rev = 0
        self._lock = threading.Lock()
        self.clock = clock

    def put(self, view_id: str, image: QImage) -> int:
        if image is None or image.isNull():
            return self.rev_of(view_id)
        if max(image.width(), image.height()) > MAX_EDGE:
            image = image.scaled(QSize(MAX_EDGE, MAX_EDGE), KEEP, SMOOTH)
        with self._lock:
            self._rev += 1
            self._images[view_id] = (image, self.clock(), self._rev)
            return self._rev

    def get(self, view_id: str) -> QImage | None:
        with self._lock:
            entry = self._images.get(view_id)
        return entry[0] if entry else None

    def rev_of(self, view_id: str) -> int:
        with self._lock:
            entry = self._images.get(view_id)
        return entry[2] if entry else 0

    def age_s(self, view_id: str) -> float | None:
        with self._lock:
            entry = self._images.get(view_id)
        return (self.clock() - entry[1]) if entry else None

    def url(self, view_id: str) -> str:
        rev = self.rev_of(view_id)
        return f"image://{PROVIDER_ID}/{view_id}?{rev}" if rev else ""

    def drop(self, view_id: str) -> None:
        with self._lock:
            self._images.pop(view_id, None)

    def views(self) -> list[str]:
        with self._lock:
            return list(self._images)


def age_text(age_s: float | None) -> str:
    """``12 s``, ``3 min``, ``2 h`` or ``no snapshot``."""
    if age_s is None:
        return "no snapshot"
    if age_s < 60:
        return f"{int(age_s)} s"
    if age_s < 3600:
        return f"{int(age_s // 60)} min"
    return f"{age_s / 3600:.1f} h"


class SnapshotProvider(QQuickImageProvider):
    def __init__(self, store: SnapshotStore) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.store = store

    def requestImage(self, image_id: str, size: QSize, requested: QSize) -> QImage:
        view_id = image_id.split("?", 1)[0]
        img = self.store.get(view_id)
        if img is None:
            img = QImage(4, 4, QImage.Format.Format_ARGB32)
            img.fill(0)
        if requested.isValid() and requested.width() > 0 and requested.height() > 0:
            img = img.scaled(requested, KEEP, SMOOTH)
        try:
            size.setWidth(img.width())
            size.setHeight(img.height())
        except Exception:
            pass
        return img


DoneFn = Callable[[str], None]
_inflight: list[Any] = []  # grab results stay referenced until they land


def capture(
    store: SnapshotStore,
    view_items: dict[str, tuple[Any, QQuickItem]],
    own_images: dict[str, QImage] | None = None,
    on_done: DoneFn | None = None,
) -> list[str]:
    """Grab each view's item on its own; ``own_images`` (from ``snapshot_image``) win and
    land at once, grabs land a frame later. ``view_items`` maps a view id to ``(window,
    item)``. ``on_done(view_id)`` is called as each snapshot reaches the store. Returns
    the views a snapshot was requested for."""
    requested: list[str] = []
    own = own_images or {}
    for vid, (win, item) in view_items.items():
        if vid in own:
            store.put(vid, own[vid])
            requested.append(vid)
            if on_done is not None:
                on_done(vid)
            continue
        if win is None or item is None or item.width() <= 1 or item.height() <= 1:
            continue
        result = item.grabToImage()
        if result is None:  # no window to render in
            continue
        _inflight.append(result)

        def landed(result=result, vid=vid) -> None:
            if result in _inflight:
                _inflight.remove(result)
            store.put(vid, result.image())
            if on_done is not None:
                on_done(vid)

        result.ready.connect(landed)
        requested.append(vid)
    return requested


__all__ = [
    "MAX_EDGE",
    "PROVIDER_ID",
    "DoneFn",
    "SnapshotProvider",
    "SnapshotStore",
    "age_text",
    "capture",
]
