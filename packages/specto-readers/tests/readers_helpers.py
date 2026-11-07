"""Importable job functions for the pool tests (spawn pickles them by reference) and
shared test-data paths."""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
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
GEO = TESTDATA / "geo" / "sites.geojson"
NOTES = TESTDATA / "notes" / "run-001-summary.md"
needs_testdata = pytest.mark.skipif(not CAM_A.exists(), reason=f"no test data at {TESTDATA}")


def first_jpg() -> Path:
    return sorted(CAM_A.glob("*.jpg"))[0]


def first_json() -> Path:
    return sorted(CAM_A.glob("*.json"))[0]


def add_one(x: int, progress) -> int:
    progress(0.5, "halfway")
    return x + 1


def make_array(n: int, progress, dtype: str = "uint8") -> np.ndarray:
    return np.arange(n, dtype=dtype).reshape(2, -1)


def make_list(progress) -> list:
    return [np.zeros((2, 2), np.float32), "tag", np.ones(3, np.int16)]


def empty_array(progress) -> np.ndarray:
    return np.zeros((0, 4), np.uint8)


def raise_error(progress) -> None:
    raise ValueError("decoder exploded")


def crash(progress) -> None:
    os._exit(7)


def sleep_then(x: float, progress) -> float:
    time.sleep(x)
    return x


def pid(progress) -> int:
    return os.getpid()


def qt_loaded(progress) -> bool:
    import sys

    return any(m.startswith("PySide6") or m.startswith("PyQt") for m in sys.modules)


def has_module(name: str, progress) -> bool:
    import sys

    return name in sys.modules
