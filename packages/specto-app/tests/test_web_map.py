"""Seam 2 (#46): the ``map`` host on ports, headless. ``features`` (a read contents
link's GeoJSON files) becomes one layer per file; ``selection`` in highlights, a feature
click on the page goes out on ``selection``; layer visibility, colour and the basemap
are the view's own params written by the page; hover is reported for ``snap``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web_helpers import Sink, follow, new_engine, registry, tree_emits_all

pytest.importorskip("PySide6.QtCore")

from specto_app.views.map_view import (
    BATCH,
    FEATURES,
    PALETTE,
    MapViewHost,
    probe_tiles,
    set_tiles_online,
    tiles_online_cached,
)
from specto_app.web.basemaps import BASEMAPS as MAP_BASEMAPS
from specto_core.contents import ContentEntry, ContentValue
from specto_core.links import Selection


def compose_maps(engine) -> None:
    """A map ``kobi`` drawing the geo folder, and a second map ``tamu`` following its selection."""
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "map", "label": "Geo", "layout": {"size": [800, 600]}}
    doc["views"]["tamu"] = {"type": "map", "label": "Twin", "layout": {"position": [820, 0]}}
    doc["links"]["l2"] = {
        "kind": "read_contents",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "features"},
        "params": {"sources": ["geo"]},
        "summary": {"sentence": "The map draws every GeoJSON file under geo/."},
    }
    doc["links"]["l3"] = follow("kobi", "selection", "tamu", "selection")
    engine.live.submit(doc, origin="agent:s1", label="maps")


@pytest.fixture(autouse=True)
def offline():
    set_tiles_online(False)
    yield
    set_tiles_online(None)


def test_map_layers_come_from_the_features_input_and_the_params(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_maps(engine)
    reg = registry(engine)
    h = reg.host("kobi")
    assert isinstance(h, MapViewHost)
    sink = Sink(h.bridge)
    h.bridge.ready()
    cfg = sink.configured[0]
    assert cfg["capabilities"] == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:selection",
        "net:tiles",
        "params:basemap",
        "params:layers",
        "params:limit",
        "params:panels",
        "read:link:l2",
    ]
    # the read contents link off the project source resolved at submit (#83): the files
    # are already on `features`
    h.flush()
    lay = sink.last("layers")
    assert lay["link"] == "l2" and [x["id"] for x in lay["layers"]] == ["geo/points", "geo/route"]
    assert lay["layers"][0] == {
        "id": "geo/points",
        "key": "geo/points",
        "label": "points",
        "url": "specto://items/l2/geo%2Fpoints/geojson",
        "visible": True,
        "color": PALETTE[0],
        "features": 2,
        "bbox": [-122.12, 47.62, -122.1, 47.64],
        "error": None,
    }
    assert lay["layers"][1]["features"] == 1 and lay["layers"][1]["color"] == PALETTE[1]
    assert h.ctx.health_of("kobi") == {"status": "ok", "message": "2 layers", "hint": ""}

    # the page writes layer overrides and the basemap as the view's own params
    res = json.loads(
        h.bridge.setParams(
            json.dumps({"layers": {"geo/points": {"visible": False, "color": "#123456"}}})
        )
    )
    assert res["ok"] and engine.live.history[-1].origin == "user"
    h.flush()
    lay = sink.last("layers")
    assert lay["layers"][0]["visible"] is False and lay["layers"][0]["color"] == "#123456"
    assert lay["layers"][1]["visible"] is True
    snap = h.snapshot()
    assert snap["layers"] == [
        {"id": "geo/points", "visible": False, "color": "#123456", "features": 2},
        {"id": "geo/route", "visible": True, "color": PALETTE[1], "features": 1},
    ]
    assert (
        snap["basemap"] == "none" and snap["params"]["layers"]["geo/points"]["color"] == "#123456"
    )

    # online with net:tiles: osm; the params can still turn it off
    set_tiles_online(True)
    h.reach = tiles_online_cached()
    assert h.basemap() == "osm"
    assert json.loads(h.bridge.setParams(json.dumps({"basemap": "none"})))["ok"]
    h.flush()
    assert h.basemap() == "none" and sink.last("layers")["basemap"] == "none"
    # satellite (#157): the page gets the provider table and draws the one shown; a name
    # no provider has shows none
    assert json.loads(h.bridge.setParams(json.dumps({"basemap": "satellite"})))["ok"]
    h.flush()
    lay = sink.last("layers")
    assert h.basemap() == "satellite" and lay["basemap"] == "satellite"
    assert [b["name"] for b in lay["basemaps"]] == ["osm", "satellite"]
    sat = lay["basemaps"][1]
    assert "World_Imagery" in sat["tiles"][0] and "Esri" in sat["attribution"]
    assert json.loads(h.bridge.setParams(json.dumps({"basemap": "moon"})))["ok"]
    assert h.basemap() == "none"

    # the LAYERS panel's collapse and width are the view's own `panels` param (#130)
    panels = {"layers": {"collapsed": True, "width": 240}}
    res = json.loads(h.bridge.setParams(json.dumps({"panels": panels})))
    assert res["ok"] and engine.live.data["views"]["kobi"]["params"]["panels"] == panels

    # the link goes away: no layers, nothing to draw
    h.receive("features", None)
    h.flush()
    assert sink.last("layers")["layers"] == [] and h.ctx.health_of("kobi")["status"] == "paused"


def test_each_provider_is_judged_by_its_own_tile_hosts(tmp_path: Path):
    """#157: OSM answering says nothing about Esri. With Esri's host down, satellite is
    unreachable (the page disables it and says why) and shows none; OSM still shows."""
    set_tiles_online({"tile.openstreetmap.org": True, "server.arcgisonline.com": False})
    # the probe keeps what it knows per host: nothing goes out for these two
    assert probe_tiles(timeout=0.001) == {
        "tile.openstreetmap.org": True,
        "server.arcgisonline.com": False,
    }
    engine, _ = new_engine(tmp_path)
    compose_maps(engine)
    h = registry(engine).host("kobi")
    sink = Sink(h.bridge)
    h.bridge.ready()
    h.flush()
    rows = {b["name"]: b["reachable"] for b in sink.last("layers")["basemaps"]}
    assert rows == {"osm": True, "satellite": False} and h.online is True
    assert h.basemap() == "osm"
    assert json.loads(h.bridge.setParams(json.dumps({"basemap": "satellite"})))["ok"]
    h.flush()
    assert h.basemap() == "none" and sink.last("layers")["basemap"] == "none"
    # not probed yet: nothing is judged, nothing shows
    h.reach = None
    assert h.reachable(MAP_BASEMAPS[0]) is None and h.online is None
    assert h.basemap() == "none"


def test_map_click_emits_the_selection_and_hover_is_reported(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_maps(engine)
    reg = registry(engine)
    h, twin = reg.host("kobi"), reg.host("tamu")
    s2 = Sink(twin.bridge)
    twin.bridge.ready()
    tree_emits_all(engine)

    # a feature click on the page: emitted on selection, the twin map receives it
    res = json.loads(
        h.bridge.emit("selection", json.dumps({"keys": ["geo/points#1"], "last": "geo/points#1"}))
    )
    assert res["ok"] and res["result"] == {"links": ["l3"]}
    assert engine.links.output_of("kobi", "selection") == Selection(
        keys=["geo/points#1"], last="geo/points#1"
    )
    twin.flush()
    assert s2.last_port("selection") == {"keys": ["geo/points#1"], "last": "geo/points#1"}
    assert h.snapshot()["selected"] == {"keys": ["geo/points#1"], "last": "geo/points#1"}
    assert twin.snapshot()["selected"] == {"keys": ["geo/points#1"], "last": "geo/points#1"}
    # a bad value is refused by the port type
    res = json.loads(h.bridge.emit("selection", json.dumps({"keys": "x"})))
    assert not res["ok"] and res["error"]["code"] == "invalid_args"

    # hovering a feature shows its properties; the host reports it for snap
    h.bridge.send("hover", json.dumps({"id": "geo/points#0", "properties": {"name": "depot"}}))
    assert h.snapshot()["hovered"] == {"id": "geo/points#0", "properties": {"name": "depot"}}
    h.bridge.send("hover", "null")
    assert h.snapshot()["hovered"] is None


def many_geojson(n: int, link: str = "l2") -> ContentValue:
    """A features value of ``n`` GeoJSON files (and one text file that is no layer)."""
    entries = [
        ContentEntry(
            key=f"big/f{i:04d}",
            path=f"big/f{i:04d}.geojson",
            format="geojson",
            meta={"features": 1, "bbox": [i, 0, i + 1, 1]},
        )
        for i in range(n)
    ]
    entries.append(ContentEntry(key="big/readme", path="big/readme.txt", format="text"))
    return ContentValue(root="", entries=entries, link=link)


def test_over_a_batch_the_map_sends_the_first_limit_and_the_total(tmp_path: Path):
    """#181: over 200 GeoJSON files the page gets the first 200 and the total; the
    page's Load more writes ``limit`` (a user submit) and gets the next batch; the agent
    sets ``batch`` (and ``limit``) in params."""
    engine, _ = new_engine(tmp_path)
    compose_maps(engine)
    h = registry(engine).host("kobi")
    sink = Sink(h.bridge)
    h.bridge.ready()
    h.flush()
    lay = sink.last("layers")
    assert (lay["total"], lay["limit"], lay["batch"]) == (2, BATCH, BATCH) and BATCH == 200

    h.receive(FEATURES, many_geojson(1350))
    h.flush()
    lay = sink.last("layers")
    assert len(lay["layers"]) == 200 and lay["total"] == 1350
    assert [x["id"] for x in lay["layers"][:2]] == ["big/f0000", "big/f0001"]
    assert lay["layers"][199]["id"] == "big/f0199"
    assert lay["layers"][7]["color"] == PALETTE[7 % len(PALETTE)]
    assert h.ctx.health_of("kobi")["message"] == "200 of 1,350 layers"
    snap = h.snapshot()
    assert len(snap["layers"]) == 200
    assert (snap["total"], snap["limit"], snap["batch"]) == (1350, 200, 200)

    # Load more: the page writes limit up by a batch, as the view's own param
    res = json.loads(h.bridge.setParams(json.dumps({"limit": 400})))
    assert res["ok"] and engine.live.history[-1].origin == "user"
    h.flush()
    lay = sink.last("layers")
    assert len(lay["layers"]) == 400 and lay["limit"] == 400 and lay["total"] == 1350
    assert h.ctx.health_of("kobi")["message"] == "400 of 1,350 layers"

    # the agent sets the batch and drops the limit: the first batch loads
    doc = engine.live.document()
    doc["views"]["kobi"]["params"] = {"layers": {}, "basemap": "osm", "batch": 50}
    engine.live.submit(doc, origin="agent:s1", label="smaller batches")
    h.flush()
    lay = sink.last("layers")
    assert len(lay["layers"]) == 50 and (lay["limit"], lay["batch"]) == (50, 50)
    # a limit over the total loads everything
    doc["views"]["kobi"]["params"]["limit"] = 5000
    engine.live.submit(doc, origin="agent:s1", label="everything")
    h.flush()
    lay = sink.last("layers")
    assert len(lay["layers"]) == 1350 and lay["total"] == 1350
    assert h.ctx.health_of("kobi")["message"] == "1,350 layers"


def test_every_features_delivery_is_a_new_revision_the_page_frames(tmp_path: Path):
    """#181: a data reload (a refresh, a watched arrival, a link re-resolve) reaches the
    host as a features delivery and bumps the payload's revision; a params change (a
    colour, the basemap, Load more) keeps it, so the page keeps the user's camera."""
    engine, _ = new_engine(tmp_path)
    compose_maps(engine)
    h = registry(engine).host("kobi")
    sink = Sink(h.bridge)
    h.bridge.ready()
    h.flush()
    first = sink.last("layers")["revision"]
    assert first >= 1

    assert json.loads(h.bridge.setParams(json.dumps({"basemap": "none"})))["ok"]
    h.flush()
    assert sink.last("layers")["revision"] == first

    h.receive(FEATURES, many_geojson(3))
    h.flush()
    assert sink.last("layers")["revision"] == first + 1
    # coalesced notifies keep the newest revision: a reload then a params change before
    # the page hears either still reads as a reload
    h.receive(FEATURES, many_geojson(4))
    assert json.loads(h.bridge.setParams(json.dumps({"limit": 2})))["ok"]
    h.flush()
    lay = sink.last("layers")
    assert lay["revision"] == first + 2 and len(lay["layers"]) == 2 and lay["total"] == 4

    # the link resolving again with new contents on disk is such a delivery
    geo = Path(engine.project.root) / "geo" / "points.geojson"
    data = json.loads(geo.read_text(encoding="utf-8"))
    data["features"][0]["geometry"]["coordinates"] = [-120.0, 45.0]
    geo.write_text(json.dumps(data), encoding="utf-8")
    engine.links.resolve("l2")
    h.flush()
    lay = sink.last("layers")
    assert lay["revision"] == first + 3
    assert lay["layers"][0]["id"] == "geo/points"
    assert lay["layers"][0]["bbox"] == [-122.1, 45.0, -120.0, 47.62]
