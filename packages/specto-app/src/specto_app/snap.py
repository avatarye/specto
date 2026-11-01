"""``snap``: a structured snapshot of a view or the whole canvas (spec #35 story 28).

Qt-free. The metadata comes from the canvas document, the link runtime and the live
hosts; the optional PNG and the on-screen rects come from the shell through the
``SnapProvider`` protocol (``engine.host.shell``), which is absent in a headless engine
(then ``image`` is reported as unavailable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from specto_core import Engine
from specto_core.errors import NotFound

CANVAS = "canvas"


class SnapProvider(Protocol):
    """What the shell offers ``snap``. All rects are ``[x, y, w, h]`` in window pixels."""

    def rect_of(self, oid: str) -> list[float] | None: ...

    def host_snapshot(self, view_id: str) -> dict[str, Any]: ...

    def health_of(self, view_id: str) -> dict[str, str]: ...

    def grab(self, oid: str, path: Path) -> dict[str, Any]: ...


def default_snap_path(engine: Engine, oid: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:-3]
    root = engine.project.specto_dir / "snaps" if engine.project else Path(".specto") / "snaps"
    return root / f"{oid}-{stamp}.png"


def resolve_target(engine: Engine, target: str) -> str:
    """``canvas``, a view id, ``view:<id>`` or ``@<label>`` -> ``canvas`` or the view id."""
    t = (target or CANVAS).strip()
    if t in ("", CANVAS):
        return CANVAS
    t = t.removeprefix("view:")
    views = engine.live.data["views"]
    if t in views:
        return t
    if t.startswith("@"):
        label = t[1:]
        hits = [vid for vid, v in views.items() if v.get("label") == label]
        if len(hits) == 1:
            return hits[0]
        raise NotFound(
            f"{'no' if not hits else 'several'} view(s) labelled {label!r}",
            fix="use the view id" if hits else f"view ids: {', '.join(views) or 'none'}",
        )
    raise NotFound(
        f"no view {t!r} on the canvas", fix=f"view ids: {', '.join(views) or 'none'}, or 'canvas'"
    )


def _health(provider: SnapProvider | None, vid: str) -> dict[str, str]:
    fn = getattr(provider, "health_of", None)
    return fn(vid) if callable(fn) else {"status": "ok", "message": ""}


def _link_brief(link: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "kind": link["kind"],
        "source": f"{link['source']['view']}.{link['source']['port']}",
        "target": f"{link['target']['view']}.{link['target']['port']}",
        "summary": link["summary"]["sentence"],
        "status": (status or {}).get("state"),
    }


def _view_meta(engine: Engine, vid: str, provider: SnapProvider | None) -> dict[str, Any]:
    doc = engine.live.data
    v = doc["views"][vid]
    links = doc["links"]
    runtime = engine.links
    ins = [lid for lid, ln in links.items() if ln["target"]["view"] == vid]
    outs = [lid for lid, ln in links.items() if ln["source"]["view"] == vid]
    return {
        "id": vid,
        "address": f"canvas:canvas/view:{vid}",
        "type": v["type"],
        "label": v.get("label", ""),
        "layout": v.get("layout", {}),
        "params": v.get("params", {}),
        "locked": bool(v.get("locked")),
        "health": _health(provider, vid),
        "links": {
            "in": [{"id": lid, **_link_brief(links[lid], runtime.status(lid))} for lid in ins],
            "out": [{"id": lid, **_link_brief(links[lid], runtime.status(lid))} for lid in outs],
        },
        "rect": provider.rect_of(vid) if provider else None,
        "host": provider.host_snapshot(vid) if provider else {},
    }


def _canvas_meta(engine: Engine, provider: SnapProvider | None) -> dict[str, Any]:
    doc = engine.live.data
    runtime = engine.links
    return {
        "address": "canvas:canvas",
        "label": doc.get("project", {}).get("label", ""),
        "window_frame": doc["window_frame"]["rect"],
        "root_view": doc.get("root_view"),
        "views": {
            vid: {
                "type": v["type"],
                "label": v.get("label", ""),
                "layout": v.get("layout", {}),
                "health": _health(provider, vid),
                "rect": provider.rect_of(vid) if provider else None,
            }
            for vid, v in doc["views"].items()
        },
        "links": {lid: _link_brief(ln, runtime.status(lid)) for lid, ln in doc["links"].items()},
        "rect": provider.rect_of(CANVAS) if provider else None,
    }


def build_snapshot(engine: Engine, oid: str, provider: SnapProvider | None) -> dict[str, Any]:
    if oid == CANVAS:
        kind, meta = CANVAS, _canvas_meta(engine, provider)
    else:
        kind, meta = "view", _view_meta(engine, oid, provider)
    info = engine.live.undo_info(last=3)
    meta["undo"] = {"cursor": info["cursor"], "depth": info["depth"]}
    meta["events_seq"] = engine.live.events.last_seq
    return {"kind": kind, "meta": meta}


# -- layout text (structured text an agent can assert on) --------------------------------------
def _fmt_rect(rect: list[float] | None) -> str:
    if not rect:
        return "-"
    x, y, w, h = rect
    return f"{w:g}x{h:g}@{x:g},{y:g}"


def _view_line(engine: Engine, vid: str, provider: SnapProvider | None) -> str:
    v = engine.live.data["views"][vid]
    lay = v.get("layout", {})
    pos, size = lay.get("position", [0, 0]), lay.get("size", [0, 0])
    bits = [
        f"view {vid}",
        f'"{v.get("label", "")}"',
        v["type"],
        f"at={_fmt_rect([*pos, *size])}",
        f"health={_health(provider, vid)['status']}",
    ]
    if v.get("locked"):
        bits.append("locked")
    rect = provider.rect_of(vid) if provider else None
    if rect:
        bits.append(f"px={_fmt_rect(rect)}")
    return " ".join(bits)


def layout_text(engine: Engine, oid: str, provider: SnapProvider | None) -> str:
    """The canvas (or one view) as text: one line per view with label, type, canvas rect,
    health, lock and on-screen pixel rect, then one line per link."""
    doc = engine.live.data
    lines: list[str] = []
    if oid == CANVAS:
        fr = doc["window_frame"]["rect"]
        lines.append(
            f'canvas "{doc.get("project", {}).get("label", "")}" '
            f"window_frame={_fmt_rect([fr['x'], fr['y'], fr['w'], fr['h']])} "
            f"root_view={doc.get('root_view') or '-'}"
        )
        for vid in doc["views"]:
            lines.append("  " + _view_line(engine, vid, provider))
        if doc["links"]:
            lines.append("links")
            for lid, ln in doc["links"].items():
                st = engine.links.status(lid) or {}
                lines.append(
                    f"  link {lid} {ln['kind']} {ln['source']['view']}.{ln['source']['port']}"
                    f" -> {ln['target']['view']}.{ln['target']['port']}"
                    f" {st.get('state', '-')}: {ln['summary']['sentence']}"
                )
    else:
        lines.append(_view_line(engine, oid, provider))
    return "\n".join(lines)


__all__ = [
    "CANVAS",
    "SnapProvider",
    "build_snapshot",
    "default_snap_path",
    "layout_text",
    "resolve_target",
]
