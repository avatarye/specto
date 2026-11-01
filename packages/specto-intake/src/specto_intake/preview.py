"""Read-only previews of an intake spec (HLD 3.6, principle 5): what a spec *would*
produce, without creating a collection or touching the document.

``preview`` walks the sources with an early stop (``max_files``), screens every file,
keeps the first ``limit`` matched and excluded paths, groups the kept files into items
and reports counts, the kinds histogram and a few example items. ``keys_preview`` runs a
keys regex over sample names and tabulates the named-group captures. Both are pure data
in, pure data out, so they run inline for small specs and as pool jobs for big ones
(``preview_job``). Spec errors (a bad alias, a regex without named groups) come back in
``errors`` instead of raising, so a live editor can show them while the user types.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from specto_core.errors import SpectoError

from .keys import Skipped, ext_of, group_items, strip_ext
from .screener import Screener
from .sources import FoundFile, resolve_root, walk_source
from .spec import IntakeSpec

ProgressFn = Callable[[float, str], None]

DEFAULT_LIMIT = 50
DEFAULT_MAX_FILES = 20_000

#: Key regex presets the intaker offers (label, regex). Agents get them via
#: ``intake.keys_preview`` with no regex.
#: presets anchor at a path segment start, so files one folder down (``front/00042_``)
#: and members of an archive (``drive.zip#/front/00042_``) key the same as flat ones
KEY_PRESETS: list[dict[str, str]] = [
    {"label": "leading number", "regex": r"(?:^|/)(?P<frame>\d+)"},
    {"label": "frame NNNNN_", "regex": r"(?:^|/)(?P<frame>\d{5})_"},
    {"label": "frame_stamp", "regex": r"(?:^|/)(?P<frame>\d+)_(?P<stamp>\d+)"},
    {"label": "camera/frame", "regex": r"^(?P<camera>[^/]+)/(?P<frame>\d+)"},
    {"label": "stem", "regex": r"^(?P<stem>[^/]+?)(?:\.[^./]+)?$"},
]


def _spec_errors(raw: Any, aliases: dict[str, list[str]]) -> tuple[IntakeSpec | None, list[str]]:
    try:
        spec = IntakeSpec.parse(raw)
    except SpectoError as e:
        return None, [e.message + (f" ({e.fix})" if e.fix else "")]
    errors: list[str] = []
    try:
        Screener(spec.screener, aliases)
    except SpectoError as e:
        errors.append(e.message + (f" ({e.fix})" if e.fix else ""))
    return spec, errors


def preview(
    spec: IntakeSpec | dict[str, Any],
    *,
    base: Path | str,
    aliases: dict[str, list[str]],
    ext_kinds: dict[str, list[str]],
    limit: int = DEFAULT_LIMIT,
    max_files: int = DEFAULT_MAX_FILES,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """What the spec would intake. Never raises for a bad spec: see ``errors``."""
    t0 = time.perf_counter()
    base = Path(base)
    out: dict[str, Any] = {
        "ok": False,
        "errors": [],
        "files": 0,
        "truncated": False,
        "matched_count": 0,
        "excluded_count": 0,
        "matched": [],
        "excluded": [],
        "skipped_count": 0,
        "skipped": [],
        "item_count": 0,
        "items": [],
        "key_fields": [],
        "kinds": {},
        "parts": {},
        "sources": [],
        "elapsed_s": 0.0,
    }
    parsed, errors = _spec_errors(spec, aliases)
    if parsed is None or errors:
        out["errors"] = errors
        return out
    spec = parsed
    out["key_fields"] = spec.key_fields()
    for i, src in enumerate(spec.sources):
        root = resolve_root(src, base)
        if not root.exists():
            out["errors"].append(f"source {i} root {src.root!r} does not exist (under {base})")
    if out["errors"]:
        return out

    screener = Screener(spec.screener, aliases)
    kept: list[FoundFile] = []
    files = 0
    matched: list[str] = []
    excluded: list[dict[str, str]] = []
    n_matched = n_excluded = 0
    per_source: list[dict[str, Any]] = []
    stop = False
    for i, src in enumerate(spec.sources):
        if progress:
            progress(i / len(spec.sources), f"scanning {src.root}")
        found = 0
        for f in walk_source(i, src, base):
            if files >= max_files:
                stop = True
                break
            found += 1
            files += 1
            if screener.accepts(f.rel):
                n_matched += 1
                kept.append(f)
                if len(matched) < limit:
                    matched.append(f.rel)
            else:
                n_excluded += 1
                if len(excluded) < limit:
                    ex = screener.explain(f.rel)
                    excluded.append(
                        {
                            "path": f.rel,
                            "rule": ex.deciding.rule.text() if ex.deciding else "",
                            "reason": ex.reason,
                        }
                    )
            if progress and files % 2000 == 0:
                progress(i / len(spec.sources), f"{files} files seen")
        per_source.append({"index": i, "root": src.root, "files": found})
        if stop:
            break
    if progress:
        progress(0.9, "grouping")
    skipped: list[Skipped] = []
    items = group_items(spec, kept, skipped)
    parts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for it in items:
        for role, path in it.parts.items():
            parts[role] = parts.get(role, 0) + 1
            ks = ext_kinds.get(ext_of(path.replace("\\", "/")))
            if ks:
                kinds[ks[0]] = kinds.get(ks[0], 0) + 1
    out.update(
        ok=True,
        files=files,
        truncated=stop,
        matched_count=n_matched,
        excluded_count=n_excluded,
        matched=matched,
        excluded=excluded,
        skipped_count=len(skipped),
        skipped=[s.to_dict() for s in skipped[:limit]],
        item_count=len(items),
        items=[
            {"key": it.key, "keys": it.meta.get("keys", {}), "parts": dict(it.meta.get("rel", {}))}
            for it in items[:limit]
        ],
        kinds=kinds,
        parts=parts,
        sources=per_source,
        elapsed_s=round(time.perf_counter() - t0, 4),
    )
    return out


def preview_job(
    spec: dict[str, Any],
    base: str,
    aliases: dict[str, list[str]],
    ext_kinds: dict[str, list[str]],
    limit: int,
    max_files: int,
    progress: ProgressFn,
) -> dict[str, Any]:
    """Picklable entry point for the pool (same signature family as ``scan_job``)."""
    return preview(
        spec,
        base=Path(base),
        aliases=aliases,
        ext_kinds=ext_kinds,
        limit=limit,
        max_files=max_files,
        progress=progress,
    )


def keys_preview(regex: str | None, names: list[str]) -> dict[str, Any]:
    """Captures of a keys regex over sample names: ``fields`` (named groups, in order),
    ``rows`` (one per name: ``{name, matched, captures}``) and ``error`` when the regex
    does not compile or has no named group. ``regex`` None or empty means the default
    key (path without extension)."""
    out: dict[str, Any] = {
        "regex": regex or "",
        "fields": [],
        "rows": [],
        "matched": 0,
        "unmatched": 0,
        "error": None,
        "presets": list(KEY_PRESETS),
    }
    names = [str(n).replace("\\", "/") for n in names]
    if not regex:
        out["fields"] = ["stem"]
        out["rows"] = [
            {"name": n, "matched": True, "captures": {"stem": strip_ext(n)}} for n in names
        ]
        out["matched"] = len(names)
        return out
    try:
        pat = re.compile(regex)
    except re.error as e:
        out["error"] = f"regex does not compile: {e}"
        return out
    fields = list(pat.groupindex)
    out["fields"] = fields
    if not fields:
        out["error"] = "regex has no named group; name one: (?P<frame>\\d+)"
        return out
    for n in names:
        m = pat.search(n)
        if m is None:
            out["rows"].append({"name": n, "matched": False, "captures": {}})
            out["unmatched"] += 1
        else:
            caps = {k: v for k, v in m.groupdict().items() if v is not None}
            out["rows"].append({"name": n, "matched": True, "captures": caps})
            out["matched"] += 1
    return out


def guess_keys(names: list[str], *, min_share: float = 0.8) -> str | None:
    """A keys regex when the names share a numeric prefix like ``NNNNN_<stamp>``: at
    least ``min_share`` of them (and two or more) start with the same number of digits
    followed by ``_``, ``-`` or ``.``; a stray readme does not spoil it. None when no
    such prefix is shared (the default stem key then applies)."""
    names = [n.rsplit("/", 1)[-1] for n in names if n]
    if len(names) < 2:
        return None
    widths: dict[int, int] = {}
    underscored = 0
    for n in names:
        m = re.match(r"^(\d+)([_\-.])", n)
        if m is None:
            continue
        widths[len(m.group(1))] = widths.get(len(m.group(1)), 0) + 1
        underscored += m.group(2) == "_"
    if not widths:
        return None
    width, count = max(widths.items(), key=lambda kv: kv[1])
    if count < 2 or count < min_share * len(names):
        return None
    if underscored == count:
        return rf"(?:^|/)(?P<frame>\d{{{width}}})_"
    return rf"(?:^|/)(?P<frame>\d{{{width}}})"


def sample_names(root: Path | str, *, limit: int = 200) -> list[str]:
    """Up to ``limit`` file names directly in ``root`` (sorted), for ``guess_keys``."""
    root = Path(root)
    if root.is_file():
        return [root.name]
    names: list[str] = []
    try:
        with os.scandir(root) as it:
            for e in sorted(it, key=lambda e: e.name):
                if e.is_file(follow_symlinks=False):
                    names.append(e.name)
                    if len(names) >= limit:
                        break
    except OSError:
        return []
    return names


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_FILES",
    "KEY_PRESETS",
    "guess_keys",
    "keys_preview",
    "preview",
    "preview_job",
    "sample_names",
]
