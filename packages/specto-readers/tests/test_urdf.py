"""The urdf view's robot model (SPEC-43.1, ADR 0029): a URDF parsed (the standard
library's xml.etree) into the host/page contract's model, its mesh references found by
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
    package_name_of,
    parse_urdf,
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
        "uri": "package://arm_description/meshes/base.stl",
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
            "cause": "not in package `arm_description` (found by package.xml)",
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
            "cause": "mesh format .ply is not supported (STL, OBJ, glTF)",
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
    assert geo == {
        "type": "mesh",
        "url": None,
        "ext": "stl",
        "scale": [1.0, 1.0, 1.0],
        "uri": "package://arm_description/meshes/gone.stl",
    }
    assert robot["files"] == {}

    # one health note per cause (never per file), most meshes first, naming the step
    # that found a package
    assert missing_notes(missing) == [
        "3 meshes missing: package `gripper` not found; set `params.package_paths`",
        "1 mesh missing: not in package `arm_description` (found by package.xml)",
        "1 mesh missing: mesh format .ply is not supported (STL, OBJ, glTF)",
        "1 mesh missing: http(s) is never fetched (offline)",
    ]
    two = [*missing, {**missing[0], "uri": "package://arm_description/meshes/other.stl"}]
    assert missing_notes(two)[1] == (
        "2 meshes missing: not in package `arm_description` (found by package.xml)"
    )


def test_a_dae_is_missing_until_spec_42_converts_it(tmp_path: Path):
    """The page has no DAE loader: until SPEC-42's dae.prepare is on main a DAE draws as
    a placeholder with one note (TODO(SPEC-42)); a sidecar beside it is not guessed at."""
    urdf = write(
        tmp_path / "d.urdf",
        """<robot name="d"><link name="a"><visual><geometry>
             <mesh filename="m/body.dae"/></geometry></visual></link></robot>""",
    )
    stl(tmp_path / "m" / "body.dae")
    stl(tmp_path / "m" / "body.dae.glb")
    robot = build_robot(urdf)
    geo = robot["model"]["links"][0]["visuals"][0]["geometry"]
    assert geo["ext"] is None and geo["url"] is None and "file" not in geo
    assert robot["files"] == {}
    assert robot["model"]["missing"] == [
        {
            "link": "a",
            "kind": "visual",
            "uri": "m/body.dae",
            "cause": "DAE needs the SPEC-42 converter",
        }
    ]


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


def test_a_mesh_keeps_its_uri(tmp_path: Path):
    urdf = write(tmp_path / "u.urdf", ARM)
    model = parse_urdf(urdf)
    cover = model["links"][1]["visuals"][1]["geometry"]
    assert cover["uri"] == "meshes/cover.obj" and cover["url"] is None
    assert cover["scale"] == [1.0, 1.0, 1.0]


# -- the parser: the standard library, strict about what it reads ------------------------------


def robot(body: str, name: str = "r") -> bytes:
    return f'<?xml version="1.0"?><robot name="{name}">{body}</robot>'.encode()


def test_a_doctype_or_an_entity_is_refused_before_parsing():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE robot [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><robot name="&b;"/>'
    )
    with pytest.raises(UrdfError, match="DOCTYPE or an entity"):
        parse_urdf(bomb)
    with pytest.raises(UrdfError, match="DOCTYPE"):
        parse_urdf(b'<!DOCTYPE robot SYSTEM "robot.dtd"><robot name="r"/>')
    with pytest.raises(UrdfError, match="DOCTYPE"):
        parse_urdf(b'<!DOCTYPE robot><robot name="r"/>')


def test_a_doctype_in_a_package_xml_is_not_read(tmp_path: Path):
    pkg = tmp_path / "evil"
    write(
        pkg / "package.xml",
        '<!DOCTYPE p [<!ENTITY n "arm_description">]><package><name>&n;</name></package>',
    )
    urdf = write(pkg / "urdf" / "arm.urdf", ARM)
    res = PackageResolver(urdf, environ={})
    assert res.find_package("arm_description") is None


def test_numbers_are_coerced_strictly_and_rpy_is_kept_as_written():
    model = parse_urdf(
        robot(
            '<link name="a"><visual>'
            '<origin xyz=" 1e-3  2\t3 " rpy="0.1 -0.2 3.14159265358979"/>'
            '<geometry><box size="1 2 3"/></geometry></visual></link>'
        )
    )
    origin = model["links"][0]["visuals"][0]["origin"]
    assert origin == {"xyz": [0.001, 2.0, 3.0], "rpy": [0.1, -0.2, 3.14159265358979]}


BOX = '<geometry><box size="1 1 1"/></geometry>'


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            f'<link name="a"><visual><origin xyz="1 2"/>{BOX}</visual></link>',
            "link a <origin xyz> needs 3",
        ),
        (
            f'<link name="a"><visual><origin rpy="1 x 2"/>{BOX}</visual></link>',
            "link a <origin rpy> is not numbers",
        ),
        (
            '<link name="a"><visual><geometry><box size="1 1 nan"/></geometry></visual></link>',
            "<box size> needs 3 finite",
        ),
        (
            '<link name="a"><visual><geometry><cylinder radius="0.1"/></geometry></visual></link>',
            "<cylinder> has no length",
        ),
        (
            '<link name="a"><inertial><mass value="heavy"/></inertial></link>',
            "<mass> value is not numbers",
        ),
        (
            '<link name="a"/><link name="b"/><joint name="j" type="revolute"><parent link="a"/>'
            '<child link="b"/><limit lower="-1" upper="one"/></joint>',
            "joint j <limit> upper",
        ),
        (
            '<link name="a"><visual><geometry><mesh filename="a.stl" scale="1 2"/></geometry>'
            "</visual></link>",
            "<mesh scale> needs 1 or 3",
        ),
    ],
)
def test_a_number_that_is_not_one_is_an_error_naming_its_element(body: str, match: str):
    with pytest.raises(UrdfError, match=match):
        parse_urdf(robot(body))


def test_named_materials_resolve_and_a_visuals_own_colour_wins():
    sphere = '<geometry><sphere radius="1"/></geometry>'
    model = parse_urdf(
        robot(
            '<material name="blue"><color rgba="0 0 1 1"/></material>'
            '<link name="a">'
            f'<visual>{sphere}<material name="blue"/></visual>'
            f'<visual>{sphere}<material name="blue"><color rgba="1 0 0 0.5"/></material></visual>'
            f'<visual>{sphere}<material name="nope"/></visual>'
            "</link>"
        )
    )
    mats = [v["material"] for v in model["links"][0]["visuals"]]
    assert mats[0] == {"name": "blue", "color": [0.0, 0.0, 1.0, 1.0], "texture_url": None}
    assert mats[1]["color"] == [1.0, 0.0, 0.0, 0.5]
    assert mats[2] == {"name": "nope", "color": None, "texture_url": None}


def test_mimic_defaults_and_a_mimic_of_nothing_is_refused():
    body = (
        '<link name="a"/><link name="b"/><link name="c"/>'
        '<joint name="m" type="revolute"><parent link="a"/><child link="b"/>'
        '<limit lower="-1" upper="1" effort="1" velocity="1"/></joint>'
        '<joint name="f" type="revolute"><parent link="b"/><child link="c"/>'
        '<mimic joint="m"/></joint>'
    )
    _m, f = parse_urdf(robot(body))["joints"]
    assert f["mimic"] == {"joint": "m", "multiplier": 1.0, "offset": 0.0}
    with pytest.raises(UrdfError, match="mimics 'gone'"):
        parse_urdf(robot(body.replace('<mimic joint="m"/>', '<mimic joint="gone"/>')))
    with pytest.raises(UrdfError, match="names no joint"):
        parse_urdf(robot(body.replace('<mimic joint="m"/>', "<mimic/>")))


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("<link/>", "a <link> has no name"),
        ('<link name="a"/><link name="a"/>', "two links are named 'a'"),
        (
            '<link name="a"/><link name="b"/><joint name="j" type="hinge"><parent link="a"/>'
            '<child link="b"/></joint>',
            "type 'hinge'",
        ),
        (
            '<link name="a"/><joint name="j" type="fixed"><parent link="a"/><child link="z"/>'
            "</joint>",
            "child link 'z' is not in the robot",
        ),
        (
            '<link name="a"/><link name="b"/><joint name="j" type="fixed"><parent link="a"/>'
            "</joint>",
            "has no <child link>",
        ),
        (
            '<link name="a"/><link name="b"/><link name="c"/>'
            '<joint name="j" type="fixed"><parent link="a"/><child link="c"/></joint>'
            '<joint name="k" type="fixed"><parent link="b"/><child link="c"/></joint>',
            "child of two joints",
        ),
        (
            '<link name="a"/><link name="b"/>'
            '<joint name="j" type="fixed"><parent link="a"/><child link="b"/></joint>'
            '<joint name="j" type="fixed"><parent link="b"/><child link="a"/></joint>',
            "two joints are named 'j'",
        ),
        ('<link name="a"><visual><geometry><mesh/></geometry></visual></link>', "has no filename"),
    ],
)
def test_malformed_robots_say_what_is_wrong(body: str, match: str):
    with pytest.raises(UrdfError, match=match):
        parse_urdf(robot(body))


def test_not_xml_or_not_a_robot_is_an_error():
    with pytest.raises(UrdfError, match="not well-formed"):
        parse_urdf(b'<robot name="r"><link name="a">')  # truncated
    with pytest.raises(UrdfError, match="not well-formed"):
        parse_urdf(b"")
    with pytest.raises(UrdfError, match="its root element is <sdf>"):
        parse_urdf(b'<sdf version="1.6"/>')


def test_partial_robots_parse():
    # one link, nothing else: a robot of its own, its root
    lone = parse_urdf(robot('<link name="only"/>', name=""))
    assert lone["root"] == "only" and lone["joints"] == [] and lone["name"] == "the URDF"
    # a visual with no geometry, or an empty geometry, is left out; unknown tags are ignored
    model = parse_urdf(
        robot(
            '<link name="a">'
            '<visual><origin xyz="0 0 1"/></visual>'
            "<visual><geometry/></visual>"
            f"<collision>{BOX}</collision>"
            '<inertial><mass value="2"/></inertial>'
            "</link>"
            '<gazebo reference="a"><material>Gazebo/Grey</material></gazebo>'
            '<transmission name="t"/>'
        )
    )
    link = model["links"][0]
    assert link["visuals"] == [] and len(link["collisions"]) == 1
    # an inertia left out is zeros
    assert link["inertial"]["mass"] == 2.0 and link["inertial"]["inertia"]["ixx"] == 0.0
    # an empty robot
    empty = parse_urdf(robot(""))
    assert empty["links"] == [] and empty["root"] is None


def test_a_uniform_mesh_scale_is_three_numbers():
    model = parse_urdf(
        robot(
            '<link name="a"><visual><geometry><mesh filename="a.stl" scale="0.01"/>'
            "</geometry></visual></link>"
        )
    )
    assert model["links"][0]["visuals"][0]["geometry"]["scale"] == [0.01, 0.01, 0.01]


def test_the_resolvers_folder_is_picklable_for_xacros_child_process(tmp_path: Path):
    import pickle

    pkg = package(tmp_path / "ws" / "arm_description", "arm_description")
    urdf = write(pkg / "urdf" / "arm.urdf", ARM)
    folder = PackageResolver(urdf, environ={}).folder
    again = pickle.loads(pickle.dumps(folder))
    assert again("arm_description") == str(pkg.resolve())
    assert again("nope") is None


def test_xml_text_parses_with_the_same_refusals_and_resolves_next_to_its_file(tmp_path: Path):
    """xacro's output (SPEC-43.3) arrives as text: parse_urdf(text=...) runs the same
    pre-pass, and build_robot(path, text=...) resolves meshes next to ``path``."""
    model = parse_urdf(text='<robot name="t"><link name="a"/></robot>')
    assert model["name"] == "t" and model["root"] == "a"
    with pytest.raises(UrdfError, match=r"arm.urdf.xacro declares a DOCTYPE"):
        parse_urdf(text='<!DOCTYPE r><robot name="r"/>', label="arm.urdf.xacro")
    with pytest.raises(UrdfError, match="no URDF given"):
        parse_urdf()
    xacro = write(tmp_path / "robots" / "arm.urdf.xacro", "<!-- the xacro source -->")
    stl(tmp_path / "robots" / "meshes" / "a.stl")
    expanded = (
        '<robot name="x"><link name="a"><visual><geometry>'
        '<mesh filename="meshes/a.stl"/></geometry></visual></link></robot>'
    )
    robot = build_robot(xacro, text=expanded, environ={})
    assert robot["model"]["name"] == "x" and robot["model"]["missing"] == []
    assert robot["files"]["m0"] == str((tmp_path / "robots" / "meshes" / "a.stl").resolve())
    with pytest.raises(UrdfError, match=r"arm.urdf.xacro is not well-formed"):
        build_robot(xacro, text="<robot", environ={})


# -- what may be served: typed regular files, capped; nothing out of a package -------------


def test_only_images_are_served_as_textures_and_a_secret_key_never_is(tmp_path: Path):
    """A texture reference may point anywhere on disk (an absolute path, a package
    elsewhere): only an image file gets a role; a key file or any other type is listed
    missing (kind texture) and never served."""
    secret = tmp_path / "home" / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
    urdf = write(
        tmp_path / "proj" / "t.urdf",
        f"""<robot name="t">
          <material name="m"><texture filename="{secret.as_uri()}"/></material>
          <link name="a">
            <visual><geometry><box size="1 1 1"/></geometry><material name="m"/></visual>
            <visual><geometry><box size="1 1 1"/></geometry>
              <material name="n"><texture filename="tex/none.png"/></material></visual>
          </link></robot>""",
    )
    robot = build_robot(urdf, environ={})
    assert robot["files"] == {}
    mats = [v["material"] for v in robot["model"]["links"][0]["visuals"]]
    assert all("texture_file" not in m and m["texture_url"] is None for m in mats)
    assert robot["model"]["missing"] == [
        {
            "link": "a",
            "kind": "texture",
            "uri": secret.as_uri(),
            "cause": "texture format (none) is not an image",
        },
        {
            "link": "a",
            "kind": "texture",
            "uri": "tex/none.png",
            "cause": "not found next to the URDF",
        },
    ]
    assert missing_notes(robot["model"]["missing"]) == [
        "1 texture missing: texture format (none) is not an image",
        "1 texture missing: not found next to the URDF",
    ]


def test_a_mesh_reference_to_a_non_mesh_file_is_never_served(tmp_path: Path):
    secret = tmp_path / "home" / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("key", encoding="utf-8")
    renamed = tmp_path / "home" / ".ssh" / "id_rsa.stl"
    renamed.mkdir()  # a folder named like a mesh is not a regular file
    urdf = write(
        tmp_path / "proj" / "s.urdf",
        f"""<robot name="s"><link name="a">
          <visual><geometry><mesh filename="{secret}"/></geometry></visual>
          <visual><geometry><mesh filename="{renamed.as_uri()}"/></geometry></visual>
        </link></robot>""",
    )
    robot = build_robot(urdf, environ={})
    assert robot["files"] == {}
    causes = [m["cause"] for m in robot["model"]["missing"]]
    assert causes == [
        "mesh format (none) is not supported (STL, OBJ, glTF)",
        "not found at its absolute path",
    ]


def test_a_mesh_over_the_cap_is_not_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import specto_readers.urdf as urdf_mod

    monkeypatch.setattr(urdf_mod, "MAX_SERVED_BYTES", 8)
    urdf = write(
        tmp_path / "big.urdf",
        """<robot name="b"><link name="a"><visual><geometry>
             <mesh filename="big.stl"/></geometry></visual></link></robot>""",
    )
    stl(tmp_path / "big.stl")  # 20 bytes
    robot = build_robot(urdf, environ={})
    assert robot["files"] == {}
    assert robot["model"]["missing"][0]["cause"] == "mesh file over 0 MB"


def test_package_paths_never_reach_out_of_the_package(tmp_path: Path):
    pkg = package(tmp_path / "ws" / "arm_description", "arm_description")
    urdf = write(pkg / "urdf" / "arm.urdf", ARM)
    stl(tmp_path / "outside.stl")
    res = PackageResolver(urdf, environ={})
    for uri in (
        "package://arm_description/../../outside.stl",
        "package://arm_description/meshes/../../../outside.stl",
    ):
        miss = res.resolve(uri)
        assert miss.path is None, uri
        assert miss.cause == "not in package `arm_description` (found by package.xml)"
    stl(pkg / "meshes" / "in.stl")
    assert res.resolve("package://arm_description/meshes/../meshes/in.stl").path is not None


def test_an_oversized_file_is_not_a_urdf_and_not_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import specto_readers.urdf as urdf_mod

    monkeypatch.setattr(urdf_mod, "MAX_BYTES", 64)
    big = write(tmp_path / "big.urdf", '<robot name="r">' + " " * 100 + "</robot>")
    with pytest.raises(UrdfError, match="over 0 MB"):
        parse_urdf(big)
    with pytest.raises(UrdfError, match="over 0 MB"):
        parse_urdf(text="<robot>" + " " * 100 + "</robot>")
    pkg = tmp_path / "pkg"
    write(pkg / "package.xml", "<package><name>p</name>" + " " * 100 + "</package>")
    assert package_name_of(pkg) is None


def test_a_symlink_loop_in_the_project_is_walked_once(tmp_path: Path):
    import os

    root = tmp_path / "proj"
    (root / "a").mkdir(parents=True)
    try:
        os.symlink(root, root / "a" / "loop", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges here")
    urdf = write(root / "robots" / "arm.urdf", ARM)
    res = PackageResolver(urdf, search_roots=[root], environ={})
    assert res.find_package("nowhere") is None  # terminates, finds nothing
    pkg = package(root / "a" / "deep", "arm_description")
    res = PackageResolver(urdf, search_roots=[root], environ={})
    assert res.find_package("arm_description") == (pkg.resolve(), "package.xml")
