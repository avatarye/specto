"""How ``specto://items/<link>/<key>/<role>`` finds its bytes: the values on the link
runtime's input ports (collections and read contents), by the link that delivered them;
plus the in-memory table, display names, asset location and theme flattening."""

from __future__ import annotations

import os
from pathlib import Path

from web_helpers import compose_json, new_engine, tree_emits_all

from specto_app.theme import theme_for_web
from specto_app.web import assets
from specto_app.web.items import (
    CompositeItems,
    ItemTable,
    LinkItems,
    display_name,
    display_names,
    pick_role,
)
from specto_core.kinds import Item


def test_link_items_follow_the_values_on_the_runtime(tmp_path: Path):
    engine, proj = new_engine(tmp_path, frames=3)
    ids = compose_json(engine)
    items = LinkItems(engine.links)
    assert items.keys("l3") == [] and items.parts("l3", "00001") is None  # nothing resolved yet

    tree_emits_all(engine)
    assert items.keys("l3") == ["00000", "00001", "00002"]
    parts = items.parts("l3", "00001")
    assert set(parts) == {".json"} and parts[".json"].endswith("00001_1700000000060000000.json")
    assert Path(parts[".json"]).is_absolute()
    assert items.parts("l3", "00009") is None and items.parts("l9", "00001") is None
    # the file tree's own collection is the root alone (#231): no items to reach
    assert items.keys("l1") == [] and items.parts("l1", "notes/alpha") is None

    # read contents: one role per entry, the file's extension
    doc = engine.live.document()
    doc["views"]["rena"] = {"type": "map", "label": "geo", "layout": {"position": [0, 500]}}
    doc["links"]["l5"] = {
        "kind": "read_contents",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "rena", "port": "features"},
        "params": {"sources": ["geo"]},
        "summary": {"sentence": "The map draws the geo folder."},
    }
    engine.live.submit(doc, origin="agent:s1", label="map")
    assert items.keys("l5") == ["geo/points", "geo/readme", "geo/route"]
    assert items.parts("l5", "geo/points") == {
        ".geojson": str(proj.root / "geo" / "points.geojson")
    }
    # a link that goes away takes its items with it
    del doc["views"]["rena"]
    del doc["links"]["l5"]
    engine.live.submit(doc, origin="user", label="close")
    assert items.keys("l5") == [] and ids["data"] == "l3"


def test_item_table_and_pick_role():
    t = ItemTable()
    t.set_items(
        "l1",
        [
            Item(key="00001", parts={".jpg": "/a.jpg", ".json": "/a.json"}),
            {"key": "00002", "parts": {".jpg": "/b.jpg"}},
        ],
    )
    assert t.keys("l1") == ["00001", "00002"] and t.cols() == ["l1"]
    assert t.parts("l1", "00002") == {".jpg": "/b.jpg"}
    assert pick_role(t.parts("l1", "00001"), [".geojson", ".json"]) == ".json"
    assert pick_role(t.parts("l1", "00002"), [".json"]) is None


def test_composite_first_match_wins():
    a, b = ItemTable(), ItemTable()
    a.set_items("l1", [{"key": "1", "parts": {".json": "/a"}}])
    b.set_items(
        "l1", [{"key": "1", "parts": {".json": "/b"}}, {"key": "2", "parts": {".json": "/b2"}}]
    )
    c = CompositeItems([a])
    c.add(b)
    c.add(b)
    assert len(c.resolvers) == 2
    assert c.parts("l1", "1") == {".json": "/a"} and c.parts("l1", "2") == {".json": "/b2"}
    assert c.keys("l1") == ["1"] and c.keys("zz") == [] and c.parts("zz", "1") is None
    c.remove(a)
    assert c.parts("l1", "1") == {".json": "/b"}


def test_display_names_strip_namespace_and_disambiguate(tmp_path: Path):
    items = [
        ("1:alpha", str(tmp_path / "notes" / "alpha.md")),
        ("0:front", str(tmp_path / "notes" / "img" / "front.png")),
        ("1:front", str(tmp_path / "notes" / "front.md")),
        ("plain", str(tmp_path / "plain.md")),
    ]
    assert display_name("1:alpha") == "alpha" and display_name("plain") == "plain"
    assert display_names(items) == {
        "1:alpha": "alpha",
        "0:front": "img/front",
        "1:front": "notes/front",
        "plain": "plain",
    }
    assert display_names([]) == {}


def test_web_root_and_monaco(monkeypatch, tmp_path):
    root = assets.web_root()
    assert (root / "bridge.js").is_file() and (root / "json" / "index.html").is_file()
    assert assets.monaco_ok(root) == (root / "vendor" / "vs" / "loader.js").is_file()
    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path))
    assert assets.web_root() == root  # the override must hold bridge.js to count
    (tmp_path / "bridge.js").write_text("//")
    assert assets.web_root() == tmp_path
    assert not assets.monaco_ok(tmp_path)
    try:
        assets.require_monaco()
    except Exception as e:
        assert "build.py" in str(e)
    else:
        raise AssertionError("expected a fix-it error")
    assert "SPECTO_WEB_ROOT" in os.environ


def test_theme_for_web_is_what_the_pages_expect():
    t = theme_for_web()
    assert t["color-surface"] == "#ffffff" and t["metric-pad"] == "12px"
    assert t["metric-anim-ms"] == "180" and t["metric-ghost-alpha"] == "0.45"
    assert t["font-mono"] and t["font-ui"] and t["glyph-added"] == "+" and t["theme-name"]
