"""``map`` view (spec #35 story 45; ports per ADR 0014): MapLibre GL, geojson.io-style,
one layer per GeoJSON file of its ``features`` input.

Ports: ``features`` (a ``ContentValue``, what a read contents link parsed out of the
files it was pointed at), ``selection`` (feature ids to highlight) and ``time`` (kept
for #42) in; ``selection`` out when the user clicks a feature. Feature ids are
``<entry key>#<feature index>`` (the resolver numbers features by index).

Per-layer visibility and colour live in ``params.layers[<entry key>] = {visible,
color}``, the basemap choice in ``params.basemap`` (``none`` | ``osm`` | ``satellite``,
#157), and the LAYERS
side panel's state in ``params.panels.layers`` (``{collapsed, width}``, #130); the page
writes them through ``setParams`` (user submits). The camera is the page's own
(reported in its page state).

Basemap: the raster tiles of the provider ``params.basemap`` names (``web.basemaps``: OSM
streets, Esri World Imagery with place labels) when the view holds ``net:tiles`` and the
provider's tile hosts answer (each host probed once per process, in a thread); a blank
style otherwise. The page gets the provider rows with the layers, each with whether its
hosts answered, and switches between them in its footer.
The request interceptor (``web.scheme.make_interceptor``) is what actually lets tile
requests out.

Hover: the page sends ``hover {id, properties}`` (or null) so ``snap`` can say what the
user is looking at; the page shows the properties in its side pane.

Reload and batches (#181): every ``features`` delivery bumps the payload's ``revision``;
the page takes a new revision as a data reload (it fetches every layer again and frames
the data). The page gets the first ``params.limit`` GeoJSON files (``params.batch``, 200
by default, when unset) and the ``total``; over a batch its footer shows loaded / total
and Load more, which writes ``limit`` up by a batch.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal, Slot

from specto_core.contents import ContentValue
from specto_core.kinds import PluginManifest, ViewCost, ViewManifest
from specto_core.links import Selection

from ..hosts.base import HostContext, SimpleFactory
from ..theme import COLORS
from ..web import basemaps
from ..web.capabilities import TILE_HOSTS
from ..web.scheme import item_url
from ..web.webview import WebViewHost

FEATURES, SELECTION, TIME = "features", "selection", "time"
#: layer colours, cycled per layer index: the theme's link-chain tokens (ADR 0008: no
#: raw colour; the page cycles the same tokens from its CSS variables)
PALETTE: tuple[str, ...] = tuple(COLORS[f"chain_{i}"] for i in range(6))
PROBE_TIMEOUT_S = 1.5
#: how many layers load at first, and how many Load more adds (``params.batch``, #181)
BATCH = 200


MAP_MANIFEST = ViewManifest(
    type="map",
    label="Map",
    host="web",
    doc="MapLibre map: one layer per GeoJSON file on its features input, over a raster "
    "basemap when online; click a feature to select it (emitted as the selection).",
    guide="Each layer can be toggled, coloured and fitted; feature properties show on "
    "hover; the map frames the data on every reload. The basemap needs the network "
    "(net:tiles): none, osm (OpenStreetMap streets) or satellite (Esri World Imagery with "
    "place labels); offline the map shows none. The first limit files load (unset: batch, "
    "200); over a batch the footer shows loaded / total and Load more raises limit by a "
    "batch (a user submit).",
    accepts=["geojson"],
    cost=ViewCost(web_views=1),
    defaults={"layers": {}, "basemap": basemaps.DEFAULT},
    state_schema={
        "type": "object",
        "properties": {
            "layers": {
                "type": "object",
                "additionalProperties": True,
                "default": {},
                "description": "Per layer (its entry key): {visible, color}.",
            },
            "basemap": {
                "enum": list(basemaps.NAMES),
                "default": basemaps.DEFAULT,
                "description": "The tiles under the layers (osm: streets, satellite: "
                "imagery); needs the network.",
            },
            "panels": {
                "type": "object",
                "additionalProperties": True,
                "description": "The LAYERS side panel's {collapsed, width}, under layers.",
            },
            "batch": {
                "type": "integer",
                "minimum": 1,
                "default": BATCH,
                "description": "How many GeoJSON files load at first (when limit is unset) "
                "and how many the footer's Load more adds.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "How many GeoJSON files are loaded: the first limit of the "
                "features input (unset: batch). Load more raises it by batch; set it to "
                "load more or fewer at once.",
            },
        },
        "additionalProperties": True,
    },
    example={
        "command": "canvas.submit",
        "args": {"document": {"views": {"mihu": {"type": "map", "label": "candidates"}}}},
    },
)
MAP_PLUGIN = PluginManifest(name="app-map", version="0.2.0", views=[MAP_MANIFEST])


# -- tile reachability (once per host per process) ----------------------------------------

_tiles_reach: dict[str, bool] = {}
_tiles_lock = threading.Lock()


def probe_tiles(timeout: float = PROBE_TIMEOUT_S) -> dict[str, bool]:
    """Whether each tile host accepts a TCP connection (#157: per provider host, so
    one provider can be unreachable while another answers); cached per process."""
    out: dict[str, bool] = {}
    for host in TILE_HOSTS:
        with _tiles_lock:
            known = _tiles_reach.get(host)
        if known is None:
            try:
                with socket.create_connection((host, 443), timeout=timeout):
                    known = True
            except OSError:
                known = False
            with _tiles_lock:
                _tiles_reach[host] = known
        out[host] = known
    return out


def set_tiles_online(value: bool | dict[str, bool] | None) -> None:
    """Tests and offline demos: force the probe result for every tile host (a bool), for
    some (a dict by host), or forget it (None)."""
    with _tiles_lock:
        if value is None:
            _tiles_reach.clear()
        elif isinstance(value, dict):
            _tiles_reach.update(value)
        else:
            _tiles_reach.update(dict.fromkeys(TILE_HOSTS, bool(value)))


def tiles_online_cached() -> dict[str, bool] | None:
    """Every tile host's probe result, or None while one is unknown."""
    with _tiles_lock:
        if all(h in _tiles_reach for h in TILE_HOSTS):
            return {h: _tiles_reach[h] for h in TILE_HOSTS}
    return None


class MapViewHost(WebViewHost):
    page = "map/index.html"
    vendor_files = ("maplibre-gl/maplibre-gl.js", "maplibre-gl/maplibre-gl.css")
    tilesProbed = Signal(object)

    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> None:
        super().__init__(ctx, view_id, manifest)
        #: each tile host's probe result; None until the probe is back
        self.reach: dict[str, bool] | None = tiles_online_cached()
        self.features: ContentValue | None = None
        self.selection: Selection | None = None
        self.hovered: dict[str, Any] | None = None
        self.last_layers: list[dict[str, Any]] = []
        #: every GeoJSON file of the features input; last_layers holds the first limit()
        self.total = 0
        #: features deliveries: the page frames the data again on a new one (#181)
        self.revision = 0
        self.tilesProbed.connect(self._on_probed)
        self.set_health("paused", "no features")
        if self.reach is None and self.caps.can_net("tiles"):
            threading.Thread(target=self._probe, name="specto-map-probe", daemon=True).start()

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        self.refresh_grants()
        if port == FEATURES:
            self.features = value if isinstance(value, ContentValue) else None
            self.revision += 1  # a data reload: the page frames it
            self.send_layers()
            self._health()
        elif port == SELECTION:
            self.selection = value if isinstance(value, Selection) else None
            self.page.receive_port(port, value)
        elif port == TIME:
            self.page.receive_port(port, value)

    def _health(self) -> None:
        layers = self.last_layers
        if not layers:
            self.set_health("paused", "no GeoJSON files" if self.features else "no features")
            return
        bad = [lay for lay in layers if lay["error"]]
        if len(bad) == len(layers):
            self.set_health("error", bad[0]["error"])
        else:
            n = len(layers)
            msg = f"{n:,} layer{'s' if n != 1 else ''}"
            if self.total > n:
                msg = f"{n:,} of {self.total:,} layers"
            self.set_health("ok", msg + (f", {len(bad)} unreadable" if bad else ""))

    # -- features -> layers ----------------------------------------------------------------
    def batch(self) -> int:
        """How many layers Load more adds (``params.batch``, :data:`BATCH` by default)."""
        return _count(self.page.params.get("batch"), BATCH)

    def limit(self) -> int:
        """How many layers load (``params.limit``; unset: the batch)."""
        return _count(self.page.params.get("limit"), self.batch())

    def layers(self) -> list[dict[str, Any]]:
        """One entry per GeoJSON file of the features input, the first :meth:`limit` of
        them (#181), with the params' visibility and colour merged in."""
        overrides = self.page.params.get("layers") or {}
        out: list[dict[str, Any]] = []
        total = 0
        if self.features is not None:
            limit = self.limit()
            for e in self.features.entries:
                if e.format != "geojson":
                    continue
                total += 1
                if len(out) >= limit:
                    continue
                ov = overrides.get(e.key) if isinstance(overrides, dict) else None
                ov = ov if isinstance(ov, dict) else {}
                out.append(
                    {
                        "id": e.key,
                        "key": e.key,
                        "label": Path(e.key).name,
                        "url": item_url(self.features.link, e.key, Path(e.path).suffix),
                        "visible": ov.get("visible", True) is not False,
                        "color": ov.get("color") or PALETTE[len(out) % len(PALETTE)],
                        "features": e.meta.get("features"),
                        "bbox": e.meta.get("bbox"),
                        "error": e.error,
                    }
                )
        self.last_layers = out
        self.total = total
        return out

    @property
    def online(self) -> bool | None:
        """Any tile host answered (None: not probed yet)."""
        return None if self.reach is None else any(self.reach.values())

    def reachable(self, provider: basemaps.Basemap) -> bool | None:
        """Every host of the provider answered (None: not probed yet)."""
        if self.reach is None:
            return None
        return all(self.reach.get(h, False) for h in provider.hosts())

    def basemap(self) -> str:
        """The provider shown: the one ``params.basemap`` names (``osm`` by default) when
        the view holds ``net:tiles`` and that provider's hosts answer; ``none`` otherwise,
        and for a name no provider has."""
        want = self.page.params.get("basemap") or basemaps.DEFAULT
        provider = basemaps.by_name(want)
        if provider and self.caps.can_net("tiles") and self.reachable(provider):
            return want
        return basemaps.NONE

    def layers_payload(self) -> dict[str, Any]:
        return {
            "layers": self.layers(),
            "basemap": self.basemap(),
            "basemaps": [
                {**b.payload(), "reachable": self.reachable(b)} for b in basemaps.BASEMAPS
            ],
            "online": self.online,
            "net_tiles": self.caps.can_net("tiles"),
            "link": self.features.link if self.features is not None else None,
            "total": self.total,
            "limit": self.limit(),
            "batch": self.batch(),
            "revision": self.revision,
        }

    def send_layers(self) -> dict[str, Any]:
        payload = self.layers_payload()
        self.notify("layers", payload)
        return payload

    # -- probe ----------------------------------------------------------------------------
    def _probe(self) -> None:
        self.tilesProbed.emit(probe_tiles())

    @Slot(object)
    def _on_probed(self, reach: dict[str, bool]) -> None:
        self.reach = dict(reach)
        if self.page.ready:
            self.send_layers()

    # -- hooks ---------------------------------------------------------------------------
    def on_ready(self) -> None:
        self.send_layers()
        self._health()
        if self.selection is not None:
            self.page.receive_port(SELECTION, self.selection)

    def on_params_changed(self, params: dict[str, Any]) -> None:
        self.send_layers()
        self._health()

    def on_page_event(self, kind: str, payload: Any) -> None:
        if kind == "hover":
            self.hovered = payload if isinstance(payload, dict) else None

    def snapshot(self) -> dict[str, Any]:
        snap = super().snapshot()
        emitted = self.ctx.links.output_of(self.view_id, SELECTION)
        selected = emitted if isinstance(emitted, Selection) else self.selection
        snap.update(
            {
                "layers": [
                    {k: lay[k] for k in ("id", "visible", "color", "features")}
                    for lay in self.layers()
                ],
                "total": self.total,
                "limit": self.limit(),
                "batch": self.batch(),
                "basemap": self.basemap(),
                "online": self.online,
                "selected": selected.model_dump() if selected is not None else None,
                "hovered": self.hovered,
            }
        )
        return snap


def _count(value: Any, default: int) -> int:
    """A positive whole number from params, else ``default``."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def factory() -> SimpleFactory:
    return SimpleFactory("map", MapViewHost, plugin=MAP_PLUGIN)


def register(registry: Any) -> None:
    """``specto.view_hosts`` entry point."""
    registry.add_factory(factory())


__all__ = [
    "BATCH",
    "FEATURES",
    "MAP_MANIFEST",
    "MAP_PLUGIN",
    "PALETTE",
    "SELECTION",
    "MapViewHost",
    "factory",
    "probe_tiles",
    "register",
    "set_tiles_online",
]
