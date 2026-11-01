"""``PageBridge`` + ``WebView`` without a browser, on ports: ready/configured with the
view's inputs and grants, ``emit`` lands on the view's output port (and resolves the
links fed from it), ``setParams`` is a user submit on the view's own params, ``call``
within the capability list, JS errors into health, latest value per input port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web_helpers import Sink, compose_json, new_engine, registry, tree_emits_all

pytest.importorskip("PySide6.QtCore")

from specto_app.hosts.base import HostContext, SimpleFactory
from specto_app.web.capabilities import Capabilities
from specto_app.web.runtime import runtime
from specto_app.web.webview import WebView, WebViewHost
from specto_core.links import ContentValue, Selection


def plain_host(engine, view_id: str) -> WebViewHost:
    """The base web host (no page logic) for a view of the live document."""
    ctx = HostContext(engine, None)
    return WebViewHost(ctx, view_id, ctx.manifest_for(ctx.view(view_id)["type"]))


def test_ready_sends_config_with_inputs_grants_params_and_theme(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    h = plain_host(engine, "tamu")
    sink = Sink(h.bridge)
    assert not h.page.ready
    h.bridge.ready()
    assert h.page.ready and len(sink.configured) == 1
    cfg = sink.configured[0]
    assert cfg["view"] == "tamu" and cfg["page"] == "tamu" and cfg["type"] == "json"
    assert cfg["label"] == "sidecar" and cfg["params"] == {}
    assert cfg["inputs"] == {
        "data": {"link": "l3", "kind": "intaker", "from": "fege.selected_files"},
        "selection": {"link": "l4", "kind": "follow", "from": "kobi.selection"},
    }
    assert cfg["capabilities"] == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:content",
        "emit:selection",
        "params:wrap",
        "read:link:l3",
        "read:link:l4",
    ]
    theme = cfg["theme"]
    assert theme["color-surface"].startswith("#") and theme["metric-pad"].endswith("px")
    assert theme["font-mono"] and theme["glyph-added"] == "+"
    assert runtime().caps_of("tamu") is h.caps and runtime().caps_of("nope") is None


def test_emit_lands_on_the_output_port_within_the_grants(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    # a second json view follows the first one's selection
    doc = engine.live.document()
    doc["views"]["rena"] = {"type": "json", "label": "twin", "layout": {"position": [0, 500]}}
    doc["links"]["l5"] = {
        "kind": "follow",
        "source": {"view": "tamu", "port": "selection"},
        "target": {"view": "rena", "port": "selection"},
        "summary": {"sentence": "The twin follows the sidecar view's selection."},
    }
    engine.live.submit(doc, origin="agent:s1", label="twin")
    h = plain_host(engine, "tamu")
    h.bridge.ready()
    n = len(engine.live.history)

    res = json.loads(h.bridge.emit("selection", json.dumps({"keys": ["00002"], "last": "00002"})))
    assert res["ok"] and res["result"] == {"links": ["l5"]}
    assert engine.links.output_of("tamu", "selection") == Selection(keys=["00002"], last="00002")
    assert engine.links.input_of("rena", "selection") == Selection(keys=["00002"], last="00002")
    assert len(engine.live.history) == n  # emitting is not a document edit
    assert h.bridge.calls[-1] == {"emit": "selection", "ok": True}

    content = {"root": "/p", "entries": [{"key": "a", "path": "a.json", "format": "json"}]}
    assert json.loads(h.bridge.emit("content", json.dumps(content)))["ok"]
    assert isinstance(engine.links.output_of("tamu", "content"), ContentValue)

    # not an output of this view type; a value the port type refuses
    res = json.loads(h.bridge.emit("camera", json.dumps({"eye": [0, 0, 0]})))
    assert not res["ok"] and res["error"]["code"] == "capability"
    assert "emit:camera" in res["error"]["message"] and res["error"]["fix"]
    bad = json.loads(h.bridge.emit("selection", json.dumps({"nope": 1})))
    assert not bad["ok"] and bad["error"]["code"] == "invalid_args"
    assert engine.links.output_of("tamu", "selection").last == "00002"


def test_set_params_is_a_user_submit_on_the_views_own_params(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    reg = registry(engine)
    reg.add_factory(SimpleFactory("json", WebViewHost))  # the base host, no page logic
    h = reg.host("tamu")
    sink = Sink(h.bridge)
    h.bridge.ready()
    n = len(engine.live.history)
    res = json.loads(h.bridge.setParams(json.dumps({"wrap": True})))
    assert res["ok"] and res["result"]["views"]["changed"] == ["tamu"]
    assert engine.live.data["views"]["tamu"]["params"] == {"wrap": True}
    rec = engine.live.history[-1]
    assert len(engine.live.history) == n + 1 and rec.origin == "user"
    assert rec.label == "sidecar: wrap"
    h.flush()
    assert sink.params[-1] == {"wrap": True}  # the page hears the document, not itself
    # a key the type does not grant leaves the document alone
    res = json.loads(h.bridge.setParams(json.dumps({"basemap": "none"})))
    assert not res["ok"] and res["error"]["code"] == "capability"
    assert engine.live.data["views"]["tamu"]["params"] == {"wrap": True}
    # an agent's edit of the params reaches the page the same way
    doc = engine.live.document()
    doc["views"]["tamu"]["params"]["wrap"] = False
    engine.live.submit(doc, origin="agent:s1", label="unwrap")
    h.flush()
    assert sink.params[-1] == {"wrap": False}


def test_call_within_and_outside_capabilities(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    h = plain_host(engine, "tamu")
    sink = Sink(h.bridge)
    h.bridge.call("describe", json.dumps({"target": "view:json"}), "1")
    rid, res = sink.results[-1]
    assert rid == "1" and res["ok"] and res["result"]["target"] == "view:json"
    cursor = engine.live.cursor
    h.bridge.call("canvas.undo", "{}", "2")
    rid, res = sink.results[-1]
    assert rid == "2" and not res["ok"] and res["error"]["code"] == "capability"
    assert engine.live.cursor == cursor  # nothing undone
    h.bridge.call("describe", "[1,2]", "3")
    assert not sink.results[-1][1]["ok"]
    h.bridge.call("describe", json.dumps({"target": "no.such.command"}), "4")
    assert not sink.results[-1][1]["ok"]


def test_js_errors_and_console_errors_land_in_view_health(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    h = plain_host(engine, "tamu")
    health = lambda: h.ctx.health_of("tamu")  # noqa: E731
    h.bridge.loadState("started", "")
    assert health()["status"] == "loading"
    h.bridge.jsError("TypeError: x is undefined (main.js:12)")
    assert health()["status"] == "error" and "TypeError" in health()["message"]
    h.bridge.setHealth("ok", "")
    assert health()["status"] == "ok"
    h.bridge.consoleMessage(2, "boom", 7, "specto://tamu.page/json/main.js")
    assert health() == {"status": "error", "message": "boom (main.js:7)", "hint": ""}
    h.bridge.consoleMessage(0, "fine", 1, "x.js")  # info does not change health
    assert health()["status"] == "error"
    h.bridge.loadState("error", "net::ERR_FAILED")
    assert "ERR_FAILED" in health()["message"]


def test_input_ports_reach_the_page_latest_value_once_ready(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    h = plain_host(engine, "tamu")
    sink = Sink(h.bridge)
    for k in ("00001", "00002", "00003"):
        h.receive("selection", Selection(keys=[k], last=k))
    assert h.flush() == 0  # not ready: held
    assert h.page.pending()["ports"]["selection"]["last"] == "00003"
    h.bridge.ready()  # flushes: one port message, latest value only
    assert sink.ports == [{"port": "selection", "value": {"keys": ["00003"], "last": "00003"}}]
    h.receive("selection", None)  # the link feeding the port went away
    assert h.flush() == 1 and sink.ports[-1] == {"port": "selection", "value": None}
    # the registry wires the runtime to the host: a resolved link lands as a port message
    reg = registry(engine)
    reg.add_factory(SimpleFactory("json", WebViewHost))
    hj = reg.host("tamu")
    s2 = Sink(hj.bridge)
    hj.bridge.ready()
    tree_emits_all(engine)
    assert hj.flush() == 1
    data = s2.last_port("data")
    assert data["link"] == "l3" and [it["key"] for it in data["items"]] == [
        f"0000{i}" for i in range(5)
    ]
    assert hj.snapshot()["inputs"] == {"data": "l3", "selection": "l4"}


def test_standalone_page_cannot_emit_and_registers_in_runtime(tmp_path: Path):
    engine, _ = new_engine(tmp_path)
    ctx = HostContext(engine, None)
    p = WebView(ctx, "about", "index.html", Capabilities.of(["emit:selection", "cmd:describe"]))
    assert runtime().caps_of("about") is p.caps
    res = json.loads(p.bridge.emit("selection", json.dumps({"keys": ["1"], "last": "1"})))
    assert not res["ok"] and res["error"]["code"] == "capability"
    res = json.loads(p.bridge.setParams(json.dumps({"x": 1})))
    assert not res["ok"]
    sink = Sink(p.bridge)
    p.bridge.call("describe", json.dumps({"target": "canvas"}), "9")
    assert sink.results[-1][1]["ok"]
    p.notify("hello", {"x": 1})
    assert p.flush() == 0
    p.bridge.ready()
    assert sink.last("hello") == {"x": 1}
    assert sink.configured[0]["view"] is None and sink.configured[0]["inputs"] == {}
    p.dispose()
    assert runtime().caps_of("about") is None and p.disposed


def test_refused_navigations_and_framed_page_errors_are_logged_not_silent(tmp_path: Path, capsys):
    """The QML view asks the bridge before every navigation (main frame or a frame): a
    refusal is a [WARN] line, so a page that never appears leaves a trace in launch.log.
    A console error from a framed document (the user's own page in the html view) is
    logged as [WARN] with its origin and leaves the view's health alone: the view works,
    the user's page has a bug."""
    engine, _ = new_engine(tmp_path)
    compose_json(engine)
    h = plain_host(engine, "tamu")
    assert h.bridge.allowNavigation("specto://tamu.page/json/index.html", True)
    assert h.bridge.allowNavigation("specto://tamu.items/l3/00001/html/x.html", False)
    assert not h.bridge.allowNavigation("https://maps.google.com/maps?output=embed", False)
    assert not h.bridge.allowNavigation("specto://lito.page/json/index.html", True)
    err = capsys.readouterr().err
    assert "[WARN] page tamu: navigation refused in a frame: https://maps.google.com/" in err
    assert "[WARN] page tamu: navigation refused: specto://lito.page/json/index.html" in err
    h.bridge.setHealth("ok", "")
    h.bridge.consoleMessage(
        2,
        "Uncaught ReferenceError: qt is not defined",
        3,
        "specto://tamu.items/l3/00001/html/probe.js",
    )
    assert h.ctx.health_of("tamu")["status"] == "ok"
    err = capsys.readouterr().err
    assert "[WARN] page tamu: framed page specto://tamu.items/l3/00001/html/probe.js:3: " in err
    assert "qt is not defined" in err
    # a remote frame inside the user's page (a map embed, #93) is framed as well
    maps_js = "https://maps.googleapis.com/maps-api-v3/api/js/66/9d/main.js"
    h.bridge.consoleMessage(2, "Permissions policy violation: accelerometer", 198, maps_js)
    assert h.ctx.health_of("tamu")["status"] == "ok"
    assert f"[WARN] page tamu: framed page {maps_js}:198: " in capsys.readouterr().err
    h.bridge.consoleMessage(2, "boom", 7, "specto://tamu.page/json/main.js")  # the view's own page
    assert h.ctx.health_of("tamu")["status"] == "error"
