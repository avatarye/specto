"""``html`` view: a local HTML file of the project, with its relative assets, in a frame
(ports per ADR 0014).

The host is the ``json`` host with the html roles: ``data`` is the collection its
intaker resolved, ``selection`` picks the file to show, and the shown file goes out on
``selection`` when the user picks it in the page's file list. What the page gets is a
URL on the view's own items origin, ``specto://<view>.items/<link>/<key>/html/<name>``
(``scheme.item_doc_url``): the file's own name ends the URL, so the stylesheet, script
and image URLs inside the file resolve next to it and the scheme handler serves them
from the file's folder (never above it) under the view's ``read:link:<link>`` grant.
The page shows that URL in a sandboxed ``<iframe>``: a different origin from the view's
page, so the user's scripts run but have no bridge. The view holds ``net:web`` (#93):
the page's remote images, scripts and embedded maps (a remote image, a map
frame) load over http(s), and its frames may navigate there; the view's ``network``
param set to false drops the grant and the request interceptor blocks them again.
The footer's step (#107, #130) moves through the collection's html files; the page
has no file list of its own (ADR 0020: no browsing inside viewers).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specto_core.kinds import PluginManifest, ViewCost, ViewManifest

from ..hosts.base import SimpleFactory
from ..web.capabilities import NETWORK_PARAM
from ..web.items import pick_role
from ..web.scheme import item_doc_url
from .json_view import DATA, JsonViewHost

HTML_ROLES = (".html", ".htm")

HTML_MANIFEST = ViewManifest(
    type="html",
    label="HTML",
    host="web",
    doc="A local HTML file of the project (a deck, a report, a generated page) rendered "
    "in a sandboxed frame with the files it references relative to itself. Shows the "
    "selected html file of its collection, or the first; a file stepped to goes out as "
    "the selection.",
    accepts=["html"],
    cost=ViewCost(web_views=1),
    defaults={NETWORK_PARAM: True},
    state_schema={
        "type": "object",
        "properties": {
            NETWORK_PARAM: {
                "type": "boolean",
                "default": True,
                "description": "Let the page load http(s) images, scripts and frames; "
                "false: its own files only.",
            }
        },
        "additionalProperties": True,
    },
    example={
        "view": {"type": "html", "label": "Deck"},
        "link": {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "duna", "port": "data"},
            "params": {"sources": ["decks/deck.html"]},
            "summary": {"sentence": "The html view shows decks/deck.html."},
        },
    },
)
HTML_PLUGIN = PluginManifest(name="app-html", version="0.1.0", views=[HTML_MANIFEST])


class HtmlViewHost(JsonViewHost):
    page = "html/index.html"
    needs_monaco = False
    content_port = None

    def __init__(self, ctx: Any, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)
        self.network = self.caps.can_net("web")  # the grant the frame was loaded under

    def wanted_roles(self) -> list[str]:
        return list(HTML_ROLES)

    # -- the shown file: a document on this view's items origin ------------------------
    def item_payload(self, key: str) -> dict[str, Any]:
        payload = super().item_payload(key)
        if payload.get("url") is not None:
            payload["url"] = item_doc_url(
                self.view_id, self.data_link, key, payload["role"], payload["file"]
            )
        return payload

    # -- the html files of the collection ----------------------------------------------
    def files(self) -> list[dict[str, Any]]:
        """Every item of the collection with an html part, in collection order."""
        out: list[dict[str, Any]] = []
        for key, it in self.items.items():
            role = pick_role(dict(it.parts), HTML_ROLES)
            if role is None:
                continue
            out.append(
                {
                    "key": key,
                    "name": self.names.get(key, key),
                    "url": item_doc_url(
                        self.view_id, self.data_link, key, role, Path(it.parts[role]).name
                    ),
                    "role": role,
                }
            )
        return out

    # -- hooks -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        super().receive(port, value)
        if port == DATA and self.current_key is None:
            # nothing picked and the first item is not a page: the first page it is
            first = next((f["key"] for f in self.files()), None)
            if first is not None:
                self.show_key(first)

    def on_params_changed(self, params: dict[str, Any]) -> None:
        """``network`` turned on or off: the frame reloads, so what it shows is what it
        may now fetch (a page loaded offline gets its map; one taken offline loses it)."""
        super().on_params_changed(params)
        network = self.caps.can_net("web")
        if network != self.network:
            self.network = network
            self.notify("reload", {"network": network, "capabilities": list(self.caps)})

    def snapshot(self) -> dict[str, Any]:
        snap = super().snapshot()
        snap.pop("outline", None)
        snap["files"] = [f["key"] for f in self.files()]
        return snap


def factory() -> SimpleFactory:
    return SimpleFactory("html", HtmlViewHost, plugin=HTML_PLUGIN)


def register(registry: Any) -> None:
    """``specto.view_hosts`` entry point."""
    registry.add_factory(factory())


__all__ = ["HTML_MANIFEST", "HTML_PLUGIN", "HTML_ROLES", "HtmlViewHost", "factory", "register"]
