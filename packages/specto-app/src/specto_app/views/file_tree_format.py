"""How the file tree's rows and search hits print a size, a time and an age (#115)."""

from __future__ import annotations

import time


def format_size(size: int) -> str:
    """Compact, 1024-based, at most five characters: ``12B``, ``1.5K``, ``3.2M``, ``12M``."""
    if size < 1024:
        return f"{size}B"
    value = float(size)
    for unit in "KMGT":
        value /= 1024
        if value < 1024 or unit == "T":
            return f"{value:.1f}{unit}" if value < 10 else f"{value:.0f}{unit}"
    return f"{value:.0f}T"


def format_bytes(size: int) -> str:
    """A details line's size, 1024-based: ``12 B``, ``1.5 KB``, ``3.2 MB``, ``12 MB``."""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            break
    return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"


def format_stamp(mtime_ns: int) -> str:
    """A details line's time, local, to the second: ``2026-10-22 18:49:03``; "" when
    unknown."""
    if mtime_ns <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_ns / 1e9))


#: the age buckets' upper bounds in seconds: under 10 min, under an hour, under a day
AGE_BOUNDS = (10 * 60, 60 * 60, 24 * 60 * 60)


def age_of(mtime_ns: int, now: float) -> int:
    """0..3 by ``AGE_BOUNDS`` (3: older than a day); -1 when the time is unknown."""
    if mtime_ns <= 0:
        return -1
    age = now - mtime_ns / 1e9
    return next((i for i, bound in enumerate(AGE_BOUNDS) if age < bound), len(AGE_BOUNDS))


__all__ = ["AGE_BOUNDS", "age_of", "format_bytes", "format_size", "format_stamp"]
