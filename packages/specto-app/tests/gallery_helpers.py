"""Shared pieces for the gallery tests: test data location, a frame copier and a headless
stand-in for the native item (``FakeGallery``) that records what the host asked of it."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _default_testdata() -> Path:
    """``<repo>/testdata``; from a git worktree (no testdata of its own), the main
    checkout's."""
    repo = Path(__file__).resolve().parents[3]
    git = repo / ".git"
    if not (repo / "testdata").exists() and git.is_file():
        gitdir = git.read_text(encoding="utf-8").split(":", 1)[1].strip()
        return Path(gitdir).resolve().parents[2] / "testdata"  # <main>/.git/worktrees/<n>
    return repo / "testdata"


TESTDATA = Path(os.environ.get("SPECTO_TESTDATA") or _default_testdata())
CAM_A = TESTDATA / "runs" / "run-001" / "cam-a"
CAM_B = TESTDATA / "runs" / "run-001" / "cam-b"

needs_testdata = pytest.mark.skipif(not CAM_A.exists(), reason=f"no test data at {TESTDATA}")


def copy_frames(dst: Path, n: int, series: str = "cam-a") -> Path:
    src = TESTDATA / "runs" / "run-001" / series
    dst.mkdir(parents=True, exist_ok=True)
    for j in sorted(src.glob("*.jpg"))[:n]:
        shutil.copy2(j, dst / j.name)
        side = j.with_suffix(".json")
        if side.exists():
            shutil.copy2(side, dst / side.name)
    return dst


class FakeGallery:
    """Implements ``GalleryItemLike`` without Qt. ``window`` bounds the wanted range so
    tests can check that only visible (+margin) indices are requested; ``tier`` is what
    the fake view asks the feeder to decode (0 mini, 1 full, 2 large). The layout is
    the native one at a fixed ``width``: columns follow the tile size."""

    width = 1000.0
    margin = gap = 8.0

    def __init__(self, window: int = 1_000_000, tier: int = 1) -> None:
        self.window = window
        self.tier = tier
        self._count = 0
        self.pushed: dict[int, tuple[int, int, str]] = {}
        self.pixels: dict[int, bytes] = {}  # RGBA of what push_bytes received
        self.failed_idx: set[int] = set()
        self._selected = -1
        self.revealed: list[int] = []
        self._columns = 4
        self._tile = 0.0
        self._scroll_rows = 0.0
        self.set_views: list[tuple[int, float]] = []
        self.clears = 0
        self.captions: tuple[list[str], list[str]] = ([], [])
        self.caption_mode = 1
        self.caption = ""
        self.on_selected: Callable[[int, int], None] | None = None
        self.on_activated: Callable[[int], None] | None = None
        self.on_quick_look: Callable[[int], None] | None = None
        self.on_view_changed: Callable[[], None] | None = None
        self.on_gesture_started: Callable[[], None] | None = None
        self.on_gesture_ended: Callable[[], None] | None = None
        self.on_wanted_changed: Callable[[], None] | None = None
        self.on_drag: Callable[[int, int], None] | None = None

    # -- item API ----------------------------------------------------------------------
    def set_count(self, n: int) -> None:
        self._count = n

    def count(self) -> int:
        return self._count

    def wanted(self) -> tuple[int, int, int]:
        if self._count == 0:
            return (-1, -1, self.tier)
        return (0, min(self._count, self.window) - 1, self.tier)

    def visible(self) -> tuple[int, int]:
        return self.wanted()[:2]

    def missing(self, first: int, last: int, tier: int) -> list[int]:
        skip = self.pushed.keys() | self.failed_idx
        return [i for i in range(first, last + 1) if i not in skip]

    def push_shm(self, index: int, shm: str, offset: int, w: int, h: int) -> bool:
        self.pushed[index] = (w, h, "shm")
        return True

    def push_bytes(self, index: int, rgba: bytes, w: int, h: int) -> bool:
        assert len(rgba) == w * h * 4
        self.pushed[index] = (w, h, "bytes")
        self.pixels[index] = bytes(rgba)
        return True

    def set_failed(self, index: int, failed: bool = True) -> None:
        if failed:
            self.failed_idx.add(index)
        else:
            self.failed_idx.discard(index)

    def clear(self) -> None:
        self.clears += 1
        self.pushed.clear()
        self.pixels.clear()
        self.failed_idx.clear()

    def select(self, index: int) -> None:
        self._selected = index

    def selected(self) -> int:
        return self._selected

    def reveal(self, index: int, animate: bool = True) -> None:
        self.revealed.append(index)

    def _tile_for(self, columns: int) -> float:
        return (self.width - 2 * self.margin - (columns - 1) * self.gap) / columns

    def set_view(self, columns: int, scroll_rows: float) -> None:
        self.set_columns(columns)
        self._scroll_rows = float(scroll_rows)
        self.set_views.append((self._columns, self._scroll_rows))

    def view(self) -> tuple[int, float]:
        return (self._columns, self._scroll_rows)

    def set_columns(self, columns: int) -> None:
        self._columns = int(columns)
        self._tile = self._tile_for(self._columns)

    def set_tile(self, px: float) -> None:
        self._tile = float(px)
        w = self.width - 2 * self.margin + self.gap
        self._columns = max(1, round(w / (self._tile + self.gap)))

    def tile(self) -> float:
        return self._tile

    def set_scroll_rows(self, rows: float) -> None:
        self._scroll_rows = float(rows)

    def set_captions(self, keys: list[str], names: list[str]) -> None:
        self.captions = (list(keys), list(names))

    def set_caption_mode(self, mode: int) -> None:
        self.caption_mode = mode

    def cell_aspect(self) -> float:
        return 1.5

    def stats(self) -> dict[str, Any]:
        n = len(self.pushed)
        return {"residentFull": n, "residentMini": n, "residentLarge": 0}

    def set_caption(self, text: str) -> None:
        self.caption = text

    # -- test drivers (what a user would do) ----------------------------------------
    def user_click(self, index: int) -> None:
        self._selected = index
        if self.on_selected:
            self.on_selected(index, 0)

    def user_activate(self, index: int) -> None:
        self._selected = index
        if self.on_activated:
            self.on_activated(index)

    def user_key(self, delta: int) -> int:
        """Arrow-key step as the item does it: clamp, select, reveal, report."""
        i = max(0, min(self._count - 1, (self._selected if self._selected >= 0 else 0) + delta))
        self._selected = i
        self.revealed.append(i)
        if self.on_selected:
            self.on_selected(i, 0)
        return i

    def user_drag(self, columns: int, scroll_rows: float) -> None:
        if self.on_gesture_started:
            self.on_gesture_started()
        self.set_columns(columns)
        self._scroll_rows = scroll_rows
        if self.on_view_changed:
            self.on_view_changed()

    def user_release(self) -> None:
        if self.on_gesture_ended:
            self.on_gesture_ended()
