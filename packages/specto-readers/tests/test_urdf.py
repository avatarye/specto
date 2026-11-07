"""The urdf view's robot model (SPEC-43.1, ADR 0028): a URDF parsed
(yourdfpy, parse only) into the host/page contract's model, its mesh references found by
Specto's own package:// resolver, in the design's order, and what cannot be found listed
under ``missing`` with its cause, never guessed by stripping prefixes and never fetched."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from specto_readers.urdf import (
    PackageResolver,
    UrdfError,
    build_robot,
    missing_notes,
)

ARM = """<?xml version="1.0"?>
<robot name="arm">
  <material name="grey"><color rgba="0.5 0.5 0.5 1"/></material>
  <link name="base">
    <visual>
      <geometry><mesh filename="package://arm_description/meshes/base.stl"
                      scale="0.001 0.001 0.001"/></geometry>
      <material name="grey"/>
    </visual>
    <collision><origin xyz="0 0 0.05"/><geometry><box size="0.2 0.3 0.1"/></geometry></collision>
    <inertial>
      <origin xyz="0 0 0.1"/><mass value="2.5"/>
      <inertia ixx="1" ixy="0.1" ixz="0.2" iyy="2" iyz="0.3" izz="3"/>
    </inertial>
  </link>
  <link name="upper">
    <visual>
      <origin xyz="0 0 0.2" rpy="0 1.2 0.3"/>
      <geometry><cylinder radius="0.05" length="0.4"/></geometry>
      <material name="red"><color rgba="1 0 0 1"/></material>
    </visual>
    <visual><geometry><mesh filename="meshes/cover.obj"/></geometry></visual>
    <collision><geometry><sphere radius="0.06"/></geometry></collision>
  </link>
  <link name="finger"/>
  <link name="tool"/>
  <joint name="shoulder" type="revolute">
    <parent link="base"/><child link="upper"/>
    <origin xyz="0 0 0.1" rpy="0.1 0 0"/><axis xyz="0 1 0"/>
    <limit lower="-1.5" upper="1.5" effort="10" velocity="2"/>
    <dynamics damping="0.1" friction="0.2"/>
  </joint>
  <joint name="grip" type="prismatic">
    <parent link="upper"/><child link="finger"/><axis xyz="1 0 0"/>
    <limit lower="0" upper="0.04" effort="1" velocity="1"/>
    <mimic joint="shoulder" multiplier="2" offset="0.1"/>
  </joint>
  <joint name="tool_fix" type="fixed"><parent link="finger"/><child link="tool"/></joint>
</robot>
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def package(root: Path, name: str) -> Path:
    """A ROS package folder: a package.xml naming it."""
    write(root / "package.xml", f"<package format='3'><name>{name}</name></package>")
    return root


def stl(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"solid t\nendsolid t\n")
    return path


# -- the model -----------------------------------------------------------------------------


def test_the_model_follows_the_contract(tmp_path: Path):
    pkg = package(tmp_path / "ws" / "arm_description", "arm_description")
    urdf = write(pkg / "urdf" / "arm.urdf", ARM)
    stl(pkg / "meshes" / "base.stl")
    stl(urdf.parent / "meshes" / "cover.obj")

    robot = build_robot(urdf)
    model = robot["model"]
    assert model["name"] == "arm" and model["root"] == "base"
    assert [lk["name"] for lk in model["links"]] == ["base", "upper", "finger", "tool"]
    assert model["missing"] == []

    base = model["links"][0]
    (vis,) = base["visuals"]
    assert vis["origin"] == {"xyz": [0.0, 0.0, 0.0], "rpy": [0.0, 0.0, 0.0]}
    assert vis["geometry"] == {
        "type": "mesh",
        "url": None,
        "ext": "stl",
        "scale": [0.001, 0.001, 0.001],
        "file": "m0",
    }
    # a named material takes its colour from the robot's global material
    assert vis["material"] == {"name": "grey", "color": [0.5, 0.5, 0.5, 1.0], "texture_url": None}
    (col,) = base["collisions"]
    assert col == {
        "origin": {"xyz": [0.0, 0.0, 0.05], "rpy": [0.0, 0.0, 0.0]},
        "geometry": {"type": "box", "size": [0.2, 0.3, 0.1]},
    }
    assert base["inertial"] == {
        "origin": {"xyz": [0.0, 0.0, 0.1], "rpy": [0.0, 0.0, 0.0]},
        "mass": 2.5,
        "inertia": {"ixx": 1.0, "ixy": 0.1, "ixz": 0.2, "iyy": 2.0, "iyz": 0.3, "izz": 3.0},
    }

    upper = model["links"][1]
    cyl, cover = upper["visuals"]
    assert cyl["geometry"] == {"type": "cylinder", "radius": 0.05, "length": 0.4}
    assert cyl["origin"]["xyz"] == pytest.approx([0.0, 0.0, 0.2])
    assert cyl["origin"]["rpy"] == pytest.approx([0.0, 1.2, 0.3])
    assert cyl["material"] == {"name": "red", "color": [1.0, 0.0, 0.0, 1.0], "texture_url": None}
    assert cover["geometry"]["ext"] == "obj" and cover["geometry"]["scale"] == [1.0, 1.0, 1.0]
    assert cover["material"] is None
    assert upper["collisions"][0]["geometry"] == {"type": "sphere", "radius": 0.06}
    assert model["links"][2] == {
        "name": "finger",
        "visuals": [],
        "collisions": [],
        "inertial": None,
    }

    shoulder, grip, fix = model["joints"]
    assert shoulder["name"] == "shoulder" and shoulder["type"] == "revolute"
    assert (shoulder["parent"], shoulder["child"]) == ("base", "upper")
    assert shoulder["origin"]["xyz"] == pytest.approx([0.0, 0.0, 0.1])
    assert shoulder["origin"]["rpy"] == pytest.approx([0.1, 0.0, 0.0])
    assert shoulder["axis"] == [0.0, 1.0, 0.0]
    assert shoulder["limit"] == {"lower": -1.5, "upper": 1.5, "effort": 10.0, "velocity": 2.0}
    assert shoulder["dynamics"] == {"damping": 0.1, "friction": 0.2}
    assert shoulder["mimic"] is None
    assert grip["mimic"] == {"joint": "shoulder", "multiplier": 2.0, "offset": 0.1}
    assert grip["dynamics"] is None
    # URDF's defaults: a fixed joint's axis is x, it has no limit
    assert fix["type"] == "fixed" and fix["axis"] == [1.0, 0.0, 0.0] and fix["limit"] is None

    # the mesh files the host serves, by the role the geometry names
    assert robot["files"] == {
        "m0": str((pkg / "meshes" / "base.stl").resolve()),
        "m1": str((urdf.parent / "meshes" / "cover.obj").resolve()),
    }
    assert robot["packages"] == {
        "arm_description": {"path": str(pkg.resolve()), "via": "package.xml"}
    }
    # the joints a slider moves: grip mimics shoulder, tool_fix is fixed
    assert robot["actuated"] == ["shoulder"]


def test_one_file_used_twice_is_one_role(tmp_path: Path):
    urdf = write(
        tmp_path / "twin.urdf",
        """<robot name="twin">
          <link name="a"><visual><geometry><mesh filename="m.stl"/></geometry></visual>
                         <collision><geometry><mesh filename="m.stl"/></geometry></collision></link>
        </robot>""",
    )
    stl(tmp_path / "m.stl")
    robot = build_robot(urdf)
    link = robot["model"]["links"][0]
    assert link["visuals"][0]["geometry"]["file"] == "m0"
    assert link["collisions"][0]["geometry"]["file"] == "m0"
    assert list(robot["files"]) == ["m0"]


def test_continuous_joints_and_defaults(tmp_path: Path):
    urdf = write(
        tmp_path / "wheel.urdf",
        """<robot name="cart">
          <link name="body"/><link name="wheel"/>
          <joint name="spin" type="continuous">
            <parent link="body"/><child link="wheel"/><axis xyz="0 0 1"/>
            <limit effort="5" velocity="10"/>
          </joint>
        </robot>""",
    )
    (spin,) = build_robot(urdf)["model"]["joints"]
    # URDF's limit defaults lower and upper to 0; the page wraps a continuous joint
    assert spin["limit"] == {"lower": 0.0, "upper": 0.0, "effort": 5.0, "velocity": 10.0}
    assert spin["origin"] == {"xyz": [0.0, 0.0, 0.0], "rpy": [0.0, 0.0, 0.0]}
    assert build_robot(urdf)["actuated"] == ["spin"]


def test_a_file_that_is_not_a_urdf_is_an_error(tmp_path: Path):
    with pytest.raises(UrdfError, match="not a URDF"):
        build_robot(write(tmp_path / "x.urdf", "<notarobot/>"))
    with pytest.raises(UrdfError):
        build_robot(write(tmp_path / "y.urdf", "<robot name='r'><link"))
    with pytest.raises(UrdfError, match="cannot read"):
        build_robot(tmp_path / "absent.urdf")


def test_rpy_survives_the_round_trip(tmp_path: Path):
    rpy = (0.3, -0.7, 2.9)
    urdf = write(
        tmp_path / "r.urdf",
        f"""<robot name="r"><link name="a"><visual>
              <origin xyz="1 2 3" rpy="{rpy[0]} {rpy[1]} {rpy[2]}"/>
              <geometry><box size="1 1 1"/></geometry></visual></link></robot>""",
    )
    origin = build_robot(urdf)["model"]["links"][0]["visuals"][0]["origin"]
    assert origin["xyz"] == pytest.approx([1, 2, 3])
    assert origin["rpy"] == pytest.approx(list(rpy))
    assert all(math.isfinite(v) for v in origin["rpy"])


# -- the resolver, in the design's order ------------------------------------------------------


def test_package_paths_come_first_by_name_or_by_folder(tmp_path: Path):
    # the package is found next to the URDF too, but params.package_paths wins
    near = package(tmp_path / "near" / "arm_description", "arm_description")
    urdf = write(near / "urdf" / "arm.urdf", ARM)
    stl(near / "meshes" / "base.stl")
    chosen = tmp_path / "chosen"
    stl(chosen / "meshes" / "base.stl")
    uri = "package://arm_description/meshes/base.stl"

    by_name = PackageResolver(urdf, package_paths={"arm_description": str(chosen)})
    hit = by_name.resolve(uri)
    assert hit.path == str((chosen / "meshes" / "base.stl").resolve())
    assert hit.via == "package_paths" and hit.cause is None

    # a list of folders: each is searched for the package (a folder named for it, or a
    # package.xml naming it)
    shelf = tmp_path / "shelf"
    stl(shelf / "arm_description" / "meshes" / "base.stl")
    by_folder = PackageResolver(urdf, package_paths=[str(tmp_path / "empty"), str(shelf)])
    assert by_folder.resolve(uri).path == str(
        (shelf / "arm_description" / "meshes" / "base.stl").resolve()
    )
    renamed = package(tmp_path / "shelf2" / "src" / "whatever", "arm_description")
    stl(renamed / "meshes" / "base.stl")
    deep = PackageResolver(urdf, package_paths=[str(tmp_path / "shelf2")])
    assert deep.resolve(uri).path == str((renamed / "meshes" / "base.stl").resolve())


def test_the_nearest_package_xml_walking_up_then_the_project(tmp_path: Path):
    # the URDF lives outside its package; the project holds the package elsewhere
    proj = tmp_path / "proj"
    urdf = write(proj / "robots" / "arm.urdf", ARM)
    pkg = package(proj / "src" / "arm_pkg", "arm_description")
    stl(pkg / "meshes" / "base.stl")
    res = PackageResolver(urdf, search_roots=[proj], environ={})
    hit = res.resolve("package://arm_description/meshes/base.stl")
    assert hit.path == str((pkg / "meshes" / "base.stl").resolve()) and hit.via == "package.xml"
    # a package.xml with another name is not taken, whatever its folder is called
    other = package(proj / "src" / "arm_description", "not_it")
    stl(other / "meshes" / "base.stl")
    assert res.resolve("package://arm_description/meshes/base.stl").path == hit.path


def test_ros_package_path_and_ament_prefix_path(tmp_path: Path):
    urdf = write(tmp_path / "lonely" / "arm.urdf", ARM)
    ros = tmp_path / "ros"
    stl(ros / "arm_description" / "meshes" / "base.stl")
    res = PackageResolver(urdf, environ={"ROS_PACKAGE_PATH": str(ros)})
    hit = res.resolve("package://arm_description/meshes/base.stl")
    assert hit.via == "ROS_PACKAGE_PATH" and hit.path.endswith("base.stl")

    ament = tmp_path / "install"
    stl(ament / "share" / "arm_description" / "meshes" / "base.stl")
    res = PackageResolver(urdf, environ={"AMENT_PREFIX_PATH": str(ament)})
    hit = res.resolve("package://arm_description/meshes/base.stl")
    assert hit.via == "AMENT_PREFIX_PATH"
    assert hit.path == str((ament / "share" / "arm_description" / "meshes" / "base.stl").resolve())


def test_relative_and_file_uris_resolve_directly(tmp_path: Path):
    urdf = write(tmp_path / "r" / "arm.urdf", ARM)
    rel = stl(tmp_path / "r" / "meshes" / "a.stl")
    absolute = stl(tmp_path / "elsewhere" / "b.stl")
    res = PackageResolver(urdf, environ={})
    assert res.resolve("meshes/a.stl").path == str(rel.resolve())
    assert res.resolve("meshes/a.stl").via == "relative"
    assert res.resolve(absolute.resolve().as_uri()).path == str(absolute.resolve())
    assert res.resolve(str(absolute)).path == str(absolute.resolve())


def test_nothing_is_guessed_by_stripping_prefixes(tmp_path: Path):
    # meshes/base.stl sits next to the URDF, but package://arm_description/... is not it
    urdf = write(tmp_path / "arm.urdf", ARM)
    stl(tmp_path / "meshes" / "base.stl")
    stl(tmp_path / "arm_description" / "meshes" / "base.stl")  # no package.xml: not searched
    res = PackageResolver(urdf, environ={})
    miss = res.resolve("package://arm_description/meshes/base.stl")
    assert miss.path is None and miss.cause == "package `arm_description` not found"


def test_http_is_never_fetched(tmp_path: Path):
    res = PackageResolver(write(tmp_path / "a.urdf", ARM), environ={})
    miss = res.resolve("https://example.com/base.stl")
    assert miss.path is None and miss.cause == "http(s) is never fetched (offline)"


def test_missing_meshes_are_listed_with_their_causes(tmp_path: Path):
    pkg = package(tmp_path / "arm_description", "arm_description")
    urdf = write(
        pkg / "urdf" / "arm.urdf",
        """<robot name="arm">
          <link name="a">
            <visual><geometry><mesh filename="package://arm_description/meshes/gone.stl"/></geometry></visual>
            <visual><geometry><mesh filename="package://gripper/meshes/f.stl"/></geometry></visual>
            <collision><geometry><mesh filename="package://gripper/meshes/f_col.stl"/></geometry></collision>
          </link>
          <link name="b">
            <visual><geometry><mesh filename="package://gripper/meshes/g.stl"/></geometry></visual>
            <visual><geometry><mesh filename="meshes/part.ply"/></geometry></visual>
            <visual><geometry><mesh filename="http://example.com/x.stl"/></geometry></visual>
          </link>
        </robot>""",
    )
    stl(pkg / "urdf" / "meshes" / "part.ply")
    robot = build_robot(urdf, environ={})
    missing = robot["model"]["missing"]
    assert missing == [
        {
            "link": "a",
            "kind": "visual",
            "uri": "package://arm_description/meshes/gone.stl",
            "cause": "`meshes/gone.stl` is not in package `arm_description`",
        },
        {
            "link": "a",
            "kind": "visual",
            "uri": "package://gripper/meshes/f.stl",
            "cause": "package `gripper` not found",
        },
        {
            "link": "a",
            "kind": "collision",
            "uri": "package://gripper/meshes/f_col.stl",
            "cause": "package `gripper` not found",
        },
        {
            "link": "b",
            "kind": "visual",
            "uri": "package://gripper/meshes/g.stl",
            "cause": "package `gripper` not found",
        },
        {
            "link": "b",
            "kind": "visual",
            "uri": "meshes/part.ply",
            "cause": "mesh format .ply is not supported (STL, OBJ, DAE, glTF)",
        },
        {
            "link": "b",
            "kind": "visual",
            "uri": "http://example.com/x.stl",
            "cause": "http(s) is never fetched (offline)",
        },
    ]
    # a missing mesh keeps its geometry with no url: the page draws the placeholder
    geo = robot["model"]["links"][0]["visuals"][0]["geometry"]
    assert geo == {"type": "mesh", "url": None, "ext": "stl", "scale": [1.0, 1.0, 1.0]}
    assert robot["files"] == {}

    # one health note per cause, most meshes first
    assert missing_notes(missing) == [
        "3 meshes missing: package `gripper` not found; set `params.package_paths`",
        "1 mesh missing: `meshes/gone.stl` is not in package `arm_description`",
        "1 mesh missing: mesh format .ply is not supported (STL, OBJ, DAE, glTF)",
        "1 mesh missing: http(s) is never fetched (offline)",
    ]


def test_a_dae_goes_as_dae_until_its_glb_sidecar_exists(tmp_path: Path):
    urdf = write(
        tmp_path / "d.urdf",
        """<robot name="d"><link name="a"><visual><geometry>
             <mesh filename="m/body.dae"/></geometry></visual></link></robot>""",
    )
    stl(tmp_path / "m" / "body.dae")
    robot = build_robot(urdf)
    geo = robot["model"]["links"][0]["visuals"][0]["geometry"]
    assert geo["ext"] == "dae" and robot["files"]["m0"].endswith("body.dae")
    # SPEC-42's converted sidecar next to it is preferred
    stl(tmp_path / "m" / "body.dae.glb")
    robot = build_robot(urdf)
    geo = robot["model"]["links"][0]["visuals"][0]["geometry"]
    assert geo["ext"] == "glb" and robot["files"]["m0"].endswith("body.dae.glb")


def test_a_material_texture_is_resolved_like_a_mesh(tmp_path: Path):
    urdf = write(
        tmp_path / "t.urdf",
        """<robot name="t">
          <material name="skin"><texture filename="tex/skin.png"/></material>
          <link name="a"><visual><geometry><box size="1 1 1"/></geometry>
            <material name="skin"/></visual></link></robot>""",
    )
    stl(tmp_path / "tex" / "skin.png")
    robot = build_robot(urdf)
    mat = robot["model"]["links"][0]["visuals"][0]["material"]
    assert mat == {"name": "skin", "color": None, "texture_url": None, "texture_file": "m0"}
    assert robot["files"]["m0"].endswith("skin.png")
