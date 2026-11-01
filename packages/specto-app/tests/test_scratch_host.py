"""Seam 2 (#184): the scratch pad's host on ports, headless, with real mime data. A page
is markdown: drops and pastes from Specto views (the drag and drop contract's payload)
and from other apps (file URLs, a picture, text) append markdown to the shown page (at
the editor's caret while the source shows, or to a tab's page), one undo step each;
the page gets the page's text and its items URL; the source editor's saves are steps;
the shown page goes out on ``items``; a clicked file link opens in the pad's preview."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage
from web_helpers import Sink, new_engine, registry

from specto_app.views.preview_view import PreviewHost
from specto_app.views.scratch_view import ScratchHost, mime_from
from specto_app.web.runtime import runtime
from specto_core import Engine, gestures
from specto_core.links import Collection, Selection

SPECTO = "application/x-specto-item+json"


def picture(fmt: str = "PNG", color: str = "red") -> bytes:
    img = QImage(8, 6, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt)
    return bytes(buf.data().data())


@pytest.fixture
def engine(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine, _proj = new_engine(tmp_path, frames=3)
    return engine


@pytest.fixture
def pad(engine: Engine) -> tuple[str, ScratchHost, Sink]:
    vid, _ = gestures.open_scratch(engine.live)
    host = registry(engine).host(vid)
    assert isinstance(host, ScratchHost)
    sink = Sink(host.bridge)
    host.bridge.ready()
    return vid, host, sink


def frame(engine: Engine) -> Path:
    return sorted((engine.project.root / "runs" / "cam-a").glob("*.jpg"))[0]


def tile_drag(engine: Engine, path: Path) -> QMimeData:
    """What a gallery tile's drag carries (dnd contract): the payload, the file URL, the
    path as text."""
    md = QMimeData()
    rel = path.relative_to(engine.project.root).as_posix()
    body = {
        "items": [
            {
                "view": "kobi",
                "type": "gallery",
                "key": path.stem,
                "path": rel,
                "abs": str(path),
                "kind": "image",
                "label": f"cam-a/{path.stem}",
                "meta": {},
            }
        ],
        "text": None,
        "project": str(engine.project.root),
    }
    md.setData(SPECTO, QByteArray(json.dumps(body).encode()))
    md.setUrls([QUrl.fromLocalFile(str(path))])
    md.setText(str(path))
    return md


def test_the_page_gets_the_markdown_and_its_items_url(engine, pad):
    vid, host, sink = pad
    page = sink.last("page")
    assert page["id"] == "p1" and page["name"] == "Page 1" and page["text"] == ""
    assert page["url"] == f"specto://items/scratch-{vid}/p1/md"
    assert f"read:link:scratch-{vid}" in sink.configured[0]["capabilities"]
    assert host.ctx.health_of(vid)["message"] == "empty"
    out = engine.links.output_of(vid, "items")
    assert isinstance(out, Collection) and [it.key for it in out.items] == ["p1"]


def test_a_gallery_tile_drop_is_a_quoted_link_and_a_picture_an_asset(engine, pad):
    vid, host, sink = pad
    path = frame(engine)
    assert len(host.accept_mime(tile_drag(engine, path))) == 1
    text = engine.scratch.text()
    assert text == (
        f"> [cam-a/{path.stem}](../../runs/cam-a/{path.name})\n>\n"
        f"> — kobi · runs/cam-a/{path.name}\n"
    )
    host.flush()
    assert sink.last("page")["text"] == text
    md = QMimeData()
    md.setData("image/jpeg", QByteArray(picture("JPG", "blue")))  # converted to png
    host.accept_mime(md)
    asset = engine.project.root / ".specto/scratch/p1.assets/image.png"
    assert asset.read_bytes().startswith(b"\x89PNG")
    assert engine.scratch.text().endswith("\n\n![image](p1.assets/image.png)\n")
    # the shown page goes out: its markdown, then what it links
    keys = [it.key for it in engine.links.output_of(vid, "items").items]
    assert keys == ["p1", f"runs/cam-a/{path.name}", ".specto/scratch/p1.assets/image.png"]
    assert len(engine.scratch.history) == 2  # a step per drop
    # the page's pictures are served under the pad's link, to its page only
    from specto_app.web.scheme import locate_item

    rt = runtime()

    def locate(initiator: str) -> tuple:
        entry: dict = {"status": "ok"}
        path = f"/scratch-{vid}/p1/md/p1.assets/image.png"
        got = locate_item("items", path, initiator, caps_of=rt.caps_of, items=rt.items, entry=entry)
        return got, entry["status"]

    assert locate(f"{vid}.page") == (asset.resolve(), "ok")
    assert locate("lito.page")[1] == "denied"


def test_explorer_files_web_links_text_and_a_selection_drag(engine, pad):
    _vid, host, _sink = pad
    notes = sorted((engine.project.root / "notes").glob("*.md"))
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in notes[:2]])
    assert len(host.accept_mime(md)) == 2
    assert len(engine.scratch.history) == 1  # one drop, one step
    web = QMimeData()
    web.setUrls([QUrl("https://example.com/a/cat.png")])
    host.accept_mime(web)
    text = QMimeData()
    text.setText("the lidar drifts at 00042")
    host.accept_mime(text)
    # drag185's selection: kind text, abs its note, the words in payload.text, no uri-list
    sel = QMimeData()
    body = {
        "items": [
            {
                "view": "rena",
                "type": "markdown",
                "key": notes[0].stem,
                "path": f"notes/{notes[0].name}",
                "abs": str(notes[0]),
                "kind": "text",
                "label": notes[0].stem,
            }
        ],
        "text": "the selected words",
    }
    sel.setData(SPECTO, QByteArray(json.dumps(body).encode()))
    sel.setText("the selected words")
    assert not sel.hasUrls()
    host.accept_mime(sel)
    blocks = engine.scratch.text().rstrip("\n").split("\n\n")
    assert blocks == [
        f"[{notes[0].name}](../../notes/{notes[0].name})",
        f"[{notes[1].name}](../../notes/{notes[1].name})",
        "[cat.png](https://example.com/a/cat.png)",
        "the lidar drifts at 00042",
        f"> the selected words\n>\n> — rena · notes/{notes[0].name}",
    ]
    assert host.accept_mime(QMimeData()) == []  # nothing usable: nothing added


def test_ctrl_v_and_a_drop_while_editing_land_at_the_caret(engine, pad):
    _vid, host, _sink = pad
    QGuiApplication.clipboard().setText("first")
    host.bridge.send("paste", "{}")  # the page's Ctrl+V
    QGuiApplication.clipboard().setText("second")
    host.bridge_qml.paste()  # the strip's Ctrl+V
    assert engine.scratch.text() == "first\n\nsecond\n"
    # the source shows, the caret after "first": a drop goes there
    host.bridge.send("caret", json.dumps({"page": "p1", "offset": 5}))
    md = QMimeData()
    md.setText("middle")
    host.accept_mime(md)
    assert engine.scratch.text() == "first\n\nmiddle\n\nsecond\n"
    host.bridge.send("caret", json.dumps({"page": "p1", "offset": None}))  # rendered again
    md.setText("last")
    host.accept_mime(md)
    assert engine.scratch.text().endswith("second\n\nlast\n")


def test_the_source_editor_saves_are_undo_steps_and_come_back_to_the_page(engine, pad):
    _vid, host, sink = pad
    host.bridge.send("save", json.dumps({"page": "p1", "text": "# Title\nmy notes\n", "rev": 1}))
    assert engine.scratch.text() == "# Title\nmy notes\n"
    assert engine.scratch.history[-1].label == "edit page Page 1"
    host.bridge.send("save", json.dumps({"page": "p1", "text": "# Title\nmy notes\n", "rev": 2}))
    assert len(engine.scratch.history) == 1  # the same text: no step
    engine.scratch.undo()
    host.flush()
    assert sink.last("page")["text"] == ""
    assert host.snapshot()["pages"][0]["chars"] == 0


def test_pages_tabs_drops_on_a_tab_and_the_page_param(engine, pad):
    vid, host, sink = pad
    QGuiApplication.clipboard().setText("on page one")
    host.paste()
    pid = host.add_page()  # the strip's +: a new page, shown, remembered in params.page
    assert pid == "p2" and engine.scratch.active == "p2"
    assert engine.live.data["views"][vid]["params"]["page"] == "p2"
    assert engine.live.history[-1].label == "open the scratch pad"  # navigation: no step
    host.flush()
    assert sink.last("page")["id"] == "p2" and sink.last("page")["text"] == ""
    assert [it.key for it in engine.links.output_of(vid, "items").items] == ["p2"]
    host.rename_page("p2", "lidar")
    host.bridge_qml.drop({"text/plain": "dropped on the tab"}, "p1")
    assert engine.scratch.text("p1") == "on page one\n\ndropped on the tab\n"
    assert [t["name"] for t in host.tabs] == ["Page 1", "lidar"]
    # the agent sets params.page (by name): the pad shows it and sends it out
    engine.live.submit(
        edits={"views": {vid: {"params": {"page": "Page 1"}}}}, origin="agent:s1", label="page"
    )
    host.flush()
    assert engine.scratch.active == "p1" and sink.last("page")["id"] == "p1"
    host.show_page("lidar")
    host.move_page("lidar", 0)
    assert [t["id"] for t in host.tabs] == ["p2", "p1"]
    host.close_page("Page 1")
    assert engine.scratch.page_ids() == ["p2"]
    assert engine.scratch.history[-1].label == "close page Page 1 (not empty)"
    engine.scratch.undo()
    assert "dropped on the tab" in engine.scratch.text("p1")


def test_a_clicked_file_link_opens_in_the_pads_preview(engine, pad):
    vid, host, _sink = pad
    host.accept_mime(tile_drag(engine, frame(engine)))
    name = frame(engine).name
    preview_vid = host.open_link(f"../../runs/cam-a/{name}")
    assert preview_vid is not None
    assert engine.live.data["views"][preview_vid]["type"] == "preview"
    sel = engine.links.output_of(vid, "selection")
    assert isinstance(sel, Selection) and sel.last == f"runs/cam-a/{name}"
    preview = registry(engine).host(preview_vid)
    assert isinstance(preview, PreviewHost) and preview.kind == "image"
    assert host.open_link("../../nothing.txt") is None


def test_the_qml_drop_hands_its_data_per_type(engine, pad):
    _vid, host, _sink = pad
    path = frame(engine)
    data = {
        "text/uri-list": QByteArray(QUrl.fromLocalFile(str(path)).toString().encode()),
        "text/plain": QByteArray(str(path).encode()),
    }
    md = mime_from(data)
    assert [u.toLocalFile() for u in md.urls()] == [path.as_posix()]
    assert host.bridge_qml.drop(data, "") == 1
    assert f"(../../runs/cam-a/{path.name})" in engine.scratch.text()
