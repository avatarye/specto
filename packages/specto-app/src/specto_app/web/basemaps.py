"""The map view's basemap providers (#157): the one table of raster tile servers a map
can show under its layers. ``params.basemap`` names one of them, or ``none``.

Pure (no Qt). The table is the only place a provider is described: the hosts its URL
templates name are the ``net:tiles`` allow-list (``capabilities.TILE_HOSTS``, which the
request interceptor and the capability badge consult), the map host sends the rows to
the page (``Basemap.payload``), and the page builds its footer switcher and its MapLibre
raster sources from them. A user URL template (#22) is one more row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

#: ``params.basemap`` when the map shows no tiles, only its background
NONE = "none"


@dataclass(frozen=True)
class Basemap:
    #: the ``params.basemap`` value
    name: str
    #: the footer switcher's text
    label: str
    #: raster tile URL templates (``{z}``, ``{x}``, ``{y}``), drawn bottom up
    tiles: tuple[str, ...]
    #: the credit the provider's terms require, shown in the map's attribution
    attribution: str
    max_zoom: int = 19
    #: raster templates drawn over ``tiles`` (place and boundary labels on imagery)
    overlays: tuple[str, ...] = field(default=())

    def hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(urlsplit(t).hostname or "" for t in self.tiles + self.overlays))

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "tiles": list(self.tiles),
            "overlays": list(self.overlays),
            "hosts": list(self.hosts()),
            "attribution": self.attribution,
            "max_zoom": self.max_zoom,
        }


ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services"

BASEMAPS: tuple[Basemap, ...] = (
    Basemap(
        name="osm",
        label="OSM",
        tiles=("https://tile.openstreetmap.org/{z}/{x}/{y}.png",),
        attribution="&copy; OpenStreetMap contributors",
    ),
    # Esri World Imagery: no key; its terms ask for the Esri credit on the map
    Basemap(
        name="satellite",
        label="Satellite",
        tiles=(f"{ESRI}/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}",),
        overlays=(
            f"{ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}",
        ),
        attribution="Powered by Esri &middot; Source: Esri, Maxar, Earthstar Geographics, "
        "and the GIS User Community",
    ),
)

#: every value ``params.basemap`` takes, in the switcher's order
NAMES: tuple[str, ...] = (NONE, *(b.name for b in BASEMAPS))
DEFAULT = "osm"


def by_name(name: str) -> Basemap | None:
    return next((b for b in BASEMAPS if b.name == name), None)


def tile_hosts() -> tuple[str, ...]:
    """Every host a provider's templates name, the first provider's first."""
    return tuple(dict.fromkeys(h for b in BASEMAPS for h in b.hosts()))
