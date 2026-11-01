"""Seam 2 (#154): views contribute context-menu targets and entries, headless. The file
tree and the gallery (the browsers) say what is under the cursor; the shell's
``ContextMenus`` asks the registry for the rows, opens the one menu and runs the picked
entry: Copy path lands on the clipboard. A web page asks through its bridge
(``contextmenu {x, y, target}``) and gets the same menu."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from gallery_helpers import FakeGallery
from web_helpers import compose_json, new_engine, registry, tree_emits_all

pytest.importorskip("PySide6.QtGui")

from PySide6.QtGui import QGuiApplication

from specto_app.menu import ContextMenuRegistry, MenuEntry
from specto_app.menu.shell import ContextMenus
from specto_app.views.gallery import GalleryHost


class Opened:
    """What the shell's menu was asked to show."""

    def __init__(self, menus: ContextMenus) -> None:
        self.calls: list[tuple[list, float, float, bool]] = []
        menus.openRequested.connect(lambda rows, x, y, kb: self.calls.append((rows, x, y, kb)))

    def names(self) -> list[str]:
        return [r["name"] for r in self.calls[-1][0] if "name" in r]


@pytest.fixture
def s(qapp, tmp_path: Path) -> SimpleNamespace:
    """A project (frames, notes, geo) with its file tree, every folder open, and the
    shell's menu on a headless host registry."""
    engine, proj = new_engine(tmp_path, frames=3)
    reg = registry(engine)
    tree = reg.host("fege")
    tree.load_all()  # every folder listed (#231) ...
    tree.model.expanded = {p for p, n in tree.model._nodes.items() if n.is_dir}
    tree.model._set_rows(tree.model._walk())  # ... and open
    focused = {"view": ""}
    menus = ContextMenus(reg, focused=lambda: focused["view"])
    reg.ctx.context_menus = menus
    return SimpleNamespace(
        engine=engine, proj=proj, reg=reg, tree=tree, menus=menus, focused=focused
    )


def native(s: SimpleNamespace, *rel: str) -> str:
    return os.path.join(str(s.proj.root), *rel)


def test_right_click_on_a_tree_row_copies_its_path_and_keeps_the_selection(s):
    opened = Opened(s.menus)
    s.tree.bridge.contextMenu(s.tree.model.row_of("notes/alpha.md"), 120.0, 80.0)
    assert opened.calls[-1][1:] == (120.0, 80.0, False)
    # the launchers (#186) open it, then the copy group
    assert opened.names() == [
        "launch.default",
        "launch.open_with",
        "copy.path",
        "copy.relative_path",
    ]
    assert s.tree.model.selected == set()  # a right-click is no pick

    assert s.menus.trigger("copy.path") == native(s, "notes", "alpha.md")
    assert QGuiApplication.clipboard().text() == native(s, "notes", "alpha.md")
    assert s.menus.trigger("copy.relative_path") == os.path.join("notes", "alpha.md")
    assert QGuiApplication.clipboard().text() == os.path.join("notes", "alpha.md")


def test_right_click_inside_the_selection_copies_every_selected_row_one_per_line(s):
    s.tree.select_paths(["notes/beta.md", "geo", "notes/alpha.md"])
    s.tree.bridge.contextMenu(s.tree.model.row_of("notes/beta.md"), 10.0, 10.0)
    # in the tree's order: folders first, then names
    assert s.menus.trigger("copy.path") == "\n".join(
        [native(s, "geo"), native(s, "notes", "alpha.md"), native(s, "notes", "beta.md")]
    )
    # a row outside the selection is the menu's alone
    s.tree.bridge.contextMenu(s.tree.model.row_of("geo/points.geojson"), 10.0, 10.0)
    assert s.menus.trigger("copy.relative_path") == os.path.join("geo", "points.geojson")


def test_the_menu_key_opens_the_menu_for_the_focused_views_selection(s):
    opened = Opened(s.menus)
    assert s.menus.openFor("") is False and not opened.calls  # no focused view: no menu
    s.focused["view"] = "fege"
    assert s.menus.openFocused() is False  # nothing picked: nothing to copy, no menu
    s.tree.select_paths(["notes/alpha.md"])
    assert s.menus.openFocused() is True
    assert opened.calls[-1][3] is True and "copy.path" in opened.names()
    assert s.menus.trigger("copy.path") == native(s, "notes", "alpha.md")


def test_gallery_tile_menu_opens_and_copies_the_tiles_shown_file(s):
    compose_json(s.engine)
    tree_emits_all(s.engine)
    ctx = s.reg.ctx
    host = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=FakeGallery())
    s.reg.hosts["kobi"] = host
    host.receive("data", s.engine.links.input_of("kobi", "data"))
    opened = Opened(s.menus)

    host.bridge.contextMenu(1, 50.0, 60.0)
    assert opened.names() == [
        "gallery.open_preview",
        "gallery.open_image",
        "gallery.quick_look",
        "launch.default",
        "launch.open_with",
        "copy.path",
        "copy.relative_path",
    ]
    rows = opened.calls[-1][0]
    assert {"separator": True} in rows
    assert [r.get("detail") for r in rows if r.get("name") == "gallery.quick_look"] == ["Space"]
    # the tile shows its image, not the json sidecar beside it
    front = s.proj.root / "runs" / "cam-a"
    jpg = next(n for n in os.listdir(front) if n.startswith("00001_") and n.endswith(".jpg"))
    assert s.menus.trigger("copy.path") == native(s, "runs", "cam-a", jpg)

    looks: list[int] = []
    host.bridge.quickLookRequested.connect(looks.append)
    s.menus.trigger("gallery.quick_look")
    assert looks == [1]
    assert s.menus.trigger("gallery.open_image")  # the image view's id
    assert host.gallery.selected() == 1


def test_a_web_page_asks_for_the_menu_through_its_bridge(s):
    compose_json(s.engine)
    seen = []
    s.menus.registry = ContextMenuRegistry(
        [
            MenuEntry(
                id="json.fold_all",
                label="Fold all",
                view_types=("json",),
                targets=("background",),
                handler=lambda call: seen.append(call) or "folded",
            ),
        ]
    )
    opened = Opened(s.menus)
    h = s.reg.host("tamu")
    h.bridge.ready()
    page_target = {"kind": "background", "line": 7}
    h.bridge.send("contextmenu", json.dumps({"x": 30, "y": 40, "target": page_target}))
    assert opened.names() == ["json.fold_all"]
    assert opened.calls[-1][1:] == (30.0, 40.0, False)
    assert s.menus.trigger("json.fold_all") == "folded"
    assert seen[0].host is h and seen[0].target.data == page_target
    assert seen[0].target.view_type == "json" and seen[0].target.role == "viewer"
    # a payload that is not an object is the page's background at its corner
    h.bridge.send("contextmenu", "not json")
    assert opened.calls[-1][1:3] == (0.0, 0.0) and opened.names() == ["json.fold_all"]
