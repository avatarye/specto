"""Seed the project folder for the SPEC-43 urdf page flow: robots/arm.urdf, a small arm
whose meshes are package:// paths into its own package (robots/package.xml names
``arm_description``): an STL standing 0.3 m on its Z (forearm, and again at half scale on
wrist), a thinner STL collider, and one mesh that is not there (the gripper's), so the
page draws its stand-in and health turns amber. Every joint type with a slider is in it,
plus a mimic finger and fixed joints (the hand-written page fixture's robot,
packages/specto-web/tests/fixtures/urdf/arm.json, as a URDF).

    uv run python tools/gui_flows/v2-43-urdf-seed.py <folder>
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

PACKAGE_XML = """<?xml version="1.0"?>
<package format="3">
  <name>arm_description</name>
  <version>0.1.0</version>
  <description>A small test arm for the urdf view.</description>
  <maintainer email="nobody@example.com">nobody</maintainer>
  <license>MIT</license>
</package>
"""

URDF = """<?xml version="1.0"?>
<robot name="arm">
  <material name="grey"><color rgba="0.54 0.54 0.54 1"/></material>
  <material name="orange"><color rgba="0.9 0.5 0.1 1"/></material>
  <material name="dark"><color rgba="0.1 0.1 0.1 1"/></material>
  <link name="base_link">
    <visual><origin xyz="0 0 0.05"/><geometry><box size="0.3 0.3 0.1"/></geometry>
      <material name="grey"/></visual>
    <collision><origin xyz="0 0 0.05"/><geometry><box size="0.3 0.3 0.1"/></geometry></collision>
    <inertial><origin xyz="0 0 0.05"/><mass value="8"/>
      <inertia ixx="0.07" ixy="0" ixz="0" iyy="0.07" iyz="0" izz="0.12"/></inertial>
  </link>
  <link name="upper_arm">
    <visual><origin xyz="0 0 0.21"/><geometry><cylinder radius="0.06" length="0.42"/></geometry>
      <material name="grey"/></visual>
    <collision><origin xyz="0 0 0.21"/>
      <geometry><cylinder radius="0.06" length="0.42"/></geometry></collision>
    <inertial><origin xyz="0 0 0.21"/><mass value="3.42"/>
      <inertia ixx="0.05" ixy="0" ixz="0" iyy="0.05" iyz="0" izz="0.006"/></inertial>
  </link>
  <link name="forearm">
    <visual><geometry><mesh filename="package://arm_description/meshes/segment.stl"/></geometry>
      <material name="orange"/></visual>
    <collision><geometry>
      <mesh filename="package://arm_description/meshes/segment_collision.stl"/></geometry></collision>
    <inertial><origin xyz="0 0 0.15"/><mass value="2.1"/>
      <inertia ixx="0.02" ixy="0" ixz="0" iyy="0.02" iyz="0" izz="0.003"/></inertial>
  </link>
  <link name="wrist">
    <visual><geometry>
      <mesh filename="package://arm_description/meshes/segment.stl" scale="0.5 0.5 0.5"/>
    </geometry></visual>
    <collision><origin xyz="0 0 0.04"/><geometry><sphere radius="0.05"/></geometry></collision>
  </link>
  <link name="gripper">
    <visual><geometry><mesh filename="package://arm_description/meshes/gripper.stl"/></geometry>
    </visual>
    <collision><origin xyz="0 0 0.02"/><geometry><box size="0.08 0.04 0.04"/></geometry></collision>
    <inertial><origin xyz="0 0 0.02"/><mass value="0.4"/>
      <inertia ixx="0.0001" ixy="0" ixz="0" iyy="0.0001" iyz="0" izz="0.0001"/></inertial>
  </link>
  <link name="finger_a">
    <visual><origin xyz="0 0 0.02"/><geometry><box size="0.01 0.02 0.04"/></geometry>
      <material name="dark"/></visual>
  </link>
  <link name="finger_b">
    <visual><origin xyz="0 0 0.02"/><geometry><box size="0.01 0.02 0.04"/></geometry>
      <material name="dark"/></visual>
  </link>
  <link name="tool0"/>
  <joint name="shoulder" type="revolute">
    <parent link="base_link"/><child link="upper_arm"/><origin xyz="0 0 0.1"/><axis xyz="0 1 0"/>
    <limit lower="-2.4435" upper="2.4435" effort="150" velocity="3.14"/>
    <dynamics damping="0.5" friction="0.1"/>
  </joint>
  <joint name="elbow" type="revolute">
    <parent link="upper_arm"/><child link="forearm"/><origin xyz="0 0 0.42"/><axis xyz="0 1 0"/>
    <limit lower="-2.4435" upper="2.4435" effort="100" velocity="3.14"/>
  </joint>
  <joint name="wrist_1" type="continuous">
    <parent link="forearm"/><child link="wrist"/><origin xyz="0 0 0.3"/><axis xyz="0 0 1"/>
  </joint>
  <joint name="wrist_fix" type="fixed">
    <parent link="wrist"/><child link="gripper"/><origin xyz="0 0 0.08"/>
  </joint>
  <joint name="gripper_joint" type="prismatic">
    <parent link="gripper"/><child link="finger_a"/><origin xyz="0.01 0 0.04"/><axis xyz="1 0 0"/>
    <limit lower="0" upper="0.04" effort="20" velocity="0.1"/>
  </joint>
  <joint name="finger_mimic" type="prismatic">
    <parent link="gripper"/><child link="finger_b"/>
    <origin xyz="-0.01 0 0.04" rpy="0 0 3.14159265"/><axis xyz="1 0 0"/>
    <limit lower="0" upper="0.04" effort="20" velocity="0.1"/>
    <mimic joint="gripper_joint" multiplier="1" offset="0"/>
  </joint>
  <joint name="tool_fix" type="fixed">
    <parent link="gripper"/><child link="tool0"/><origin xyz="0 0 0.1"/>
  </joint>
</robot>
"""


def write_box_stl(
    path: Path, lo: tuple[float, float, float], hi: tuple[float, float, float]
) -> None:
    """A binary STL box, outward counter-clockwise faces, zero normals (as many exporters
    write them: the page computes its own)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
    tris = [t for a, b, c, d in quads for t in ((v[a], v[b], v[c]), (v[a], v[c], v[d]))]
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0) for t in tris)
    path.write_bytes(bytes(80) + struct.pack("<I", len(tris)) + body)


def main() -> int:
    root = Path(sys.argv[1])
    robots = root / "robots"
    (robots / "meshes").mkdir(parents=True, exist_ok=True)
    (robots / "package.xml").write_text(PACKAGE_XML, encoding="utf-8")
    (robots / "arm.urdf").write_text(URDF, encoding="utf-8")
    write_box_stl(robots / "meshes" / "segment.stl", (-0.03, -0.03, 0.0), (0.03, 0.03, 0.3))
    write_box_stl(
        robots / "meshes" / "segment_collision.stl", (-0.025, -0.025, 0.0), (0.025, 0.025, 0.3)
    )
    print(f"[OK] seeded {robots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
