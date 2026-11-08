"""``markdown`` view (spec #35 story 44; ports per ADR 0014): a rendered note of its
collection.

The page (``markdown/index.html``) renders with markdown-it (GitHub CSS in style B),
Mermaid fences, KaTeX math, Obsidian callouts and wikilinks; read-only. The host is the
``json`` host with the markdown roles and two extras:

- ``notes``: the keys of the collection that have a markdown part, sent when the data
  changes and once the page is ready, so the page resolves ``[[wikilinks]]`` to keys
  itself (no note list in the page since #130: the footer's step moves through them);
- navigation: a wikilink click is a ``navigate {key, anchor}`` page event;
  the host shows that note, emits it on ``content`` and on ``selection`` (so linked
  views follow); the page keeps its own back history.

Relative images (``![](img/a.png)``) resolve next to the note through
``specto://items/<link>/<key>/md/img/a.png`` (scheme handler, ``read:link:<link>``).
``params.panels.outline`` (the outline panel's ``{collapsed, width}``, #130) is written
by the page. ``params.zoom`` is the note's reading zoom (#202: 0.5 to 3, 1 when absent;
the agent may set it): a zoom the reader made (Ctrl+wheel, Ctrl+= / - / 0, #189) comes
as a ``zoom {level}`` page event and is remembered as navigation (``live.remember``, no
undo step), so a reload and a relaunch show the note at that size. ``snapshot()`` adds
the note's heading outline, read here in Python.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from specto_core.kinds import PluginManifest, ViewCost, ViewManifest
from specto_core.links import Selection

from ..hosts.base import SimpleFactory
from ..web.items import pick_role
from ..web.scheme import item_url
from .json_view import ROLES_BY_KIND, SELECTION, JsonViewHost

MARKDOWN_ROLES = ROLES_BY_KIND["markdown"]
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
OUTLINE_MAX = 40
#: the reading zoom's range (``web/zoom.js``: 50 % to 300 %)
ZOOM_MIN, ZOOM_MAX = 0.5, 3.0


def zoom_level(value: Any) -> float | None:
    """A ``params.zoom`` value as the page keeps it: a finite number clamped to the
    range, rounded to 2 places; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    level = float(value)
    if level != level or level in (float("inf"), float("-inf")):
        return None
    return round(min(ZOOM_MAX, max(ZOOM_MIN, level)), 2)


MARKDOWN_MANIFEST = ViewManifest(
    type="markdown",
    label="Markdown",
    host="web",
    doc="Rendered markdown note (GitHub style, Mermaid, KaTeX, Obsidian callouts and "
    "wikilinks) of the selected item of its collection; wikilinks navigate within the "
    "collection and go out as the selection; read-only.",
    accepts=["markdown"],
    cost=ViewCost(web_views=1),
    state_schema={
        "type": "object",
        "properties": {
            "zoom": {
                "type": "number",
                "minimum": ZOOM_MIN,
                "maximum": ZOOM_MAX,
                "default": 1,
                "description": "The note's reading zoom (Ctrl+wheel writes it too).",
            }
        },
        "additionalProperties": True,
    },
    example={
        "command": "canvas.submit",
        "args": {"document": {"views": {"pola": {"type": "markdown", "label": "notes"}}}},
    },
)
MARKDOWN_PLUGIN = PluginManifest(name="app-markdown", version="0.2.0", views=[MARKDOWN_MANIFEST])


def headings_of(path: str | Path, limit: int = OUTLINE_MAX) -> list[str]:
    """``["# Title", "## Section", ...]`` for a markdown file (fenced code skipped)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    fence = False
    for line in text.splitlines():
        if line.startswith("```") or line.startswith("~~~"):
            fence = not fence
            continue
        if fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append(f"{m.group(1)} {m.group(2)}")
            if len(out) >= limit:
                out.append("...")
                break
    return out


class MarkdownViewHost(JsonViewHost):
    page = "markdown/index.html"
    vendor_files = (
        "markdown-it/markdown-it.umd.min.js",
        "github-markdown-css/github-markdown-light.css",
        "mermaid/mermaid.min.js",
        "katex/katex.min.js",
    )

    def wanted_roles(self) -> list[str]:
        return list(MARKDOWN_ROLES)

    # -- the note list -----------------------------------------------------------------
    def notes(self) -> list[dict[str, Any]]:
        """Every item of the collection with a markdown part, in collection order."""
        out: list[dict[str, Any]] = []
        for key, it in self.items.items():
            role = pick_role(dict(it.parts), MARKDOWN_ROLES)
            if role is None:
                continue
            out.append(
                {
                    "key": key,
                    "name": self.names.get(key, key),
                    "url": item_url(self.data_link, key, role),
                    "role": role,
                }
            )
        return out

    def send_notes(self) -> None:
        self.notify("notes", {"notes": self.notes()})

    # -- hooks -------------------------------------------------------------------------
    def on_data_changed(self) -> None:
        self.send_notes()

    def on_ready(self) -> None:
        self.send_notes()
        super().on_ready()

    def on_page_event(self, kind: str, payload: Any) -> None:
        """A wikilink click (or back): show the note and emit it as the selection.
        ``zoom {level}``: the reader zoomed the note; remembered in ``params.zoom`` as
        navigation (#202), so it comes back on reload and relaunch."""
        if kind == "zoom" and isinstance(payload, dict):
            level = zoom_level(payload.get("level"))
            if level is not None:
                self.ctx.live.remember(self.view_id, {"zoom": level})
            return
        if kind != "navigate" or not isinstance(payload, dict):
            super().on_page_event(kind, payload)
            return
        key = str(payload.get("key") or "")
        if key and self.show_key(key)["url"] is not None:
            self.emit(SELECTION, Selection(keys=[key], last=key))

    def snapshot(self) -> dict[str, Any]:
        snap = super().snapshot()
        snap["outline"] = headings_of(self.current_path) if self.current_path else []
        snap["notes"] = [n["key"] for n in self.notes()]
        return snap


def factory() -> SimpleFactory:
    return SimpleFactory("markdown", MarkdownViewHost, plugin=MARKDOWN_PLUGIN)


def register(registry: Any) -> None:
    """``specto.view_hosts`` entry point."""
    registry.add_factory(factory())


__all__ = [
    "MARKDOWN_MANIFEST",
    "MARKDOWN_PLUGIN",
    "MarkdownViewHost",
    "factory",
    "headings_of",
    "register",
    "zoom_level",
]
