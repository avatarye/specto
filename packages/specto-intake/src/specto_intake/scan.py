"""Scan: sources -> screener -> keys/grouping -> sorted items, plus the collection
facts the document keeps (item count, content hash, kinds histogram).

Pure data in, pure data out: ``scan`` takes the alias table and the extension->kinds map
instead of a ``PluginRegistry`` so it can run in a worker process. ``scan_env`` builds
both from a registry on the GUI side.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.kinds import DATA_KINDS, Item, PluginRegistry

from .keys import KeyExtractor, Skipped, ext_of, group_items
from .screener import Screener, glob_to_regex
from .sources import FoundFile, relative_to_source, walk_source
from .spec import IntakeSpec

ProgressFn = Callable[[float, str], None]


def scan_env(plugins: PluginRegistry) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(aliases, ext_kinds)`` from the locator manifests."""
    ext_kinds: dict[str, list[str]] = {}
    for lo in sorted(plugins.locators(), key=lambda m: -m.priority):
        for ext in lo.extensions:
            lst = ext_kinds.setdefault(ext.lower(), [])
            lst.extend(k for k in lo.kinds if k not in lst)
    return plugins.aliases(), ext_kinds


@dataclass
class ScanResult:
    items: list[Item]
    content_hash: str
    kinds: dict[str, int]
    files: int = 0
    skipped: list[Skipped] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def item_count(self) -> int:
        return len(self.items)

    def summary(self, *, max_skipped: int = 50) -> dict[str, Any]:
        """Picklable, document-sized facts (no item list)."""
        return {
            "item_count": self.item_count,
            "content_hash": self.content_hash,
            "kinds": dict(self.kinds),
            "files": self.files,
            "skipped": [s.to_dict() for s in self.skipped[:max_skipped]],
            "skipped_count": len(self.skipped),
            "sources": list(self.sources),
            "elapsed_s": round(self.elapsed_s, 4),
        }


def content_hash(items: list[Item]) -> str:
    """Stable over key, relative part paths, sizes and mtimes; order-sensitive."""
    h = hashlib.sha256()
    for it in items:
        rel = it.meta.get("rel", {})
        h.update(it.key.encode("utf-8"))
        h.update(b"\0")
        for role in sorted(it.parts):
            h.update(f"{role}={rel.get(role, it.parts[role])}".encode())
            h.update(b"\0")
        h.update(f"{it.meta.get('size', 0)}:{it.meta.get('mtime_ns', 0)}".encode())
        h.update(b"\n")
    return h.hexdigest()[:32]


def kinds_histogram(items: list[Item], ext_kinds: dict[str, list[str]]) -> dict[str, int]:
    """Count each part under its primary data kind (the binding check reads this)."""
    out: dict[str, int] = {}
    for it in items:
        for path in it.parts.values():
            kinds = ext_kinds.get(ext_of(path.replace("\\", "/")))
            if kinds:
                out[kinds[0]] = out.get(kinds[0], 0) + 1
    return {k: v for k, v in out.items() if k in DATA_KINDS}


def scan(
    spec: IntakeSpec | dict[str, Any],
    *,
    base: Path | str,
    aliases: dict[str, list[str]],
    ext_kinds: dict[str, list[str]],
    progress: ProgressFn | None = None,
) -> ScanResult:
    t0 = time.perf_counter()
    spec = IntakeSpec.parse(spec)
    base = Path(base)
    screener = Screener(spec.screener, aliases)
    skipped: list[Skipped] = []
    kept: list[FoundFile] = []
    files = 0
    per_source: list[dict[str, Any]] = []
    n_sources = len(spec.sources)
    for i, src in enumerate(spec.sources):
        if progress:
            progress(i / n_sources, f"scanning {src.root}")
        found = 0
        for f in walk_source(i, src, base):
            found += 1
            files += 1
            if screener.accepts(f.rel):
                kept.append(f)
            if progress and files % 2000 == 0:
                progress(i / n_sources, f"{files} files seen")
        per_source.append({"index": i, "root": src.root, "files": found})
    if progress:
        progress(0.9, "grouping")
    items = group_items(spec, kept, skipped)
    result = ScanResult(
        items=items,
        content_hash=content_hash(items),
        kinds=kinds_histogram(items, ext_kinds),
        files=files,
        skipped=skipped,
        sources=per_source,
        elapsed_s=time.perf_counter() - t0,
    )
    if progress:
        progress(1.0, f"{len(items)} items")
    return result


def explain(
    spec: IntakeSpec | dict[str, Any],
    path: str | Path,
    *,
    base: Path | str,
    aliases: dict[str, list[str]],
) -> dict[str, Any]:
    """Which source, rule and key a path gets: pure, no document (HLD 3.6)."""
    spec = IntakeSpec.parse(spec)
    base = Path(base)
    hit = relative_to_source(Path(path), spec.sources, base)
    out: dict[str, Any] = {"path": str(path), "source": None, "rel": None}
    if hit is None:
        out.update(
            included=False,
            reason="not under any source root",
            screener=None,
            keys=None,
            item_key=None,
        )
        return out
    index, rel = hit
    src = spec.sources[index]
    out.update(source=index, root=src.root, rel=rel)
    if src.pattern and re.fullmatch(glob_to_regex(src.pattern), rel, re.IGNORECASE) is None:
        out.update(
            included=False,
            reason=f"does not match source pattern {src.pattern!r}",
            screener=None,
            keys=None,
            item_key=None,
        )
        return out
    screened = Screener(spec.screener, aliases).explain(rel)
    out["screener"] = screened.to_dict()
    extractor = KeyExtractor(spec)
    fields = extractor.extract(rel)
    out["keys"] = fields
    if not screened.included:
        out.update(included=False, reason=screened.reason, item_key=None)
    elif fields is None:
        out.update(included=False, reason="keys regex did not match", item_key=None)
    else:
        out.update(
            included=True,
            reason=screened.reason,
            item_key=extractor.group_key(index, fields),
            role=ext_of(rel),
        )
    return out


def items_to_json(items: list[Item]) -> str:
    return json.dumps([it.model_dump() for it in items], separators=(",", ":"))


def items_from_json(text: str) -> list[Item]:
    return [Item.model_validate(d) for d in json.loads(text)]
