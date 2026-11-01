"""The project folder as the agent sees it (#169): every file under the project root
(``project.all_files``, the whole folder; the project's own ``.specto/`` never in it),
folded to a folder summary for the prompt context, and listed folder by folder or by
glob for the ``list_files`` tool.

The listing always comes from the project root, never from what a view's intaker
found: a file tree narrowed to some sources or screened to some kinds lists a part of
the folder, and summarising from whichever tree resolved last made the count jump from
turn to turn. One ``scandir`` pass (about 0.1 s for 12000 files), cached until its
owner forgets it (``ProjectListing.forget``: the project was rescanned).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .errors import InvalidArgs, NotFound

SPECTO_DIR = ".specto"
#: folders in the prompt context's summary before deeper levels fold into their parent
MAX_FOLDERS = 60
#: what ``list_files`` returns by default and at most
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


def walk_project(root: str | Path) -> list[str]:
    """Every file under ``root``, POSIX paths relative to it, sorted; the top-level
    ``.specto/`` is left out (as an intaker leaves it out). Unreadable folders are
    skipped."""
    base = Path(root)
    out: list[str] = []
    stack: list[tuple[str, str]] = [(str(base), "")]
    while stack:
        path, rel = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            child = f"{rel}{e.name}"
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if child != SPECTO_DIR:
                    stack.append((e.path, child + "/"))
            else:
                out.append(child)
    return sorted(out)


class ProjectListing:
    """``walk_project`` of one root, cached until ``forget`` (or for ``ttl_s`` seconds
    when given), so a large folder is not walked again for every read."""

    def __init__(
        self,
        root: str | Path,
        *,
        ttl_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root)
        self.ttl_s = ttl_s
        self.clock = clock
        self._paths: list[str] | None = None
        self._at = 0.0

    def paths(self) -> list[str]:
        now = self.clock()
        expired = self.ttl_s is not None and now - self._at > self.ttl_s
        if self._paths is None or expired:
            self._paths = walk_project(self.root)
            self._at = now
        return self._paths

    def forget(self) -> None:
        self._paths = None


# -- the prompt context's summary ----------------------------------------------------------
def _folder(path: str) -> str:
    return path.rpartition("/")[0] or "."


def _ext(path: str) -> str:
    name = path.rpartition("/")[2]
    return os.path.splitext(name)[1].lower() or name


def _cut(folder: str, depth: int) -> str:
    """``folder`` cut to its first ``depth`` levels (the root stays the root)."""
    return folder if folder == "." else "/".join(folder.split("/")[:depth])


def folder_summary(paths: Iterable[str], *, max_folders: int = MAX_FOLDERS) -> dict[str, Any]:
    """``{count, folders: {folder: {ext: n}}}``: every folder with files in it and the
    extensions it holds. When there are more than ``max_folders``, the deepest level
    that fits is kept and everything below it is folded into its folder there:
    ``depth`` says that level, a folded folder's counts cover its whole subtree and
    ``deeper`` names how many subfolders each hides (``list_files`` shows them)."""
    paths = list(paths)
    direct: dict[str, dict[str, int]] = {}
    for p in paths:
        counts = direct.setdefault(_folder(p), {})
        counts[_ext(p)] = counts.get(_ext(p), 0) + 1
    out: dict[str, Any] = {"count": len(paths)}
    if len(direct) <= max_folders:
        out["folders"] = dict(sorted(direct.items()))
        return out
    deepest = max(f.count("/") + 1 for f in direct if f != ".")
    depth = 1
    for d in range(deepest, 0, -1):
        if len({_cut(f, d) for f in direct}) <= max_folders:
            depth = d
            break
    folded: dict[str, dict[str, int]] = {}
    hidden: dict[str, set[str]] = {}
    for folder, counts in direct.items():
        top = _cut(folder, depth)
        into = folded.setdefault(top, {})
        for ext, n in counts.items():
            into[ext] = into.get(ext, 0) + n
        if top != folder:
            # every folder level between the cut and this one is hidden below ``top``
            parts = folder.split("/")
            for k in range(depth + 1, len(parts) + 1):
                hidden.setdefault(top, set()).add("/".join(parts[:k]))
    names = sorted(folded)
    out["depth"] = depth
    out["folders"] = {f: folded[f] for f in names[:max_folders]}
    out["deeper"] = {f: len(hidden[f]) for f in names[:max_folders] if f in hidden}
    if len(names) > max_folders:
        out["more_folders"] = len(names) - max_folders
    out["note"] = (
        f"{len(direct)} folders: shown to depth {depth}, deeper folders counted into their "
        "parent (deeper: how many subfolders each hides); call list_files with a path to "
        "see inside one, or a glob to find files by name"
    )
    return out


# -- list_files ------------------------------------------------------------------------
def glob_regex(pattern: str) -> re.Pattern[str]:
    """A path glob: ``*`` and ``?`` within one folder level, ``**`` across levels
    (``**/`` also matches no folder at all), case-insensitive."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out, re.IGNORECASE)


def _norm(path: str | None) -> str:
    p = str(path or ".").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    p = p.strip("/")
    return p or "."


def _folders_of(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        parts = p.split("/")[:-1]
        for k in range(1, len(parts) + 1):
            out.add("/".join(parts[:k]))
    return out


def _closest(path: str, folders: set[str]) -> tuple[str, list[str]]:
    """The deepest existing ancestor of ``path`` and its subfolders' names."""
    parts = path.split("/")
    for k in range(len(parts) - 1, -1, -1):
        parent = "/".join(parts[:k]) or "."
        if parent == "." or parent in folders:
            prefix = "" if parent == "." else parent + "/"
            kids = sorted(
                {
                    f[len(prefix) :]
                    for f in folders
                    if f.startswith(prefix) and "/" not in f[len(prefix) :]
                }
            )
            return parent, kids
    return ".", []


def list_files(
    paths: list[str],
    path: str | None = None,
    glob: str | None = None,
    depth: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """One folder of the project (``path``, default the root): its subfolders down to
    ``depth`` levels (default 1) with the files each holds (their whole subtree), and
    its files at those levels. With ``glob``, the files below ``path`` whose path
    (relative to ``path``) matches, at any depth unless ``depth`` is given; a pattern
    without a slash matches the file name. At most ``limit`` entries come back
    (``truncated`` and the totals say what was left out)."""
    where = _norm(path)
    n = DEFAULT_LIMIT if limit is None else int(limit)
    if n < 1:
        raise InvalidArgs(
            "limit must be at least 1", fix=f"1..{MAX_LIMIT}; default {DEFAULT_LIMIT}"
        )
    n = min(n, MAX_LIMIT)
    if depth is not None and int(depth) < 1:
        raise InvalidArgs("depth must be at least 1", fix="1 lists the folder's own entries")
    folders = _folders_of(paths)
    if where != "." and where not in folders:
        lower = {f.lower(): f for f in folders}
        if where.lower() in lower:
            where = lower[where.lower()]
        elif where in paths:
            raise InvalidArgs(f"{where} is a file, not a folder", fix="pass its folder as path")
        else:
            parent, kids = _closest(where, folders)
            shown = ", ".join(kids[:40]) + (f" (+{len(kids) - 40})" if len(kids) > 40 else "")
            raise NotFound(
                f"no folder {where!r} in the project",
                fix=f"{'the project root' if parent == '.' else parent} has: "
                f"{shown or 'no folders'}"
                "; list_files with that path, or a glob, finds the right one",
            )
    prefix = "" if where == "." else where + "/"
    below = [p[len(prefix) :] for p in paths if p.startswith(prefix)]
    out: dict[str, Any] = {"path": where, "total_files": len(below)}
    if glob:
        pat = glob_regex(glob.replace("\\", "/").strip("/"))
        by_name = "/" not in glob.strip("/")
        hits = [
            r
            for r in below
            if (depth is None or r.count("/") < int(depth))
            and pat.fullmatch(r.rpartition("/")[2] if by_name else r)
        ]
        out["glob"] = glob
        out["matches"] = len(hits)
        out["files"] = [prefix + r for r in hits[:n]]
        out["truncated"] = len(hits) > n
        return out
    levels = 1 if depth is None else int(depth)
    sub: dict[str, int] = {}
    own: list[str] = []
    for r in below:
        parts = r.split("/")
        if len(parts) <= levels:
            own.append(prefix + r)
        for k in range(1, min(len(parts) - 1, levels) + 1):
            key = prefix + "/".join(parts[:k])
            sub[key] = sub.get(key, 0) + 1
    kids = {f: 0 for f in sub}
    for f in folders:
        if f.startswith(prefix):
            parent = f.rpartition("/")[0]
            if parent in kids:
                kids[parent] += 1
    rows = [{"path": f, "files": sub[f], "folders": kids[f]} for f in sorted(sub)]
    room = max(0, n - len(rows[:n]))
    out["folders"] = rows[:n]
    out["files"] = own[:room]
    out["truncated"] = len(rows) > n or len(own) > room
    if out["truncated"]:
        out["note"] = "more entries than limit: narrow path, lower depth or raise limit"
    return out


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_FOLDERS",
    "MAX_LIMIT",
    "ProjectListing",
    "folder_summary",
    "glob_regex",
    "list_files",
    "walk_project",
]
