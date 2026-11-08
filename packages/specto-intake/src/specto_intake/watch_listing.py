"""The intaker watcher's table, from a full listing or kept up to date from change
notifications (SPEC-8, ADR 0027).

``watch_table`` lists every source: ``{"<source index>:<rel>": "<size>:<mtime_ns>"}``,
archives as single files, the project's ``.specto/`` and writers' temporaries
(``transient``) left out; ``watch_intaker`` is the intaker's watcher over it.
``IntakerListing`` gives the same table for the paths a change notice names, without
listing anything else:

- a path that is now a file is stat'ed and kept or dropped by the walk's own rules (the
  source's pattern and depth, symlinked folders only when the source follows links);
- a path that is now a folder is walked, and what was listed under it before but is
  gone now is dropped (a folder moved in, or replaced);
- a path that is gone is dropped, with everything listed under it (a folder removed or
  moved away).

On a case-insensitive file system (NTFS, APFS) a notice's name is checked against the
folder's own entry names, so a rename that only changes case (``a.jpg`` to ``A.jpg``)
replaces the entry instead of adding one; on a case-sensitive one names are compared as
they are. A notice for a source root itself (a single-file source rewritten, a source
folder removed) asks for a full listing instead (``apply`` returns None). A folder
index over the table makes dropping a folder cost its own entries, not a pass over the
table. Folders reached through a symbolic link or a junction send no notices from
inside; a full listing records them and ``note()`` says they are covered by the
periodic listing only.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from specto_core.errors import SpectoError
from specto_core.fswatch import NotifyUnavailable, Unavailable, case_insensitive
from specto_core.links import ARRIVALS_CAP, FilesValue, arrival_entry

from .screener import glob_to_regex
from .sources import hidden, posix, resolve_root, walk_dir, walk_source
from .spec import IntakeSpec, SourceSpec, intake_spec

#: a writer's temporary while it writes (``.<name>.<pid>.tmp``, ``.<name>.part``): never
#: an arrival of its own, the rename into place is
_TRANSIENT = re.compile(r"^\..+\.(tmp|part)$", re.IGNORECASE)


def transient(name: str) -> bool:
    """A file name that is an atomic writer's temporary (a dotfile ending ``.tmp`` or
    ``.part``): the watcher ignores it."""
    return _TRANSIENT.match(name) is not None


def _spec_and_base(link: dict[str, Any], value: Any, root: str) -> tuple[IntakeSpec, Path]:
    spec = intake_spec(link.get("params") or {})
    base = Path(root) if root else Path.cwd()
    files = value if isinstance(value, FilesValue) else FilesValue.model_validate(value)
    if files.root:
        base = Path(files.root)
    return spec, base


def watch_table(spec: IntakeSpec, base: Path, links: list[str] | None = None) -> dict[str, str]:
    """One ``scandir`` pass over the sources: the watcher's table. ``links`` collects the
    folders reached through a symbolic link or a junction."""
    base_posix = str(base).replace("\\", "/").rstrip("/") + "/"
    table: dict[str, str] = {}
    for i, src in enumerate(spec.sources):
        try:
            for f in walk_source(
                i, src, base, expand_containers=False, links=links, skip=transient
            ):
                full = f"{f.root}/{f.rel}"
                rel_proj = (
                    full[len(base_posix) :] if full.lower().startswith(base_posix.lower()) else ""
                )
                if hidden(rel_proj):
                    continue
                table[arrival_entry(i, f.rel)] = f"{f.size}:{f.mtime_ns}"
        except SpectoError as e:  # a source that is not there (yet) is an entry too
            table[f"{i}:"] = f"missing:{e.message}"
    return table


def watch_intaker(
    link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any]
) -> dict[str, str]:
    """The intaker's watcher: what its sources hold now (``watch_table``)."""
    spec, base = _spec_and_base(link, value, root)
    return watch_table(spec, base)


class _Source:
    def __init__(self, index: int, src: SourceSpec, base: Path) -> None:
        self.index = index
        self.src = src
        self.root = resolve_root(src, base)
        self.exists = self.root.exists()
        self.is_file = self.root.is_file()
        self.root_posix = posix(self.root)
        self.dir_posix = posix(self.root.parent) if self.is_file else self.root_posix
        folder = self.root.parent if self.is_file else self.root
        #: names match regardless of case here (probed on the source's folder)
        self.ci = case_insensitive(str(folder)) if self.exists else os.name == "nt"
        fold = str.lower if self.ci else (lambda t: t)
        self._fold = fold
        self._dir_key = fold(self.dir_posix.rstrip("/")) + "/"
        self._root_key = fold(self.root_posix)
        self.pattern = (
            re.compile(glob_to_regex(src.pattern.replace("\\", "/")), re.IGNORECASE)
            if src.pattern
            else None
        )

    def rel(self, path_posix: str) -> str | None:
        """``path_posix`` relative to this source's root folder; '' for the root itself
        (or the file a single-file source names); None outside it."""
        key = self._fold(path_posix)
        if self.is_file:
            return "" if key == self._root_key else None
        if key == self._dir_key[:-1]:
            return ""
        return path_posix[len(self._dir_key) :] if key.startswith(self._dir_key) else None


class _Counts:
    """What one ``apply`` did to the table: entries added, removed and changed, and the
    added keys (at most ``ARRIVALS_CAP``)."""

    __slots__ = ("added", "arrived", "changed", "removed")

    def __init__(self) -> None:
        self.added = self.removed = self.changed = 0
        self.arrived: list[str] = []


class IntakerListing:
    """``WatchListing`` for an intaker link (see the module doc)."""

    def __init__(self, link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any]):
        self.spec, base = _spec_and_base(link, value, root)
        self.base = base
        self.base_low = posix(base).lower().rstrip("/") + "/"
        self.sources = [_Source(i, s, base) for i, s in enumerate(self.spec.sources)]
        #: "<index>:<folder rel>/" ('' rel: "<index>:") -> keys of the files right in it
        self._dirs: dict[str, set[str]] = {}
        #: folders reached through links at the last full listing
        self.link_dirs: list[str] = []
        #: one batch's folder listings (actual entry names by folded name)
        self._names: dict[str, dict[str, str]] = {}

    # -- WatchListing ------------------------------------------------------------------
    def roots(self) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        for s in self.sources:
            if not s.exists:
                raise NotifyUnavailable(
                    f"source {s.root_posix} is not there", reason=Unavailable.MISSING
                )
            out.append((str(s.root.parent) if s.is_file else str(s.root), not s.is_file))
        return out

    def full(self) -> dict[str, str]:
        links: list[str] = []
        table = watch_table(self.spec, self.base, links)
        self.link_dirs = links
        return table

    def reset(self, table: dict[str, str]) -> None:
        self._dirs = {}
        for key in table:
            self._index(key)

    def source_missing(self, table: dict[str, str]) -> bool:
        return any(v.startswith("missing:") for v in table.values())

    def note(self) -> str:
        n = len(self.link_dirs)
        if not n:
            return ""
        what = "folder" if n == 1 else "folders"
        return f"{n} {what} reached through links covered by the periodic listing only"

    def apply(
        self, table: dict[str, str], paths: list[str]
    ) -> tuple[int, int, int, list[str]] | None:
        counts = _Counts()
        self._names = {}
        for raw in paths:
            p = posix(raw)
            low = p.lower()
            if low.startswith(self.base_low) and hidden(p[len(self.base_low) :]):
                continue
            if transient(p.rsplit("/", 1)[-1]):
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
                    self._apply_named(table, s, rel, raw, counts)
        self._names = {}
        return counts.added, counts.removed, counts.changed, counts.arrived

    # -- one path ------------------------------------------------------------------------
    def _actual_name(self, s: _Source, path: str) -> str | None:
        """The name the folder holds for the last part of ``path`` (case-insensitive file
        systems: the notice may name it in an old case); None when it holds none."""
        folder, name = os.path.split(path)
        names = self._names.get(folder)
        if names is None:
            try:
                with os.scandir(folder) as it:
                    names = {e.name.lower(): e.name for e in it}
            except OSError:
                names = {}
            self._names[folder] = names
        return names.get(name.lower())

    def _apply_named(
        self, table: dict[str, str], s: _Source, rel: str, path: str, counts: _Counts
    ) -> None:
        if s.ci:
            name = os.path.basename(path)
            actual = self._actual_name(s, path)
            if actual is not None and actual != name:
                # the notice names an old case of an entry that is there under another
                # (a rename that only changed case): the entry moves to the folder's own
                # name, a change rather than an arrival and a removal
                new_rel = rel[: len(rel) - len(name)] + actual
                old_key, new_key = f"{s.index}:{rel}", f"{s.index}:{new_rel}"
                if old_key in table and new_key not in table:
                    stamp = table[old_key]
                    self._drop(table, old_key, _Counts())
                    self._set(table, new_key, stamp, _Counts())
                    counts.changed += 1
                else:
                    self._gone(table, s, rel, counts)
                rel, path = new_rel, os.path.join(os.path.dirname(path), actual)
            elif actual is None:
                self._gone(table, s, rel, counts)
                return
        self._apply_one(table, s, rel, path, counts)

    def _gone(self, table: dict[str, str], s: _Source, rel: str, counts: _Counts) -> None:
        key = f"{s.index}:{rel}"
        if key in table:
            self._drop(table, key, counts)
        else:  # not a file the table held: a folder, maybe
            self._drop_under(table, key + "/", counts)

    def _apply_one(
        self, table: dict[str, str], s: _Source, rel: str, path: str, counts: _Counts
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
        else:
            self._gone(table, s, rel, counts)

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
        self, table: dict[str, str], s: _Source, rel: str, path: str, counts: _Counts
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
        for f in walk_dir(
            s.index,
            Path(path),
            s.root_posix,
            rel + "/",
            depth,
            s.src,
            s.pattern,
            expand_containers=False,
            links=self.link_dirs,
            skip=transient,
        ):
            full = f"{f.root}/{f.rel}"
            if full.lower().startswith(self.base_low) and hidden(full[len(self.base_low) :]):
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

    def _set(self, table: dict[str, str], key: str, stamp: str, counts: _Counts) -> None:
        before = table.get(key)
        if before == stamp:
            return
        table[key] = stamp
        if before is None:
            counts.added += 1
            if len(counts.arrived) < ARRIVALS_CAP:
                counts.arrived.append(key)
            self._index(key)
        else:
            counts.changed += 1

    def _drop(self, table: dict[str, str], key: str, counts: _Counts) -> None:
        if table.pop(key, None) is None:
            return
        counts.removed += 1
        folder = self._dirs.get(self._folder_of(key))
        if folder is not None:
            folder.discard(key)
            if not folder:
                del self._dirs[self._folder_of(key)]

    def _keys_under(self, prefix: str) -> list[str]:
        # the names come from the disk, as the notices' do: compared as they are
        return [k for d, keys in self._dirs.items() if d.startswith(prefix) for k in keys]

    def _drop_under(self, table: dict[str, str], prefix: str, counts: _Counts) -> None:
        for k in self._keys_under(prefix):
            self._drop(table, k, counts)


def intaker_listing(
    link: dict[str, Any], value: Any, *, root: str, env: dict[str, Any]
) -> IntakerListing:
    return IntakerListing(link, value, root=root, env=env)


__all__ = ["IntakerListing", "intaker_listing", "transient", "watch_intaker", "watch_table"]
