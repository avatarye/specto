"""The intaker watcher's table kept up to date from change notifications (SPEC-8).

``watch_intaker`` lists every source: ``{"<source index>:<rel>": "<size>:<mtime_ns>"}``,
archives as single files, the project's ``.specto/`` left out. ``IntakerListing`` gives
the same table for the paths a change notice names, without listing anything else:

- a path that is now a file is stat'ed and kept or dropped by the walk's own rules (the
  source's pattern and depth, symlinked folders only when the source follows links);
- a path that is now a folder is walked, and what was listed under it before but is
  gone now is dropped (a folder moved in, or replaced);
- a path that is gone is dropped, with everything listed under it (a folder removed or
  moved away).

A notice for a source root itself (a single-file source rewritten, a source folder
removed) asks for a full listing instead (``apply`` returns None). A folder index over
the table makes dropping a folder cost its own entries, not a pass over the table.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from specto_core.fswatch import NotifyUnavailable
from specto_core.links import FilesValue

from .screener import glob_to_regex
from .sources import _walk_dir, resolve_root
from .spec import SourceSpec

SPECTO_DIR = ".specto"


def _posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def _hidden(rel_to_project: str) -> bool:
    return rel_to_project == SPECTO_DIR or rel_to_project.startswith(SPECTO_DIR + "/")


class _Source:
    def __init__(self, index: int, src: SourceSpec, base: Path) -> None:
        self.index = index
        self.src = src
        self.root = resolve_root(src, base)
        self.exists = self.root.exists()
        self.is_file = self.root.is_file()
        self.root_posix = _posix(self.root)
        self.dir_posix = _posix(self.root.parent) if self.is_file else self.root_posix
        self._dir_low = self.dir_posix.lower().rstrip("/") + "/"
        self._root_low = self.root_posix.lower()
        self.pattern = (
            re.compile(glob_to_regex(src.pattern.replace("\\", "/")), re.IGNORECASE)
            if src.pattern
            else None
        )

    def rel(self, path_posix: str) -> str | None:
        """``path_posix`` relative to this source's root folder; '' for the root itself
        (or the file a single-file source names); None outside it."""
        low = path_posix.lower()
        if self.is_file:
            return "" if low == self._root_low else None
        if low == self._dir_low[:-1]:
            return ""
        return path_posix[len(self._dir_low) :] if low.startswith(self._dir_low) else None


class IntakerListing:
    """``WatchListing`` for an intaker link (see the module doc)."""

    def __init__(self, link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any]):
        from .resolve import intake_spec

        spec = intake_spec(link.get("params") or {})
        base = Path(root) if root else Path.cwd()
        files = value if isinstance(value, FilesValue) else FilesValue.model_validate(value)
        if files.root:
            base = Path(files.root)
        self.base_low = _posix(base).lower().rstrip("/") + "/"
        self.sources = [_Source(i, s, base) for i, s in enumerate(spec.sources)]
        #: "<index>:<folder rel>/" ('' rel: "<index>:") -> keys of the files right in it
        self._dirs: dict[str, set[str]] = {}

    # -- WatchListing ------------------------------------------------------------------
    def roots(self) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        for s in self.sources:
            if not s.exists:
                raise NotifyUnavailable(f"source {s.root_posix} is not there", retry=True)
            out.append((str(s.root.parent) if s.is_file else str(s.root), not s.is_file))
        return out

    def reset(self, table: dict[str, str]) -> None:
        self._dirs = {}
        for key in table:
            self._index(key)

    def apply(self, table: dict[str, str], paths: list[str]) -> tuple[int, int, int] | None:
        counts = [0, 0, 0]
        for raw in paths:
            p = _posix(raw)
            low = p.lower()
            if low.startswith(self.base_low) and _hidden(p[len(self.base_low) :]):
                continue
            for s in self.sources:
                rel = s.rel(p)
                if rel is None:
                    continue
                if rel == "" and s.is_file:
                    # the file a single-file source names: rewritten, or back
                    try:
                        st = os.stat(raw)
                    except OSError:
                        return None  # gone: the full listing says it is missing
                    if not stat.S_ISREG(st.st_mode):
                        return None
                    self._set(
                        table, f"{s.index}:{s.root.name}", f"{st.st_size}:{st.st_mtime_ns}", counts
                    )
                elif rel == "":
                    return None  # a source folder itself: only a full listing can tell
                else:
                    self._apply_one(table, s, rel, raw, counts)
        return counts[0], counts[1], counts[2]

    # -- one path ------------------------------------------------------------------------
    def _apply_one(
        self, table: dict[str, str], s: _Source, rel: str, path: str, counts: list[int]
    ) -> None:
        key = f"{s.index}:{rel}"
        try:
            st = os.stat(path)
        except OSError:
            st = None
        if st is not None and not self._reachable(s, rel):
            st = None  # below the source's depth, or through a symlinked folder
        if st is not None and stat.S_ISDIR(st.st_mode):
            self._drop(table, key, counts)
            self._walk(table, s, rel, path, counts)
        elif st is not None and stat.S_ISREG(st.st_mode) and self._wanted(s, rel):
            self._set(table, key, f"{st.st_size}:{st.st_mtime_ns}", counts)
        elif key in table:
            self._drop(table, key, counts)
        else:  # gone, and not a file the table held: a folder, maybe
            self._drop_under(table, key + "/", counts)

    def _reachable(self, s: _Source, rel: str) -> bool:
        """The walk would reach ``rel``: within the depth, and through no symlinked
        folder unless the source follows links."""
        folders = rel.split("/")[:-1]
        if s.src.depth is not None and len(folders) > s.src.depth:
            return False
        if not s.src.follow_links and folders:
            d = s.root
            for part in folders:
                d = d / part
                if os.path.islink(d):
                    return False
        return True

    def _wanted(self, s: _Source, rel: str) -> bool:
        return s.pattern is None or s.pattern.fullmatch(rel) is not None

    def _walk(
        self, table: dict[str, str], s: _Source, rel: str, path: str, counts: list[int]
    ) -> None:
        """A folder now: list it as the walk would, and drop what was under it before
        but is not now."""
        prefix = f"{s.index}:{rel}/"
        if not s.src.follow_links and os.path.islink(path):
            self._drop_under(table, prefix, counts)
            return
        depth = rel.count("/") + 1
        if s.src.depth is not None and depth > s.src.depth:
            self._drop_under(table, prefix, counts)
            return
        seen: set[str] = set()
        for f in _walk_dir(
            s.index, Path(path), s.root_posix, rel + "/", depth, s.src, s.pattern, False
        ):
            full = f"{f.root}/{f.rel}"
            if full.lower().startswith(self.base_low) and _hidden(full[len(self.base_low) :]):
                continue
            k = f"{s.index}:{f.rel}"
            seen.add(k)
            self._set(table, k, f"{f.size}:{f.mtime_ns}", counts)
        for k in self._keys_under(prefix):
            if k not in seen:
                self._drop(table, k, counts)

    # -- the table and its folder index --------------------------------------------------
    @staticmethod
    def _folder_of(key: str) -> str:
        cut = key.rfind("/")
        return key[: cut + 1] if cut >= 0 else key[: key.index(":") + 1]

    def _index(self, key: str) -> None:
        self._dirs.setdefault(self._folder_of(key), set()).add(key)

    def _set(self, table: dict[str, str], key: str, stamp: str, counts: list[int]) -> None:
        before = table.get(key)
        if before == stamp:
            return
        table[key] = stamp
        if before is None:
            counts[0] += 1
            self._index(key)
        else:
            counts[2] += 1

    def _drop(self, table: dict[str, str], key: str, counts: list[int]) -> None:
        if table.pop(key, None) is None:
            return
        counts[1] += 1
        folder = self._dirs.get(self._folder_of(key))
        if folder is not None:
            folder.discard(key)
            if not folder:
                del self._dirs[self._folder_of(key)]

    def _keys_under(self, prefix: str) -> list[str]:
        # the names come from the disk, as the notices' do: compared as they are
        return [k for d, keys in self._dirs.items() if d.startswith(prefix) for k in keys]

    def _drop_under(self, table: dict[str, str], prefix: str, counts: list[int]) -> None:
        for k in self._keys_under(prefix):
            self._drop(table, k, counts)


def intaker_listing(
    link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any]
) -> IntakerListing:
    return IntakerListing(link, value, root=root, env=env)


__all__ = ["IntakerListing", "intaker_listing"]
