"""Ports on the view frame (#81, ADR 0018; glossary: port, identicon): a view's input
circles sit on the frame's left vertical edge near the top-left corner (half inside the
frame, half out), one per linked input stacked downwards, each showing the identicon of
the upstream view that feeds it; the output circle sits on the right edge near the
top-right corner and shows the view's own identicon. Unlinked ports are drawn hollow.
Hovering a circle shows its tip (#127): a box hanging centred under the port, a caret
pointing at it, at most 200 px wide and wrapping, with a data section (what flows, its
type when the words do not say it, from or to whom; "two-way with <name>" for a two-way
link) and a filter section (the link's summary), each led by an icon with its text
indented past it; no filter, no section. View names in a tip are links: the tip stays
open while the pointer moves into it, and clicking a name pans to that view and flashes
it. The normal view draws no link curves and a port opens nothing (the curves are the
wiring layer's, #82). The overworld cards carry the ports at the same canvas spots.
Offscreen Qt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.canvas.identicon import identicon_url
from specto_app.theme import COLORS, METRICS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
SENTENCE = "The gallery shows every picture in the pics folder."
TWO_WAY = "Picking in one gallery picks in the other."
LOOSE = "The loose gallery shows nothing from pics."
PAD, GUTTER, PORT, INSET = 12, METRICS["gutter"], METRICS["port_px"], METRICS["port_inset"]
STACK = METRICS["port_stack"]
# the port tips, word for word (#127): data (what, from whom; names are links), filter
PROJECT = {"text": "project", "view": "project"}
KOBI_IN = {
    "what": "all files",
    "parts": [{"text": "from "}, PROJECT],
    "filter": "The gallery shows every picture in the pics folder",
}
KOBI_TWO_WAY = {
    "what": "picked items (selection)",
    "parts": [{"text": "two-way with "}, {"text": "tamu", "view": "tamu"}],
    "filter": "Picking in one gallery picks in the other",
}
TIP_MAX = 200
GRACE = METRICS["tip_grace_ms"]
RING = 2  # the port circle's stroke ring (#92)


def compose(app, spin) -> None:
    """Three pictures in pics/; a gallery pinned on them (l2 from the project source),
    a second gallery on the picks (l3) whose selection is two-way with the first (l4),
    and a gallery whose only link is its own pin (l5, #83: every view hangs off the
    project source) and which nothing reads from."""
    root = app.project.root
    (root / "pics").mkdir()
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(QColor("#4080c0"))
    for name in ("a.png", "b.png", "c.png"):
        assert img.save(str(root / "pics" / name))
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Pics",
        "layout": {"position": [400, 40], "size": [640, 400]},
    }
    doc["views"]["tamu"] = {
        "type": "gallery",
        "label": "Picked",
        "layout": {"position": [400, 460], "size": [640, 400]},
    }
    doc["views"]["pola"] = {
        "type": "gallery",
        "label": "Loose",
        "layout": {"position": [1060, 40], "size": [400, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "pictures",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["pics"], "screener": ["@images"]},
        "summary": {"sentence": SENTENCE},
    }
    doc["links"]["l3"] = {
        "kind": "intaker",
        "scope": "selection",
        "label": "picked files",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "tamu", "port": "data"},
        "params": {"sources": ["."], "screener": ["@images"]},
        "summary": {"sentence": "The gallery shows the files picked in the file tree."},
    }
    doc["links"]["l4"] = {
        "kind": "follow",
        "two_way": True,
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "params": {},
        "summary": {"sentence": TWO_WAY},
    }
    doc["links"]["l5"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "nothing",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "pola", "port": "data"},
        "params": {"sources": ["pics"], "screener": ["*.none"]},
        "summary": {"sentence": LOOSE},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "compose"}, AGENT)
    spin(until=lambda: "pola" in app.hosts.hosts)
    spin(until=lambda: getattr(app.engine.links.input_of("kobi", "data"), "items", None))


def area_rect(item) -> tuple[float, float, float, float]:
    """The item's rect in canvas-area px."""
    area = item
    while area is not None and area.objectName() != "canvasArea":
        area = area.parentItem()
    p = item.mapToItem(area, item.boundingRect().topLeft())
    return (p.x(), p.y(), item.width(), item.height())


def centre(item):
    return item.mapToScene(item.boundingRect().center()).toPoint()


def tip_shown(root, tag: str) -> bool:
    """A closed tip leaves the overlay: missing counts as not shown."""
    tip = find_item(root, "tip:" + tag)
    return tip is not None and tip.isVisible()


def tip_of(item) -> dict:
    """A port circle's tip: ``{what, detail, filter}``."""
    return item.property("tip")


def hover(app, spin, name: str):
    item = find_item(app.window.contentItem(), name)
    assert item is not None and item.isVisible(), name
    QTest.mouseMove(app.window, centre(item))
    spin(GRACE + 100)  # a tip still open hands over after a short dwell (#127)
    return item


def test_the_controller_lists_one_input_circle_per_linked_input_with_its_identicon(app, spin):
    compose(app, spin)
    views = app.controller.views
    # the linked views share chain 0; the loose gallery, fed by the project source
    # alone (not a view, in no chain), is a chain of its own
    assert views["kobi"]["chain"] == views["tamu"]["chain"] == "chain_0"
    assert views["pola"]["chain"] == "chain_1"
    assert [(p["link"], p["from"], p["twoWay"]) for p in views["kobi"]["inPorts"]] == [
        ("l2", "project", False),
        ("l4", "tamu", True),
    ]
    tamu = [(p["link"], p["from"]) for p in views["tamu"]["inPorts"]]
    assert tamu == [("l3", "fege"), ("l4", "kobi")]
    assert [p["from"] for p in views["pola"]["inPorts"]] == ["project"]
    assert views["fege"]["inPorts"][0]["from"] == "project"
    # the circles' images: the upstream view's identicon in, the view's own out. Hubs
    # (the project source, seeded "root", and file trees) are neutral wherever they show
    assert views["kobi"]["inPorts"][0]["icon"] == identicon_url("root", None, round=True)
    assert views["fege"]["icon"] == identicon_url("fege", None, round=True)
    assert views["kobi"]["inPorts"][1]["icon"] == identicon_url("tamu", 0, round=True)
    assert views["kobi"]["icon"] == identicon_url("kobi", 0, round=True)
    assert views["pola"]["icon"] == identicon_url("pola", 1, round=True)
    # what flows out: the two-way link emits from both ends
    assert views["fege"]["outLinked"] and views["kobi"]["outLinked"] and views["tamu"]["outLinked"]
    assert not views["pola"]["outLinked"]
    # the hover texts
    assert views["kobi"]["inPorts"][0]["tip"] == KOBI_IN
    assert views["kobi"]["inPorts"][1]["tip"] == KOBI_TWO_WAY
    assert views["kobi"]["outTip"] == {
        "what": "picked items (selection)",
        "parts": [{"text": "to "}, {"text": "tamu", "view": "tamu"}],
        "filter": "",
    }


def test_input_circles_stack_on_the_left_edge_and_the_output_circle_sits_on_the_right(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    fx, fy, fw, _ = area_rect(find_item(root, "frame:tamu"))
    first = find_item(root, "port:tamu:in")
    second = find_item(root, "port:tamu:in:1")
    pout = find_item(root, "port:tamu:out")
    assert first.isVisible() and second.isVisible() and pout.isVisible()
    assert find_item(root, "port:tamu:in:2") is None
    ix, iy, iw, ih = area_rect(first)
    assert (iw, ih) == (PORT, PORT)
    # centred on the frame's left edge, INSET below its top: half in, half out
    assert (ix + iw / 2, iy + ih / 2) == (fx, fy + INSET)
    assert (fx, fy + INSET) == (PAD + 400 + GUTTER, PAD + 460 + GUTTER + INSET)
    # #121: the circle's centre sits on the line between the header and the body
    _, ly, _, lh = area_rect(find_item(root, "separator:tamu"))
    assert lh == 1 and abs((iy + ih / 2) - (ly + lh / 2)) <= 0.5
    assert iy + ih / 2 - fy == METRICS["header_h"] + 1  # under the frame's 1 px inset
    sx, sy, _, _ = area_rect(second)
    assert (sx, sy) == (ix, iy + PORT + STACK)
    ox, oy, ow, oh = area_rect(pout)
    assert (ox + ow / 2, oy + oh / 2) == (fx + fw, fy + INSET)
    # each circle shows its identicon: the upstream view's in, the view's own out
    assert first.property("icon") == identicon_url("fege", None, round=True)  # a hub
    assert second.property("icon") == identicon_url("kobi", 0, round=True)
    assert pout.property("icon") == identicon_url("tamu", 0, round=True)
    assert first.property("linked") and second.property("linked") and pout.property("linked")
    for it in (first, second, pout):
        assert find_item(it, "identicon:" + it.objectName()).isVisible()
    # the loose gallery's output circle is hollow (it says what can be connected); its
    # one input shows the project source's "root" identicon
    lin, lout = find_item(root, "port:pola:in"), find_item(root, "port:pola:out")
    assert lin.isVisible() and lout.isVisible()
    assert lin.property("linked") is True and lout.property("linked") is False
    assert lin.property("icon") == identicon_url("root", None, round=True)
    assert find_item(root, "port:pola:in:1") is None
    # the same ports on the overworld cards, at the same canvas spots
    app.overworld.toggle()
    spin(until=lambda: app.overworld.active)
    card = find_item(root, "overCard:kobi")
    oin, oout = find_item(root, "overPort:kobi:in"), find_item(root, "overPort:kobi:out")
    assert oin is not None and oout is not None and oin.width() == PORT
    assert (oin.x() + oin.width() / 2, oin.y() + oin.height() / 2) == (GUTTER, GUTTER + INSET)
    assert oout.x() + oout.width() / 2 == card.width() - GUTTER
    app.overworld.toggle()


def test_the_normal_view_draws_no_link_curves(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    assert {w["id"] for w in app.controller.wires} == {"l1", "l2", "l3", "l4", "l5"}  # #82
    for lid in ("l1", "l2", "l3", "l4"):
        assert find_item(root, f"wire:{lid}") is None


def test_hovering_an_input_circle_shows_the_filter_and_the_type_and_opens_no_overlay(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    port = hover(app, spin, "port:kobi:in")
    assert tip_of(port) == KOBI_IN
    tip = find_item(root, "tip:port:kobi:in")
    assert tip is not None and tip.isVisible()
    assert app.controller.wiring is False and find_item(root, "wire:l2") is None
    # the two-way end names the other view
    port = hover(app, spin, "port:kobi:in:1")
    assert tip_of(port) == KOBI_TWO_WAY
    assert find_item(root, "tip:port:kobi:in:1").isVisible()
    assert not tip_shown(root, "port:kobi:in")
    # the file tree's input: the project's intaker, from the project source
    port = hover(app, spin, "port:fege:in")
    assert tip_of(port) == {
        "what": "all files",
        "parts": [{"text": "from "}, PROJECT],
        "filter": "The file tree shows every file in the project folder",
    }
    # the loose view's input names the project source
    port = hover(app, spin, "port:pola:in")
    assert tip_of(port)["filter"] == "The loose gallery shows nothing from pics"


def test_hovering_the_output_circle_shows_the_data_types(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    port = hover(app, spin, "port:fege:out")
    # all files come from the project: the tree's output is its picks
    assert tip_of(port) == {
        "what": "picked files",
        "parts": [{"text": "to "}, {"text": "tamu", "view": "tamu"}],
        "filter": "",
    }
    assert find_item(root, "tip:port:fege:out").isVisible()
    port = hover(app, spin, "port:pola:out")
    assert tip_of(port) == {
        "what": "picked items (selection), time",
        "parts": [{"text": "not linked"}],
        "filter": "",
    }
    hover(app, spin, "slot:pola")
    spin(GRACE + 100)  # the tip lingers a moment for the pointer to reach it, then goes
    assert not tip_shown(root, "port:pola:out")
    assert app.controller.undoDepth == 1  # looking is not an edit


def scene_box(item) -> tuple[float, float, float, float]:
    r = item.mapRectToScene(item.boundingRect())
    return (r.x(), r.y(), r.width(), r.height())


def test_a_port_tip_hangs_centred_under_its_port_with_a_caret_and_wraps_at_200_px(app, spin):
    """#127: the box hangs under the port, centred on it, a caret pointing up at the
    port's centre; capped at 200 px, a long filter wraps; data over filter, each led by
    its icon, the text indented past the icon (wrapped lines too). An input circle with
    others stacked under it hangs its tip below the stack, so the tip never covers them."""
    compose(app, spin)
    root = app.window.contentItem()
    port = hover(app, spin, "port:kobi:in")
    px, _, pw, _ = scene_box(port)
    tip = find_item(root, "tip:port:kobi:in")
    assert tip is not None and tip.isVisible()
    tx, ty, tw, _ = scene_box(tip)
    assert tw <= TIP_MAX
    assert abs((tx + tw / 2) - (px + pw / 2)) <= 0.5  # centred under the port
    # just below the stack (kobi has two inputs), clear of the grown circle
    _, sy, _, sh = scene_box(find_item(root, "port:kobi:in:1"))
    assert sy + sh < ty <= sy + sh + 12
    # the last of the stack hangs its tip right under itself
    hover(app, spin, "port:kobi:in:1")
    _, ty1, _, _ = scene_box(find_item(root, "tip:port:kobi:in:1"))
    assert ty1 == ty
    hover(app, spin, "port:kobi:in")
    caret = find_item(root, "tipCaret:port:kobi:in")
    cx, cy, cw, _ = scene_box(caret)
    assert caret.isVisible() and abs((cx + cw / 2) - (px + pw / 2)) <= 0.5
    assert cy < ty  # the caret rises above the box towards the port
    # the sections, word for word: no headings, an icon each
    assert find_item(root, "tipDataLabel:port:kobi:in") is None
    what = find_item(root, "tipWhat:port:kobi:in")
    filt = find_item(root, "tipFilter:port:kobi:in")
    link = find_item(root, "tipLink:port:kobi:in:project")
    assert what.property("text") == "all files"
    assert link.property("text") == "project"
    assert filt.property("text") == "The gallery shows every picture in the pics folder"
    data_icon = find_item(root, "tipDataIcon:port:kobi:in")
    filter_icon = find_item(root, "tipFilterIcon:port:kobi:in")
    assert data_icon.property("name") == "data" and filter_icon.property("name") == "filter"
    for item in (what, filt, link, data_icon, filter_icon):
        assert item.isVisible()
    # the summary is longer than 200 px allows: the box is at its cap and the filter wraps
    assert tw == TIP_MAX and filt.property("lineCount") > 1
    # a hanging indent: each section's text starts past its icon, wrapped lines with it
    # (one text item), the data and filter texts on one left edge
    ix, iy, iw, _ = scene_box(data_icon)
    fix, fiy, _, _ = scene_box(filter_icon)
    wx, wy, _, _ = scene_box(what)
    fx, fy, _, _ = scene_box(filt)
    assert ix == fix and wx == fx and wx >= ix + iw
    assert wy < fy and iy < fiy  # data over filter, each icon on its section
    assert abs(iy - wy) <= 4 and abs(fiy - fy) <= 4  # the icon at the section's start
    # an unlinked output has nothing to filter: no filter section
    hover(app, spin, "port:pola:out")
    assert find_item(root, "tipWhat:port:pola:out").property("text") == (
        "picked items (selection), time"
    )
    assert not find_item(root, "tipFilter:port:pola:out").isVisible()
    assert not find_item(root, "tipFilterIcon:port:pola:out").isVisible()
    assert find_item(root, "tipDataIcon:port:pola:out").isVisible()


def test_a_tip_near_the_window_edge_stays_inside_and_its_caret_still_points_at_the_port(app, spin):
    """The file tree's input circle sits a few px from the window's left edge: the box
    shifts inside the window, the caret stays on the port."""
    compose(app, spin)
    root = app.window.contentItem()
    port = hover(app, spin, "port:fege:in")
    px, _, pw, _ = scene_box(port)
    tx, _, tw, _ = scene_box(find_item(root, "tip:port:fege:in"))
    assert tx >= 0 and tx + tw <= app.window.width()
    assert tx > px + pw / 2 - tw / 2  # moved right of centred
    cx, _, cw, _ = scene_box(find_item(root, "tipCaret:port:fege:in"))
    assert abs((cx + cw / 2) - (px + pw / 2)) <= 0.5


def click_at(app, spin, item) -> None:
    QTest.mouseClick(
        app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(item)
    )
    spin(30)


def test_the_tip_stays_open_while_the_pointer_moves_into_it_and_a_name_is_a_link(app, spin):
    """The pointer leaves the port for the tip: the tip waits for it and stays while it
    is inside; a name underlines under the pointer; leaving the tip closes it."""
    compose(app, spin)
    root = app.window.contentItem()
    hover(app, spin, "port:tamu:in")
    link = find_item(root, "tipLink:port:tamu:in:fege")
    assert link is not None and link.property("text") == "fege"
    assert link.property("color").name() == COLORS["accent"]
    assert not link.property("font").underline()
    QTest.mouseMove(app.window, centre(link))
    spin(GRACE + 100)
    assert tip_shown(root, "port:tamu:in")
    assert link.property("font").underline()
    # plain words are not links
    assert find_item(root, "tipLink:port:tamu:in:") is None
    QTest.mouseMove(app.window, QPoint(app.window.width() - 5, app.window.height() - 5))
    spin(GRACE + 100)
    assert not tip_shown(root, "port:tamu:in")


def test_clicking_a_name_in_a_tip_pans_to_that_view_and_flashes_it(app, spin):
    """#127: a view's name in a tip jumps to it, like an overworld chip: the canvas pans
    until the view is on screen and the view pulses; the project's name goes to the
    root node (Home) and the node pulses."""
    compose(app, spin)
    root = app.window.contentItem()
    flashed: list[str] = []
    app.controller.flash.connect(flashed.append)
    # pan right until the file tree and the root node are off screen, the galleries' left
    # edges (and their input circles) still on it
    app.controller.setPan(380.0, 0.0)
    spin(30)
    vx, vy, vw, vh = app.controller.viewport_rect()
    fx, fy, fw, fh = app.controller.rect_of("fege")
    assert fx + fw < vx  # the file tree is out of sight
    hover(app, spin, "port:tamu:in")
    click_at(app, spin, find_item(root, "tipLink:port:tamu:in:fege"))
    vx, vy, vw, vh = app.controller.viewport_rect()
    assert vx <= fx and fx + fw <= vx + vw and vy <= fy and fy + fh <= vy + vh
    assert flashed == ["fege"] and app.controller.focusedView == "fege"
    spin(GRACE + 100)
    assert not tip_shown(root, "port:tamu:in")  # the jump closes the tip
    # the project's name: Home, the root node pulses
    app.controller.setPan(380.0, 0.0)
    spin(30)
    hover(app, spin, "port:kobi:in")
    click_at(app, spin, find_item(root, "tipLink:port:kobi:in:project"))
    assert app.controller.pan == {"x": 0.0, "y": 0.0}
    assert flashed == ["fege", "project"]
    # a view already on screen is not panned to, only flashed
    hover(app, spin, "port:tamu:in:1")
    click_at(app, spin, find_item(root, "tipLink:port:tamu:in:1:kobi"))
    assert app.controller.pan == {"x": 0.0, "y": 0.0} and flashed[-1] == "kobi"
    assert app.controller.undoDepth == 1  # looking is not an edit


def compose_trees(app, spin) -> None:
    """The user's shape on #92: a second and a third file tree fed by the project's
    tree and the project source, each feeding a gallery (chains through several trees),
    and a gallery pinned off the project source that feeds nothing (#83: no view is
    unfed, so the loose one shows a hollow output circle)."""
    doc = app.live.document()
    pos = {"navo": (300, 40), "zuni": (700, 40), "kalo": (300, 480), "vura": (700, 480)}
    for vid, kind in (
        ("navo", "file_tree"),
        ("zuni", "gallery"),
        ("kalo", "file_tree"),
        ("vura", "gallery"),
    ):
        doc["views"][vid] = {
            "type": kind,
            "layout": {"position": list(pos[vid]), "size": [360, 400]},
        }
    doc["views"]["pola"] = {
        "type": "gallery",
        "layout": {"position": [1100, 40], "size": [300, 300]},
    }

    def intaker(src: str, port: str, dst: str, scope: str) -> dict:
        return {
            "kind": "intaker",
            "scope": scope,
            "source": {"view": src, "port": port},
            "target": {"view": dst, "port": "data"},
            "params": {"sources": ["."], "screener": []},
            "summary": {"sentence": f"{dst} shows the files of {src}."},
        }

    doc["links"]["l2"] = intaker("fege", "selected_files", "navo", "selection")
    doc["links"]["l3"] = intaker("navo", "selected_files", "zuni", "selection")
    doc["links"]["l4"] = intaker("project", "all_files", "kalo", "pinned")
    doc["links"]["l5"] = intaker("kalo", "selected_files", "vura", "selection")
    doc["links"]["l6"] = intaker("project", "all_files", "pola", "pinned")
    # one link between ordinary views, whose identicon keeps the chain colour
    doc["links"]["l7"] = {
        "kind": "follow",
        "source": {"view": "zuni", "port": "selection"},
        "target": {"view": "vura", "port": "selection"},
        "summary": {"sentence": "vura follows what zuni picks."},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "compose"}, AGENT)
    spin(until=lambda: "pola" in app.hosts.hosts)
    spin(100)


def circle_pixels(shot, item, frac: float) -> list[str]:
    """The colours inside a circle's centred square of ``frac`` of its width."""
    dpr = shot.devicePixelRatio()
    c = item.mapToScene(item.boundingRect().center())
    r = item.width() * frac / 2
    out = []
    for dy in range(-int(r), int(r) + 1):
        for dx in range(-int(r), int(r) + 1):
            out.append(shot.pixelColor(int((c.x() + dx) * dpr), int((c.y() + dy) * dpr)).name())
    return out


def test_every_linked_input_circle_shows_its_identicon_inset_in_a_ring(app, spin):
    """#92: each linked input circle carries an identicon the provider served, inset in
    a ring of the chain's colour: the cells and their ground inside, the ring around;
    an unlinked circle stays hollow in the neutral ring."""
    from specto_app.canvas.identicon import identicon_colors, parse_image_id

    compose_trees(app, spin)
    root = app.window.contentItem()
    shot = app.window.grabWindow()
    # hubs upstream (the project source, file trees) are neutral; an ordinary upstream
    # (zuni into vura's second circle) carries the chain
    for tag, hub in (
        ("port:navo:in", True),
        ("port:zuni:in", True),
        ("port:kalo:in", True),
        ("port:vura:in", True),
        ("port:vura:in:1", False),
    ):
        port = find_item(root, tag)
        assert port.isVisible() and port.property("linked"), tag
        img = find_item(port, "identicon:" + tag)
        # the provider served it: an image ready at the inner disc, inside the ring
        assert img.isVisible() and img.property("implicitWidth") > 0, tag
        assert img.width() == PORT - 2 * RING and img.x() == RING, tag
        _, chain, is_round = parse_image_id(port.property("icon").split("/", 3)[-1])
        assert is_round and (chain is None) == hub, tag
        fg, bg = identicon_colors(chain)
        inside = set(circle_pixels(shot, port, 0.5))
        assert fg in inside and bg in inside, (tag, inside)
        # the ring: the chain's colour on the circle's edge
        edge = port.mapToScene(port.boundingRect().center())
        ring = shot.pixelColor(int(edge.x() - PORT / 2 + RING / 2), int(edge.y()))
        assert ring.name() == port.property("ringColor").name(), tag
        assert port.property("ringColor") == port.property("tone"), tag
    loose = find_item(root, "port:pola:out")
    assert not loose.property("linked")
    assert loose.property("ringColor").name() == COLORS["chain_none"]
    assert set(circle_pixels(shot, loose, 0.4)) == {COLORS["surface"]}


def tip_fill(app, tag: str) -> str:
    """The colour the tip is drawn in: a pixel just inside its left edge, clear of text."""
    tip = find_item(app.window.contentItem(), "tip:" + tag)
    assert tip is not None and tip.isVisible(), tag
    r = tip.mapRectToScene(tip.boundingRect())
    return app.window.grabWindow().pixelColor(int(r.left()) + 3, int(r.center().y())).name()


def test_a_port_tip_reads_apart_from_an_ordinary_tip(app, spin):
    """#111: port tips (filter summary and data type) have their own background, so they
    read apart from the header buttons' tips, which keep theirs."""
    compose(app, spin)
    hover(app, spin, "port:kobi:in")
    assert tip_fill(app, "port:kobi:in") == COLORS["tip_port_bg"]
    hover(app, spin, "port:fege:out")
    assert tip_fill(app, "port:fege:out") == COLORS["tip_port_bg"]
    hover(app, spin, "close:kobi")
    assert tip_fill(app, "close:kobi") == COLORS["text"]
