"""The basemap provider table (#157): one place for the map's raster tile servers; the
``net:tiles`` allow-list and the map's ``basemap`` param follow it."""

from __future__ import annotations

from specto_app.views.map_view import MAP_MANIFEST
from specto_app.web import basemaps
from specto_app.web.capabilities import TILE_HOSTS


def test_the_providers_are_none_osm_and_satellite_and_name_the_tile_hosts():
    assert basemaps.NAMES == ("none", "osm", "satellite")
    assert TILE_HOSTS == ("tile.openstreetmap.org", "server.arcgisonline.com")
    sat = basemaps.by_name("satellite")
    assert sat is not None and sat.hosts() == ("server.arcgisonline.com",)
    assert sat.tiles[0].endswith("/World_Imagery/MapServer/tile/{z}/{y}/{x}")
    assert sat.overlays and "Esri" in sat.attribution
    assert basemaps.by_name("none") is None


def test_the_map_param_schema_offers_every_provider():
    prop = MAP_MANIFEST.state_schema["properties"]["basemap"]
    assert prop["enum"] == list(basemaps.NAMES) and prop["default"] == "osm"
    assert MAP_MANIFEST.defaults["basemap"] == "osm"
