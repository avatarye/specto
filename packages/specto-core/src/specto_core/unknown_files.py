"""Unknown files in a submitted canvas document (spec #35 story 24).

A document may name files that are not there: an intaker source that matches nothing
under the project folder, or a Python link whose code file is missing. Both are
reported at submit with the schema and port-graph problems, each with a fix-it, so the
agent fixes everything in one retry. Only a project (a real folder) can be checked; a
headless canvas skips this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .canvas_document import Problem

GLOB_CHARS = frozenset("*?[")


def source_exists(root: Path, source: str) -> bool:
    """A source names a file or folder under ``root`` (``.`` is the folder itself), or
    a glob that matches at least one entry."""
    if any(c in source for c in GLOB_CHARS):
        try:
            return next(root.glob(source), None) is not None
        except (ValueError, OSError):
            return False
    return (root / source).exists()


def file_problems(doc: dict[str, Any], root: Path | None) -> list[Problem]:
    if root is None:
        return []
    root = Path(root)
    out: list[Problem] = []
    for lid, link in (doc.get("links") or {}).items():
        kind = link.get("kind")
        if kind == "intaker":
            for i, src in enumerate(link.get("params", {}).get("sources") or []):
                if source_exists(root, str(src)):
                    continue
                out.append(
                    Problem(
                        path=f"/links/{lid}/params/sources/{i}",
                        message=f"source {src!r} matches no file or folder under the project",
                        fix="point it at a path or glob under the project folder ('.' is the "
                        "whole folder); the project's files in the context list them",
                    )
                )
        elif kind == "python":
            py = link.get("python") or {}
            path = str(py.get("path", ""))
            if path and not (root / path).is_file():
                out.append(
                    Problem(
                        path=f"/links/{lid}/python/path",
                        message=f"{path} is not in the project",
                        fix=f"write it with def {py.get('entry', 'resolve')}(items, params, "
                        "root) under .specto/links/, or point path at an existing file",
                    )
                )
    return out


__all__ = ["file_problems", "source_exists"]
