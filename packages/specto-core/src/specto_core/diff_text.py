"""A submit's patch as lines a human reads (spec #35 story 15: the detailed diff, on
request). One line per view or link added, removed or changed, and one per changed
root field; the inverse patch supplies the old values."""

from __future__ import annotations

from typing import Any

from .patch import get_at


def _brief_view(view: dict[str, Any]) -> str:
    lay = view.get("layout") or {}
    (x, y), (w, h) = lay.get("position", (0, 0)), lay.get("size", (0, 0))
    return f"{view.get('type', '?')} {view.get('label', '')!r} at {x:g},{y:g} {w:g}x{h:g}"


def _brief_link(link: dict[str, Any]) -> str:
    src, dst = link.get("source") or {}, link.get("target") or {}
    return (
        f"{link.get('kind', '?')} {src.get('view')}.{src.get('port')} -> "
        f"{dst.get('view')}.{dst.get('port')}"
    )


def _old_value(inverse: list[dict[str, Any]], path: str) -> Any:
    for op in inverse:
        if op.get("path") == path and "value" in op:
            return op["value"]
    return None


def _show(value: Any) -> str:
    return repr(value) if isinstance(value, str) else str(value)


def describe_patch(patch: list[dict[str, Any]], inverse: list[dict[str, Any]]) -> list[str]:
    """``+`` added, ``-`` removed, ``~`` changed; views and links by id with a brief,
    fields as ``old -> new``."""
    out: list[str] = []
    for op in patch:
        path = op.get("path", "")
        parts = [p for p in path.split("/") if p]
        kind = op.get("op")
        if len(parts) >= 2 and parts[0] in ("views", "links"):
            noun = "view" if parts[0] == "views" else "link"
            oid = parts[1]
            if len(parts) == 2:
                if kind == "add":
                    brief = _brief_view(op["value"]) if noun == "view" else _brief_link(op["value"])
                    out.append(f"+ {noun} {oid}: {brief}")
                elif kind == "remove":
                    old = _old_value(inverse, path) or {}
                    brief = _brief_view(old) if noun == "view" else _brief_link(old)
                    out.append(f"- {noun} {oid}: {brief}")
                else:
                    out.append(f"~ {noun} {oid}: replaced")
                continue
            field = ".".join(parts[2:])
            if kind == "add":
                out.append(f"~ {noun} {oid} {field}: added {_show(op.get('value'))}")
            elif kind == "remove":
                out.append(f"~ {noun} {oid} {field}: removed {_show(_old_value(inverse, path))}")
            else:
                old = _old_value(inverse, path)
                out.append(f"~ {noun} {oid} {field}: {_show(old)} -> {_show(op.get('value'))}")
            continue
        field = ".".join(parts) or "/"
        if kind == "remove":
            out.append(f"~ {field}: removed {_show(_old_value(inverse, path))}")
        elif kind == "add":
            out.append(f"~ {field}: added {_show(op.get('value'))}")
        else:
            out.append(f"~ {field}: {_show(_old_value(inverse, path))} -> {_show(op.get('value'))}")
    return out


def value_at(doc: dict[str, Any], pointer: str) -> Any:
    """``get_at`` that answers ``None`` for a missing path (used by the strip's diff)."""
    try:
        return get_at(doc, pointer)
    except Exception:
        return None


__all__ = ["describe_patch", "value_at"]
