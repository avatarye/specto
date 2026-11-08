"""The ``content`` port value (ADRs 0014, 0016): files parsed at resolve time.

A ``read_contents`` link turns the files on its input into a :class:`ContentValue`, one
:class:`ContentEntry` per file: GeoJSON as a FeatureCollection whose features carry
their index as ``id`` (so a selection can name ``<entry key>#<index>``), JSON as the
parsed value, text as the string. A file that does not parse keeps its entry with
``error`` set, so one bad file never empties a map. The json and markdown views emit the
same shape for the one file they show.

Pure: no file walking here (the intaker's source matching lives in ``specto_intake``);
this module only names formats and parses one file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ContentFormat = Literal["geojson", "json", "text"]

#: extension -> format; anything else is not readable contents (images, video, ...)
FORMAT_BY_EXT: dict[str, ContentFormat] = {
    ".geojson": "geojson",
    ".json": "json",
    ".md": "text",
    ".markdown": "text",
    ".txt": "text",
    ".csv": "text",
    ".yaml": "text",
    ".yml": "text",
    ".toml": "text",
    ".log": "text",
    ".xml": "text",
    ".html": "text",
}

GEOMETRY_TYPES = (
    "Point",
    "LineString",
    "Polygon",
    "MultiPoint",
    "MultiLineString",
    "MultiPolygon",
    "GeometryCollection",
)


class ContentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(description="The relative path without its extension (POSIX).")
    path: str = Field(description="The relative POSIX path from the value's root.")
    format: ContentFormat
    data: Any = Field(default=None, description="Parsed JSON / FeatureCollection / text.")
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="geojson: features, types, bbox; text: lines, bytes.",
    )
    error: str | None = Field(default=None, description="Why the file could not be read.")


class ContentValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = ""
    entries: list[ContentEntry] = Field(default_factory=list)
    link: str = Field(default="", description="The link that delivered it (runtime stamp).")
    truncated: str = Field(
        default="", description="Which source the walk cut short, and its fix-it (SPEC-94)."
    )

    def entry(self, key: str) -> ContentEntry | None:
        return next((e for e in self.entries if e.key == key), None)


def format_for(path: str, override: str | None = None) -> ContentFormat | None:
    """The format a file is read as: ``override`` when given, else by extension; None
    when the file is not readable contents."""
    if override:
        if override not in ("geojson", "json", "text"):
            raise ValueError(f"unknown content format {override!r} (geojson, json or text)")
        return override  # type: ignore[return-value]
    return FORMAT_BY_EXT.get(Path(path).suffix.lower())


def key_for(rel: str) -> str:
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    if "." in name[1:]:
        return rel[: len(rel) - len(name) + name.rindex(".", 1)]
    return rel


def read_entry(abs_path: str | Path, rel: str, fmt: ContentFormat) -> ContentEntry:
    """Parse one file as ``fmt``; a failure lands on the entry, never raises."""
    rel = rel.replace("\\", "/")
    entry = ContentEntry(key=key_for(rel), path=rel, format=fmt)
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        entry.error = f"{rel}: {e.strerror or e}"
        return entry
    if fmt == "text":
        entry.data = text
        entry.meta = {"lines": len(text.splitlines()), "bytes": len(text.encode("utf-8"))}
        return entry
    try:
        data = json.loads(text)
    except ValueError as e:
        entry.error = f"{rel}: not JSON ({e})"
        return entry
    if fmt == "json":
        entry.data = data
        return entry
    try:
        entry.data, entry.meta = normalize_geojson(data)
    except ValueError as e:
        entry.error = f"{rel}: {e}"
    return entry


def normalize_geojson(data: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """A FeatureCollection with ``id = index`` on every feature, plus
    ``{features, types, bbox}`` (bbox rounded to 4 decimals, None without coordinates)."""
    if not isinstance(data, dict):
        raise ValueError("not a GeoJSON object")
    t = data.get("type")
    if t == "FeatureCollection":
        features = list(data.get("features") or [])
    elif t == "Feature":
        features = [data]
    elif t in GEOMETRY_TYPES:
        features = [{"type": "Feature", "properties": {}, "geometry": data}]
    else:
        raise ValueError(f"not GeoJSON (type {t!r})")
    out: list[dict[str, Any]] = []
    types: set[str] = set()
    box = [180.0, 90.0, -180.0, -90.0]
    seen = False
    for i, f in enumerate(features):
        if not isinstance(f, dict):
            raise ValueError(f"feature {i} is not an object")
        g = f.get("geometry")
        f = {**f, "id": i, "properties": f.get("properties") or {}}
        if isinstance(g, dict) and g.get("type"):
            types.add(str(g["type"]))
            for x, y in _positions(g):
                seen = True
                box[0], box[1] = min(box[0], x), min(box[1], y)
                box[2], box[3] = max(box[2], x), max(box[3], y)
        out.append(f)
    meta: dict[str, Any] = {"features": len(out), "types": sorted(types)}
    meta["bbox"] = [round(v, 4) for v in box] if seen else None
    return {"type": "FeatureCollection", "features": out}, meta


def _positions(geometry: dict[str, Any]):
    if geometry.get("type") == "GeometryCollection":
        for g in geometry.get("geometries") or []:
            if isinstance(g, dict):
                yield from _positions(g)
        return
    stack = [geometry.get("coordinates")]
    while stack:
        c = stack.pop()
        if not isinstance(c, list) or not c:
            continue
        if isinstance(c[0], int | float):
            if len(c) >= 2:
                yield float(c[0]), float(c[1])
        else:
            stack.extend(c)


__all__ = [
    "FORMAT_BY_EXT",
    "GEOMETRY_TYPES",
    "ContentEntry",
    "ContentFormat",
    "ContentValue",
    "format_for",
    "key_for",
    "normalize_geojson",
    "read_entry",
]
