"""The scratch pad (#184) at seam 1: the store in .specto/scratch (named pages of
markdown, their assets, undo, the migration from the card store), a drop's mime data read
in the contract's order and turned into markdown, and the ``scratch`` view type on the
canvas (one per canvas, a source of its own, opened from the project node)."""

from __future__ import annotations

import json
from itertools import count
from pathlib import Path

import pytest

from specto_core import gestures
from specto_core.canvas_document import CanvasInvalid, unreachable_views
from specto_core.canvas_example import worked_example
from specto_core.engine import Engine
from specto_core.errors import InvalidArgs, NotFound, TxnError
from specto_core.live_canvas import LiveCanvas
from specto_core.project import Project
from specto_core.scratch import NewItem, ScratchStore, entries_from_mime, joined

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24  # the store checks the signature only


def ticking():
    """A clock whose stamps strictly increase (a step's ``ts``)."""
    n = count(1)
    return lambda: f"2026-10-25T10:00:{next(n):02d}.000+00:00"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "runs").mkdir(parents=True)
    (root / "runs" / "00042.jpg").write_bytes(b"jpg")
    return root


@pytest.fixture
def store(root: Path) -> ScratchStore:
    return ScratchStore(root, clock=ticking())


def pages(root: Path) -> dict:
    return json.loads((root / ".specto/scratch/pages.json").read_text(encoding="utf-8"))


def md(root: Path, page: str = "p1") -> str:
    return (root / f".specto/scratch/{page}.md").read_text(encoding="utf-8")


# -- the store ---------------------------------------------------------------------------
def test_a_page_is_one_markdown_file_appended_to_and_kept(store, root):
    assert store.text() == "" and [p["name"] for p in store.pages] == ["Page 1"]
    store.append("# Findings\nthe lidar drifts")
    store.append("second block")
    assert md(root) == "# Findings\nthe lidar drifts\n\nsecond block\n"
    assert pages(root)["pages"] == [{"id": "p1", "name": "Page 1"}]
    again = ScratchStore(root)
    assert again.text() == md(root) and again.active == "p1"


def test_markdown_of_drops_text_pictures_files_and_specto_quotes(store, root):
    frame = root / "runs" / "00042.jpg"
    text, assets = store.markdown_of(
        [
            NewItem(kind="text", text="plain words"),
            NewItem(kind="image", png=PNG, label="front crop"),
            NewItem(kind="file", path=str(frame)),
            NewItem(
                kind="text",
                text="selected\nwords",
                source={"view": "rena", "key": "notes", "path": "notes.md"},
            ),
            NewItem(kind="file", path=str(frame), label="00042", source={"view": "kobi"}),
        ]
    )
    assert assets == [("front-crop.png", PNG)]
    assert text.split("\n\n") == [
        "plain words",
        "![front crop](p1.assets/front-crop.png)",
        "[00042.jpg](../../runs/00042.jpg)",
        "> selected\n> words\n>\n> — rena · notes.md",
        "> [00042](../../runs/00042.jpg)\n>\n> — kobi",
    ]
    step = store.append(text, assets=assets)
    assert (root / ".specto/scratch/p1.assets/front-crop.png").read_bytes() == PNG
    assert step.assets == ["p1.assets/front-crop.png"]
    # a second picture of the same name does not overwrite the first
    again, assets2 = store.markdown_of([NewItem(kind="image", png=PNG, label="front crop")])
    assert assets2[0][0] == "front-crop-2.png" and "front-crop-2.png" in again


def test_a_drop_lands_at_the_caret_as_its_own_block():
    assert joined("", "a") == "a\n"
    assert joined("one\n", "two") == "one\n\ntwo\n"
    assert joined("one\ntwo\n", "mid", at=4) == "one\n\nmid\n\ntwo\n"
    assert joined("one\n\ntwo\n", "mid", at=5) == "one\n\nmid\n\ntwo\n"


def test_the_collection_is_the_page_then_what_it_links(store, root):
    store.append(
        "![crop](p1.assets/c.png)\n\n[frame](../../runs/00042.jpg)\n\n"
        "[web](https://example.com) and [gone](../../nothing.txt)"
    )
    (root / ".specto/scratch/p1.assets").mkdir(parents=True)
    (root / ".specto/scratch/p1.assets/c.png").write_bytes(PNG)
    col = store.collection()
    assert [it.key for it in col.items] == [
        "p1",
        ".specto/scratch/p1.assets/c.png",
        "runs/00042.jpg",
    ]
    assert list(col.items[0].parts) == [".md"] and col.items[0].meta["kind"] == "page"
    assert [it.meta["kind"] for it in col.items[1:]] == ["image", "file"]
    assert Path(col.items[2].parts[".jpg"]) == root / "runs" / "00042.jpg"


def test_appends_and_writes_are_steps_and_undo_takes_the_assets(store, root):
    store.append("keep me")
    text, assets = store.markdown_of([NewItem(kind="image", png=PNG, label="pic")])
    store.append(text, assets=assets)
    pic = root / ".specto/scratch/p1.assets/pic.png"
    assert pic.exists()
    assert store.write("rewritten\n").label == "edit page Page 1"
    assert store.write("rewritten\n") is None  # nothing changed: no step
    store.undo()
    assert "![pic]" in store.text()
    store.undo()
    assert store.text() == "keep me\n" and not pic.exists()
    store.redo()
    assert pic.exists() and "![pic]" in store.text()
    store.undo()
    store.undo()
    assert store.text() == "" and store.top is None
    with pytest.raises(TxnError):
        store.undo()
    ScratchStore(root)
    assert not (root / ".specto/scratch/.trash").exists()


def test_a_turn_squashes_into_one_step_and_a_user_step_stays_its_own(store):
    store.append("mine")  # the user's, before the turn
    start = store.cursor
    store.append("a", origin="agent:prompt")
    store.append("b", origin="agent:prompt")
    folded = store.squash(start, label="agent turn", origin="agent:prompt")
    assert folded is not None and [s.label for s in store.history] == ["add mine", "agent turn"]
    store.undo()
    assert store.text() == "mine\n"
    store.redo()
    start = store.cursor
    store.append("c", origin="agent:prompt")
    store.write("user edit\n", origin="user")
    store.append("d", origin="agent:prompt")
    store.append("e", origin="agent:prompt")
    store.squash(start, label="turn 2", origin="agent:prompt")
    assert [s.label for s in store.history[-3:]] == ["add c", "edit page Page 1", "turn 2"]


def test_what_is_refused_before_anything_is_written(store, root):
    with pytest.raises(InvalidArgs):
        store.append("   ")
    with pytest.raises(InvalidArgs):
        store.markdown_of([NewItem(kind="image", png=b"GIF89a")])
    with pytest.raises(NotFound):
        store.markdown_of([NewItem(kind="file", path="runs/missing.jpg")])
    assert not (root / ".specto" / "scratch" / "p1.md").exists()


# -- pages -------------------------------------------------------------------------------
def test_pages_are_files_named_in_pages_json_and_come_back(store, root):
    store.append("first page note")
    step = store.add_page("lidar")
    assert step.label == "add page lidar" and store.active == "p2" and md(root, "p2") == ""
    store.append("drift at 00042")  # the active page
    store.append("back on one", page="Page 1")  # by name
    assert md(root) == "first page note\n\nback on one\n" and md(root, "p2") == "drift at 00042\n"
    data = pages(root)
    assert data["pages"] == [{"id": "p1", "name": "Page 1"}, {"id": "p2", "name": "lidar"}]
    assert data["active"] == "p2"
    # the active page is navigation: no step, kept for the next launch
    depth = len(store.history)
    assert store.set_active("Page 1") and len(store.history) == depth
    again = ScratchStore(root)
    assert again.active == "p1" and again.text("lidar") == "drift at 00042\n"


def test_rename_move_and_close_a_page_each_one_step(store, root):
    store.append("a")
    store.add_page("lidar")
    text, assets = store.markdown_of([NewItem(kind="image", png=PNG, label="pic")])
    store.append(text, assets=assets)
    store.add_page()  # an unnamed page gets the next free "Page <n>"
    assert [p["name"] for p in store.pages] == ["Page 1", "lidar", "Page 3"]
    store.rename_page("p3", "exposure")
    store.move_page("exposure", 0)
    assert [p["name"] for p in store.pages] == ["exposure", "Page 1", "lidar"]
    with pytest.raises(InvalidArgs):
        store.rename_page("p1", "LIDAR")  # names are unique, case aside
    step = store.remove_page("lidar")
    assert step.label == "close page lidar (not empty)"
    assert store.page_ids() == ["p3", "p1"]
    assert not (root / ".specto/scratch/p2.md").exists()
    assert not (root / ".specto/scratch/p2.assets").exists()
    assert store.active == "p3"  # it was not the shown page
    store.set_active("p1")
    store.remove_page("p1")
    assert store.active == "p3"  # the shown page went: its neighbour shows
    store.undo()
    store.undo()
    assert store.page_ids() == ["p3", "p1", "p2"]
    assert (root / ".specto/scratch/p2.assets/pic.png").read_bytes() == PNG
    assert "![pic]" in store.text("p2")
    store.redo()
    assert "p2" not in store.page_ids()
    store.remove_page("p3")
    with pytest.raises(InvalidArgs):
        store.remove_page("p1")  # the pad keeps one page


# -- the card store before markdown pages ---------------------------------------------------
def test_a_card_store_before_pages_becomes_page_one_markdown(root):
    old = root / ".specto" / "scratch"
    old.mkdir(parents=True)
    (old / "s1.md").write_text("# kept\nwords", encoding="utf-8")
    (old / "s2.png").write_bytes(PNG)
    items = [
        {"id": "s1", "kind": "text", "label": "kept", "file": "s1.md"},
        {"id": "s2", "kind": "image", "label": "pic", "file": "s2.png"},
        {"id": "s3", "kind": "file", "label": "frame", "path": "runs/00042.jpg"},
    ]
    (old / "index.json").write_text(json.dumps({"next": 4, "items": items}), encoding="utf-8")
    store = ScratchStore(root)
    assert store.page_ids() == ["p1"]
    assert store.text() == (
        "# kept\nwords\n\n![pic](p1.assets/s2.png)\n\n[frame](../../runs/00042.jpg)\n"
    )
    assert (old / "p1.assets" / "s2.png").read_bytes() == PNG
    assert not (old / "index.json").exists() and not (old / "s1.md").exists()
    assert pages(root)["version"] == 2
    assert ScratchStore(root).text() == store.text()  # once


def test_a_card_store_with_pages_becomes_one_markdown_file_per_page(root):
    old = root / ".specto" / "scratch"
    for pid, items in {
        "p1": [{"id": "s1", "kind": "text", "label": "a", "file": "s1.md"}],
        "p2": [{"id": "s2", "kind": "image", "label": "b", "file": "s2.png"}],
    }.items():
        (old / pid).mkdir(parents=True)
        for it in items:
            (old / pid / it["file"]).write_bytes(b"note one" if it["kind"] == "text" else PNG)
        (old / pid / "index.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    pages_json = {"version": 1, "active": "p2", "next": 3, "next_page": 3}
    pages_json["pages"] = [{"id": "p1", "name": "Page 1"}, {"id": "p2", "name": "lidar"}]
    (old / "pages.json").write_text(json.dumps(pages_json), encoding="utf-8")
    store = ScratchStore(root)
    assert [p["name"] for p in store.pages] == ["Page 1", "lidar"] and store.active == "p2"
    assert store.text("p1") == "note one\n"
    assert store.text("lidar") == "![b](p2.assets/s2.png)\n"
    assert not (old / "p1").exists() and not (old / "p2").exists()


# -- drops and pastes ------------------------------------------------------------------------
def payload(*items: dict, text: str | None = None) -> bytes:
    return json.dumps({"items": list(items), "text": text, "project": "x"}).encode()


def test_specto_files_are_references_even_with_a_png_beside_them(root):
    tile = {
        "view": "kobi",
        "type": "gallery",
        "key": "00042",
        "path": "runs/00042.jpg",
        "abs": str(root / "runs" / "00042.jpg"),
        "kind": "image",
        "label": "front/00042",
    }
    out = entries_from_mime(
        {
            "application/x-specto-item+json": payload(tile),
            "image/png": PNG,
            "text/uri-list": b"file:///elsewhere.jpg",
        }
    )
    assert [(e.kind, e.label) for e in out] == [("file", "front/00042")]
    assert out[0].source == {
        "view": "kobi",
        "key": "00042",
        "path": "runs/00042.jpg",
        "label": "front/00042",
    }


def test_a_rendered_picture_is_one_image(root):
    frame = {"view": "tamu", "type": "video", "key": "12.4", "kind": "frame", "label": "t 12.4"}
    out = entries_from_mime({"application/x-specto-item+json": payload(frame), "image/png": PNG})
    assert [(e.kind, e.png, e.label) for e in out] == [("image", PNG, "t 12.4")]
    crop = {"view": "duna", "abs": str(root / "runs" / "00042.jpg"), "kind": "image"}
    crop["meta"] = {"rendered": True}
    out = entries_from_mime({"application/x-specto-item+json": payload(crop), "image/png": PNG})
    assert [e.kind for e in out] == ["image"]


def test_a_text_selection_is_the_words_not_its_file(root):
    """drag185: a selection in a markdown, json or html view is one item of kind text whose
    abs is the note it came from, the words in payload.text; no text/uri-list."""
    note = root / "notes.md"
    note.write_text("# notes\nthe lidar drifts at 00042\n", encoding="utf-8")
    sel = {"view": "rena", "type": "markdown", "key": "notes", "path": "notes.md"}
    sel |= {"abs": str(note), "kind": "text", "label": "notes"}
    out = entries_from_mime(
        {
            "application/x-specto-item+json": payload(sel, text="lidar drifts"),
            "text/plain": b"lidar drifts",
            "text/html": b"<p>lidar drifts</p>",
        }
    )
    assert [(e.kind, e.text, e.path) for e in out] == [("text", "lidar drifts", None)]
    assert out[0].source == {"view": "rena", "key": "notes", "path": "notes.md", "label": "notes"}


def test_a_crop_without_uri_list_is_the_png(root):
    crop = {"view": "duna", "type": "image", "key": "00042", "kind": "image"}
    crop |= {"abs": str(root / "runs" / "00042.jpg"), "label": "00042 crop"}
    crop["meta"] = {"crop": [10, 10, 40, 30], "size": [640, 480], "rendered": True}
    out = entries_from_mime(
        {"application/x-specto-item+json": payload(crop), "image/png": PNG, "text/plain": b"x"}
    )
    assert [(e.kind, e.png, e.label) for e in out] == [("image", PNG, "00042 crop")]


def test_a_specto_text_and_items_without_files():
    out = entries_from_mime({"application/x-specto-item+json": payload(text="a selection")})
    assert [(e.kind, e.text) for e in out] == [("text", "a selection")]
    prim = {"view": "usdv", "key": "w#/World/Car", "kind": "prim", "label": "/World/Car"}
    out = entries_from_mime({"application/x-specto-item+json": payload(prim)})
    assert [(e.kind, e.text) for e in out] == [("text", "/World/Car")]


def test_outside_apps_png_then_files_then_text(root):
    a = root / "runs" / "00042.jpg"
    b = root / "runs" / "two words.txt"
    b.write_text("x", encoding="utf-8")
    assert [e.kind for e in entries_from_mime({"image/png": PNG, "text/plain": b"x"})] == ["image"]
    uris = f"{a.as_uri()}\r\n{b.as_uri()}\r\nhttps://example.com/cat.png\r\n".encode()
    out = entries_from_mime({"text/uri-list": uris, "text/plain": b"ignored"})
    assert [(e.kind, e.path or e.text) for e in out] == [
        ("file", a.as_posix()),
        ("file", b.as_posix()),
        ("text", "[cat.png](https://example.com/cat.png)"),
    ]
    assert entries_from_mime({"text/plain": "naïve\n".encode()})[0].text == "naïve\n"
    assert entries_from_mime({"text/plain": b"   "}) == []


# -- the view type on the canvas ---------------------------------------------------------------
def scratch_view(**layout) -> dict:
    return {"type": "scratch", "label": "scratch pad", "layout": layout or {"position": [0, 600]}}


def test_the_scratch_pad_needs_no_link_and_feeds_what_follows_it():
    live = LiveCanvas.headless("demo")
    live.submit(worked_example(), origin="agent:s1", label="compose")
    res = live.submit(
        edits={
            "views": {
                "new1": scratch_view(),
                "new2": {"type": "gallery", "label": "kept", "layout": {"position": [400, 600]}},
            },
            "links": {
                "new3": {
                    "kind": "follow",
                    "source": {"view": "new1", "port": "items"},
                    "target": {"view": "new2", "port": "data"},
                    "summary": {"sentence": "The gallery shows the scratch items."},
                }
            },
        },
        origin="agent:s1",
        label="scratch pad and its gallery",
    )
    assert not unreachable_views(live.data)
    pad = res.renamed["new1"]
    with pytest.raises(CanvasInvalid) as e:
        live.submit(edits={"views": {"new4": scratch_view()}}, origin="agent:s1")
    assert any("at most one" in p["message"] and pad in p["message"] for p in e.value.problems)


def test_describe_view_scratch():
    engine = Engine(project=None)
    info = engine.call("describe", {"target": "view:scratch"})["info"]
    assert info["role"] == "browser"
    assert info["ports"]["outputs"] == {"items": "collection", "selection": "selection"}
    assert info["ports"]["inputs"] == {"selection": "selection"}


def test_open_scratch_adds_it_beside_the_file_tree_once(tmp_path: Path):
    engine = Engine(project=Project.init(tmp_path / "p", "demo"))
    live = engine.live
    vid, res = gestures.open_scratch(live)
    assert res is not None and res.origin == "user"
    view = live.data["views"][vid]
    assert view["type"] == "scratch" and view["label"] == "scratch pad"
    tree = next(v for v in live.data["views"].values() if v["type"] == "file_tree")
    assert view["layout"]["position"][0] > tree["layout"]["position"][0]
    assert gestures.open_scratch(live) == (vid, None)


def test_open_from_scratch_follows_its_items_and_pick(tmp_path: Path):
    engine = Engine(project=Project.init(tmp_path / "p", "demo"))
    live = engine.live
    pad, _ = gestures.open_scratch(live)
    vid, res = gestures.open_from_scratch(live, pad, "image")
    assert res is not None and live.data["views"][vid]["type"] == "image"
    ends = {
        (ln["source"]["port"], ln["target"]["port"], ln.get("two_way", False))
        for ln in live.data["links"].values()
        if ln["source"]["view"] == pad
    }
    assert ends == {("items", "data", False), ("selection", "selection", True)}
    assert gestures.open_from_scratch(live, pad, "image") == (vid, None)
    preview, res = gestures.open_from_scratch(live, pad, "preview")
    assert res is not None and preview != vid


def test_a_file_url_is_a_local_path_by_the_platform_s_rules():
    import os

    from specto_core.scratch import local_path

    assert local_path("https://x.org/a.png") is None
    assert local_path("file://server/share/a.png") == "//server/share/a.png"
    assert local_path("file://localhost/data/a%20b.png") == "/data/a b.png"
    if os.name == "nt":
        assert local_path("file:///C:/a%20b.png") == "C:/a b.png"
    else:
        assert local_path("file:///home/u/a%20b.png") == "/home/u/a b.png"
        assert local_path("file:///C:/a.png") == "/C:/a.png"  # a folder named C: here
