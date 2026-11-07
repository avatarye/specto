"""The 3D view's resolve-time cache (``<cache>/scene3d``): one folder, records written whole
or not at all.

A record is the scene entry ``prepare_scene`` returned for a file, as JSON, named by the
file's identity (``cache_key``: path, size, mtime); derived files (point buffers, kd-trees,
GLBs) sit beside it. ``cached`` reads a record that still holds, else makes and writes it.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

CACHE_SUBDIR = "scene3d"

Record = dict[str, Any]


def cache_folder(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / CACHE_SUBDIR


#: how often, and how far apart, a Windows sharing violation is tried again: a rename onto
#: a file another thread or process has open (reading it, or renaming onto it) fails with
#: PermissionError for the moment that lasts
SHARING_TRIES = 20
SHARING_WAIT_S = 0.025


def retry_sharing[T](fn: Callable[[], T]) -> T:
    """``fn()``, tried again while it meets a sharing violation (PermissionError on
    Windows); the last one is raised. Elsewhere a PermissionError is final."""
    for attempt in range(SHARING_TRIES):
        try:
            return fn()
        except PermissionError:
            if sys.platform != "win32" or attempt == SHARING_TRIES - 1:
                raise
            time.sleep(SHARING_WAIT_S)
    raise AssertionError("unreachable")


def write_atomic(data: bytes, dst: Path) -> None:
    """``dst`` gets ``data`` whole or not at all: a temporary beside it (``.<name>.<pid>.
    <thread>.tmp``, so concurrent writers never share one), then a rename, tried again
    over a passing sharing violation."""
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        retry_sharing(lambda: os.replace(tmp, dst))
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_record(path: Path) -> Record | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def write_record(path: Path, record: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(json.dumps(record).encode("utf-8"), path)


def cached(
    path: Path,
    make: Callable[[], Record],
    valid: Callable[[Record], bool] = lambda _r: True,
) -> Record:
    """The record at ``path`` when it reads and ``valid`` accepts it, else ``make()``'s,
    written there. A record that cannot be written (on Windows, another reader holding the
    old one makes the rename fail) is still returned: the cache is only a shortcut."""
    rec = read_record(path)
    if rec is not None and valid(rec):
        return rec
    rec = make()
    with contextlib.suppress(OSError):
        write_record(path, rec)
    return rec


__all__ = [
    "CACHE_SUBDIR",
    "Record",
    "cache_folder",
    "cached",
    "read_record",
    "retry_sharing",
    "write_atomic",
    "write_record",
]
