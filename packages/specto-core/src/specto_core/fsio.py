"""Disk writes that survive a sharing lock.

On Windows a sync client (Dropbox) or a scanner opens a file it noticed without
FILE_SHARE_DELETE for a moment; replacing or appending to it then fails with
``PermissionError`` (WinError 5 or 32). The lock is short, so a few retries with a
short backoff land most writes; a persistent one is the caller's to report.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: the backoff: FIRST, 2x FIRST, ... capped at MAX, between ATTEMPTS tries
ATTEMPTS = 6
QUICK_ATTEMPTS = 2  # while a lock persists: one short retry, so the UI does not stall
FIRST_DELAY_S = 0.02
MAX_DELAY_S = 0.16


def retry_locked[T](fn: Callable[[], T], *, attempts: int = ATTEMPTS) -> T:
    """Run ``fn``; on ``PermissionError`` wait and try again, up to ``attempts`` times.
    The last error is raised unchanged."""
    delay = FIRST_DELAY_S
    for i in range(max(1, attempts)):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, MAX_DELAY_S)
    raise AssertionError("unreachable")
