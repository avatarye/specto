"""Seam 1 (#185): what a drag out of a view carries, Qt-free. A view's target (the same
one its context menu acts on) becomes the mime data of the drag contract: Specto's own
json first, then a picture, the files' URLs, and plain text."""

from __future__ import annotations

import json
import os

from specto_app.drag.payload import (
    PNG_MIME,
    SPECTO_MIME,
    TEXT_MIME,
    URI_MIME,
    DragItem,
    DragPayload,
    payload_for,
)
from specto_app.menu import MenuItemRef, MenuTarget

ROOT = os.path.normpath("C:/proj") if os.name == "nt" else "/proj"


def native(*rel: str) -> str:
    return os.path.join(ROOT, *rel)


def target(*items: MenuItemRef, view_type: str = "gallery") -> MenuTarget:
    return MenuTarget(
        view_id="kobi", view_type=view_type, role="browser", kind="items", items=items, root=ROOT
    )


def specto(p: DragPayload) -> dict:
    return json.loads(p.formats()[SPECTO_MIME].decode("utf-8"))


def test_a_gallery_tile_drags_as_an_image_item_with_its_file():
    jpg = native("runs", "run-001", "cam-a", "00042.jpg")
    p = payload_for(
        target(MenuItemRef(key="00042", path=jpg, kind="image2d")),
        labels={"00042": "front/00042"},
    )
    root = ROOT.replace("\\", "/")
    assert specto(p) == {
        "items": [
            {
                "view": "kobi",
                "type": "gallery",
                "key": "00042",
                "path": "runs/run-001/cam-a/00042.jpg",
                "abs": f"{root}/runs/run-001/cam-a/00042.jpg",
                "kind": "image",
                "label": "front/00042",
                "meta": {},
            }
        ],
        "text": None,
        "project": root,
    }
    fm = p.formats()
    uri = fm[URI_MIME].decode()
    assert uri.endswith("\r\n") and uri.startswith("file:///")
    assert uri.strip().endswith("runs/run-001/cam-a/00042.jpg")
    assert fm[TEXT_MIME].decode() == jpg  # the path, native separators
    assert PNG_MIME not in fm


def test_formats_come_in_the_order_drop_targets_read_them():
    p = payload_for(
        target(MenuItemRef(key="a", path=native("a.png"))),
        png=b"\x89PNG..",
        text="crop",
        uri_list=True,
    )
    assert list(p.formats()) == [SPECTO_MIME, PNG_MIME, URI_MIME, TEXT_MIME]
    assert p.formats()[PNG_MIME] == b"\x89PNG.."


def test_several_tree_rows_drag_one_url_and_one_path_per_line_in_order():
    t = target(
        MenuItemRef(key="geo", path=native("geo"), is_dir=True),
        MenuItemRef(key="notes/alpha.md", path=native("notes", "alpha.md")),
        MenuItemRef(key="runs/x.jpg", path=native("runs", "x.jpg")),
        view_type="file_tree",
    )
    p = payload_for(t)
    items = specto(p)["items"]
    assert [(i["key"], i["path"], i["kind"], i["label"], i["meta"]) for i in items] == [
        ("geo", "geo", "file", "geo", {"dir": True}),
        ("notes/alpha.md", "notes/alpha.md", "file", "alpha.md", {}),
        ("runs/x.jpg", "runs/x.jpg", "image", "x.jpg", {}),
    ]
    assert p.formats()[URI_MIME].decode().count("\r\n") == 3
    assert p.formats()[TEXT_MIME].decode().splitlines() == [
        native("geo"),
        native("notes", "alpha.md"),
        native("runs", "x.jpg"),
    ]
    assert p.label == "geo  +2"


def test_a_file_outside_the_project_keeps_its_absolute_path():
    other = os.path.normpath("D:/elsewhere/b.json") if os.name == "nt" else "/elsewhere/b.json"
    p = payload_for(target(MenuItemRef(key=other, path=other)))
    item = specto(p)["items"][0]
    assert item["path"] == item["abs"] == other.replace("\\", "/")


def test_a_container_entry_is_an_item_but_no_file_url():
    zipped = native("pack.zip") + "#inner/a.jpg"
    p = payload_for(target(MenuItemRef(key="a", path=zipped, kind="image2d")))
    item = specto(p)["items"][0]
    assert item["path"] == "pack.zip#inner/a.jpg" and item["kind"] == "image"
    assert URI_MIME not in p.formats()
    assert p.formats()[TEXT_MIME].decode() == zipped  # its address, as there is no file
    assert p.label == "a.jpg"


def test_a_text_selection_drags_its_text_with_the_file_it_came_from():
    md = native("notes", "alpha.md")
    p = payload_for(
        target(MenuItemRef(key="alpha", path=md), view_type="markdown"),
        kind="text",
        text="the selected words",
        html="<b>the</b> selected words",
    )
    doc = specto(p)
    assert doc["text"] == "the selected words"
    assert doc["items"][0]["kind"] == "text" and doc["items"][0]["path"] == "notes/alpha.md"
    fm = p.formats()
    assert fm[TEXT_MIME].decode() == "the selected words"  # the text, not the path
    assert fm["text/html"].decode() == "<b>the</b> selected words"
    assert URI_MIME not in fm  # a text editor would open the file instead of the words


def test_a_picture_goes_out_without_the_file_url_unless_asked():
    t = target(MenuItemRef(key="a", path=native("a.png")))
    assert URI_MIME not in payload_for(t, png=b"\x89PNG..").formats()
    assert URI_MIME in payload_for(t, png=b"\x89PNG..", uri_list=True).formats()


def test_text_alone_carries_no_items_and_no_urls():
    p = DragPayload(text="loose words", project="C:/proj")
    assert specto(p) == {"items": [], "text": "loose words", "project": "C:/proj"}
    assert list(p.formats()) == [SPECTO_MIME, TEXT_MIME]
    assert p.label == "loose words"


def test_meta_rides_along_on_every_item():
    p = payload_for(target(MenuItemRef(key="a", path=native("a.png"))), meta={"crop": [1, 2, 3, 4]})
    assert specto(p)["items"][0]["meta"] == {"crop": [1, 2, 3, 4]}
    assert DragItem(view="v", type="t", abs="C:/x#y").file == ""
