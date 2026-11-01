"""Theme tokens for style B, Calm Canvas (docs/review/gui-styles.md, HLD section 10).

The app owns the values. QML reads them through the ``theme`` context object, web views
and the CLI read them from ``api-info.json`` (``engine.api_info(theme_tokens=THEME)``), so
every surface shares names and nobody writes a raw colour (contracts section 15).

Rules the tokens encode:

- warm light background, white rounded cards with a soft shadow, almost no chrome
- agent changes are glyph plus colour, never colour alone: ``added`` / ``changed`` /
  ``removed`` each have a glyph and a colour, and removals are ghosted (``ghost_alpha``)
- 13-14 px UI text, monospace for addresses (``view:v3``)
"""

from __future__ import annotations

from typing import Any

from specto_core.float_layer import COLLAPSED_H

try:  # the token table is importable without Qt (api-info, tests)
    from PySide6.QtCore import Property, QObject, Signal, Slot

    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

THEME_NAME = "calm-canvas-light"

COLORS: dict[str, str] = {
    "bg": "#f4efe6",
    "bg_tint": "#ece6da",
    "surface": "#ffffff",
    "surface_alt": "#faf7f1",
    "surface_hover": "#f6f1e8",
    "border": "#e3dccd",
    "border_strong": "#cfc5b2",
    "text": "#3a3630",
    "text_muted": "#7a7266",
    "text_faint": "#a89f91",
    "accent": "#2f6fab",
    "accent_soft": "#dce8f5",
    "focus": "#2f6fab",
    # a selected (or focused) view's frame (#120): the ink, a colour no link chain uses,
    # with a soft halo of the same ink around it
    "selected": "#3a3630",
    "selected_halo": "#383a3630",
    "agent": "#b86a2b",
    "agent_soft": "#f6e6d6",
    "added": "#3f8f5a",
    "added_soft": "#e1f1e6",
    "changed": "#c98a1e",
    "changed_soft": "#f8ecd2",
    "removed": "#c0504d",
    "removed_soft": "#f6dedd",
    "ghost": "#9b9186",
    "lock": "#6d6358",
    "ok": "#3f8f5a",
    "loading": "#2f6fab",
    "paused": "#a89f91",
    "error": "#c0504d",
    "approval": "#b86a2b",
    "approval_soft": "#f6e6d6",
    "disconnected": "#9b9186",  # a view no link reaches from the project source (#83)
    "shadow": "#1a000000",
    "scrim": "#66f4efe6",
    "drop_zone": "#662f6fab",
    "mono_bg": "#f3efe7",
    "video_bg": "#141210",
    # transparency behind an image (#171): neutral greys, so they tint no colour
    "checker_light": "#f2f2f2",
    "checker_dark": "#c4c4c4",
    # a port circle's hover tip (#111): a tinted surface, apart from the dark ordinary tips
    "tip_port_bg": "#e4edf7",
    "tip_port_border": "#a9c1dc",
    "tip_port_text": "#24364a",
    # link chains (#45): connected sets of linked views share one of these, in the normal
    # view (faint curves, frame strokes) and the overworld (clear wires, link nodes)
    "chain_0": "#4a7fb5",
    "chain_0_soft": "#dde8f3",
    "chain_1": "#c2783a",
    "chain_1_soft": "#f7e6d5",
    "chain_2": "#5f9e6e",
    "chain_2_soft": "#e0f0e4",
    "chain_3": "#8a63b8",
    "chain_3_soft": "#eae2f4",
    "chain_4": "#c9657a",
    "chain_4_soft": "#f8e0e5",
    "chain_5": "#2a9d8f",
    "chain_5_soft": "#d9efec",
    "chain_none": "#cfc5b2",
    # a file's modified time in the file tree's details (#115), by age, a hue each so
    # they tell apart at a glance: green while fresh, then teal, amber, the faint text
    "age_minutes": "#2e8b57",  # under 10 minutes
    "age_hour": "#2f8a9e",  # under an hour
    "age_day": "#a6742c",  # under a day
    "age_old": "#a89f91",  # older
}


GLYPHS: dict[str, str] = {
    "added": "+",
    "changed": "~",
    "removed": "-",
    "moved": ">",
    "locked": "#",
    "agent": "@",
    "user": "",
    "ok": "",
    "loading": "..",
    "paused": "||",
    "error": "!",
    "approval": "?",
    "disconnected": "x",
}

METRICS: dict[str, float] = {
    "radius_card": 14,
    "radius_inner": 8,
    "radius_pill": 999,
    "pad": 12,
    "pad_tight": 6,
    "gap": 12,
    "header_h": 30,
    # a collapsed view (#114): its header, the frame's 1 px inset and the gutter, twice;
    # the float layer's geometry goes by it too, so core owns the number (#116)
    "collapsed_h": COLLAPSED_H,
    "bar_h": 40,
    # web views (#130): the footer row, a header/footer icon button, a side panel's
    # header and its collapsed strip
    "footer_h": 24,
    "icon_btn": 22,
    "panel_bar": 24,
    "shadow_blur": 18,
    "shadow_y": 4,
    "font_px": 13,
    "font_px_small": 11,
    "font_px_title": 15,
    "handle_px": 8,
    "gutter": 2,  # the transparent band around a view frame: snapped views show 2 * gutter
    "port_px": 20,  # the port circle's diameter, centred on the frame's vertical edge
    # from the frame's top edge down to the port circle's centre: the header/body
    # separator, under the frame's 1 px inset and the header (#121)
    "port_inset": 31,
    "port_stack": 4,  # the gap between a view's stacked input circles (#81)
    "link_dot_px": 9,  # a link node, the dot on a wire (#121)
    "link_dot_hit": 20,  # the dot's click and hover area, centred on it
    # a port tip (#127): a box hanging centred under its port, capped and wrapping, a
    # caret pointing up at the port, its data and filter sections each led by an icon;
    # it waits this long for the pointer to move from the port into it (its names are
    # links)
    "tip_max_w": 200,
    "tip_caret": 6,
    "tip_icon_px": 14,
    "tip_grace_ms": 250,
    "project_node_radius": 12,  # the root node, a rounded tile (#127)
    "ghost_alpha": 0.45,
    "flash_ms": 900,
    "anim_ms": 180,
}

FONTS: dict[str, str] = {
    "ui": "Segoe UI",
    "mono": "Cascadia Mono",
}

THEME: dict[str, Any] = {
    "name": THEME_NAME,
    "style": "B Calm Canvas",
    "colors": COLORS,
    "glyphs": GLYPHS,
    "metrics": METRICS,
    "fonts": FONTS,
    "densities": ["comfortable"],
}


def theme_tokens() -> dict[str, Any]:
    """A copy of the token table for ``api-info.json``."""
    return {
        "name": THEME["name"],
        "style": THEME["style"],
        "colors": dict(COLORS),
        "glyphs": dict(GLYPHS),
        "metrics": dict(METRICS),
        "fonts": dict(FONTS),
        "densities": list(THEME["densities"]),
    }


def theme_for_web() -> dict[str, str]:
    """The tokens flattened to CSS-variable-friendly names for web views:
    ``color-surface``, ``glyph-added``, ``metric-pad`` (``px`` for lengths, bare numbers
    for ratios and durations), ``font-ui``. A page does
    ``style.setProperty("--" + name, value)`` for each entry."""
    out: dict[str, str] = {"theme-name": THEME_NAME}
    for k, v in COLORS.items():
        out[f"color-{k.replace('_', '-')}"] = v
    for k, v in GLYPHS.items():
        out[f"glyph-{k.replace('_', '-')}"] = v
    for k, v in METRICS.items():
        if k in ("ghost_alpha",) or k.endswith("_ms"):
            out[f"metric-{k.replace('_', '-')}"] = str(v)
        else:
            out[f"metric-{k.replace('_', '-')}"] = f"{v:g}px"
    for k, v in FONTS.items():
        out[f"font-{k.replace('_', '-')}"] = v
    return out


def flat_theme() -> dict[str, Any]:
    """Colours, glyphs, metrics and fonts in one flat map (``surface``, ``added``,
    ``pad``, ``mono``) for QML code that wants ``SpectoTheme.surface``-style access
    without the ``theme.colors.*`` nesting. ``card`` and ``bg`` are kept as aliases."""
    out: dict[str, Any] = {}
    out.update(COLORS)
    out.update({f"glyph_{k}": v for k, v in GLYPHS.items()})
    out.update(METRICS)
    out.update({f"font_{k}": v for k, v in FONTS.items()})
    out["card"] = COLORS["surface"]
    out["name"] = THEME_NAME
    return out


if HAVE_QT:

    class ThemeObject(QObject):
        """QML-facing token table: ``theme.colors.surface``, ``theme.metrics.pad``,
        ``theme.glyph("added")``. Bindings re-evaluate when ``changed`` fires."""

        changed = Signal()

        def __init__(self, tokens: dict[str, Any] | None = None, parent: QObject | None = None):
            super().__init__(parent)
            self._tokens = tokens or theme_tokens()

        def _get(self, key: str) -> Any:
            return self._tokens[key]

        @Property("QVariantMap", notify=changed)
        def colors(self) -> dict[str, str]:
            return dict(self._tokens["colors"])

        @Property("QVariantMap", notify=changed)
        def glyphs(self) -> dict[str, str]:
            return dict(self._tokens["glyphs"])

        @Property("QVariantMap", notify=changed)
        def metrics(self) -> dict[str, float]:
            return dict(self._tokens["metrics"])

        @Property("QVariantMap", notify=changed)
        def fonts(self) -> dict[str, str]:
            return dict(self._tokens["fonts"])

        @Property(str, notify=changed)
        def name(self) -> str:
            return str(self._tokens["name"])

        @Slot(str, result=str)
        def color(self, key: str) -> str:
            return str(self._tokens["colors"].get(key, "#ff00ff"))

        @Slot(str, result=str)
        def glyph(self, key: str) -> str:
            return str(self._tokens["glyphs"].get(key, "?"))

        @Slot(str, result=float)
        def metric(self, key: str) -> float:
            return float(self._tokens["metrics"].get(key, 0))

        def tokens(self) -> dict[str, Any]:
            return self._tokens

        def set_tokens(self, tokens: dict[str, Any]) -> None:
            self._tokens = tokens
            self.changed.emit()


__all__ = [
    "COLORS",
    "FONTS",
    "GLYPHS",
    "HAVE_QT",
    "METRICS",
    "THEME",
    "THEME_NAME",
    "flat_theme",
    "theme_for_web",
    "theme_tokens",
]
if HAVE_QT:
    __all__.append("ThemeObject")
