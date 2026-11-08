"""The urdf view host (SPEC-43.1; ADR 0029's host and page contract).
Built by the host registry from a submitted canvas, it parses the robot its intaker
delivered (on the job manager), serves the robot's meshes through the items host under
that link, and speaks the contract's messages: ``model``, ``pose`` and ``params`` out;
``select``, ``joints``, ``loaded`` and ``frame`` in, the camera as a port emit and the
view params as the page's own writes. A mesh it cannot find or the page cannot read turns
health amber (``warn``) with one note per cause."""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest
from web_helpers import Sink

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.canvas.controller import VIEW_KEYS
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.hosts.stepping import STEP_KEYS
from specto_app.theme import COLORS
from specto_app.views.scene3d_view import environment_entries
from specto_app.views.urdf_view import URDF_MANIFEST, UrdfHost, factory
from specto_app.web.runtime import reset_runtime, runtime
from specto_core import Engine, Project
from specto_core.contents import ContentValue
from specto_core.jobs import InlineJobManager
from specto_core.kinds import ViewCost
from specto_core.links import CameraValue, Selection

ARM = """<?xml version="1.0"?>
<robot name="arm">
  <link name="base">
    <visual><geometry><mesh filename="package://arm_description/meshes/base.stl"/></geometry></visual>
    <collision><geometry><box size="0.2 0.2 0.1"/></geometry></collision>
  </link>
  <link name="upper">
    <visual><geometry>
      <mesh filename="../meshes/cover.obj" scale="0.001 0.001 0.001"/>
    </geometry></visual>
    <inertial>
      <mass value="1.5"/><inertia ixx="1" ixy="0" ixz="0" iyy="1" iyz="0" izz="1"/>
    </inertial>
  </link>
  <link name="finger">
    <visual><geometry><mesh filename="package://gripper/meshes/finger.stl"/></geometry></visual>
  </link>
  <link name="tool"/>
  <joint name="shoulder" type="revolute">
    <parent link="base"/><child link="upper"/><axis xyz="0 1 0"/>
    <limit lower="-1.5" upper="1.5" effort="10" velocity="2"/>
  </joint>
  <joint name="grip" type="prismatic">
    <parent link="upper"/><child link="finger"/><axis xyz="1 0 0"/>
    <limit lower="0" upper="0.04" effort="1" velocity="1"/>
  </joint>
  <joint name="tool_fix" type="fixed"><parent link="finger"/><child link="tool"/></joint>
</robot>
"""
CART = """<robot name="cart"><link name="body"/><link name="wheel"/>
<joint name="spin" type="continuous"><parent link="body"/><child link="wheel"/>
<axis xyz="0 0 1"/></joint></robot>"""
CAMERA = {
    "position": [1.0, 2.0, 3.0],
    "target": [0.0, 0.0, 0.0],
    "up": [0.0, 0.0, 1.0],
    "fov_y": 0.8,
    "near": 0.01,
    "far": 100.0,
}
REGISTRIES: list[HostRegistry] = []


def seed_robots(root: Path) -> dict[str, Path]:
    pkg = root / "robots" / "arm_description"
    (pkg / "urdf").mkdir(parents=True)
    (pkg / "meshes").mkdir()
    (pkg / "package.xml").write_text(
        "<package format='3'><name>arm_description</name></package>", encoding="utf-8"
    )
    (pkg / "urdf" / "arm.urdf").write_text(ARM, encoding="utf-8")
    (pkg / "meshes" / "base.stl").write_bytes(b"solid b\nendsolid b\n")
    (pkg / "meshes" / "cover.obj").write_text("v 0 0 0\n", encoding="utf-8")
    (root / "robots" / "cart.urdf").write_text(CART, encoding="utf-8")
    elsewhere = root / "vendor" / "grip_pkg"
    (elsewhere / "meshes").mkdir(parents=True)
    (elsewhere / "meshes" / "finger.stl").write_bytes(b"solid f\nendsolid f\n")
    return {"pkg": pkg, "gripper": elsewhere}


def map_gripper(engine: Engine) -> str:
    """vendor/grip_pkg as the gripper package: a mapped folder needs a package.xml naming
    it (SPEC-43.1 review 3); the search finds it only once that is written."""
    folder = engine.project.root / "vendor" / "grip_pkg"
    (folder / "package.xml").write_text(
        "<package format='3'><name>gripper</name></package>", encoding="utf-8"
    )
    return "vendor/grip_pkg"


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ROS_PACKAGE_PATH", raising=False)
    monkeypatch.delenv("AMENT_PREFIX_PATH", raising=False)
    reset_runtime()
    clear_pending()
    proj = Project.init(tmp_path / "proj", "robots")
    seed_robots(proj.root)
    eng = Engine(project=proj, jobs=InlineJobManager())
    yield eng
    for reg in REGISTRIES:
        reg.dispose_all()
    REGISTRIES.clear()
    eng.links.close()
    reset_runtime()
    gc.collect()


def submit_robot(engine: Engine, params: dict | None = None) -> None:
    doc = engine.live.document()
    doc["views"]["robo"] = {
        "type": "urdf",
        "label": "Arm",
        "layout": {"position": [300, 0], "size": [800, 600]},
        "params": dict(params or {}),
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "robots",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "robo", "port": "data"},
        "params": {"sources": ["robots"], "screener": ["@urdf"]},
        "summary": {"sentence": "The urdf view shows the robots under robots/."},
    }
    engine.live.submit(doc, origin="agent:test", label="urdf")


def set_params(engine: Engine, **params) -> None:
    doc = engine.live.document()
    doc["views"]["robo"].setdefault("params", {}).update(params)
    engine.live.submit(doc, origin="agent:test", label="params")


def make_host(engine: Engine) -> tuple[HostContext, UrdfHost, Sink]:
    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(factory())
    REGISTRIES.append(reg)
    host = reg.host("robo")
    assert isinstance(host, UrdfHost)
    sink = Sink(host.bridge)
    host.bridge.ready()
    host.flush()
    return ctx, host, sink


def arm_key(host: UrdfHost) -> str:
    return next(k for k in host.keys if k.endswith("arm"))


def test_the_manifest_is_a_viewer_of_urdf_with_the_3d_views_cost(engine: Engine):
    m = URDF_MANIFEST
    assert m.type == "urdf" and m.host == "web" and m.accepts == ["urdf"]
    assert m.cost == ViewCost(web_views=1, viewports3d=1)
    props = m.state_schema["properties"]
    assert set(props) == {
        "joints",
        "package_paths",
        "view_mode",
        "up",
        "environment",
        "environment_intensity",
        "environment_rotation",
        "environment_background",
        "show_grid",
        "show_frames",
        "show_joint_axes",
        "show_com",
        "camera_speed",
        "camera_mode",
        "selected",
        "selected_links",
        "panels",
    }
    assert props["view_mode"]["enum"] == [
        "lit",
        "unlit",
        "wireframe",
        "collision",
        "visual_collision",
    ]
    assert props["up"]["enum"] == ["x", "y", "z", "-x", "-y", "-z"]
    assert props["up"]["default"] == "z" == m.defaults["up"]  # REP 103: ROS is Z-up
    assert props["show_grid"]["default"] is True  # design decision 10
    assert props["camera_mode"]["enum"] == ["free", "orbit"]
    assert props["environment"]["enum"][0] == "none"
    # registered with the app's views, describe answers with the params
    install_reg = HostRegistry(HostContext(engine, None), load_entry_points=False)
    install_reg.add_factory(factory())
    REGISTRIES.append(install_reg)
    info = engine.call("describe", {"target": "view:urdf"})["info"]
    assert info["role"] == "viewer" and "package_paths" in info["state_schema"]["properties"]
    assert engine.plugins.typed_view_for("robots/arm.urdf") == "urdf"


def test_the_page_gets_the_model_with_mesh_urls_on_the_items_host(engine: Engine):
    submit_robot(engine, {"selected": None})
    ctx, host, sink = make_host(engine)
    assert len(host.keys) == 2  # arm.urdf and cart.urdf, in collection order
    key = host.key
    assert key == arm_key(host)
    msg = sink.last("model")
    assert msg["generation"] >= 1
    # the 3d view's HDRIs ride with the model, for the page's environment controls
    assert msg["environments"] == environment_entries()
    model = msg["model"]
    assert model["name"] == "arm" and model["root"] == "base"
    base, upper, finger, _tool = model["links"]
    geo = base["visuals"][0]["geometry"]
    enc = key.replace("/", "%2F")
    assert geo == {
        "type": "mesh",
        "url": f"specto://items/l2/{enc}/m0/base.stl",
        "ext": "stl",
        "scale": [1.0, 1.0, 1.0],
        "uri": "package://arm_description/meshes/base.stl",  # for the properties panel
    }
    cover = upper["visuals"][0]["geometry"]
    assert cover["url"] == f"specto://items/l2/{enc}/m1/cover.obj" and cover["ext"] == "obj"
    assert cover["scale"] == [0.001, 0.001, 0.001]
    # the missing finger mesh keeps no url; the page draws the placeholder
    assert finger["visuals"][0]["geometry"]["url"] is None
    assert model["missing"] == [
        {
            "link": "finger",
            "kind": "visual",
            "uri": "package://gripper/meshes/finger.stl",
            "cause": "package `gripper` not found",
        }
    ]
    # the items host serves the meshes as roles of the robot's item, under its link, to
    # this view's page
    parts = runtime().items.parts("l2", key)
    assert parts[".m0"].endswith("base.stl") and parts[".m1"].endswith("cover.obj")
    assert parts[".urdf"].endswith("arm.urdf")
    assert host.caps.can_read("l2")
    # health amber, one note per cause
    assert ctx.health_of("robo") == {
        "status": "warn",
        "message": "arm: 4 links, 3 joints · 1 mesh missing: package `gripper` not found; "
        "set `params.package_paths`",
        "hint": "",
    }
    assert "warn" in COLORS


def test_package_paths_find_what_was_missing(engine: Engine):
    submit_robot(engine)
    ctx, host, sink = make_host(engine)
    assert sink.last("model")["model"]["missing"]
    set_params(engine, package_paths={"gripper": map_gripper(engine)})  # under the project
    host.flush()
    model = sink.last("model")["model"]
    assert model["missing"] == []
    url = model["links"][2]["visuals"][0]["geometry"]["url"]
    assert url.endswith("/m2/finger.stl")
    assert ctx.health_of("robo")["status"] == "ok"
    assert ctx.health_of("robo")["message"] == "arm: 4 links, 3 joints"
    snap = host.snapshot()
    assert snap["packages"]["gripper"]["via"] == "package_paths"
    assert snap["packages"]["arm_description"]["via"] == "package.xml"


def test_the_pose_comes_from_params_clamped_into_the_limits(engine: Engine):
    submit_robot(engine)
    ctx, host, sink = make_host(engine)
    assert sink.last("pose") == {"joints": {"shoulder": 0.0, "grip": 0.0}, "source": "zero"}
    set_params(engine, joints={"shoulder": 2.0, "grip": 0.01, "nope": 1.0})
    host.flush()
    assert sink.last("pose") == {"joints": {"shoulder": 1.5, "grip": 0.01}, "source": "params"}
    snap = host.snapshot()
    assert snap["pose"] == {"shoulder": 1.5, "grip": 0.01} and snap["pose_source"] == "params"
    assert snap["unknown_joints"] == ["nope"]
    assert "1 joint name not in the robot: nope" in ctx.health_of("robo")["message"]


def test_a_slider_release_replaces_params_joints_in_one_user_submit(engine: Engine):
    submit_robot(engine, {"joints": {"shoulder": 0.5, "nope": 3.0}})
    _ctx, host, sink = make_host(engine)
    steps = len(engine.live.history)
    # the page sends the full map of the actuated joints; it replaces params.joints
    host.bridge.send("joints", json.dumps({"shoulder": 0.5, "grip": 0.02}))
    host.flush()
    params = engine.live.data["views"]["robo"]["params"]
    assert params["joints"] == {"shoulder": 0.5, "grip": 0.02}
    assert len(engine.live.history) == steps + 1
    summary = engine.live.history_summaries()[-1]
    assert summary["origin"] == "user" and summary["label"] == "Arm: joints"
    assert sink.last("pose") == {"joints": {"shoulder": 0.5, "grip": 0.02}, "source": "params"}
    # beyond the limit: clamped before it is written; names it lacks are dropped
    host.bridge.send("joints", json.dumps({"shoulder": 0.5, "grip": 1.0, "ghost": 1}))
    assert engine.live.data["views"]["robo"]["params"]["joints"] == {
        "shoulder": 0.5,
        "grip": 0.04,
    }
    # the same map again is no submit
    steps = len(engine.live.history)
    host.bridge.send("joints", json.dumps({"shoulder": 0.5, "grip": 0.04}))
    assert len(engine.live.history) == steps
    # Reset pose: every joint at 0
    host.bridge.send("joints", json.dumps({"shoulder": 0.0, "grip": 0.0}))
    host.flush()
    assert sink.last("pose")["joints"] == {"shoulder": 0.0, "grip": 0.0}
    engine.live.undo()
    host.flush()
    assert sink.last("pose")["joints"]["grip"] == 0.04


def test_a_pick_selects_a_link_and_sends_its_properties_on_content(engine: Engine):
    submit_robot(engine)
    ctx, host, _sink = make_host(engine)
    key = host.key
    host.bridge.send("select", json.dumps({"link": "upper", "joint": None, "add": False}))
    assert ctx.links.output_of("robo", "selection") == Selection(
        keys=[f"{key}#upper"], last=f"{key}#upper"
    )
    content = ctx.links.output_of("robo", "content")
    assert isinstance(content, ContentValue)
    (entry,) = content.entries
    assert entry.key == f"{key}#upper" and entry.path == "arm.urdf#upper"
    assert entry.format == "json"
    assert entry.data["kind"] == "link" and entry.data["name"] == "upper"
    assert entry.data["inertial"]["mass"] == 1.5 and entry.data["parent_joint"] == "shoulder"

    # Ctrl adds; a joint row selects its child link and its properties are the joint's
    host.bridge.send("select", json.dumps({"link": None, "joint": "grip", "add": True}))
    sel = ctx.links.output_of("robo", "selection")
    assert sel.keys == [f"{key}#upper", f"{key}#finger"] and sel.last == f"{key}#finger"
    data = ctx.links.output_of("robo", "content").entries[0].data
    assert data["kind"] == "joint" and data["name"] == "grip" and data["type"] == "prismatic"
    assert host.snapshot()["selected"] == {"links": ["upper", "finger"], "joint": "grip"}

    # nothing: an empty selection and an empty content
    host.bridge.send("select", json.dumps({"link": None, "joint": None, "add": False}))
    assert ctx.links.output_of("robo", "selection") == Selection(keys=[], last=None)
    assert ctx.links.output_of("robo", "content").entries == []


def test_params_selected_links_select_the_links(engine: Engine):
    """The page writes its link selection to params.selected_links (setParams); the agent
    may too. The host selects those links: selection out and content, no echo of what
    is selected already."""
    submit_robot(engine)
    ctx, host, _sink = make_host(engine)
    key = host.key
    set_params(engine, selected_links=["upper", "ghost", "base"])
    host.flush()
    sel = ctx.links.output_of("robo", "selection")
    assert sel.keys == [f"{key}#upper", f"{key}#base"] and sel.last == f"{key}#base"
    assert ctx.links.output_of("robo", "content").entries[0].data["name"] == "base"
    # the page picked a link and then writes the same list: nothing more goes out
    host.bridge.send("select", json.dumps({"link": "finger", "joint": None, "add": False}))
    out = ctx.links.output_of("robo", "selection")
    emitted = []
    ctx.links.emit = lambda *a, **k: emitted.append(a)  # type: ignore[method-assign]
    set_params(engine, selected_links=["finger"])
    host.flush()
    assert emitted == [] and out.keys == [f"{key}#finger"]
    # the page may write it: the grant is there
    assert "params:selected_links" in host.caps


def test_a_selection_from_outside_is_shown_and_not_echoed(engine: Engine):
    submit_robot(engine)
    ctx, host, sink = make_host(engine)
    cart = next(k for k in host.keys if k.endswith("cart"))
    host.receive("selection", Selection(keys=[f"{cart}#wheel"], last=f"{cart}#wheel"))
    host.flush()
    assert host.key == cart and sink.last("model")["model"]["name"] == "cart"
    assert sink.last_port("selection") == {"keys": [f"{cart}#wheel"], "last": f"{cart}#wheel"}
    assert ctx.links.output_of("robo", "selection") is None  # not echoed
    assert host.snapshot()["selected"] == {"links": ["wheel"], "joint": None}
    # its properties still go out on content (selection propagates to content)
    assert ctx.links.output_of("robo", "content").entries[0].key == f"{cart}#wheel"


def test_the_camera_goes_out_and_an_outside_camera_is_not_echoed(engine: Engine):
    submit_robot(engine)
    ctx, host, sink = make_host(engine)
    host.bridge.emit("camera", json.dumps(CAMERA))
    assert ctx.links.output_of("robo", "camera") == CameraValue(**CAMERA)
    outside = CameraValue(position=(0, 0, 10), target=(0, 0, 0))
    host.receive("camera", outside)
    host.flush()
    assert sink.last_port("camera") == outside.model_dump(mode="json")
    assert ctx.links.output_of("robo", "camera") == CameraValue(**CAMERA)
    assert host.snapshot()["camera"] == CAMERA


def test_params_reach_the_page_with_their_defaults(engine: Engine):
    submit_robot(engine, {"view_mode": "collision"})
    _ctx, host, sink = make_host(engine)
    params = sink.last("params")
    assert params["view_mode"] == "collision" and params["up"] == "z"
    assert params["show_grid"] is True and params["environment"] == "none"
    assert params["camera_mode"] == "free" and params["camera_speed"] == 1.0
    set_params(engine, show_frames=True)
    host.flush()
    assert sink.last("params")["show_frames"] is True
    assert sink.last("params")["view_mode"] == "collision"


def test_the_page_writes_its_view_params_itself(engine: Engine):
    """The VIEW panel, the footer and the side panels write their params through
    Specto.setParams (one user submit each), as the 3d view's page does."""
    submit_robot(engine)
    _ctx, host, sink = make_host(engine)
    panels = {"robot": {"collapsed": True, "width": 260}, "view": {"collapsed": False}}
    reply = json.loads(host.bridge.setParams(json.dumps({"panels": panels})))
    assert reply["ok"] is True
    host.bridge.setParams(json.dumps({"view_mode": "visual_collision", "up": "-y"}))
    params = engine.live.data["views"]["robo"]["params"]
    assert params["panels"] == panels and params["view_mode"] == "visual_collision"
    host.flush()
    assert sink.last("params")["up"] == "-y"
    # never the pose: that is the joints message, clamped by the host
    refused = json.loads(host.bridge.setParams(json.dumps({"joints": {"grip": 9}})))
    assert refused["ok"] is False and refused["error"]["code"] == "capability"
    # a double-click frames on the page; the host only takes note (no submit)
    steps = len(engine.live.history)
    host.bridge.send("frame", json.dumps({"link": "upper"}))
    assert len(engine.live.history) == steps and host.snapshot()["framed"] == "upper"


def test_meshes_the_page_could_not_read_join_the_health_notes(engine: Engine):
    """The host owns the health dot: the page's ``loaded`` failures join the notes."""
    submit_robot(engine, {"package_paths": {"gripper": map_gripper(engine)}})
    ctx, host, _sink = make_host(engine)
    assert ctx.health_of("robo")["status"] == "ok"
    failed = [
        {"link": "base", "kind": "visual", "url": "u0", "error": "STL: bad header"},
        {"link": "upper", "kind": "visual", "url": "u1", "error": "STL: bad header"},
        {"link": "finger", "kind": "visual", "url": "u2", "error": "404"},
    ]
    host.bridge.send("loaded", json.dumps({"ms": 41.5, "meshes": 3, "failed": failed}))
    assert ctx.health_of("robo") == {
        "status": "warn",
        "message": "arm: 4 links, 3 joints · 2 meshes unreadable: STL: bad header "
        "· 1 mesh unreadable: 404",
        "hint": "",
    }
    assert host.snapshot()["loaded"] == {"ms": 41.5, "meshes": 3, "failed": failed}
    host.bridge.send("loaded", json.dumps({"ms": 30, "meshes": 3, "failed": []}))
    assert ctx.health_of("robo")["status"] == "ok"


def test_the_footer_steps_through_the_robots_and_params_selected_picks_one(engine: Engine):
    submit_robot(engine)
    _ctx, host, sink = make_host(engine)
    first = host.key
    host.bridge.send("step", json.dumps({"delta": 1}))
    host.flush()
    assert host.key != first and sink.last("step") == {"index": 1, "count": 2}
    assert sink.last("model")["model"]["name"] == "cart"
    assert host.snapshot()["robot"] == "cart"


def test_params_selected_names_the_robot_shown(engine: Engine):
    submit_robot(engine)
    cart = next(k for k in make_host(engine)[1].keys if k.endswith("cart"))
    REGISTRIES.pop().dispose_all()
    set_params(engine, selected=cart)
    _ctx, host, sink = make_host(engine)
    assert host.key == cart and sink.last("model")["model"]["name"] == "cart"


def test_a_file_that_is_not_a_urdf_is_an_error_in_health(engine: Engine, tmp_path: Path):
    (tmp_path / "proj" / "robots" / "cart.urdf").write_text("<nope/>", encoding="utf-8")
    submit_robot(engine)
    ctx, host, sink = make_host(engine)
    host.bridge.send("step", json.dumps({"delta": 1}))
    host.flush()
    health = ctx.health_of("robo")
    assert health["status"] == "error" and "not a URDF" in health["message"]
    assert sink.last("model") == {
        "generation": host.generation,
        "model": None,
        "environments": environment_entries(),
    }
    assert host.snapshot()["error"] and host.snapshot()["robot"] is None


def test_snap_reports_the_robot(engine: Engine):
    submit_robot(engine, {"joints": {"shoulder": 0.25}, "environment": "none"})
    _ctx, host, _sink = make_host(engine)
    host.bridge.emit("camera", json.dumps(CAMERA))
    snap = host.snapshot()
    assert snap["robot"] == "arm" and snap["file"].endswith("arm.urdf")
    assert snap["links"] == 4 and snap["joints"] == 3 and snap["actuated"] == ["shoulder", "grip"]
    assert snap["pose"] == {"shoulder": 0.25, "grip": 0.0} and snap["pose_source"] == "params"
    assert snap["view_mode"] == "lit" and snap["environment"] == "none"
    assert snap["missing"] == [
        {
            "link": "finger",
            "kind": "visual",
            "uri": "package://gripper/meshes/finger.stl",
            "cause": "package `gripper` not found",
        }
    ]
    assert snap["camera"] == CAMERA
    assert snap["count"] == 2 and snap["item"] == host.key
    assert isinstance(snap["parse_ms"], float)


def test_the_page_grants_and_the_keys_it_shares_with_the_3d_view(engine: Engine):
    submit_robot(engine)
    _ctx, host, _sink = make_host(engine)
    caps = set(host.caps)
    assert {"emit:camera", "emit:selection", "emit:content", "read:link:l2"} <= caps
    for p in (
        "panels",
        "camera_speed",
        "camera_mode",
        "view_mode",
        "up",
        "environment",
        "environment_intensity",
        "environment_rotation",
        "environment_background",
        "show_grid",
        "show_frames",
        "show_joint_axes",
        "show_com",
        "selected_links",
    ):
        assert f"params:{p}" in caps, p
    assert host.vendor_files == (
        "babylon/babylon.js",
        "babylon/babylonjs.loaders.min.js",
        "babylon/fflate.js",
    )
    # the pose and the package paths go through the host, never the page's own writes
    assert "params:joints" not in caps and "params:package_paths" not in caps
    assert STEP_KEYS["urdf"] == ("PageUp/PageDown",)  # the keyboard flies the camera
    assert VIEW_KEYS["urdf"] == VIEW_KEYS["3d"]


def test_pause_and_dispose(engine: Engine):
    submit_robot(engine)
    _ctx, host, sink = make_host(engine)
    key = host.key
    host.pause()
    host.flush()
    assert sink.last("render") == {"running": False}
    host.resume()
    host.flush()
    assert sink.last("render") == {"running": True}
    host.dispose()
    assert runtime().caps_of("robo") is None
    # the meshes are no longer served as roles of the item
    assert ".m0" not in (runtime().items.parts("l2", key) or {})


def test_next_to_a_mesh_only_its_material_library_and_images_are_served(engine: Engine):
    """ADR 0029: a URDF's meshes may live anywhere on disk; next to an OBJ the items host
    serves its .mtl and images only, and nothing next to the URDF or an STL."""
    from specto_app.web.scheme import locate_item

    submit_robot(engine, {"package_paths": {"gripper": map_gripper(engine)}})
    _ctx, host, _sink = make_host(engine)
    key = host.key
    meshes = Path(runtime().items.parts("l2", key)[".m1"]).parent  # cover.obj's folder
    (meshes / "cover.mtl").write_text("newmtl grey\n", encoding="utf-8")
    (meshes / "skin.png").write_bytes(b"\x89PNG\r\n")
    (meshes / "id_rsa").write_text("secret", encoding="utf-8")
    (meshes / "notes.txt").write_text("secret", encoding="utf-8")
    enc = key.replace("/", "%2F")

    def get(role: str, name: str) -> Path | None:
        entry: dict = {}
        return locate_item(
            "items",
            f"/l2/{enc}/{role}/{name}",
            "robo.page",
            caps_of=runtime().caps_of,
            items=runtime().items,
            entry=entry,
        )

    assert get("m1", "cover.obj") is not None  # the mesh itself, by its own name
    assert get("m1", "cover.mtl") is not None and get("m1", "skin.png") is not None
    assert get("m1", "id_rsa") is None and get("m1", "notes.txt") is None
    assert get("m0", "cover.mtl") is None  # nothing next to an STL
    assert get("urdf", "arm.urdf") is None  # nor next to the URDF
    # another link's items are untouched by the urdf view's rule
    assert runtime().items.side_allowed("other", "k", ".md", meshes / "notes.txt") is True


def test_a_slider_map_keeps_only_actuated_non_mimic_joints(engine: Engine, tmp_path: Path):
    (tmp_path / "proj" / "robots" / "cart.urdf").write_text(
        """<robot name="cart"><link name="a"/><link name="b"/><link name="c"/>
        <joint name="m" type="revolute"><parent link="a"/><child link="b"/>
          <limit lower="-1" upper="1" effort="1" velocity="1"/></joint>
        <joint name="f" type="revolute"><parent link="b"/><child link="c"/>
          <limit lower="-1" upper="1" effort="1" velocity="1"/><mimic joint="m"/></joint>
        </robot>""",
        encoding="utf-8",
    )
    submit_robot(engine)
    _ctx, host, _sink = make_host(engine)
    host.bridge.send("step", json.dumps({"delta": 1}))
    host.flush()
    assert host.snapshot()["robot"] == "cart"
    host.bridge.send("joints", json.dumps({"m": 0.5, "f": 0.9}))
    assert engine.live.data["views"]["robo"]["params"]["joints"] == {"m": 0.5}


def test_a_warn_view_still_offers_its_gestures_in_the_status_bar():
    from specto_app.canvas.controller import GESTURES, status_hint

    info = {"id": "robo", "type": "urdf", "health": "warn", "healthMessage": "arm: 1 mesh missing"}
    line = status_hint(info, hovered=True)
    assert line.startswith("robo: arm: 1 mesh missing") and line.endswith(GESTURES)
    assert VIEW_KEYS["urdf"] in line


def test_a_mesh_swapped_after_the_parse_is_checked_again_when_served(engine: Engine):
    submit_robot(engine)
    _ctx, host, _sink = make_host(engine)
    key = host.key
    parts = runtime().items.parts("l2", key)
    stl_path = Path(parts[".m0"])
    assert stl_path.name == "base.stl"
    stl_path.unlink()
    stl_path.mkdir()  # no longer a regular file
    after = runtime().items.parts("l2", key)
    assert ".m0" not in after and ".m1" in after and ".urdf" in after


def test_a_mesh_swapped_for_a_link_is_judged_by_its_target(engine: Engine, tmp_path: Path):
    """Review item 6: the served file's resolved path is checked when it is served, so a
    mesh replaced by a link to another file after the parse serves nothing."""
    from specto_app.web.scheme import locate_item

    submit_robot(engine)
    _ctx, host, _sink = make_host(engine)
    key = host.key
    stl_path = Path(runtime().items.parts("l2", key)[".m0"])
    secret = tmp_path / "proj" / "notes.txt"
    secret.write_text("secret", encoding="utf-8")
    stl_path.unlink()
    try:
        stl_path.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks need privileges here")
    assert ".m0" not in runtime().items.parts("l2", key)
    enc = key.replace("/", "%2F")
    entry: dict = {}
    got = locate_item(
        "items",
        f"/l2/{enc}/m0/base.stl",
        "robo.page",
        caps_of=runtime().caps_of,
        items=runtime().items,
        entry=entry,
    )
    assert got is None
    assert runtime().items.side_allowed("l2", key, ".m0", secret) is False


def test_a_file_outside_the_project_and_its_packages_is_not_served(engine: Engine, tmp_path: Path):
    outside = tmp_path / "elsewhere" / "far.stl"
    outside.parent.mkdir()
    outside.write_bytes(b"solid f\nendsolid f\n")
    (tmp_path / "proj" / "robots" / "cart.urdf").write_text(
        f"""<robot name="cart"><link name="a"><visual><geometry>
        <mesh filename="{outside.as_uri()}"/></geometry></visual></link></robot>""",
        encoding="utf-8",
    )
    submit_robot(engine)
    ctx, host, _sink = make_host(engine)
    host.bridge.send("step", json.dumps({"delta": 1}))
    host.flush()
    snap = host.snapshot()
    assert snap["robot"] == "cart"
    assert snap["missing"][0]["cause"] == "outside the project and its packages"
    assert "outside the project and its packages" in ctx.health_of("robo")["message"]


def test_one_items_request_asks_the_robot_resolver_once(engine: Engine):
    from specto_app.web.scheme import locate_item

    submit_robot(engine)
    _ctx, host, _sink = make_host(engine)
    key = host.key
    meshes = host._meshes
    calls = []
    real = meshes.parts

    def counted(link_id, k):
        calls.append(k)
        return real(link_id, k)

    meshes.parts = counted  # type: ignore[method-assign]
    enc = key.replace("/", "%2F")
    assert (
        locate_item(
            "items",
            f"/l2/{enc}/m1/cover.obj",
            "robo.page",
            caps_of=runtime().caps_of,
            items=runtime().items,
            entry={},
        )
        is not None
    )
    assert len(calls) == 1


def test_the_robot_s_guard_root_is_the_project(engine: Engine):
    """SPEC-91 on main judges a part's own path against the delivering resolver's
    ``guard_root``; the robot resolver answers with the project (its scope's first
    folder), so a project on a share serves its own robot, and nothing for another link."""
    submit_robot(engine)
    _ctx, host, _sink = make_host(engine)
    meshes = host._meshes
    assert meshes.guard_root("l2") == meshes.scope[0]
    assert Path(meshes.scope[0]) == Path(engine.project.root).resolve()
    assert meshes.guard_root("elsewhere") is None
    assert runtime().items.guard_root("l2") == meshes.scope[0]
