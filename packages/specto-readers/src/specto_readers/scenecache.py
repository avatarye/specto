"""The 3D view's resolve-time cache (``<cache>/scene3d``): one folder, records written whole
or not at all.

- **Records**: a record is the scene entry ``prepare_scene`` returned for a file, as JSON,
  named by the file's identity (``cache_key``: path, size, mtime); derived files (point
  buffers, kd-trees, GLBs) sit beside it. ``cached`` reads a record that still holds,
  else makes and writes it; a record that cannot be written is skipped.
- **Temporaries**: every temporary Specto writes is named by ``unique_temp``
  (``.<stem>.<pid>.<thread><suffix>``) with one of three endings: ``write_atomic``'s
  ``.tmp``, the DAE converter's ``.convert.glb`` and its child's ``.convert.glb.log``.
- **Retry**: ``retry_sharing`` tries an operation again over a Windows sharing or lock
  violation (another process has the file open for the moment); any other error,
  access-denied included, is raised at once.
- **Sweep**: ``sweep_stale`` removes temporaries of those names older than an hour (a
  crashed process's), nothing else, and nothing in a linked folder.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
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
#: a file another thread or process has open (reading it, or renaming onto it) fails for
#: the moment that lasts
SHARING_TRIES = 20
SHARING_WAIT_S = 0.025
#: ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION; ERROR_ACCESS_DENIED (5) is a real
#: refusal and is not tried again
SHARING_WINERRORS = (32, 33)


def retry_sharing[T](fn: Callable[[], T]) -> T:
    """``fn()``, tried again while it meets a Windows sharing or lock violation; the last
    one is raised. Any other error, a real access-denied included, is raised at once."""
    for attempt in range(SHARING_TRIES):
        try:
            return fn()
        except OSError as e:
            if getattr(e, "winerror", None) not in SHARING_WINERRORS:
                raise
            if attempt == SHARING_TRIES - 1:
                raise
            time.sleep(SHARING_WAIT_S)
    raise AssertionError("unreachable")


#: the temporaries Specto writes here, by what they end in: write_atomic's, the DAE
#: converter's GLB and its child's log (``unique_temp`` names them all)
TMP_SUFFIX = ".tmp"
CONVERT_TEMP_SUFFIX = ".convert.glb"
CONVERT_LOG_SUFFIX = CONVERT_TEMP_SUFFIX + ".log"
TEMP_SUFFIXES = (TMP_SUFFIX, CONVERT_TEMP_SUFFIX, CONVERT_LOG_SUFFIX)
_TEMP_NAME = re.compile(
    r"^\..+\.\d+\.\d+(?:" + "|".join(re.escape(s) for s in TEMP_SUFFIXES) + r")$"
)
#: temporaries older than this are a crashed run's: swept before a conversion
STALE_AFTER_S = 3600.0


def unique_temp(folder: Path, stem: str, suffix: str) -> Path:
    """``.<stem>.<pid>.<thread><suffix>`` in ``folder``: no other process or thread uses
    the name while this one lives."""
    return folder / f".{stem}.{os.getpid()}.{threading.get_ident()}{suffix}"


def is_temp(name: str) -> bool:
    """Whether a file name is one of ``unique_temp``'s with a ``TEMP_SUFFIXES`` ending."""
    return bool(_TEMP_NAME.match(name))


def sweep_stale(folder: Path, now: float | None = None) -> int:
    """Remove a dead run's temporaries (``is_temp``) older than ``STALE_AFTER_S`` from
    ``folder``; nothing else, and nothing when the folder is a link or junction (its
    files are someone else's). Returns how many went."""
    try:
        if folder.is_symlink() or folder.is_junction():
            return 0
        entries = list(folder.iterdir())
    except OSError:
        return 0
    now = time.time() if now is None else now
    gone = 0
    for p in entries:
        if not is_temp(p.name):
            continue
        with contextlib.suppress(OSError):
            if p.is_file() and now - p.stat().st_mtime > STALE_AFTER_S:
                p.unlink()
                gone += 1
    return gone


def write_atomic(data: bytes, dst: Path) -> None:
    """``dst`` gets ``data`` whole or not at all: a temporary beside it (``unique_temp``,
    so concurrent writers never share one), then a rename, tried again over a passing
    sharing violation."""
    tmp = unique_temp(dst.parent, dst.name, TMP_SUFFIX)
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
    "CONVERT_LOG_SUFFIX",
    "CONVERT_TEMP_SUFFIX",
    "Record",
    "cache_folder",
    "cached",
    "read_record",
    "retry_sharing",
    "sweep_stale",
    "unique_temp",
    "write_atomic",
    "write_record",
]
