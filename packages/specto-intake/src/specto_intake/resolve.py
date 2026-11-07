"""The intaker link's resolver and watcher, and the read_contents resolver (ADRs 0014, 0016):
files in, a collection or parsed contents out.

``resolve_intaker`` is what the link runtime runs as a job for every ``intaker`` link,
on a worker thread or process: it takes the files arriving on the link's input (the
project folder, or the paths a file tree emitted), keeps the ones under the intaker's
``sources``, screens them, extracts keys, groups parts into items and sorts them (the
M2 scan pipeline, over an explicit file list as well as a folder walk). The project's
own ``.specto/`` folder never enters a collection.

``watch_intaker`` is the watcher the runtime polls for an intaker with ``watch: true``:
one ``scandir`` pass over the intaker's sources (archives as single files, nothing
decoded) giving ``{source index:relative path: size:mtime}``; a changed table means
files arrived, left or were rewritten, and the runtime resolves the link again.

``resolve_read_contents`` does the same source matching for a ``read_contents`` link and
parses each file (``specto_core.contents``): GeoJSON features for a map, JSON sidecars,
text; the result is a ``ContentValue`` on the target's ``content`` port.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from specto_core.contents import ContentValue, format_for, read_entry
from specto_core.errors import SpectoError
from specto_core.links import (
    Collection,
    FilesValue,
    register_preparer,
    register_resolver,
    register_watcher,
)

from .keys import group_items
from .scene3d import attach_scenes
from .screener import Screener, glob_to_regex
from .sources import FoundFile, relative_to_source, resolve_root, walk_source
from .spec import IntakeSpec

SPECTO_DIR = ".specto"
#: the view types whose intakers prepare 3D parts at resolve (the 3D view, and the
#: preview, which renders a 3D item in the 3D view's host)
SCENE_VIEWS = ("3d", "preview")


def intake_spec(params: dict[str, Any]) -> IntakeSpec:
    """The canvas document's ``IntakerParams`` as an ``IntakeSpec``."""
    return IntakeSpec(
        sources=list(params.get("sources") or ["."]),
        screener=list(params.get("screener") or []),
        keys=params.get("keys"),
        group_by=list(params.get("group_by") or []),
        sort=params.get("sort", "natural"),
        watch=bool(params.get("watch", False)),
    )


def _hidden(rel_to_project: str) -> bool:
    return rel_to_project == SPECTO_DIR or rel_to_project.startswith(SPECTO_DIR + "/")


class _SourceRoot:
    """One source, resolved once: where it is on disk, and how a candidate path is
    matched against it as text (``placed``)."""

    def __init__(self, index: int, src: Any, base: Path) -> None:
        self.index = index
        root = resolve_root(src, base)
        self.is_file = root.is_file()
        self.root = _posix(root)
        root_dir = root.parent if self.is_file else root
        self.dir = _posix(root_dir)
        self._dir_key = self.dir.lower().rstrip("/") + "/"
        self._file_key = self.root.lower()
        self.pattern = (
            re.compile(glob_to_regex(src.pattern.replace("\\", "/")), re.IGNORECASE)
            if src.pattern
            else None
        )

    def placed(self, target: str) -> str | None:
        """``target`` (resolved, POSIX) relative to this source's root directory, or None
        when it lies outside it (or is not the file a single-file source names)."""
        low = target.lower()
        if self.is_file:
            return target[len(self._dir_key) :] if low == self._file_key else None
        if low == self._dir_key[:-1]:
            return ""
        if not low.startswith(self._dir_key):
            return None
        rel = target[len(self._dir_key) :]
        return None if self.pattern is not None and self.pattern.fullmatch(rel) is None else rel


def _posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def _found_from_paths(
    spec: IntakeSpec, base: Path, paths: list[str], stats: dict[str, list[int]] | None = None
) -> list[FoundFile]:
    """Explicit relative paths placed under the intaker's sources (the ones outside every
    source, or not matching a source pattern, are simply not this intaker's). The work
    is per kept file, not per candidate: a file tree hands every intaker off it the whole
    folder's paths (``all_files``) or a picked folder's, and 16k of them took seconds
    when each one was resolved and stat'ed before the match (#70). The source roots
    resolve once; a candidate is placed as text under the resolved project folder and
    only a placed one is stat'ed, once, unless the sender already knew its stat
    (``stats``, a file tree's picks: #76)."""
    out: list[FoundFile] = []
    roots = [_SourceRoot(i, s, base) for i, s in enumerate(spec.sources)]
    base_posix = _posix(base.resolve()).rstrip("/")
    for raw in paths:
        rel_proj = raw.replace("\\", "/")
        if _hidden(rel_proj):
            continue
        abs_path = base / rel_proj
        if os.path.isabs(rel_proj) or ".." in rel_proj.split("/"):
            # a path outside the project (an absolute tree path, a parent step): the
            # general placement, resolving it against the sources
            hit = relative_to_source(abs_path, spec.sources, base)
            if hit is None:
                continue
            index, rel = hit
            if roots[index].pattern is not None and roots[index].pattern.fullmatch(rel) is None:
                continue
            root_dir = roots[index].dir
        else:
            target = f"{base_posix}/{rel_proj}" if rel_proj not in ("", ".") else base_posix
            best: tuple[int, str] | None = None
            for r in roots:  # the longest matching root wins, as relative_to_source
                rel_r = r.placed(target)
                if rel_r is not None and (best is None or len(r.dir) > len(roots[best[0]].dir)):
                    best = (r.index, rel_r)
            if best is None:
                continue
            index, rel = best
            root_dir = roots[index].dir
        known = (stats or {}).get(raw)
        if known is not None and len(known) == 2:
            size, mtime_ns = int(known[0]), int(known[1])
        else:
            try:
                st = os.stat(abs_path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            size, mtime_ns = st.st_size, st.st_mtime_ns
        out.append(FoundFile(index, root_dir, rel, str(abs_path), size, mtime_ns))
    return out


def _found_from_walk(spec: IntakeSpec, base: Path) -> list[FoundFile]:
    base_posix = str(base).replace("\\", "/").rstrip("/") + "/"
    out: list[FoundFile] = []
    for i, src in enumerate(spec.sources):
        for f in walk_source(i, src, base):
            full = f"{f.root}/{f.rel}"
            rel_proj = (
                full[len(base_posix) :] if full.lower().startswith(base_posix.lower()) else ""
            )
            if _hidden(rel_proj):
                continue
            out.append(f)
    return out


def resolve_intaker(
    link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any], progress: Any = None
) -> Collection:
    spec = intake_spec(link.get("params") or {})
    base = Path(root) if root else Path.cwd()
    files = value if isinstance(value, FilesValue) else FilesValue.model_validate(value)
    if files.root:
        base = Path(files.root)
    if progress:
        progress(0.0, "finding files")
    found = (
        _found_from_walk(spec, base)
        if files.paths is None
        else _found_from_paths(spec, base, files.paths, files.stats)
    )
    screener = Screener(spec.screener, env.get("aliases") or {})
    kept = [f for f in found if screener.accepts(f.rel)]
    if progress:
        progress(0.8, f"{len(kept)} files, grouping")
    items = group_items(spec, kept)
    if link.get("target_type") in SCENE_VIEWS:
        attach_scenes(items, progress=progress, root=base)
    if progress:
        progress(1.0, f"{len(items)} items")
    return Collection(items=items, root=str(base))


def watch_intaker(
    link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any]
) -> dict[str, str]:
    spec = intake_spec(link.get("params") or {})
    base = Path(root) if root else Path.cwd()
    files = value if isinstance(value, FilesValue) else FilesValue.model_validate(value)
    if files.root:
        base = Path(files.root)
    base_posix = str(base).replace("\\", "/").rstrip("/") + "/"
    table: dict[str, str] = {}
    for i, src in enumerate(spec.sources):
        try:
            for f in walk_source(i, src, base, expand_containers=False):
                full = f"{f.root}/{f.rel}"
                rel_proj = (
                    full[len(base_posix) :] if full.lower().startswith(base_posix.lower()) else ""
                )
                if _hidden(rel_proj):
                    continue
                table[f"{i}:{f.rel}"] = f"{f.size}:{f.mtime_ns}"
        except SpectoError as e:  # a source that is not there (yet) is an entry too
            table[f"{i}:"] = f"missing:{e.message}"
    return table


register_resolver("intaker", resolve_intaker)
register_watcher("intaker", watch_intaker)
# a Python link's collection for these views is prepared the same way (#176)
register_preparer(SCENE_VIEWS, attach_scenes)


def _found(spec: IntakeSpec, files: FilesValue, root: str) -> tuple[Path, list[FoundFile]]:
    base = Path(files.root) if files.root else (Path(root) if root else Path.cwd())
    found = (
        _found_from_walk(spec, base)
        if files.paths is None
        else _found_from_paths(spec, base, files.paths, files.stats)
    )
    return base, found


def resolve_read_contents(
    link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any], progress: Any = None
) -> ContentValue:
    params = link.get("params") or {}
    spec = IntakeSpec(sources=list(params.get("sources") or ["."]))
    files = value if isinstance(value, FilesValue) else FilesValue.model_validate(value)
    fmt = params.get("format") or None
    if progress:
        progress(0.0, "finding files")
    base, found = _found(spec, files, root)
    base_posix = str(base).replace("\\", "/").rstrip("/") + "/"
    todo = []
    for f in found:
        kind = format_for(f.rel, fmt)
        if kind is None:
            continue
        full = f"{f.root}/{f.rel}"
        rel = full[len(base_posix) :] if full.lower().startswith(base_posix.lower()) else f.rel
        todo.append((f.path, rel, kind))
    todo.sort(key=lambda t: t[1].lower())
    entries = []
    for i, (path, rel, kind) in enumerate(todo):
        if progress:
            progress(i / max(1, len(todo)), f"reading {rel}")
        entries.append(read_entry(path, rel, kind))
    if progress:
        progress(1.0, f"{len(entries)} files")
    return ContentValue(root=str(base), entries=entries)


register_resolver("read_contents", resolve_read_contents)

__all__ = ["intake_spec", "resolve_intaker", "resolve_read_contents", "watch_intaker"]
