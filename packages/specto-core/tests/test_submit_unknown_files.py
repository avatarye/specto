"""Seam 1 (spec #35 story 24): a submit naming an unknown file is rejected with the other
problems, each with a fix-it: an intaker source that matches nothing under the project
folder, and a Python link whose file is not there. A headless canvas (no project) has no
folder to check against and skips the check."""

from __future__ import annotations

from pathlib import Path

import pytest

from specto_core import Engine, Project
from specto_core.canvas_document import CanvasInvalid
from specto_core.live_canvas import LiveCanvas


def with_gallery(live: LiveCanvas, sources: list[str]) -> dict:
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 0], "size": [600, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": sources},
        "summary": {"sentence": "The gallery shows the front frames."},
    }
    return doc


def with_python(doc: dict, path: str) -> dict:
    doc["views"]["tamu"] = {
        "type": "image",
        "label": "Pick",
        "layout": {"position": [950, 0], "size": [600, 400]},
    }
    doc["links"]["l3"] = {
        "kind": "python",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "python": {"path": path, "entry": "resolve", "resolves_to": "table", "output": "selection"},
        "summary": {"sentence": "The image follows the gallery's pick through a table."},
    }
    return doc


def paths_of(e: CanvasInvalid) -> set[str]:
    return {p["path"] for p in e.problems}


def test_an_intaker_source_matching_nothing_is_an_unknown_file(project: Project):
    live = Engine(project=project).live
    with pytest.raises(CanvasInvalid) as e:
        live.submit(with_gallery(live, ["runs/front"]), origin="agent:s1")
    assert paths_of(e.value) == {"/links/l2/params/sources/0"}
    problem = e.value.problems[0]
    assert "runs/front" in problem["message"] and "project's files" in problem["fix"]
    assert "kobi" not in live.data["views"]

    (project.root / "runs" / "front").mkdir(parents=True)
    assert live.submit(with_gallery(live, ["runs/front"]), origin="agent:s1").touched


def test_a_glob_source_counts_when_it_matches_a_file(project: Project):
    live = Engine(project=project).live
    (project.root / "runs" / "front").mkdir(parents=True)
    (project.root / "runs" / "front" / "00001.jpg").write_bytes(b"x")
    with pytest.raises(CanvasInvalid) as e:
        live.submit(with_gallery(live, ["runs/**/*.png", "."]), origin="agent:s1")
    assert paths_of(e.value) == {"/links/l2/params/sources/0"}
    assert live.submit(with_gallery(live, ["runs/**/*.jpg", "."]), origin="agent:s1").touched


def test_a_missing_python_file_is_reported_with_the_other_problems(project: Project):
    live = Engine(project=project).live
    (project.root / "runs" / "front").mkdir(parents=True)
    doc = with_python(with_gallery(live, ["runs/nope"]), ".specto/links/pick.py")
    with pytest.raises(CanvasInvalid) as e:
        live.submit(doc, origin="agent:s1")
    assert paths_of(e.value) == {"/links/l2/params/sources/0", "/links/l3/python/path"}
    py = next(p for p in e.value.problems if p["path"].endswith("python/path"))
    assert "pick.py" in py["message"] and "def resolve" in py["fix"]

    Path(project.root / ".specto" / "links" / "pick.py").write_text(
        "def resolve(items, params, root):\n    return {}\n", encoding="utf-8"
    )
    doc = with_python(with_gallery(live, ["runs/front"]), ".specto/links/pick.py")
    assert live.submit(doc, origin="agent:s1").views["added"] == ["kobi", "tamu"]


def test_a_headless_canvas_has_no_folder_to_check():
    live = LiveCanvas.headless("demo")
    assert live.submit(with_gallery(live, ["runs/nowhere"]), origin="agent:s1").touched
