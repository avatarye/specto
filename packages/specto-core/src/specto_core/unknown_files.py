"""Unknown files in a submitted canvas document (spec #35 story 24).

A document may name files that are not there: an intaker source that matches nothing
(under the project folder, or on this machine for an absolute local path), or a Python
link whose code file is missing. Both are
reported at submit with the schema and port-graph problems, each with a fix-it, so the
agent fixes everything in one retry. Only a project (a real folder) can be checked; a
headless canvas skips this.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

from .canvas_document import Problem
from .pathguard import path_refusal, split_glob


def source_exists(root: Path, source: str) -> bool:
    """A source names a file or folder under ``root`` (``.`` is the folder itself), or
    a glob that matches at least one entry. A source the path guard refuses (a network
    or device path; the document's validation reports it first, SPEC-72) is never looked
    up: it does not exist here.

    An absolute glob (an absolute local path, ``C:/data/**/*.jpg``) is never walked: this
    runs on every intaker on every submit, on the canvas thread, and a walk from a drive's
    root has no bound. It exists when its literal base folder (the segments before the
    first one with glob magic) is a folder (SPEC-79)."""
    if path_refusal(source) is not None:
        return False
    base, pattern = split_glob(source)  # the intake spec's own split (SPEC-94)
    if pattern is not None:
        if PurePath(base).anchor:
            return Path(base).is_dir()
        try:
            return next(root.glob(source), None) is not None
        except (ValueError, OSError, NotImplementedError):
            return False
    return (root / base).exists()


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
                out.append(_missing_source(f"/links/{lid}/params/sources/{i}", str(src)))
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


def _missing_source(path: str, src: str) -> Problem:
    """The problem for a source that names nothing: a relative one under the project
    folder, an absolute one on this machine."""
    base, pattern = split_glob(src)
    if PurePath(base).anchor:
        what = f"its folder {base!r}" if pattern is not None else "it"
        return Problem(
            path=path,
            message=f"source {src!r} names an absolute local path; {what} does not exist",
            fix="point it at an absolute local path that exists, or at a path or glob under "
            "the project folder ('.' is the whole folder)",
        )
    return Problem(
        path=path,
        message=f"source {src!r} matches no file or folder under the project",
        fix="point it at a path or glob under the project folder ('.' is the whole folder) "
        "or at an absolute local path; the project's files in the context list them",
    )


__all__ = ["file_problems", "source_exists"]
