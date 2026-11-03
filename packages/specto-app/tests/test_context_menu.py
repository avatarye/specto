"""Seam 1 (#154): the context-menu registry, headless and Qt-free. Entries are data (id,
label, icon, shortcut, scope, predicates, group, handler); a view's target says what is
under the cursor; the registry answers the rows to show and runs the one picked."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from specto_app.menu import (
    ContextMenuRegistry,
    MenuCall,
    MenuEntry,
    MenuItemRef,
    MenuTarget,
    default_registry,
)

ROOT = os.path.abspath(os.path.join(os.sep, "data", "proj"))


def tree_target(*rels: str, kind: str = "items", root: str = ROOT) -> MenuTarget:
    items = tuple(
        MenuItemRef(key=r, path=os.path.join(ROOT, *r.split("/")), is_dir="." not in r)
        for r in rels
    )
    return MenuTarget(
        view_id="fege", view_type="file_tree", role="browser", kind=kind, items=items, root=root
    )


class Clip:
    def __init__(self) -> None:
        self.text: str | None = None

    def __call__(self, text: str) -> None:
        self.text = text


def run(reg: ContextMenuRegistry, entry_id: str, target: MenuTarget) -> tuple[object, Clip]:
    clip = Clip()
    return reg.run(entry_id, MenuCall(target=target, clipboard=clip)), clip


# -- the registry ----------------------------------------------------------------------
def test_rows_follow_the_scope_groups_and_order_of_the_entries():
    reg = ContextMenuRegistry(groups=("open", "copy"))
    reg.add(MenuEntry(id="b.copy", label="Copy", group="copy", handler=lambda c: None))
    reg.add(
        MenuEntry(id="a.open", label="Open", group="open", shortcut="Enter", handler=lambda c: None)
    )
    reg.add(
        MenuEntry(
            id="a.look", label="Look", group="open", order=-1, icon="eye", handler=lambda c: None
        )
    )
    reg.add(
        MenuEntry(
            id="g.only", label="Gallery only", view_types=("gallery",), handler=lambda c: None
        )
    )
    reg.add(MenuEntry(id="v.view", label="On the view", targets=("view",), handler=lambda c: None))
    rows = reg.rows(tree_target("a.md"))
    assert rows == [
        {"name": "a.look", "text": "Look", "icon": "eye", "detail": "", "enabled": True},
        {"name": "a.open", "text": "Open", "icon": "", "detail": "Enter", "enabled": True},
        {"separator": True},
        {"name": "b.copy", "text": "Copy", "icon": "", "detail": "", "enabled": True},
    ]
    assert [r["name"] for r in reg.rows(tree_target(kind="view"))] == ["v.view"]
    assert reg.rows(tree_target(kind="background")) == []


def test_visible_and_enabled_predicates_read_the_target():
    reg = ContextMenuRegistry()
    reg.add(
        MenuEntry(
            id="one", label="Only one", handler=lambda c: "ran", visible=lambda t: len(t.items) == 1
        )
    )
    reg.add(
        MenuEntry(
            id="files",
            label="Files only",
            handler=lambda c: "ran",
            enabled=lambda t: not any(i.is_dir for i in t.items),
        )
    )
    assert [(r["name"], r["enabled"]) for r in reg.rows(tree_target("a.md"))] == [
        ("one", True),
        ("files", True),
    ]
    assert [(r["name"], r["enabled"]) for r in reg.rows(tree_target("a.md", "notes"))] == [
        ("files", False)
    ]
    # a hidden or disabled entry does not run, whatever asked for it
    assert run(reg, "files", tree_target("notes"))[0] is None
    assert run(reg, "one", tree_target("a.md", "b.md"))[0] is None
    assert run(reg, "one", tree_target("a.md"))[0] == "ran"


def test_an_entry_id_is_unique_and_can_be_removed():
    reg = ContextMenuRegistry()
    entry = MenuEntry(id="x", label="X", handler=lambda c: None)
    reg.add(entry)
    with pytest.raises(ValueError):
        reg.add(entry)
    reg.add(MenuEntry(id="x", label="X2", handler=lambda c: None), replace=True)
    assert reg.get("x").label == "X2"
    reg.remove("x")
    assert reg.get("x") is None and reg.rows(tree_target("a.md")) == []


def test_a_failing_predicate_hides_its_entry_only():
    reg = ContextMenuRegistry()
    reg.add(MenuEntry(id="bad", label="Bad", handler=lambda c: None, visible=lambda t: 1 / 0))
    reg.add(MenuEntry(id="good", label="Good", handler=lambda c: None))
    assert [r["name"] for r in reg.rows(tree_target("a.md"))] == ["good"]


# -- the browsers' first entries: Copy path, Copy relative path -------------------------
def test_browsers_copy_the_absolute_path_with_native_separators():
    reg = default_registry()
    names = [r.get("name") for r in reg.rows(tree_target("runs/front/00001.jpg"))]
    # the tree's own open entries (#43), the launchers (#186), then the copy group
    assert [n for n in names if n] == [
        "tree.open_dedicated",
        "tree.open_contextual",
        "launch.default",
        "launch.open_with",
        "copy.path",
        "copy.relative_path",
    ]
    result, clip = run(reg, "copy.path", tree_target("runs/front/00001.jpg"))
    want = os.path.join(ROOT, "runs", "front", "00001.jpg")
    assert clip.text == want and result == want
    assert "/" not in clip.text if os.sep == "\\" else True


def test_several_items_copy_one_path_per_line():
    reg = default_registry()
    target = tree_target("notes/a.md", "runs/front", "b.md")
    _, clip = run(reg, "copy.path", target)
    assert clip.text == "\n".join(
        [
            os.path.join(ROOT, "notes", "a.md"),
            os.path.join(ROOT, "runs", "front"),
            os.path.join(ROOT, "b.md"),
        ]
    )
    _, clip = run(reg, "copy.relative_path", target)
    assert clip.text == "\n".join(
        [os.path.join("notes", "a.md"), os.path.join("runs", "front"), "b.md"]
    )


def test_relative_path_needs_a_project_root_holding_every_item():
    reg = default_registry()
    elsewhere = os.path.abspath(os.path.join(os.sep, "elsewhere", "x.md"))
    outside = MenuTarget(
        view_id="fege",
        view_type="file_tree",
        role="browser",
        kind="items",
        root=ROOT,
        items=(MenuItemRef(key="x", path=elsewhere),),
    )
    rows = {r["name"]: r for r in reg.rows(outside) if "name" in r}
    assert rows["copy.path"]["enabled"] and not rows["copy.relative_path"]["enabled"]
    rows = {r["name"]: r for r in reg.rows(tree_target("a.md", root="")) if "name" in r}
    assert not rows["copy.relative_path"]["enabled"]
    result, clip = run(reg, "copy.relative_path", outside)
    assert result is None and clip.text is None  # disabled: the clipboard is left alone


def test_copy_entries_belong_to_browsers_only():
    reg = default_registry()
    viewer = MenuTarget(
        view_id="rena",
        view_type="image",
        role="viewer",
        kind="items",
        items=(MenuItemRef(key="a", path=os.path.join(ROOT, "a.jpg")),),
        root=ROOT,
    )
    assert "copy.path" not in [r.get("name") for r in reg.rows(viewer)]


# -- the file tree's open entries (#43) -------------------------------------------------
class FakeTreeHost:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []

    def open_path(self, path: str, mode: str) -> SimpleNamespace:
        self.opened.append((path, mode))
        return SimpleNamespace(view="lito")  # what the tree host answers: the view opened


def test_the_trees_open_entries_open_the_one_file_under_the_cursor_its_own_way():
    """"Open in its own view" and "Open in contextual view" lead the file tree's menu for
    a file: the row that was right-clicked (``data.clicked``) even inside a wider
    selection, else the one file the target holds; a folder, or several files with no
    clicked row, show neither. Each runs the host's ``open_path`` with its mode."""
    reg = default_registry()
    rows = {r["name"]: r for r in reg.rows(tree_target("notes/a.md")) if "name" in r}
    assert rows["tree.open_dedicated"]["text"] == "Open in its own view"
    assert rows["tree.open_contextual"]["text"] == "Open in contextual view"
    assert rows["tree.open_dedicated"]["detail"] == "Double-click"
    assert rows["tree.open_contextual"]["detail"] == "Ctrl+Double-click"
    host = FakeTreeHost()
    call = MenuCall(target=tree_target("notes/a.md"), host=host)
    assert reg.run("tree.open_dedicated", call) == "lito"
    assert reg.run("tree.open_contextual", call) == "lito"
    assert host.opened == [("notes/a.md", "dedicated"), ("notes/a.md", "contextual")]

    names = lambda t: [r["name"] for r in reg.rows(t) if "name" in r]  # noqa: E731
    assert "tree.open_dedicated" not in names(tree_target("notes"))
    assert "tree.open_dedicated" not in names(tree_target("notes/a.md", "notes/b.md"))
    wide = tree_target("notes/a.md", "notes/b.md")
    clicked = MenuTarget(**{**wide.__dict__, "data": {"clicked": "notes/b.md"}})
    assert names(clicked)[:2] == ["tree.open_dedicated", "tree.open_contextual"]
    host = FakeTreeHost()
    reg.run("tree.open_contextual", MenuCall(target=clicked, host=host))
    assert host.opened == [("notes/b.md", "contextual")]
    folder = MenuTarget(**{**tree_target("notes", "notes/a.md").__dict__, "data": {"clicked": "notes"}})
    assert "tree.open_contextual" not in names(folder)
