"""Keys, grouping and sort order (HLD 3.6).

A keys regex with named groups turns a relative path into key fields; files that share
the ``group_by`` fields form one multi-part ``Item`` (``00042.jpg`` + ``00042.json``).
Without a keys regex the key is the relative path without extension, so sidecars still
group. Keys are namespaced by source index when a spec has several sources and
``merge_roots`` is false, so two roots cannot collide silently (R13).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from specto_core.kinds import Item

from .sources import FoundFile
from .spec import IntakeSpec

_NUM = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple[Any, ...]:
    """``frame_10`` sorts after ``frame_9``; case-insensitive."""
    parts = _NUM.split(text)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


def strip_ext(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    if "." in name[1:]:
        return rel[: len(rel) - len(name) + name.rindex(".", 1)]
    return rel


def ext_of(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    if "." in name[1:]:
        return name[name.rindex(".", 1) :].lower()
    return ""


@dataclass
class KeyExtractor:
    spec: IntakeSpec
    regex: re.Pattern[str] | None = None
    fields: list[str] = field(default_factory=list)
    namespaced: bool = False

    def __post_init__(self) -> None:
        self.regex = re.compile(self.spec.keys) if self.spec.keys else None
        self.fields = self.spec.key_fields()
        self.namespaced = len(self.spec.sources) > 1 and not self.spec.merge_roots

    def extract(self, rel: str) -> dict[str, str] | None:
        """Key fields for a relative path, or None when the keys regex does not match."""
        if self.regex is None:
            return {"stem": strip_ext(rel)}
        m = self.regex.search(rel)
        if m is None:
            return None
        return {k: v for k, v in m.groupdict().items() if v is not None}

    def group_key(self, source: int, fields: dict[str, str]) -> str:
        values = [fields.get(f, "") for f in self.fields]
        key = "/".join(values)
        return f"{source}:{key}" if self.namespaced else key


@dataclass
class Skipped:
    path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


def _role_for(parts: dict[str, str], f: FoundFile) -> str:
    role = ext_of(f.rel) or f.rel.rsplit("/", 1)[-1]
    if role not in parts:
        return role
    head = f.rel.rsplit("/", 1)[0] if "/" in f.rel else f.root.rsplit("/", 1)[-1]
    cand = f"{head}/{role}"
    n = 1
    while cand in parts:
        n += 1
        cand = f"{head}/{role}#{n}"
    return cand


def group_items(
    spec: IntakeSpec, files: list[FoundFile], skipped: list[Skipped] | None = None
) -> list[Item]:
    """Build items from screened files: extract keys, group by key, then sort."""
    extractor = KeyExtractor(spec)
    groups: dict[str, dict[str, Any]] = {}
    for f in files:
        fields = extractor.extract(f.rel)
        if fields is None:
            if skipped is not None:
                skipped.append(Skipped(f"{f.root}/{f.rel}", "keys regex did not match"))
            continue
        key = extractor.group_key(f.source, fields)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "parts": {},
                "rel": {},
                "keys": fields,
                "source": f.source,
                "root": f.root,
                "size": 0,
                "mtime_ns": 0,
                "stat": {},
            }
        role = _role_for(g["parts"], f)
        g["parts"][role] = f.path
        g["rel"][role] = f.rel
        g["stat"][role] = [f.size, f.mtime_ns]  # per part, for file listings
        g["size"] += f.size
        g["mtime_ns"] = max(g["mtime_ns"], f.mtime_ns)
    items = [
        Item(
            key=key,
            parts=g["parts"],
            meta={
                "keys": g["keys"],
                "source": g["source"],
                "root": g["root"],
                "rel": g["rel"],
                "size": g["size"],
                "mtime_ns": g["mtime_ns"],
                "stat": g["stat"],
            },
        )
        for key, g in groups.items()
    ]
    items.sort(key=_sort_fn(spec, extractor))
    for i, it in enumerate(items):
        it.index = i
    return items


def _sort_fn(spec: IntakeSpec, extractor: KeyExtractor):
    def primary_rel(it: Item) -> str:
        return next(iter(it.meta["rel"].values()))

    if spec.sort == "key":
        return lambda it: (
            tuple(natural_key(it.meta["keys"].get(f, "")) for f in extractor.fields),
            it.meta["source"],
            natural_key(primary_rel(it)),
        )
    if spec.sort == "mtime":
        return lambda it: (it.meta["mtime_ns"], it.meta["source"], natural_key(primary_rel(it)))
    return lambda it: (it.meta["source"], natural_key(primary_rel(it)))
