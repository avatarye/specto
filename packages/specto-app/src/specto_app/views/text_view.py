"""The ``text`` view host (SPEC-32): one short text the canvas document carries in
``params.text``, shown as it is. It is the "Not supported" view: a file no view type
shows opens in one saying ``Not supported: <name> (<ext>)``, titled like any dedicated
view (``<name> · <folder>``) and fed the file by the same pinned link, so the file's
dedicated view is recognised the same way whatever its kind. The link is the view's
identity only: nothing is read from the file, nothing goes out.

One :class:`TextHost` per live text view. It owns ``Specto/Text/TextView.qml`` and
follows ``params.text`` through the document (an agent's change, an undo).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specto_app.hosts.base import HostContext, QuickViewHost
from specto_core.errors import SpectoError
from specto_core.kinds import PluginManifest, ViewManifest

TEXT_QML = Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Text" / "TextView.qml"

TEXT_MANIFEST = ViewManifest(
    type="text",
    label="Text",
    host="quick",
    doc="One short text the document carries (params.text), shown as it is; the view a "
    "file no view type shows opens in ('Not supported: <name> (<ext>)'). Its data input "
    "takes the pinned link naming the file it stands for; nothing is read or emitted.",
    accepts=[],
    defaults={"text": ""},
    state_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The text shown."}},
        "additionalProperties": True,
    },
    example={
        "view": {
            "type": "text",
            "label": "blob.bin · notes",
            "params": {"text": "Not supported: blob.bin (.bin)"},
        },
        "link": {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "tuli", "port": "data"},
            "params": {"sources": ["notes/blob.bin"]},
            "summary": {"sentence": "The text view shows notes/blob.bin."},
        },
    },
)
TEXT_PLUGIN = PluginManifest(name="app-text", version="0.1.0", views=[TEXT_MANIFEST])


class TextHost(QuickViewHost):
    """One live text view; see the module docstring."""

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)
        self.text = ""
        if ctx.qml_engine is not None:
            self.item = ctx.create_item(TEXT_QML, properties={"viewId": view_id})
        self._apply_params()
        self.set_health("ok", "")

    def _apply_params(self) -> None:
        try:
            text = str(self.params.get("text") or "")
        except SpectoError:  # the view left the document
            return
        self.text = text
        if self.item is not None:
            self.item.setProperty("text", text)

    # -- contract ------------------------------------------------------------------------------
    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        prefix = f"/views/{self.view_id}"
        if any(
            op.get("path", "").startswith(f"{prefix}/params") or op.get("path") == prefix
            for op in patch
        ):
            self._apply_params()

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "text": self.text}


class TextFactory:
    type = "text"
    plugin = TEXT_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> TextHost:
        return TextHost(ctx, view_id, manifest)


def factory() -> TextFactory:
    return TextFactory()


def register(registry: Any) -> None:
    registry.add_factory(factory())


__all__ = ["TEXT_MANIFEST", "TEXT_PLUGIN", "TEXT_QML", "TextFactory", "TextHost", "factory"]
