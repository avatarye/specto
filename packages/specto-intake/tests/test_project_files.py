"""Seam 1 (#83, #231): the project source's ``all_files`` output means the whole project
folder. Pinned intakers hang off it and scan it themselves; the file tree's own intaker
is never walked (the tree gets the root alone and lists its folders on demand), and the
file tree no longer emits ``all_files``."""

from __future__ import annotations

from pathlib import Path

import pytest
from intake_helpers import copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Collection, FilesValue

pytestmark = needs_testdata

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 4, "cam-a")
    return proj


def pinned_gallery(doc: dict) -> dict:
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Cam A",
        "layout": {"position": [380, 0], "size": [640, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery always shows the cam-a run."},
    }
    return doc


def test_project_all_files_means_the_whole_folder_and_the_tree_is_not_walked_for_it(
    project: Project, monkeypatch: pytest.MonkeyPatch
):
    from specto_intake import resolve as resolve_module

    walks: list[str] = []
    real_walk = resolve_module.walk_source

    def walk(index, src, base, **kw):
        walks.append(src.root)
        return real_walk(index, src, base, **kw)

    monkeypatch.setattr(resolve_module, "walk_source", walk)
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links

    tree_col = links.input_of("fege", "data")
    assert isinstance(tree_col, Collection) and tree_col.items == []  # the root alone
    assert tree_col.root == str(project.root) and tree_col.link == "l1"
    assert walks == []  # a canvas holding only a tree does no recursive scan (#231)
    assert links.watching() == []  # and the runtime watches nothing for it
    # the port means the whole folder; nothing has counted it
    assert links.output_of("project", "all_files") == FilesValue(root=str(project.root))
    assert links.project_files is None
    # what a pick of the tree's root stands for
    assert tree_files(project.root).paths[0] == "runs/cam-a/00000_1700000000000000000.jpg"

    # a pinned intaker off the project source resolves against the folder at once
    res = engine.live.submit(pinned_gallery(engine.live.document()), origin="agent:s1")
    assert res.links["added"] == ["l2"]
    col = links.input_of("kobi", "data")
    assert isinstance(col, Collection) and [it.key for it in col.items] == [
        "00000",
        "00001",
        "00002",
        "00003",
    ]
    assert walks == ["runs/cam-a"]  # the pinned intaker walked its own sources
    # the file tree has nothing to say about all files any more
    assert links.output_of("fege", "all_files") is None
