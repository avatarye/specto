"""The overworld v2 and the link visuals (#45, spec #35 stories 67-71): Tab pauses every
view and shows cards with snapshots; moves and resizes are free there and resolve snapped
into the normal view as one user submit; links are chain-coloured wires with link nodes
that open the link view (Delete, Ask agent to change; a dot since #121); the normal
view carries the same chains on its port identicons and draws no curves (#81). Offscreen
Qt; real mouse input is the scripted flow tools/gui_flows/v2-45-overworld.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
SENTENCE = "The gallery shows the files picked in the file tree."


def submit(app, doc: dict, label: str) -> dict:
    return app.engine.call("canvas.submit", {"document": doc, "label": label}, AGENT)


def compose(app, spin, *, far: bool = True) -> None:
    """A gallery fed by the file tree (one chain with it) and, far to the right, an
    unlinked gallery that starts off screen."""
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 40], "size": [640, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "label": "picked files",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["."], "screener": ["@images"]},
        "summary": {"sentence": SENTENCE},
    }
    if far:
        doc["views"]["tamu"] = {
            "type": "gallery",
            "label": "Far",
            "layout": {"position": [3000, 0], "size": [640, 400]},
        }
        pin(doc, "tamu")
    submit(app, doc, "compose")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    if far:
        spin(until=lambda: app.controller.views["tamu"]["paused"])


def click(app, name: str) -> None:
    item = find_item(app.window.contentItem(), name)
    assert item is not None and item.isVisible(), name
    p = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)


def test_chain_colours_match_in_both_levels_and_only_the_overworld_draws_the_curves(app, spin):
    compose(app, spin)
    views = app.controller.views
    assert views["fege"]["chain"] == views["kobi"]["chain"] == "chain_0"
    assert views["tamu"]["chain"] == "chain_1"  # pinned off the project source: its own
    wires = {w["id"]: w for w in app.controller.wires}
    assert set(wires) == {"l1", "l2", "l3"}
    assert wires["l2"]["chain"] == "chain_0" and wires["l2"]["sentence"] == SENTENCE
    assert wires["l1"]["source"] == "project" and wires["l1"]["chain"] == "chain_0"
    root = app.window.contentItem()
    assert find_item(root, "wire:l2") is None  # the normal view draws no curves (#81)

    cards = {c["id"]: c for c in app.overworld.cards}
    assert (
        cards["fege"]["chain"] == cards["kobi"]["chain"] == "chain_0"
        and cards["tamu"]["chain"] == "chain_1"
    )
    assert {w["id"] for w in app.overworld.wires} == {"l1", "l2", "l3"}


def test_the_overworld_pauses_every_view_shows_snapshots_and_resumes_the_on_screen_ones(app, spin):
    compose(app, spin)
    ow = app.overworld
    assert not ow.active
    was_active = ow.active
    ow.toggle()
    spin(until=lambda: ow.active != was_active)
    assert ow.active
    assert all(app.hosts.is_paused(v) for v in ("fege", "kobi", "tamu"))
    cards = {c["id"]: c for c in ow.cards}
    assert cards["fege"]["hasSnapshot"] and cards["fege"]["snapshot"].startswith(
        "image://snaps/fege?"
    )
    assert cards["kobi"]["hasSnapshot"]
    assert cards["kobi"]["rect"] == {"x": 300, "y": 40, "w": 640, "h": 400}
    assert not cards["tamu"]["hasSnapshot"]  # it was off screen: nothing to grab
    root = app.window.contentItem()
    assert find_item(root, "overworld").isVisible()
    assert not find_item(root, "canvasArea").isVisible()
    assert find_item(root, "overCard:tamu") is not None  # the whole canvas, not the viewport
    # the card header follows the normal view's rule (#80): `<name>: <label>`, nothing else
    assert find_item(root, "overTitle:kobi").property("text") == "kobi: Front"
    assert ow.pausedCount == 3

    was_active = ow.active
    ow.toggle()
    spin(until=lambda: ow.active != was_active)
    assert not ow.active
    assert not app.hosts.is_paused("fege") and not app.hosts.is_paused("kobi")
    assert app.hosts.is_paused("tamu")  # still off screen
    assert app.controller.views["fege"]["health"] == "ok"
    assert find_item(root, "canvasArea").isVisible()
    assert app.controller.undoDepth == 1  # looking is not an edit


def test_a_move_in_the_overworld_is_free_and_resolves_snapped_as_one_user_submit(app, spin):
    compose(app, spin, far=False)
    ow = app.overworld
    was_active = ow.active
    ow.toggle()
    spin(until=lambda: ow.active != was_active)
    # while dragging nothing snaps: the card follows the pointer
    assert ow.dragMove("kobi", 366.0, 5.0) == {"x": 366.0, "y": 5.0, "w": 640.0, "h": 400.0}
    # the drop resolves against the tree's right edge (360) and the frame's top (0)
    assert ow.endMove("kobi", 366.0, 5.0)
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [360.0, 0.0]
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", "move Front") and app.controller.undoDepth == 2
    assert next(c for c in ow.cards if c["id"] == "kobi")["rect"]["x"] == 360.0
    assert next(e for e in app.controller.viewIds if e["id"] == "kobi")["x"] == 360.0

    # resize the same way: free while held, the frame's right and bottom edges on drop
    free = ow.dragResize("kobi", 280.0, 0.0, 1313.0, 894.0, ["right", "bottom"])
    assert (free["w"], free["h"]) == (1313.0, 894.0)
    assert ow.endResize("kobi", 280.0, 0.0, 1313.0, 894.0, ["right", "bottom"])
    assert app.live.data["views"]["kobi"]["layout"]["size"] == [1320.0, 900.0]
    assert app.live.history[-1].label == "resize Front" and app.controller.undoDepth == 3

    was_active = ow.active
    ow.toggle()
    spin(until=lambda: ow.active != was_active)
    view = find_item(app.window.contentItem(), "view:kobi")
    assert view.mapToScene(view.boundingRect().topLeft()).x() == 12 + 280  # pad + canvas x
    slot = app.hosts.slot_of("kobi")
    assert slot.mapToScene(slot.boundingRect().topLeft()).x() == 12 + 280 + 3  # gutter + clip inset
    assert app.controller.undo() and app.controller.undo()
    assert app.live.data["views"]["kobi"]["layout"] == {"position": [300, 40], "size": [640, 400]}


def test_a_link_node_is_a_dot_that_opens_the_link_view_with_delete_and_ask_agent(app, spin):
    compose(app, spin, far=False)
    ow = app.overworld
    was_active = ow.active
    ow.toggle()
    spin(until=lambda: ow.active != was_active)
    root = app.window.contentItem()
    view = find_item(root, "linkView")
    assert view is None or not view.isVisible()
    # #121: the node is a dot with no label; a click opens the link view itself
    node = find_item(root, "linkNode:l2")
    assert node.width() == node.height() and find_item(node, "dot:linkNode:l2").isVisible()
    click(app, "linkNode:l2")
    spin(30)
    assert ow.selectedLink == "l2"
    view = find_item(root, "linkView")
    assert view is not None and view.isVisible()
    assert find_item(root, "linkSentence").property("text") == SENTENCE
    for name in ("linkDelete", "linkAskAgent", "linkClose"):
        assert find_item(root, name) is not None and find_item(root, name).isVisible(), name
    assert find_item(root, "linkSummary") is None
    summary = ow.linkSummary("l2")
    assert summary["sentence"] == SENTENCE and summary["kind"] == "intaker"
    assert summary["source"] == "fege.selected_files" and summary["target"] == "kobi.data"

    # the link view shows the kind, ports and parameters
    details = ow.linkDetails("l2")
    assert details["kind"] == "intaker" and '"sources"' in details["params"]
    assert details["status"] in ("resolved", "resolving", "waiting", "failed")
    assert details["code"] == ""  # not a Python link
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(20)
    assert not view.isVisible()

    # Ask agent to change pre-fills the prompt bar with the link and its sentence
    asked: list[str] = []
    ow.askAgentRequested.connect(asked.append)
    ow.askAgent("l2")
    assert asked and "l2" in asked[0] and SENTENCE in asked[0]

    # Delete is one user submit, even of the gallery's only link (#83: reachability binds
    # the agent only); the gallery is left "not connected" and leaves the chain
    assert ow.deleteLink("l2")
    spin(20)
    assert "l2" not in app.live.data["links"]
    rec = app.live.history[-1]
    assert (rec.origin, rec.label) == ("user", f"delete link: {SENTENCE}")
    assert ow.selectedLink == "" and {w["id"] for w in ow.wires} == {"l1"}
    assert app.controller.views["kobi"]["chain"] == ""
    assert app.controller.views["kobi"]["health"] == "disconnected"
    ow.toggle()


def compose_four(app, spin) -> None:
    """The v2-45 canvas on the temp project: the tree feeding kobi and tamu, rena and
    pola fed by the project and linked two-way on frame; pola locked."""
    import json
    from pathlib import Path

    canvas = Path(__file__).resolve().parents[3] / "tools" / "gui_flows" / "v2-45-canvas.json"
    sample = json.loads(canvas.read_text(encoding="utf-8"))
    doc = app.live.document()
    for vid, v in sample["views"].items():
        if vid != "fege":
            doc["views"][vid] = {**v, "locked": vid == "pola"}
    for lid, ln in sample["links"].items():
        if lid != "l1":
            params = dict(ln.get("params", {}))
            if "sources" in params:
                params["sources"] = ["."]
            doc["links"][lid] = {**ln, "params": params}
    submit(app, doc, "compose four")
    spin(until=lambda: all(v in app.hosts.hosts for v in ("kobi", "tamu", "rena", "pola")))


def texts_under(item) -> list[str]:
    out = []
    for child in item.childItems():
        t = child.property("text")
        if isinstance(t, str) and t:
            out.append(t)
        out.extend(texts_under(child))
    return out


def test_link_nodes_keep_clear_of_the_card_headers_and_cards_carry_identicon_ports(app, spin):
    """#90: the link-node chips never cover a card's header; the cards show the #81
    port circles (the upstream view's identicon in, the view's own out, one input
    circle per linked input) and follow the #80 header rule: `name: label`, the health
    dot and the lock in the header, no footer with counts or health text."""
    from specto_app.canvas.identicon import identicon_url
    from specto_app.theme import METRICS

    compose_four(app, spin)
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    spin(80)  # the node chips report their sizes, the places settle
    root = app.window.contentItem()
    content = find_item(root, "overCard:kobi").parentItem()

    def content_rect(item):
        p = item.mapToItem(content, item.boundingRect().topLeft())
        return (p.x(), p.y(), item.width(), item.height())

    def overlaps(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    views = ("fege", "kobi", "tamu", "rena", "pola")
    headers = {v: content_rect(find_item(root, f"overHeader:{v}")) for v in views}
    places = ow.nodePlaces
    wires = {w["id"]: w for w in ow.wires}
    for lid in ("l1", "l2", "l3", "l4", "l5", "l6"):
        node = find_item(root, f"linkNode:{lid}")
        assert node is not None and node.isVisible(), lid
        nr = content_rect(node)
        assert nr[2] == nr[3] > 10, (lid, nr)  # a dot with room to click, not a point
        # #121: on its wire always; off the headers and port circles unless no spot on
        # the wire is, when it takes the middle
        assert on_wire(places[lid], wires[lid]), lid
        if places[lid] == {"x": wires[lid]["nx"], "y": wires[lid]["ny"]}:
            continue
        for v, hb in headers.items():
            assert not overlaps(nr, hb), (lid, nr, v, hb)
        # nor on a port circle (it would hide the identicon)
        for v in views:
            for name in (f"overPort:{v}:in", f"overPort:{v}:in:1", f"overPort:{v}:out"):
                pc = find_item(root, name)
                if pc is not None:
                    assert not overlaps(nr, content_rect(pc)), (lid, nr, name)
        # the chip is centred on the place the controller chose
        assert abs(nr[0] + nr[2] / 2 - places[lid]["x"]) < 0.5
        assert abs(nr[1] + nr[3] / 2 - places[lid]["y"]) < 0.5
    # tree -> tamu runs over the cards' bodies: its dot stays on the wire's middle;
    # tree -> kobi lies wholly in the header band: its dot keeps the middle too
    for lid in ("l2", "l3"):
        assert places[lid] == {"x": wires[lid]["nx"], "y": wires[lid]["ny"]}, lid
    # a drag keeps the rule: the dot follows the card and, its wire now leaving the
    # header band, stays off the header
    ow.dragMove("kobi", 600.0, 300.0)
    spin(30)
    node = find_item(root, "linkNode:l2")
    assert not overlaps(content_rect(node), content_rect(find_item(root, "overHeader:kobi")))
    ow.cancelDrag("kobi")

    # the cards: the #81 port facts, no in/out counts
    cards = {c["id"]: c for c in ow.cards}
    assert "inputs" not in cards["pola"] and "outputs" not in cards["pola"]
    assert [p["from"] for p in cards["pola"]["inPorts"]] == ["rena", "project"]
    # chains: the tree and kobi (0), tamu pinned off the project source alone (1), rena
    # and pola (2): the project source joins no chain (#83)
    assert cards["pola"]["inPorts"][0]["icon"] == identicon_url("rena", 2, round=True)
    assert cards["pola"]["icon"] == identicon_url("pola", 2, round=True)
    assert cards["pola"]["outLinked"] is True and cards["tamu"]["outLinked"] is False
    tip = cards["kobi"]["inPorts"][0]["tip"]
    assert tip["what"] == "picked files"
    assert tip["parts"] == [{"text": "from "}, {"text": "fege", "view": "fege"}]
    port, stack = METRICS["port_px"], METRICS["port_stack"]
    pin0, pin1 = find_item(root, "overPort:pola:in"), find_item(root, "overPort:pola:in:1")
    pout = find_item(root, "overPort:pola:out")
    assert pin0.isVisible() and pin1.isVisible() and pout.isVisible()
    assert pin0.property("icon") == identicon_url("rena", 2, round=True)
    assert pin1.property("icon") == identicon_url("root", None, round=True)  # a hub: neutral
    assert pout.property("icon") == identicon_url("pola", 2, round=True)
    assert (pin1.x(), pin1.y()) == (pin0.x(), pin0.y() + port + stack)
    for it in (pin0, pin1, pout):
        assert find_item(it, "identicon:" + it.objectName()).isVisible(), it.objectName()
    # tamu takes one link and emits nothing: one input circle, a hollow output
    assert find_item(root, "overPort:tamu:in:1") is None
    tout = find_item(root, "overPort:tamu:out")
    assert tout.property("linked") is False
    assert not find_item(tout, "identicon:overPort:tamu:out").isVisible()

    # the header: `name: label`, the health dot and the lock; no footer text
    assert find_item(root, "overTitle:pola").property("text") == "pola: Rear B"
    assert find_item(root, "overHealth:pola").isVisible()
    assert find_item(root, "overLock:pola").isVisible()
    assert not find_item(root, "overLock:kobi").isVisible()
    for v in views:
        texts = texts_under(find_item(root, f"overCard:{v}"))
        assert not any(" in, " in t or t in ("paused", "locked") for t in texts), (v, texts)
    ow.toggle()
    spin(until=lambda: not ow.active)


def on_wire(place: dict, wire: dict) -> bool:
    """True when ``place`` is one of the spots a link dot may take on ``wire`` (#121)."""
    from specto_app.canvas.chains import NODE_STEPS, curve_point

    return any(
        abs(x - place["x"]) < 1e-6 and abs(y - place["y"]) < 1e-6
        for x, y in (curve_point(wire, t) for t in NODE_STEPS)
    )
