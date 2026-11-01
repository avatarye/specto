"""Seam 2 (#107; spec #103 decision 4): viewers and players step through their input
collection. Previous / next move through the collection in its order and stop at the
ends; a view with a selection output emits the item it steps to (a user gesture like a
pick: no submit, no undo step); the controls hear where the view is (index, count)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app_doc_helpers import pin
from test_pdf_host import papers
from test_video_host import VIDEO_DIR, video_items
from test_web_hosts import compose_deck, compose_notes
from web_helpers import Sink, compose_json, make_deck, new_engine, registry, tree_emits_all

from specto_app.canvas.controller import GESTURES, status_hint
from specto_app.hosts import HostContext
from specto_app.views.image_view import ImageHost
from specto_app.views.pdf_view import PdfHost
from specto_app.views.video_view import VideoHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, Selection

needs_video = pytest.mark.skipif(not (VIDEO_DIR / "cam-a.mp4").exists(), reason="no cam-a.mp4")


def frames(root: Path, n: int = 3) -> Collection:
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(n):
        p = root / f"{i:05d}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes(16) + b"\xff\xd9")
        items.append(Item(key=f"{i:05d}", parts={".jpg": str(p)}))
    return Collection(items=items, root=str(root))


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager())


def place(engine: Engine, view_id: str, view_type: str) -> HostContext:
    doc = engine.live.document()
    doc["views"][view_id] = {"type": view_type, "label": view_type, "layout": {}}
    pin(doc, view_id)
    engine.live.submit(doc, origin="agent:s1", label=view_type)
    return HostContext(engine, None)


def test_image_steps_in_collection_order_stops_at_the_ends_and_emits_the_pick(
    engine: Engine, tmp_path: Path
):
    ctx = place(engine, "tamu", "image")
    host = ImageHost(ctx, "tamu", ctx.manifest_for("image"), headless=True)
    assert host.step_item(1) == -1 and host.step_state() == {"index": -1, "count": 0}

    host.receive("data", frames(tmp_path / "front"))
    assert host.step_state() == {"index": 0, "count": 3}
    depth = engine.live.undo_info()["depth"]

    assert host.step_item(1) == 1 and host.key == "00001"
    assert ctx.links.output_of("tamu", "selection") == Selection(keys=["00001"], last="00001")
    assert host.step_item(1) == 2 and host.step_state() == {"index": 2, "count": 3}
    # the end stops: no wrap to the first, nothing new emitted
    assert host.step_item(1) == 2 and host.key == "00002"
    assert host.step_item(-1) == 1 and host.step_item(-1) == 0 and host.step_item(-1) == 0
    assert ctx.links.output_of("tamu", "selection").last == "00000"
    assert engine.live.undo_info()["depth"] == depth  # a pick, not a submit


# -- web viewers: the page sends `step {delta}`, hears `step {index, count}` ---------------
def test_json_page_steps_through_the_collection_and_emits_the_pick(tmp_path: Path):
    engine, _ = new_engine(tmp_path, frames=3)
    compose_json(engine)
    h = registry(engine).host("tamu")
    sink = Sink(h.bridge)
    h.bridge.ready()
    tree_emits_all(engine)
    h.flush()
    assert sink.last("item")["key"] == "00000" and sink.last("step") == {"index": 0, "count": 3}
    depth = engine.live.undo_info()["depth"]

    h.bridge.send("step", json.dumps({"delta": 1}))
    h.flush()
    assert sink.last("item")["key"] == "00001" and sink.last("step") == {"index": 1, "count": 3}
    assert engine.links.output_of("tamu", "selection") == Selection(keys=["00001"], last="00001")
    assert engine.links.output_of("tamu", "content").entries[0].data["frame"] == 1
    h.bridge.send("step", json.dumps({"delta": 99}))  # End: the last, no wrap
    h.bridge.send("step", json.dumps({"delta": 1}))
    h.flush()
    assert h.current_key == "00002" and sink.last("step") == {"index": 2, "count": 3}
    # a selection from the linked gallery moves the controls too, and is not re-emitted
    engine.links.emit("kobi", "selection", Selection(keys=["00000"], last="00000"))
    h.flush()
    assert sink.last("step") == {"index": 0, "count": 3}
    assert engine.links.output_of("tamu", "selection").last == "00002"
    assert engine.live.undo_info()["depth"] == depth


@pytest.mark.parametrize("view_type", ["markdown", "html"])
def test_markdown_and_html_pages_step_and_emit_the_pick(tmp_path: Path, view_type: str):
    engine, proj = new_engine(tmp_path)
    if view_type == "html":
        make_deck(proj.root / "deck")
        engine.links.resolve("l1")  # the project scan ran before the deck was written
        compose_deck(engine)
        first, second = "deck", "notes"
    else:
        compose_notes(engine)
        first, second = "alpha", "beta"
    h = registry(engine).host("kobi")
    sink = Sink(h.bridge)
    h.bridge.ready()
    tree_emits_all(engine)
    h.flush()
    assert h.current_key == first and sink.last("step") == {"index": 0, "count": 2}

    h.bridge.send("step", json.dumps({"delta": -1}))  # the first: stays
    h.flush()
    assert h.current_key == first and engine.links.output_of("kobi", "selection") is None
    h.bridge.send("step", json.dumps({"delta": 1}))
    h.flush()
    assert h.current_key == second and sink.last("item")["key"] == second
    assert sink.last("step") == {"index": 1, "count": 2}
    assert engine.links.output_of("kobi", "selection") == Selection(keys=[second], last=second)


# -- pdf: steps documents, emits nothing (no selection output) -------------------------
def test_pdf_steps_through_its_documents_and_emits_nothing(qapp, engine: Engine, tmp_path):
    ctx = place(engine, "v3", "pdf")
    host = PdfHost(ctx, "v3", ctx.manifest_for("pdf"), headless=True)
    host.receive("data", papers(tmp_path / "papers"))  # a.pdf, b.pdf and a note
    assert host.step_state() == {"index": 0, "count": 2}  # the note is not a document

    assert host.step_item(1) == 1 and host.key == "b" and host.page_count == 5
    assert host.step_item(1) == 1  # the end stops
    assert host.step_state() == {"index": 1, "count": 2}
    assert ctx.links.output_of("v3", "selection") is None


# -- video: a player steps its videos and emits itself -------------------------------------
@needs_video
def test_video_steps_through_its_videos_and_emits_the_pick(engine: Engine, tmp_path: Path):
    ctx = place(engine, "rena", "video")
    host = VideoHost(ctx, "rena", ctx.manifest_for("video"), headless=True)
    host.receive("data", Collection(items=video_items(tmp_path / "video", "take-a", "take-b")))
    assert host.step_state() == {"index": 0, "count": 2}
    depth = engine.live.undo_info()["depth"]

    assert host.step_item(1) == 1 and host.key == "take-b"
    assert ctx.links.output_of("rena", "selection") == Selection(keys=["take-b"], last="take-b")
    assert host.step_item(1) == 1 and host.step_state() == {"index": 1, "count": 2}
    assert engine.live.undo_info()["depth"] == depth  # live, not a submit


# -- the status bar names the keys (#80) ---------------------------------------------------
def test_the_status_bar_names_each_views_step_keys_while_it_is_hovered():
    def line(view_type: str, *, hovered: bool = True) -> str:
        info = {"id": "tamu", "type": view_type, "health": "ok", "healthMessage": "00001"}
        return status_hint(info, hovered=hovered)

    both = "Left/Right or PageUp/PageDown previous / next item"
    ends = "Home/End first / last"  # the focused view's before the canvas's Home (#113)
    assert line("image") == f"tamu: 00001  ·  {both}, {ends}  ·  {GESTURES}"
    assert all(ends in line(t) for t in ("video", "pdf", "usd"))
    assert ends not in line("markdown") and ends not in line("json")
    assert "hold Space and drag to pan" in line("gallery")  # the canvas's Space (#113)
    assert both in line("markdown") and both in line("html")
    assert "PageUp/PageDown previous / next item" in line("json") and "Left" not in line("json")
    assert "Left/Right previous / next item" in line("pdf") and "PageUp" not in line("pdf")
    assert "PageUp/PageDown previous / next item" in line("video")
    assert "previous / next" not in line("gallery")  # a browser does not step
    assert line("image", hovered=False) == "tamu: 00001"


def test_the_footer_step_buttons_name_the_keys_that_step_the_view():
    """#126: the step buttons' tips name the view's own keys, not one pair for all."""
    from specto_app.hosts.stepping import step_tips

    assert step_tips("image") == ["previous (Left or PageUp)", "next (Right or PageDown)"]
    assert step_tips("pdf") == ["previous (Left)", "next (Right)"]
    assert step_tips("video") == ["previous (PageUp)", "next (PageDown)"]
    assert step_tips("gallery") == ["previous", "next"]
