"""Seam 2 (#46): the ``json`` and ``markdown`` hosts on ports, headless. ``data`` is
the collection their intaker resolved, ``selection`` picks the item to show; the page
gets an item URL on the link's items host; the shown file goes out on ``content``; a
note picked inside the markdown view goes out on ``selection``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web_helpers import Sink, compose_json, follow, intaker, new_engine, registry, tree_emits_all

pytest.importorskip("PySide6.QtCore")

from specto_app.views.json_view import JsonViewHost, outline_of
from specto_app.views.markdown_view import MarkdownViewHost, headings_of
from specto_app.web.runtime import runtime
from specto_core.contents import ContentEntry
from specto_core.links import ContentValue, Selection


def test_json_view_shows_the_selected_item_and_emits_its_content(tmp_path: Path):
    engine, proj = new_engine(tmp_path, frames=3)
    compose_json(engine)
    reg = registry(engine)
    h = reg.host("tamu")
    assert isinstance(h, JsonViewHost) and h.current_key is None
    sink = Sink(h.bridge)
    h.bridge.ready()
    assert sink.last("item") == {"key": None, "url": None}
    assert h.ctx.health_of("tamu") == {"status": "ok", "message": "(no item selected)", "hint": ""}

    tree_emits_all(engine)  # the intaker resolves: the collection lands on `data`
    h.flush()
    item = sink.last("item")
    assert item["key"] == "00000" and item["url"] == "specto://items/l3/00000/json"
    assert item["role"] == ".json" and item["roles"] == [".json"] and "path" not in item
    assert item["name"] == "00000" and item["file"] == "00000_1700000000000000000.json"
    content = engine.links.output_of("tamu", "content")
    assert isinstance(content, ContentValue) and content.root == str(proj.root)
    assert [e.path for e in content.entries] == ["runs/cam-a/00000_1700000000000000000.json"]
    assert content.entries[0].format == "json" and content.entries[0].data["frame"] == 0

    # the gallery's selection reaches the json view through the follow link
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))
    h.flush()
    assert sink.last("item")["key"] == "00002"
    assert engine.links.output_of("tamu", "content").entries[0].data["frame"] == 2
    assert engine.links.output_of("tamu", "selection") is None  # shown, never republished
    snap = h.snapshot()
    assert snap["item"] == "00002" and snap["url"] == "specto://items/l3/00002/json"
    assert snap["outline"][:3] == ["frame: number", "camera: string", "stamp_ns: number"]
    assert snap["count"] == 3 and snap["inputs"] == {"data": "l3", "selection": "l4"}
    assert snap["capabilities"] == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:content",
        "emit:selection",
        "params:wrap",
        "read:link:l3",
        "read:link:l4",
    ]
    assert runtime().items.parts("l3", "00002")[".json"].endswith("00002_1700000000120000000.json")

    # a key the collection does not have is reported, the shown item stays
    engine.links.emit("kobi", "selection", Selection(keys=["00009"], last="00009"))
    h.flush()
    item = sink.last("item")
    assert item["key"] == "00009" and item["url"] is None and "not in" in item["reason"]
    assert h.current_key == "00002"
    # the collection goes away: nothing to show, nothing on the content port
    h.receive("data", None)
    h.flush()
    assert sink.last("item") == {"key": None, "url": None}
    assert engine.links.output_of("tamu", "content").entries == []
    assert h.ctx.health_of("tamu")["status"] == "paused"


def test_json_view_remembers_a_selection_that_arrives_before_its_data(tmp_path: Path):
    engine, _ = new_engine(tmp_path, frames=3)
    compose_json(engine)
    reg = registry(engine)
    h = reg.host("tamu")
    engine.links.emit("kobi", "selection", Selection(keys=["00001"], last="00001"))
    assert h.current_key is None
    tree_emits_all(engine)
    assert h.current_key == "00001" and h.current_url == "specto://items/l3/00001/json"


def test_outline_of(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"a": 1, "b": "x", "c": [1, 2], "d": {"k": 1}, "e": None, "f": True}))
    assert outline_of(p) == [
        "a: number",
        "b: string",
        "c: array[2]",
        "d: object{1}",
        "e: null",
        "f: bool",
    ]
    p.write_text(json.dumps({f"k{i}": i for i in range(30)}))
    out = outline_of(p)
    assert len(out) == 25 and out[-1] == "... 6 more"
    p.write_text("[1,2,3]")
    assert outline_of(p) == ["array[3]"]
    p.write_text("not json")
    assert outline_of(p) == []
    assert outline_of(tmp_path / "missing.json") == []


def test_hosts_are_disposed_with_their_views(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    reg = registry(engine)
    h = reg.host("tamu")
    assert runtime().caps_of("tamu") is not None
    doc = engine.live.document()
    del doc["views"]["tamu"]
    del doc["links"]["l3"]
    del doc["links"]["l4"]
    engine.live.submit(doc, origin="user", label="close")
    assert h.disposed and runtime().caps_of("tamu") is None and "tamu" not in reg.hosts


# -- markdown ------------------------------------------------------------------------------
def compose_notes(engine) -> None:
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "markdown", "label": "Notes", "layout": {"size": [600, 500]}}
    link = intaker("selected_files", "kobi", ["notes"], ["@markdown"])
    del link["params"]["keys"]  # keys are the note names
    doc["links"]["l2"] = link
    engine.live.submit(doc, origin="agent:s1", label="the notes")


def test_markdown_host_lists_notes_shows_the_first_and_follows_a_wikilink(tmp_path: Path):
    engine, proj = new_engine(tmp_path)
    compose_notes(engine)
    reg = registry(engine)
    h = reg.host("kobi")
    assert isinstance(h, MarkdownViewHost)
    sink = Sink(h.bridge)
    h.bridge.ready()
    assert sink.last("notes") == {"notes": []}
    tree_emits_all(engine)
    h.flush()
    notes = sink.last("notes")["notes"]
    assert notes == [
        {"key": "alpha", "name": "alpha", "url": "specto://items/l2/alpha/md", "role": ".md"},
        {"key": "beta", "name": "beta", "url": "specto://items/l2/beta/md", "role": ".md"},
    ]
    assert sink.last("item")["key"] == "alpha"  # nothing picked yet: the first note
    content = engine.links.output_of("kobi", "content")
    assert content.entries[0].path == "notes/alpha.md" and content.entries[0].format == "text"
    assert content.entries[0].data.startswith("# Alpha")

    # the page follows a wikilink: the host shows the note, emits it as the selection
    # (so linked views follow) and snapshots its outline
    h.bridge.send("navigate", json.dumps({"key": "beta", "anchor": ""}))
    h.flush()
    assert h.current_key == "beta" and sink.last("item")["url"] == "specto://items/l2/beta/md"
    assert engine.links.output_of("kobi", "selection") == Selection(keys=["beta"], last="beta")
    assert engine.links.output_of("kobi", "content").entries[0].path == "notes/beta.md"
    snap = h.snapshot()
    assert snap["outline"] == ["# Beta"] and snap["notes"] == ["alpha", "beta"]
    assert snap["capabilities"] == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:content",
        "emit:selection",
        "params:panels",
        "read:link:l2",
    ]
    assert headings_of(proj.root / "notes" / "alpha.md") == ["# Alpha", "## Part"]

    # the outline panel's collapse and width are the view's own `panels` param (#130),
    # written as a user submit
    panels = {"outline": {"collapsed": True, "width": 240}}
    res = json.loads(h.bridge.setParams(json.dumps({"panels": panels})))
    assert res["ok"] and engine.live.data["views"]["kobi"]["params"] == {"panels": panels}
    assert engine.live.history[-1].origin == "user"
    res = json.loads(h.bridge.setParams(json.dumps({"basemap": "none"})))
    assert not res["ok"] and res["error"]["code"] == "capability"


def test_the_reading_zoom_is_remembered_as_a_view_param_and_the_agent_may_set_it(tmp_path):
    """#202: the page's ``zoom {level}`` event lands in ``params.zoom`` as navigation
    (no undo step), clamped to the page's range; the page gets it back as params, as it
    gets a level the agent sets; a fresh view has none (the page reads that as 1)."""
    engine, _proj = new_engine(tmp_path)
    compose_notes(engine)
    reg = registry(engine)
    h = reg.host("kobi")
    assert isinstance(h, MarkdownViewHost)
    sink = Sink(h.bridge)
    h.bridge.ready()
    assert "zoom" not in sink.configured[-1]["params"]
    steps = len(engine.live.history)

    h.bridge.send("zoom", json.dumps({"level": 1.21}))
    h.flush()
    assert engine.live.data["views"]["kobi"]["params"] == {"zoom": 1.21}
    assert len(engine.live.history) == steps  # navigation, not an edit
    assert list(engine.live.journal.read())[-1].kind == "navigate"
    assert sink.params[-1]["zoom"] == 1.21

    # out of range or no number: clamped, or left alone
    h.bridge.send("zoom", json.dumps({"level": 9}))
    assert engine.live.data["views"]["kobi"]["params"]["zoom"] == 3
    h.bridge.send("zoom", json.dumps({"level": "big"}))
    h.bridge.send("zoom", json.dumps({"level": True}))
    assert engine.live.data["views"]["kobi"]["params"]["zoom"] == 3
    h.bridge.send("zoom", json.dumps({"level": 3}))  # the same again: nothing written
    assert list(engine.live.journal.read())[-1].patch[-1]["value"] == 3

    # the agent sets it: one submit, the page gets the level with its params
    doc = engine.live.document()
    doc["views"]["kobi"]["params"]["zoom"] = 2
    engine.live.submit(doc, origin="agent:s1", label="larger notes")
    h.flush()
    assert sink.params[-1]["zoom"] == 2 and len(engine.live.history) == steps + 1
    assert h.snapshot()["params"]["zoom"] == 2


# -- html ----------------------------------------------------------------------------------
def compose_deck(engine) -> None:
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "html", "label": "Deck", "layout": {"size": [800, 600]}}
    link = intaker("selected_files", "kobi", ["deck"], ["@html"])
    del link["params"]["keys"]
    doc["links"]["l2"] = link
    engine.live.submit(doc, origin="agent:s1", label="the deck")


def test_html_host_shows_the_first_page_in_its_own_items_origin_and_steps(tmp_path):
    """Seam 2: the html view's page gets the picked (or first) .html file as a URL on
    the view's own ``<view>.items`` origin, ending in the file's name so the page's
    relative assets resolve next to it; the footer's step (#130: no file list) shows
    the next html file and emits it as the selection."""
    from urllib.parse import quote

    from web_helpers import make_deck

    from specto_app.views.html_view import HtmlViewHost
    from specto_app.web.scheme import locate_item

    engine, proj = new_engine(tmp_path)
    make_deck(proj.root / "deck")
    engine.links.resolve("l1")  # the project scan ran before the deck was written
    compose_deck(engine)
    reg = registry(engine)
    h = reg.host("kobi")
    assert isinstance(h, HtmlViewHost)
    sink = Sink(h.bridge)
    h.bridge.ready()
    assert sink.configured[0]["capabilities"] == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:selection",
        "net:web",
        "read:link:l2",
    ]
    tree_emits_all(engine)
    h.flush()
    assert sink.last("files") is None  # the step replaces the file list (#130)
    files = h.files()
    assert [f["name"] for f in files] == ["deck", "notes"] and files[0]["role"] == ".html"
    key = files[0]["key"]
    deck_url = f"specto://kobi.items/l2/{quote(key, safe='')}/html/deck.html"
    assert files[0]["url"] == deck_url
    item = sink.last("item")
    assert item["key"] == key and item["url"] == deck_url and item["file"] == "deck.html"
    assert engine.links.output_of("kobi", "content") is None  # no content port on html
    assert h.ctx.health_of("kobi")["status"] == "ok"

    # the scheme serves the page and what it references relative to itself, under the
    # view's grants: the frame's own origin (or no initiator) may read, another page not
    base = f"/l2/{quote(key, safe='')}/html"
    rt = runtime()

    def locate(path, initiator="kobi.items", host="kobi.items"):
        entry = {"status": "ok"}  # as the handler starts a request
        p = locate_item(host, path, initiator, caps_of=rt.caps_of, items=rt.items, entry=entry)
        return p, entry["status"]

    assert locate(base + "/deck.html") == ((proj.root / "deck" / "deck.html").resolve(), "ok")
    assert locate(base + "/style.css") == ((proj.root / "deck" / "style.css").resolve(), "ok")
    assert locate(base + "/img/a.png") == ((proj.root / "deck" / "img" / "a.png").resolve(), "ok")
    assert locate(base + "/deck.html", initiator=None)[1] == "ok"
    assert locate(base + "/deck.html", initiator="kobi.page")[1] == "ok"
    assert locate(base + "/nope.css")[1] == "not_found"
    assert locate(base + "/../../notes/alpha.md")[1] == "bad_item_path"
    assert locate(base + "/deck.html", initiator="lito.page")[1] == "denied"
    assert locate(base + "/deck.html", host="files.items")[1] == "denied"  # no read:link:l2

    # the footer's next: shown, and out on selection
    h.bridge.send("step", json.dumps({"delta": 1}))
    h.flush()
    assert h.current_key == files[1]["key"] and sink.last("item")["file"] == "notes.html"
    assert engine.links.output_of("kobi", "selection") == Selection(
        keys=[files[1]["key"]], last=files[1]["key"]
    )
    snap = h.snapshot()
    assert snap["files"] == [files[0]["key"], files[1]["key"]] and snap["item"] == files[1]["key"]


def test_html_view_holds_the_network_by_default_and_network_false_turns_it_off(tmp_path, capsys):
    """Seam 2 (#93): the user's page in the html view loads remote images and embeds
    (a remote image, a map frame) unless the view's ``network`` param is
    false. The grant covers http(s) requests made in the frame (attributed by the
    initiator, or by the top-level page for requests a remote frame makes itself) and
    http(s) navigations of frames; the main frame never leaves the view's page. Turned
    off by the agent, the same requests are refused and logged; a json view beside it
    gains nothing. The param is in what ``describe`` tells the agent."""
    from web_helpers import make_deck

    from specto_app.web.scheme import net_decision

    engine, proj = new_engine(tmp_path)
    make_deck(proj.root / "deck")
    compose_deck(engine)
    doc = engine.live.document()
    doc["views"]["tamu"] = {"type": "json", "label": "sidecar", "layout": {"position": [820, 0]}}
    doc["links"]["l9"] = {
        **intaker("all_files", "tamu", ["deck"], ["@json"]),
        "params": {"sources": ["."]},
    }
    engine.live.submit(doc, origin="agent:s1", label="a json view")
    reg = registry(engine)
    h = reg.host("kobi")
    sink = Sink(h.bridge)
    h.bridge.ready()
    assert "net:web" in sink.configured[0]["capabilities"]
    # what the agent reads: the param, its default and what it does
    info = engine.call("describe", {"target": "view:html"})
    info = info["info"] if isinstance(info, dict) else info.info
    assert info["defaults"]["network"] is True
    assert "network" in info["state_schema"]["properties"]["network"]["description"]
    caps_of = runtime().caps_of

    def net(initiator, host, *, scheme="https", first_party="kobi.page", main_frame=False):
        return net_decision(
            initiator, scheme, host, caps_of, first_party_host=first_party, main_frame=main_frame
        )

    street = "maps.googleapis.com"
    embed = "https://www.google.com/maps/embed?pb=1"
    assert net("kobi.items", street) == "ok"  # the framed page's <img>
    assert net("www.google.com", "maps.gstatic.com") == "ok"  # inside the map embed
    assert net("kobi.items", "example.org", scheme="http") == "ok"
    assert net("kobi.page", "example.org", main_frame=True) == "denied:main_frame"
    assert h.bridge.allowNavigation(embed, False)  # the map embed's frame
    assert not h.bridge.allowNavigation(embed, True)  # never the view's own page
    # another view's page gains nothing from the html view's grant
    assert net("tamu.page", street, first_party="tamu.page") == "denied:net"

    reloads = len([k for k, _ in sink.notifies if k == "reload"])
    doc = engine.live.document()
    doc["views"]["kobi"]["params"] = {"network": False}
    engine.live.submit(doc, origin="agent:s1", label="offline deck")
    h.flush()
    assert not h.caps.can_net("web")
    # the frame reloads under the new grant: what it shows matches what it may fetch
    assert len([k for k, _ in sink.notifies if k == "reload"]) == reloads + 1
    assert "net:web" not in sink.last("reload")["capabilities"]  # the badge follows
    assert net("kobi.items", street) == "denied:net"
    assert net("www.google.com", "maps.gstatic.com") == "denied:net"
    assert not h.bridge.allowNavigation(embed, False)
    assert "[WARN] page kobi: navigation refused in a frame: " + embed in capsys.readouterr().err

    doc["views"]["kobi"]["params"] = {"network": True}
    engine.live.submit(doc, origin="agent:s1", label="online deck")
    h.flush()
    assert h.caps.can_net("web") and net("kobi.items", street) == "ok"
    assert len([k for k, _ in sink.notifies if k == "reload"]) == reloads + 2
    doc["views"]["kobi"]["label"] = "Deck online"  # not a network change: no reload
    engine.live.submit(doc, origin="agent:s1", label="relabel")
    h.flush()
    assert len([k for k, _ in sink.notifies if k == "reload"]) == reloads + 2


def test_json_view_shows_content_linked_from_another_views_content_port(tmp_path: Path):
    # #140: a usd view's selected prim goes out on `content`; a json view shows it
    engine, _ = new_engine(tmp_path, frames=1)
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "layout": {"size": [600, 400]}}
    doc["views"]["tamu"] = {"type": "json", "label": "prim", "layout": {"position": [620, 0]}}
    doc["links"]["l8"] = intaker("all_files", "kobi", ["."], ["@usd"])
    doc["links"]["l9"] = follow("kobi", "content", "tamu", "content")
    engine.live.submit(doc, origin="agent:s1", label="prim properties")
    h = registry(engine).host("tamu")
    sink = Sink(h.bridge)
    h.bridge.ready()

    prim = {"path": "/World/Box", "type": "Mesh", "properties": {"size": 2.0}}
    entry = ContentEntry(key="w#/World/Box", path="w.usda#/World/Box", format="json", data=prim)
    engine.links.emit("kobi", "content", ContentValue(root=str(tmp_path), entries=[entry]))
    h.flush()
    item = sink.last("item")
    assert item["key"] == "w#/World/Box" and item["url"] is None
    assert json.loads(item["text"]) == prim and item["role"] == ".json"
    assert item["file"] == "w.usda#/World/Box"
    snap = h.snapshot()
    assert snap["item"] == "w#/World/Box"
    assert snap["outline"] == ["path: string", "type: string", "properties: object{1}"]
    assert engine.links.output_of("tamu", "content") is None  # shown, not republished

    # the selection cleared upstream: nothing shown
    engine.links.emit("kobi", "content", ContentValue(root=str(tmp_path)))
    h.flush()
    assert sink.last("item") == {"key": None, "url": None}
