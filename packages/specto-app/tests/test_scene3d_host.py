"""Seam 2 (#47): the 3D view host on ports. Built by the host registry from a submitted
canvas, it receives the collection its intaker resolved (items carrying scene buffers)
and tells its page what to load from the items host under that link; the page's camera
becomes the ``camera`` output; a pick ray from the page is answered from the
resolve-time kd-tree and becomes the ``selection`` output; camera and selection arriving
on the input ports go to the page and are not echoed."""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest
from scene3d_helpers import mesh_samples, seed_scans
from web_helpers import Sink

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.canvas.controller import GESTURES, status_hint
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.scene3d_view import Scene3DHost, factory
from specto_app.web.runtime import reset_runtime, runtime
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import CameraValue, Selection

CAMERA = {
    "position": [3.0, 4.0, 12.5],
    "target": [3.0, 4.0, 2.5],
    "up": [0.0, 1.0, 0.0],
    "fov_y": 0.8,
    "near": 0.1,
    "far": 1000.0,
}
REGISTRIES: list[HostRegistry] = []


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    reset_runtime()
    clear_pending()
    proj = Project.init(tmp_path / "proj", "scans")
    seed_scans(proj.root)
    eng = Engine(project=proj, jobs=InlineJobManager())
    yield eng
    # these QObjects were made with no QGuiApplication: take them down here, not whenever
    # the cyclic GC runs inside a later test's Qt event loop
    for reg in REGISTRIES:
        reg.dispose_all()
    REGISTRIES.clear()
    eng.links.close()
    reset_runtime()
    gc.collect()


def submit_scene(engine: Engine, *, with_link: bool = True) -> None:
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "3d",
        "label": "Scans",
        "layout": {"position": [300, 0], "size": [800, 600]},
    }
    # a view keeps a link from the project source (#83): ``with_link=False`` screens
    # everything out instead
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "scans",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["scans"], "screener": ["@3d" if with_link else "*.none"]},
        "summary": {"sentence": "The 3D view shows every scan under scans/."},
    }
    engine.live.submit(doc, origin="agent:test", label="3d")


def make_host(engine: Engine) -> tuple[HostContext, Scene3DHost]:
    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(factory())
    REGISTRIES.append(reg)
    host = reg.host("kobi")
    assert isinstance(host, Scene3DHost)
    return ctx, host


def test_the_host_tells_its_page_what_to_load_from_the_items_host(engine: Engine, tmp_path: Path):
    submit_scene(engine)
    ctx, host = make_host(engine)  # the registry replays the resolved collection
    scene = host.scene_payload()
    by = {e["id"]: e for e in scene["entries"]}
    assert list(by) == ["cloud", "gauss", "model", "quad", "raw"]
    assert by["cloud"] == {
        "id": "cloud",
        "index": 0,
        "kind": "pointcloud",
        "label": "cloud.ply",
        "url": "specto://items/l2/cloud/points",
        "count": 5,
        "bounds": [[-1.0, 0.0, -2.0], [3.0, 4.0, 2.5]],
        "size": 250,
        "size_label": "250 B",
    }
    assert by["quad"]["kind"] == "mesh" and by["quad"]["url"] == "specto://items/l2/quad/glb"
    assert by["quad"]["ext"] == ".glb" and by["quad"]["count"] == 4
    assert by["model"]["url"] == "specto://items/l2/model/glb" and by["model"]["ext"] == ".glb"
    assert by["gauss"]["url"] == "specto://items/l2/gauss/ply" and by["gauss"]["kind"] == "splat"
    assert by["raw"]["url"] == "specto://items/l2/raw/splat" and by["raw"]["ext"] == ".splat"
    assert scene["params"] == {
        "point_size": 2,
        "up": "y",
        "camera_speed": 1.0,
        "view_mode": "lit",  # the footer's mode pill (#153)
        # the footer's HDRI pill and the VIEW panel's environment group (#175)
        "environment": "none",
        "environment_intensity": 1.0,
        "environment_background": False,
        "environment_rotation": 0.0,
    }
    assert ctx.health_of("kobi") == {"status": "ok", "message": "5 items", "hint": ""}
    # the items host serves the derived buffers beside the item's own part, to this view
    parts = runtime().items.parts("l2", "cloud")
    assert parts[".points"].startswith(str(tmp_path / "cache"))
    assert parts[".ply"].endswith("cloud.ply") and sorted(parts) == [".kdtree", ".ply", ".points"]
    assert runtime().items.parts("l2", "quad")[".glb"].endswith(".glb")
    assert host.caps.can_read("l2") and not host.caps.can_net("tiles")
    assert list(host.caps) == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:camera",
        "emit:selection",
        "params:camera_speed",
        "params:environment",
        "params:environment_background",
        "params:environment_intensity",
        "params:environment_rotation",
        "params:panels",
        "params:point_size",
        "params:up",
        "params:view_mode",
        "read:link:l2",
    ]

    # the page connecting later gets the scene it missed
    sink = Sink(host.bridge)
    host.bridge.ready()
    assert sink.configured[0]["type"] == "3d" and sink.configured[0]["view"] == "kobi"
    assert sink.last("scene")["entries"] == scene["entries"]

    # the link lets nothing through: the page is told the scene is empty
    submit_scene(engine, with_link=False)
    host.flush()
    assert host.scene_payload()["entries"] == [] and sink.last("scene")["entries"] == []
    assert ctx.health_of("kobi") == {"status": "ok", "message": "0 items", "hint": ""}


def test_each_entry_carries_its_file_size_for_the_items_panel(engine: Engine, tmp_path: Path):
    # the items panel shows the size under each name (#129), formatted as the file tree
    # formats it; a file of 1.5 MiB reads "1.5 MB"
    big = tmp_path / "proj" / "scans" / "big.splat"
    big.write_bytes(bytes(3 * 512 * 1024))
    submit_scene(engine)
    _ctx, host = make_host(engine)
    by = {e["id"]: e for e in host.scene_payload()["entries"]}
    assert by["big"]["size"] == 1572864 and by["big"]["size_label"] == "1.5 MB"
    assert by["raw"]["size"] == 64 and by["raw"]["size_label"] == "64 B"


def test_robot_meshes_load_through_babylon_and_the_dae_through_its_glb(
    engine: Engine, tmp_path: Path
):
    """SPEC-42: an STL and an OBJ go to Babylon's loaders by ``ext`` with the bounds resolve
    measured; the OBJ is fetched by its own name so its MTL and texture resolve next to it
    on the items host; a DAE loads as the GLB written beside it and declares its up axis
    (the view's up param leaves it alone)."""
    from specto_app.web.scheme import locate_item

    samples = mesh_samples()
    scans = tmp_path / "proj" / "scans"
    samples.write_stl(scans / "link.stl")
    samples.write_obj(scans / "panel.obj")
    samples.write_dae(scans / "robot.dae")
    submit_scene(engine)
    _ctx, host = make_host(engine)
    by = {e["id"]: e for e in host.scene_payload()["entries"]}
    box = [list(samples.BOX_LO), list(samples.BOX_HI)]
    link = by["link"]
    assert (link["kind"], link["url"], link["ext"]) == (
        "mesh",
        "specto://items/l2/link/stl",
        ".stl",
    )
    assert link["count"] == 36 and _rounded(link["bounds"]) == box and "up" not in link
    panel = by["panel"]
    assert panel["url"] == "specto://items/l2/panel/obj/panel.obj" and panel["ext"] == ".obj"
    assert panel["count"] == 24 and _rounded(panel["bounds"]) == box
    robot = by["robot"]
    assert robot["url"] == "specto://items/l2/robot/glb" and robot["ext"] == ".glb"
    assert robot["label"] == "robot.dae" and robot["up"] == "y"
    assert _rounded(robot["bounds"]) == samples.DAE_BOUNDS_M_YUP
    assert runtime().items.parts("l2", "robot")[".glb"] == str(scans / "robot.dae.glb")

    rt = runtime()

    def served(path: str) -> Path | None:
        entry: dict = {"status": "ok"}
        return locate_item(
            "items", path, "kobi.page", caps_of=rt.caps_of, items=rt.items, entry=entry
        )

    # what Babylon's OBJ loader asks for next to specto://items/l2/panel/obj/panel.obj
    assert served("/l2/panel/obj/panel.mtl") == (scans / "panel.mtl").resolve()
    assert served("/l2/panel/obj/textures/panel.png") == (scans / "textures/panel.png").resolve()
    assert served("/l2/panel/obj/../secret.txt") is None


def _rounded(bounds) -> list[list[float]]:
    return [[round(float(x), 5) + 0.0 for x in row] for row in bounds]


def test_the_page_camera_is_the_camera_output_and_an_outside_camera_is_not_echoed(
    engine: Engine,
):
    submit_scene(engine)
    ctx, host = make_host(engine)
    sink = Sink(host.bridge)
    host.bridge.ready()

    reply = json.loads(host.bridge.emit("camera", json.dumps(CAMERA)))
    assert reply == {"ok": True, "result": {"links": []}}
    assert ctx.links.output_of("kobi", "camera") == CameraValue(**CAMERA)

    outside = CameraValue(position=(0, 0, 10), target=(0, 0, 0))
    host.receive("camera", outside)
    host.flush()
    assert sink.last_port("camera") == outside.model_dump(mode="json")
    assert ctx.links.output_of("kobi", "camera") == CameraValue(**CAMERA)
    # the page may not publish on ports it does not own or call commands it was not granted
    refused = json.loads(host.bridge.emit("data", "{}"))
    assert refused["ok"] is False and refused["error"]["code"] == "capability"
    host.bridge.call("canvas.undo", "{}", "3")
    assert sink.results[-1][1]["error"]["code"] == "capability"


def test_a_pick_ray_from_the_page_is_answered_from_the_kd_tree_as_a_selection(engine: Engine):
    submit_scene(engine)
    ctx, host = make_host(engine)
    sink = Sink(host.bridge)
    host.bridge.ready()

    # straight down -z at point 3 (3, 4, 2.5) from ten units in front of it
    hit = host.pick_ray([3.0, 4.0, 12.5], [0.0, 0.0, -1.0], tolerance=0.01)
    assert hit == {"item": "cloud", "index": 3, "position": [3.0, 4.0, 2.5], "distance": 10.0}
    assert ctx.links.output_of("kobi", "selection") == Selection(keys=["cloud#3"], last="cloud#3")
    host.flush()
    assert sink.last("picked") == hit
    # slightly off and the cone (1 % of the distance) still catches it; far off it misses
    assert host.pick_ray([3.05, 4.0, 12.5], [0.0, 0.0, -1.0], tolerance=0.01)["index"] == 3
    assert host.pick_ray([3.5, 4.0, 12.5], [0.0, 0.0, -1.0], tolerance=0.01) is None
    host.flush()
    assert sink.last("picked") is None  # the page hears about the miss too
    # the page sends it the same way
    host.bridge.send(
        "pick",
        json.dumps({"origin": [0.0, 0.0, 10.0], "direction": [0.0, 1.0, -12.0], "tolerance": 0.02}),
    )
    host.flush()
    assert sink.last("picked")["index"] == 1
    assert ctx.links.output_of("kobi", "selection").keys == ["cloud#1"]
    # a mesh or splat the page picked itself becomes a whole-item selection
    host.bridge.send("select", json.dumps({"item": "quad", "position": [0.5, 0.5, 0.0]}))
    assert ctx.links.output_of("kobi", "selection") == Selection(keys=["quad"], last="quad")
    assert host.snapshot()["picked"] == {"item": "quad", "index": None, "position": [0.5, 0.5, 0.0]}

    # a selection from outside goes to the page and is not emitted back
    host.receive("selection", Selection(keys=["cloud#2"], last="cloud#2"))
    host.flush()
    assert sink.last_port("selection") == {"keys": ["cloud#2"], "last": "cloud#2"}
    assert ctx.links.output_of("kobi", "selection") == Selection(keys=["quad"], last="quad")

    snap = host.snapshot()
    assert snap["entries"] == 5 and snap["points"] == 5 and snap["selection"] == ["cloud#2"]
    assert snap["inputs"] == {"data": "l2"} and snap["kinds"]["splat"] == 2


def test_pause_stops_the_render_loop_and_resume_restarts_it(engine: Engine):
    submit_scene(engine)
    _ctx, host = make_host(engine)
    sink = Sink(host.bridge)
    host.bridge.ready()
    host.pause()
    host.flush()
    assert sink.last("render") == {"running": False}
    host.resume()
    host.flush()
    assert sink.last("render") == {"running": True}
    host.dispose()
    assert runtime().caps_of("kobi") is None


def test_the_scene_payload_starts_the_camera_at_the_world_origin(engine: Engine):
    """The page's first camera (before any F) targets (0, 0, 0), far enough back to hold
    everything the scene's bounds reach from the origin; F (frame all) stays the page's."""
    import math

    from specto_app.views.scene3d_view import DEFAULT_FOV_Y, FIRST_LOOK, initial_camera

    submit_scene(engine)
    _ctx, host = make_host(engine)
    scene = host.scene_payload()
    cam = scene["camera"]
    assert cam["target"] == [0.0, 0.0, 0.0]
    # the farthest bound corner from the origin sets the distance, as frame-all does
    # from the scene's centre
    radius = max(
        math.sqrt(x * x + y * y + z * z)
        for e in scene["entries"]
        if e.get("bounds")
        for x in (e["bounds"][0][0], e["bounds"][1][0])
        for y in (e["bounds"][0][1], e["bounds"][1][1])
        for z in (e["bounds"][0][2], e["bounds"][1][2])
    )
    distance = radius / math.sin(DEFAULT_FOV_Y / 2) * 1.05
    assert math.dist(cam["position"], (0, 0, 0)) == pytest.approx(distance)
    n = math.sqrt(sum(c * c for c in FIRST_LOOK))
    assert cam["position"] == pytest.approx([-c / n * distance for c in FIRST_LOOK])
    assert cam["up"] == [0.0, 1.0, 0.0] and cam["fov_y"] == DEFAULT_FOV_Y
    assert cam["near"] == pytest.approx(max(0.001, radius / 5000))
    assert cam["far"] == pytest.approx(max(100.0, radius * 50))
    # nothing with bounds: a default distance, still at the origin
    empty = initial_camera([])
    assert empty["target"] == [0.0, 0.0, 0.0] and math.dist(empty["position"], (0, 0, 0)) > 1
    assert initial_camera([{"bounds": None}]) == empty


def test_a_new_collection_replaces_the_old_items_and_their_pick_data(engine: Engine):
    """Switching source (another pick, a re-pointed intaker): the host tells the page
    only the new collection's items and forgets the old kd-trees and the old pick."""
    from specto_core.links import Collection

    submit_scene(engine)
    _ctx, host = make_host(engine)
    sink = Sink(host.bridge)
    host.bridge.ready()
    assert [e["id"] for e in host.entries] == ["cloud", "gauss", "model", "quad", "raw"]
    picked = host.pick_ray([0.0, 0.0, 10.0], [0.0, 0.0, -1.0], tolerance=0.5)
    assert picked is not None and picked["item"] == "cloud" and "cloud" in host._trees

    old = host.collection
    keep = [it for it in old.items if it.key in ("quad", "gauss")]  # y stays, x goes
    host.receive("data", Collection(items=keep, root=old.root, link=old.link, generation=2))
    host.flush()

    assert [e["id"] for e in host.entries] == ["gauss", "quad"]
    assert [e["id"] for e in sink.last("scene")["entries"]] == ["gauss", "quad"]
    assert sink.last("scene")["generation"] == 2
    assert host._trees == {} and set(host._files) == {"gauss", "quad"}
    assert host.picked is None  # the picked cloud left with its collection
    assert host.pick_ray([0.0, 0.0, 10.0], [0.0, 0.0, -1.0], tolerance=0.5) is None
    assert host.snapshot()["health"]["message"] == "2 items"


def test_the_status_bar_names_the_3d_keys_while_the_view_is_hovered():
    # the page's own "WASD fly · F frame" hint left it with the in-body bar (#129)
    info = {"id": "kobi", "type": "3d", "health": "ok", "healthMessage": "3 items"}
    keys = (
        "drag turns and walks, right-drag looks (W A S D Q E fly, wheel sets speed), "
        "middle-drag pans, wheel dollies, Alt+drag orbits, F frames"
    )
    assert status_hint(info, hovered=True) == f"kobi: 3 items  ·  {keys}  ·  {GESTURES}"
    assert status_hint(info, hovered=False) == "kobi: 3 items"
    assert keys not in status_hint({**info, "type": "gallery"}, hovered=True)
