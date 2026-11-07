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


#: how many phrases a derived label lists before "and N more changes"
LABEL_PHRASES = 3


def patch_label(patch: list[dict[str, Any]]) -> str:
    """A submit's label from its patch, for a submit that gave none (SPEC-18): one phrase
    per view or link it changed, in patch order ("moved kobi", "added image 'Rear'",
    "relabelled tamu 'rear'"). Links added or removed with a view added or removed in the
    same patch are part of that view's phrase."""
    order: list[tuple[str, str]] = []
    ops: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for op in patch:
        parts = [p for p in op.get("path", "").split("/") if p]
        key = (
            (parts[0], parts[1])
            if len(parts) >= 2 and parts[0] in ("views", "links")
            else (
                "root",
                ".".join(parts) or "/",
            )
        )
        if key not in ops:
            order.append(key)
            ops[key] = []
        ops[key].append({**op, "parts": parts[2:] if key[0] != "root" else parts})
    whole = {
        oid
        for (section, oid), group in ops.items()
        if section == "views" and any(not op["parts"] for op in group)
    }
    view_removed = any(
        section == "views" and op["op"] == "remove" and not op["parts"]
        for (section, _), group in ops.items()
        for op in group
    )
    phrases = [
        phrase
        for key in order
        if (phrase := _object_phrase(key, ops[key], whole, view_removed)) is not None
    ]
    if len(phrases) > LABEL_PHRASES:
        shown = phrases[: LABEL_PHRASES - 1]
        return f"{', '.join(shown)} and {len(phrases) - len(shown)} more changes"
    if len(phrases) > 1:
        return f"{', '.join(phrases[:-1])} and {phrases[-1]}"
    return phrases[0] if phrases else ""


def _object_phrase(
    key: tuple[str, str], group: list[dict[str, Any]], whole_views: set[str], view_removed: bool
) -> str | None:
    section, oid = key
    if section == "root":
        return "set the root view" if oid == "root_view" else f"changed {oid}"
    head = group[0]
    if not head["parts"]:  # the whole object added, removed or replaced
        if section == "links":
            if head["op"] == "remove":  # a remove op names no ends: the cascade's guess
                return None if view_removed else f"removed link {oid}"
            value = head.get("value") or {}
            ends = {(value.get(end) or {}).get("view") for end in ("source", "target")}
            if ends & whole_views:
                return None  # part of its new view's phrase
            return f"linked {_ends(value)}" if head["op"] == "add" else f"changed link {oid}"
        if head["op"] == "add":
            value = head.get("value") or {}
            return f"added {value.get('type', 'view')} {value.get('label', oid)!r}"
        return f"removed {oid}" if head["op"] == "remove" else f"replaced {oid}"
    if section == "links":
        return f"changed link {oid}"
    fields = {
        tuple(op["parts"][:2]) if op["parts"][0] == "layout" else (op["parts"][0],) for op in group
    }
    if fields <= {("layout", "position"), ("layout", "size")}:
        return {
            frozenset({("layout", "position")}): f"moved {oid}",
            frozenset({("layout", "size")}): f"resized {oid}",
        }.get(frozenset(fields), f"moved and resized {oid}")
    if fields == {("label",)}:
        return f"relabelled {oid} {group[0].get('value')!r}"
    if fields == {("layout", "collapsed")}:
        return f"{'collapsed' if group[0].get('value') else 'expanded'} {oid}"
    if fields == {("params",)}:
        names = sorted({op["parts"][1] for op in group if len(op["parts"]) > 1})
        return f"set {oid} {', '.join(names)}" if names else f"set {oid} params"
    return f"changed {oid}"


def _ends(link: dict[str, Any]) -> str:
    src, dst = link.get("source") or {}, link.get("target") or {}
    return f"{src.get('view')} to {dst.get('view')}"


__all__ = ["describe_patch", "patch_label", "value_at"]
