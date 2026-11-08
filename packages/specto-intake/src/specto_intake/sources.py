"""Walk sources into files: a file, a directory (with depth and link policy) or a glob.

Every found file carries the index of the source that produced it, the resolved root
and the POSIX path relative to that root; the screener and the keys regex see only the
relative path (HLD 3.6).

Containers (zip, tar; ``specto_core.containers``) expand lazily into virtual entries:
``drive.zip`` under a root yields ``drive.zip#/front/00042.jpg`` and so on, listed from
the archive's directory without extracting anything. A source whose root *is* an
archive yields its members relative to the archive's folder. ``expand_containers=False``
lists the archive as one file instead (the watcher's cheap snapshot).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from specto_core.containers import is_container, join_virtual, list_members, split_virtual
from specto_core.errors import NotFound
from specto_core.pathguard import path_refusal
from specto_core.project import SPECTO_DIR

from .screener import glob_to_regex
from .spec import SourceSpec, root_refused


@dataclass(frozen=True)
class FoundFile:
    source: int
    root: str  # resolved root directory, POSIX separators
    rel: str  # path relative to root, POSIX separators
    path: str  # absolute native path
    size: int
    mtime_ns: int


def resolve_root(src: SourceSpec, base: Path) -> Path:
    """Where a source is on disk. The root's text is judged first (SPEC-72): a network,
    device or rooted path is refused before ``resolve`` or any caller's ``exists`` can
    reach it, whatever made the spec."""
    why = path_refusal(src.root)
    if why is not None:
        raise root_refused(src.root, why)
    root = Path(src.root).expanduser()
    if not root.is_absolute():
        root = Path(base) / root
    return root.resolve()


def posix(p: Path | str) -> str:
    """``p`` with forward slashes (the intake's path text)."""
    return str(p).replace("\\", "/")


def hidden(rel_to_project: str) -> bool:
    """A project-relative POSIX path that is, or lies under, ``.specto/``: never listed,
    never an arrival."""
    return rel_to_project == SPECTO_DIR or rel_to_project.startswith(SPECTO_DIR + "/")


def walk_source(
    index: int,
    src: SourceSpec,
    base: Path,
    *,
    expand_containers: bool = True,
    links: list[str] | None = None,
    skip: Callable[[str], bool] | None = None,
) -> Iterator[FoundFile]:
    root = resolve_root(src, base)
    if not root.exists():
        raise NotFound(
            f"source {index} root {posix(root)} does not exist",
            fix="check the path; relative roots resolve against the project folder",
        )
    pattern = (
        re.compile(glob_to_regex(src.pattern.replace("\\", "/")), re.IGNORECASE)
        if src.pattern
        else None
    )
    if root.is_file():
        st = root.stat()
        found = FoundFile(
            index, posix(root.parent), root.name, str(root), st.st_size, st.st_mtime_ns
        )
        if expand_containers and is_container(root.name):
            yield from _expand(found, pattern)
        else:
            yield found
        return
    root_posix = posix(root)
    yield from walk_dir(
        index,
        root,
        root_posix,
        "",
        0,
        src,
        pattern,
        expand_containers=expand_containers,
        links=links,
        skip=skip,
    )


def _expand(archive: FoundFile, pattern: re.Pattern[str] | None) -> Iterator[FoundFile]:
    """The members of a container as virtual entries; an unreadable archive stays one
    plain file so the screener and the skipped list still see it."""
    try:
        members = list_members(archive.path)
    except Exception:
        yield archive
        return
    for m in members:
        rel = join_virtual(archive.rel, m.path)
        if pattern is not None and pattern.fullmatch(rel) is None:
            continue
        yield FoundFile(
            archive.source,
            archive.root,
            rel,
            join_virtual(archive.path, m.path),
            m.size,
            m.mtime_ns or archive.mtime_ns,
        )


def walk_dir(
    index: int,
    directory: Path,
    root_posix: str,
    prefix: str,
    depth: int,
    src: SourceSpec,
    pattern: re.Pattern[str] | None,
    *,
    expand_containers: bool = True,
    links: list[str] | None = None,
    skip: Callable[[str], bool] | None = None,
) -> Iterator[FoundFile]:
    """The files under ``directory`` (``prefix`` its path below the source root,
    ``depth`` its depth), as ``walk_source`` finds them. ``links`` collects the folders
    descended into through a symbolic link or a junction (no change notices come from
    inside them); ``skip(name)`` leaves files out by name."""
    try:
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return
    subdirs: list[tuple[str, Path]] = []
    for e in entries:
        rel = f"{prefix}{e.name}"
        try:
            if e.is_dir(follow_symlinks=src.follow_links):
                if src.depth is None or depth < src.depth:
                    subdirs.append((rel + "/", Path(e.path)))
                    if links is not None and (e.is_symlink() or e.is_junction()):
                        links.append(rel)
                continue
            if skip is not None and skip(e.name):
                continue
            if not e.is_file(follow_symlinks=True):
                continue
            container = expand_containers and is_container(e.name)
            if not container and pattern is not None and pattern.fullmatch(rel) is None:
                continue
            st = e.stat(follow_symlinks=True)
        except OSError:
            continue
        found = FoundFile(index, root_posix, rel, e.path, st.st_size, st.st_mtime_ns)
        if container:
            yield from _expand(found, pattern)
        else:
            yield found
    for sub_prefix, sub in subdirs:
        yield from walk_dir(
            index,
            sub,
            root_posix,
            sub_prefix,
            depth + 1,
            src,
            pattern,
            expand_containers=expand_containers,
            links=links,
            skip=skip,
        )


def relative_to_source(path: Path, sources: list[SourceSpec], base: Path) -> tuple[int, str] | None:
    """Which source contains ``path``: ``(index, rel)`` for the longest matching root.
    A virtual path (``drive.zip#/front/a.jpg``) is placed by its archive; ``rel`` keeps
    the member part."""
    text = str(path)
    inner: str | None = None
    hit = split_virtual(text)
    if hit is not None:
        text, inner = hit
    p = Path(text).expanduser()
    target = posix(Path(posix(p if p.is_absolute() else Path(base) / p)).resolve())
    best: tuple[int, str] | None = None
    best_len = -1
    for i, src in enumerate(sources):
        root = resolve_root(src, base)
        root_dir = root.parent if root.is_file() else root
        rp = posix(root_dir)
        if target.lower() == rp.lower() or target.lower().startswith(rp.lower().rstrip("/") + "/"):
            if root.is_file() and target.lower() != posix(root).lower():
                continue
            if len(rp) > best_len:
                rel = target[len(rp) :].lstrip("/")
                best, best_len = (i, join_virtual(rel, inner) if inner else rel), len(rp)
    return best
