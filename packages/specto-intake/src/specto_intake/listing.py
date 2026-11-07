"""The file tree's folder-at-a-time listing (#231): one level on demand, and the files
under a picked folder, over an intaker's sources and screener without a recursive scan
of the whole project.

``TreeLister`` takes the same spec an intaker takes (sources with their patterns, depth
and link policy; the screener with its aliases) and answers two questions:

- ``list_dir(path)``: the entries one level below a tree path, folders first. The root
  (``""``) lists the project folder; a source narrowed to a subfolder shows only the
  folders on the way to it; a source outside the project appears under its absolute
  path. An archive is listed as a folder whose members are virtual entries
  (``drive.zip#/front/00042.jpg``, ``specto_core.containers``), read from the archive's
  directory when it is expanded.
- ``files_under(paths)``: every file under the given folders (or the files named), as
  the recursive scan would list them, with the stat the listing saw, so a folder pick
  still means every file under it. It checks ``cancelled`` between folders.

Tree paths are POSIX, relative to the base (the project folder); the screener and a
source's pattern see the path relative to that source's root, as they do in a scan.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from specto_core.containers import is_container, join_virtual, list_members, split_virtual

from .screener import Screener, glob_to_regex
from .search import HitFilter
from .sources import resolve_root
from .spec import IntakeSpec, SourceSpec

SPECTO_DIR = ".specto"
#: (size in bytes, mtime in ns)
Stat = tuple[int, int]


@dataclass(frozen=True)
class Entry:
    """One row of a listing: a file, a folder, or an archive shown as a folder. A folder
    carries its own modified time (#38), from one stat of the folder itself (never of
    what is under it; SPEC-40: not the parent's directory entry); an archive its file's
    size and time; a folder on the way to a source, or inside an archive, carries none."""

    path: str  # the tree path (POSIX, relative to the base; virtual inside an archive)
    name: str
    is_dir: bool
    size: int = 0
    mtime_ns: int = 0
    archive: bool = False


def _posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def _hidden(tree_path: str) -> bool:
    return tree_path == SPECTO_DIR or tree_path.startswith(SPECTO_DIR + "/")


_DRIVE = re.compile(r"^[A-Za-z]:(/|$)")


def _absolute(tree_path: str) -> bool:
    """A tree path that is absolute (a source outside the project) or steps out of its
    folder: never joined under the project root."""
    return (
        tree_path.startswith("/")
        or _DRIVE.match(tree_path) is not None
        or ".." in tree_path.split("/")
    )


class _Root:
    """One source, placed in the tree: its prefix (the tree path of its root folder,
    ``""`` for the project folder itself, absolute when it lies outside) and the rules
    that apply below it."""

    def __init__(self, index: int, src: SourceSpec, base: Path) -> None:
        self.index = index
        self.src = src
        root = resolve_root(src, base)
        self.is_file = root.is_file()
        self.dir = root.parent if self.is_file else root
        self.file_name = root.name if self.is_file else ""
        base_posix = _posix(base.resolve()).rstrip("/")
        dir_posix = _posix(self.dir)
        if dir_posix.lower() == base_posix.lower():
            self.prefix = ""
        elif dir_posix.lower().startswith(base_posix.lower() + "/"):
            self.prefix = dir_posix[len(base_posix) + 1 :]
        else:
            self.prefix = dir_posix
        self.inside = self.prefix == "" or not os.path.isabs(self.prefix)
        self.pattern = (
            re.compile(glob_to_regex(src.pattern.replace("\\", "/")), re.IGNORECASE)
            if src.pattern
            else None
        )

    def holds(self, path: str) -> bool:
        """Is ``path`` this source's root folder or below it? The project folder holds
        every relative path; an absolute one (a source outside the project) only its own
        source's."""
        if self.prefix == "":
            return not _absolute(path)
        return path == self.prefix or path.startswith(self.prefix + "/")

    def rel(self, path: str) -> str:
        """``path`` relative to this source's root folder (what the pattern and the
        screener see)."""
        return path if self.prefix == "" else path[len(self.prefix) :].lstrip("/")

    def abs(self, path: str) -> Path:
        rel = self.rel(path)
        return self.dir / rel if rel else self.dir

    def depth_ok(self, folder_rel: str) -> bool:
        """May the folder at ``folder_rel`` (relative to the root, "" the root) list
        its subfolders? ``depth`` counts folders below the root: 0 lists the root only."""
        if self.src.depth is None:
            return True
        depth = folder_rel.count("/") + 1 if folder_rel else 0
        return depth < self.src.depth

    def accepts(self, path: str, screener: Screener) -> bool:
        """A file at tree path ``path`` passes this source's pattern, the screener, and
        is not the project's own ``.specto/``."""
        if self.inside and _hidden(path):
            return False
        rel = self.rel(path)
        if self.is_file and rel != self.file_name:
            return False
        if self.pattern is not None and self.pattern.fullmatch(rel) is None:
            return False
        return screener.accepts(rel)


class TreeLister:
    """Folder-at-a-time listing over an intaker's sources and screener (module doc)."""

    def __init__(
        self,
        base: str | Path,
        spec: IntakeSpec | dict[str, Any] | None = None,
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.base = Path(base)
        self.spec = IntakeSpec.parse(spec) if spec is not None else IntakeSpec(sources=["."])
        self.screener = Screener(self.spec.screener, aliases or {})
        self.roots = [_Root(i, s, self.base) for i, s in enumerate(self.spec.sources)]

    # -- placing a tree path -----------------------------------------------------------------
    def _root_of(self, path: str) -> _Root | None:
        """The source whose root folder holds ``path`` (the deepest one), or None when
        ``path`` lies above every source (a folder on the way to one)."""
        best: _Root | None = None
        for r in self.roots:
            if r.holds(path) and (best is None or len(r.prefix) > len(best.prefix)):
                best = r
        return best

    def _way_down(self, path: str) -> list[Entry]:
        """``path`` is above the sources: the next folder on the way to each of them."""
        out: dict[str, Entry] = {}
        for r in self.roots:
            if not r.prefix or r.prefix == path:
                continue
            if path and not r.prefix.startswith(path + "/"):
                continue
            rest = r.prefix[len(path) + 1 :] if path else r.prefix
            name = rest.split("/", 1)[0]
            child = f"{path}/{name}" if path else name
            out.setdefault(child, Entry(child, name, True))
        return sorted(out.values(), key=lambda e: e.name.lower())

    def accepts(self, path: str) -> bool:
        """Would a file at tree path ``path`` be listed: under a source, passing its
        pattern and the screener (what a search hit must pass, #232)."""
        r = self._root_of(path)
        return r is not None and not r.is_file and r.accepts(path, self.screener)

    def accepts_dir(self, path: str) -> bool:
        """Would a folder at tree path ``path`` be listed: under a source that lists
        folders that deep, not the project's own ``.specto/`` (what a folder hit must
        pass, SPEC-38). The screener and a source's pattern are for files: a folder
        passes them as the listing's folders do."""
        r = self._root_of(path)
        if r is None or r.is_file or not path or (r.inside and _hidden(path)):
            return False
        if path == r.prefix:
            return True  # the source's own root folder
        return r.depth_ok(r.rel(path.rpartition("/")[0]))

    def hit_filter(self) -> HitFilter:
        """What a search hit must pass to be a row of this tree: files ``accepts``,
        folders ``accepts_dir`` (SPEC-38)."""
        return HitFilter(file=self.accepts, folder=self.accepts_dir)

    # -- one level ---------------------------------------------------------------------------
    def list_dir(self, path: str = "") -> list[Entry]:
        """The entries one level below tree path ``path`` (``""``: the root), folders
        first, each group sorted by name case-insensitively. A folder that is not
        there, or cannot be read, lists nothing."""
        path = path.strip("/")
        virtual = split_virtual(path)
        if virtual is not None:
            archive_path, inner = virtual
            r = self._root_of(archive_path)
            if r is None:
                return []
            return _sorted(self._list_archive(r, archive_path, inner))
        r = self._root_of(path)
        if r is None:
            return self._way_down(path)
        entries = self._list_folder(r, path)
        # a source nested deeper than this one and not on disk under it (another
        # absolute root): also on the way down
        seen = {e.path for e in entries}
        entries.extend(e for e in self._way_down(path) if e.path not in seen)
        return _sorted(entries)

    def _list_folder(self, r: _Root, path: str) -> list[Entry]:
        folder = r.abs(path)
        if r.is_file:
            # a single-file source: its folder shows that file alone
            f = r.dir / r.file_name
            if path != r.prefix or not f.is_file():
                return []
            st = f.stat()
            return self._file_entries(r, r.prefix, [(r.file_name, st.st_size, st.st_mtime_ns)])
        if folder.is_file():
            # the tree path names an archive: its top level
            return self._list_archive(r, path, "") if is_container(folder.name) else []
        try:
            with os.scandir(folder) as it:
                found = list(it)
        except OSError:
            return []
        rel_folder = r.rel(path)
        dirs_ok = r.depth_ok(rel_folder)
        out: list[Entry] = []
        files: list[tuple[str, int, int]] = []
        for e in found:
            child = f"{path}/{e.name}" if path else e.name
            try:
                if e.is_dir(follow_symlinks=r.src.follow_links):
                    if dirs_ok and not (r.inside and _hidden(child)):
                        mtime_ns = _dir_mtime_ns(e, r.src.follow_links)
                        out.append(Entry(child, e.name, True, mtime_ns=mtime_ns))
                    continue
                if not e.is_file(follow_symlinks=True):
                    continue
                st = e.stat(follow_symlinks=True)
            except OSError:
                continue
            files.append((e.name, st.st_size, st.st_mtime_ns))
        out.extend(self._file_entries(r, path, files))
        return out

    def _file_entries(
        self, r: _Root, folder: str, files: list[tuple[str, int, int]]
    ) -> list[Entry]:
        out: list[Entry] = []
        for name, size, mtime_ns in files:
            child = f"{folder}/{name}" if folder else name
            if is_container(name):
                if not (r.inside and _hidden(child)):
                    # a member may pass the pattern the archive itself does not
                    out.append(Entry(child, name, True, size, mtime_ns, archive=True))
            elif r.accepts(child, self.screener):
                out.append(Entry(child, name, False, size, mtime_ns))
        return out

    def _list_archive(self, r: _Root, archive_path: str, inner: str) -> list[Entry]:
        """One level inside an archive: the members directly under ``inner`` (``""``:
        the top), the folders between as virtual folders."""
        try:
            members = list_members(str(r.abs(archive_path)))
        except Exception:
            return []
        prefix = inner.strip("/")
        prefix = prefix + "/" if prefix else ""
        dirs: dict[str, Entry] = {}
        out: list[Entry] = []
        for m in members:
            if not m.path.startswith(prefix):
                continue
            rest = m.path[len(prefix) :]
            if not rest:
                continue
            head, sep, _tail = rest.partition("/")
            child = join_virtual(archive_path, prefix + head)
            if sep:
                dirs.setdefault(child, Entry(child, head, True))
            elif r.accepts(child, self.screener):
                out.append(Entry(child, head, False, m.size, m.mtime_ns))
        return list(dirs.values()) + out

    # -- the files under picked rows -------------------------------------------------------
    def files_under(
        self, paths: list[str], cancelled: Callable[[], bool] | None = None
    ) -> tuple[list[str], dict[str, Stat]]:
        """Every file under the folders named (a file named is itself), sorted and
        unique, with the stat seen. Stops early, with what it has, once ``cancelled()``
        is true."""
        stats: dict[str, Stat] = {}
        stop = cancelled or (lambda: False)
        for p in paths:
            if stop():
                break
            self._collect(p.strip("/"), stats, stop)
        return sorted(stats), stats

    def _collect(self, path: str, stats: dict[str, Stat], stop: Callable[[], bool]) -> None:
        virtual = split_virtual(path)
        if virtual is not None:
            archive_path, inner = virtual
            r = self._root_of(archive_path)
            if r is not None:
                self._members_under(r, archive_path, inner, stats)
            return
        r = self._root_of(path)
        if r is None:
            # above the sources: every source on the way down from here
            for root in self.roots:
                if root.prefix and (not path or root.prefix.startswith(path + "/")):
                    self._collect(root.prefix, stats, stop)
            return
        if path == "":
            # the whole tree: the sources outside the project too
            for root in self.roots:
                if _absolute(root.prefix):
                    self._collect(root.prefix, stats, stop)
        if r.is_file:
            f = r.dir / r.file_name
            if path in (r.prefix, join_path(r.prefix, r.file_name)) and f.is_file():
                st = f.stat()
                self._file(r, join_path(r.prefix, r.file_name), st.st_size, st.st_mtime_ns, stats)
            return
        target = r.abs(path)
        if target.is_dir():
            self._walk(r, path, target, stats, stop)
            return
        try:
            st = target.stat()
        except OSError:
            return
        self._file(r, path, st.st_size, st.st_mtime_ns, stats)

    def _walk(self, r: _Root, path: str, folder: Path, stats: dict[str, Stat], stop: Any) -> None:
        if stop():
            return
        try:
            with os.scandir(folder) as it:
                found = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        dirs_ok = r.depth_ok(r.rel(path))
        subdirs: list[tuple[str, Path]] = []
        for e in found:
            child = f"{path}/{e.name}" if path else e.name
            try:
                if e.is_dir(follow_symlinks=r.src.follow_links):
                    if dirs_ok and not (r.inside and _hidden(child)):
                        subdirs.append((child, Path(e.path)))
                    continue
                if not e.is_file(follow_symlinks=True):
                    continue
                st = e.stat(follow_symlinks=True)
            except OSError:
                continue
            self._file(r, child, st.st_size, st.st_mtime_ns, stats)
        for child, sub in subdirs:
            if stop():
                return
            self._walk(r, child, sub, stats, stop)

    def _file(self, r: _Root, path: str, size: int, mtime_ns: int, stats: dict[str, Stat]) -> None:
        name = path.rpartition("/")[2]
        if is_container(name):
            if not (r.inside and _hidden(path)):
                self._members_under(r, path, "", stats, size, mtime_ns)
        elif r.accepts(path, self.screener):
            stats[path] = (size, mtime_ns)

    def _members_under(
        self,
        r: _Root,
        archive_path: str,
        inner: str,
        stats: dict[str, Stat],
        size: int = 0,
        mtime_ns: int = 0,
    ) -> None:
        try:
            members = list_members(str(r.abs(archive_path)))
        except Exception:
            # an unreadable archive stays one plain file, as in a scan
            if not inner and r.accepts(archive_path, self.screener):
                stats[archive_path] = (size, mtime_ns)
            return
        prefix = inner.strip("/")
        prefix = prefix + "/" if prefix else ""
        for m in members:
            if prefix and not m.path.startswith(prefix):
                continue
            child = join_virtual(archive_path, m.path)
            if r.accepts(child, self.screener):
                stats[child] = (m.size, m.mtime_ns or mtime_ns)


def _dir_mtime_ns(e: os.DirEntry, follow_links: bool) -> int:
    """A folder's own modified time (#38): one stat of the folder itself, never of what
    is under it. Not the directory entry's copy (SPEC-40): on Windows the parent's entry
    keeps the time from when the folder was last opened, so a change since leaves it
    behind, for as long as nothing opens the folder (about 20 us a folder). 0 when it
    cannot be read (the folder still lists)."""
    try:
        return os.stat(e.path, follow_symlinks=follow_links).st_mtime_ns
    except OSError:
        return 0


def join_path(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _sorted(entries: list[Entry]) -> list[Entry]:
    """Folders first, then files, each by name case-insensitively; duplicates (two
    sources listing one entry) collapse."""
    seen: set[str] = set()
    out: list[Entry] = []
    for e in sorted(entries, key=lambda e: (not e.is_dir, e.name.lower())):
        if e.path not in seen:
            seen.add(e.path)
            out.append(e)
    return out


__all__ = ["Entry", "Stat", "TreeLister", "join_path"]
