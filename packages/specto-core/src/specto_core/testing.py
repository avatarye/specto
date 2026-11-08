"""Helpers for the test suites only; nothing in Specto imports this module.

``trap_paths`` is the path guard's no-filesystem-call trap (``specto_core.pathguard``):
every filesystem call whose arguments name ``word`` records itself and raises, while the
rest run (a store's own files are read and written meanwhile).
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

#: the calls the trap covers, by owner
PATH_CALLS = (
    *("is_file", "exists", "stat", "lstat", "resolve", "is_dir", "is_symlink"),
    *("read_bytes", "read_text", "unlink", "replace", "rename", "open"),
)
OS_CALLS = ("stat", "lstat", "listdir", "scandir", "replace", "rename", "unlink", "remove")
OS_PATH_CALLS = ("realpath", "exists", "abspath", "isfile", "isdir")


def trap_paths(monkeypatch: Any, calls: list[str], word: str = "host") -> None:
    """Patch every call in ``PATH_CALLS``, ``OS_CALLS`` and ``OS_PATH_CALLS`` through
    ``monkeypatch`` (pytest's fixture) so that one naming ``word`` appends its arguments
    to ``calls`` and raises ``AssertionError``. ``monkeypatch.undo()`` lifts it."""

    def trapped(real):
        def call(*a, **k):
            named = [str(x) for x in a if isinstance(x, (str, os.PathLike))]
            if any(word in x.lower() for x in named):
                calls.append(" ".join(named))
                raise AssertionError(f"a filesystem call on {named}")
            return real(*a, **k)

        return call

    for owner, names in ((pathlib.Path, PATH_CALLS), (os, OS_CALLS), (os.path, OS_PATH_CALLS)):
        for name in names:
            monkeypatch.setattr(owner, name, trapped(getattr(owner, name)))
