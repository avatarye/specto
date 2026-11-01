"""Seam 2 (#43): the target view of an unapproved Python link shows a 'needs approval'
state; the user's Approve on that view records the file's hash, the link resolves and
a pick in the source gallery lands through the Python table in the target gallery."""

from __future__ import annotations

from pathlib import Path

import pytest
from gallery_helpers import FakeGallery, copy_frames, needs_testdata

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.canvas.controller import CanvasController
from specto_app.hosts import HostContext, HostRegistry
from specto_app.views.file_tree import FileTreeFactory
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Selection

LINK_FILE = ".specto/links/cam_a_to_cam_b.py"
CAM_A_TO_CAM_B = """\
def resolve(items, params, root):
    return {it["key"]: it["key"].replace("cam-a/", "cam-b/") for it in items}
"""
KEYS = r"(?P<series>cam-a|cam-b)/(?P<frame>\d{5})_\d+\.jpg$"


@needs_testdata
def test_approving_on_the_target_view_runs_the_link_and_picks_follow_through_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "run-001" / "cam-a", 6, "cam-a")
    copy_frames(proj.root / "runs" / "run-001" / "cam-b", 4, "cam-b")
    (proj.root / LINK_FILE).write_text(CAM_A_TO_CAM_B, encoding="utf-8")
    engine = Engine(project=proj, jobs=InlineJobManager())
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())
    reg.host("fege")

    doc = engine.live.document()
    for vid, lid, cam, x in (("kobi", "l2", "cam-a", 300), ("tamu", "l3", "cam-b", 960)):
        doc["views"][vid] = {"type": "gallery", "label": cam, "layout": {"position": [x, 0]}}
        doc["links"][lid] = {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": vid, "port": "data"},
            "params": {
                "sources": ["runs/run-001"],
                "screener": ["@images"],
                "keys": KEYS.replace("cam-a|cam-b", cam),  # the other camera does not key
                "group_by": ["series", "frame"],
            },
            "summary": {"sentence": f"The {cam} gallery shows the {cam} frames of run-001."},
        }
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "python": {"path": LINK_FILE, "resolves_to": "table", "output": "selection"},
        "summary": {"sentence": "Picking a cam-a frame picks the cam-b frame Python maps it to."},
    }
    engine.live.submit(doc, origin="agent:s1", label="cam-a and cam-b through python")
    front, rear = reg.host("kobi"), reg.host("tamu")
    front.pump(), rear.pump()
    controller = CanvasController(engine, hosts=reg)

    # the cam-b gallery offers Approve for the link file; nothing ran
    assert controller.views["tamu"]["approval"] == LINK_FILE
    assert controller.views["kobi"]["approval"] == ""
    fakes["kobi"].user_click(2)
    assert fakes["tamu"].selected() == -1

    assert controller.approveView("tamu") is True

    assert controller.views["tamu"]["approval"] == ""
    assert ctx.health_of("tamu")["status"] == "ok"
    assert fakes["tamu"].selected() == 2  # the pick made before approval lands now
    fakes["kobi"].user_click(1)
    assert fakes["tamu"].selected() == 1
    assert ctx.links.input_of("tamu", "selection") == Selection(
        keys=["cam-b/00001"], last="cam-b/00001"
    )
