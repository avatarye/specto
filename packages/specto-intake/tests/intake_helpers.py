"""Shared paths and fixtures-as-functions for the intake tests (no conftest: the core
tests import their own ``conftest`` by name)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

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
RUN = TESTDATA / "runs" / "run-001"
CAM_A = RUN / "cam-a"
CAM_B = RUN / "cam-b"

ALIASES = {
    "@images": [".jpg", ".jpeg", ".png"],
    "@json": [".json"],
    "@text": [".txt", ".csv", ".yaml"],
    "@markdown": [".md"],
    "@geo": [".geojson"],
}
EXT_KINDS = {
    ".jpg": ["image2d"],
    ".jpeg": ["image2d"],
    ".png": ["image2d"],
    ".json": ["json"],
    ".geojson": ["geojson", "json"],
    ".csv": ["text"],
    ".yaml": ["text"],
    ".txt": ["text"],
    ".md": ["markdown", "text"],
}

needs_testdata = pytest.mark.skipif(not CAM_A.exists(), reason=f"no test data at {TESTDATA}")


def copy_frames(dst: Path, n: int, series: str = "cam-a") -> Path:
    """Copy the first ``n`` jpg+json pairs of a series into ``dst``; returns ``dst``."""
    src = RUN / series
    dst.mkdir(parents=True, exist_ok=True)
    jpgs = sorted(src.glob("*.jpg"))[:n]
    for j in jpgs:
        shutil.copy2(j, dst / j.name)
        side = j.with_suffix(".json")
        if side.exists():
            shutil.copy2(side, dst / side.name)
    return dst


def tree_files(root: Path, spec: dict | None = None):
    """What a file tree's root pick stands for (#231): every file under the project
    as its lister finds them (the tree's intaker itself is never walked)."""
    from specto_core.links import FilesValue
    from specto_intake.listing import TreeLister

    paths, _ = TreeLister(root, spec).files_under([""])
    return FilesValue(root=str(root), paths=paths)


def make_tree(root: Path, files: dict[str, str | bytes]) -> Path:
    """Create files under ``root`` from ``{relative posix path: content}``."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return root
