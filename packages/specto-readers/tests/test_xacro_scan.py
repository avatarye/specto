"""The literal package scan behind the one xacro Approve card (SPEC-43.3, design
amendment 16): every literal ``$(find name)`` in the root and the files it reaches by
literal includes, read as text and never evaluated."""

from __future__ import annotations

import os
from pathlib import Path

from specto_readers.xacro_expand import literal_packages, package_hash

XNS = 'xmlns:xacro="http://www.ros.org/wiki/xacro"'


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _robot(body: str) -> str:
    return f'<robot name="a" {XNS}>{body}</robot>'


def test_packages_in_the_root_and_its_literal_includes_are_all_listed(tmp_path: Path):
    arm = tmp_path / "pkgs" / "arm_description"
    grip = tmp_path / "pkgs" / "gripper_description"
    _write(
        arm / "urdf" / "arm.xacro",
        f'<robot {XNS}><xacro:include filename="$(find gripper_description)/g.xacro"/>'
        '<mesh filename="$(find arm_description)/meshes/a.stl"/></robot>',
    )
    _write(grip / "g.xacro", '<robot><mesh filename="$(find gripper_description)/g.stl"/></robot>')
    _write(tmp_path / "robot" / "parts" / "base.xacro", '<robot><link name="base"/></robot>')
    root = _write(
        tmp_path / "robot" / "top.urdf.xacro",
        _robot(
            '<xacro:include filename="$(find arm_description)/urdf/arm.xacro"/>'
            "<xacro:include filename='parts/base.xacro'/>"
        ),
    )
    folders = {"arm_description": str(arm), "gripper_description": str(grip)}
    scan = literal_packages(root, find_package=folders.get, roots=[tmp_path / "robot"])
    assert scan.packages == {
        "arm_description": os.path.abspath(arm),
        "gripper_description": os.path.abspath(grip),
    }
    assert scan.missing == [] and scan.dynamic is False
    assert {Path(f).name for f in scan.files} == {
        "top.urdf.xacro",
        "arm.xacro",
        "g.xacro",
        "base.xacro",
    }
    # what the card approves: one package hash each, bound to the root's approval
    assert scan.hashes("r" * 64) == {
        name: package_hash("r" * 64, name, folder) for name, folder in scan.packages.items()
    }


def test_unfound_packages_and_dynamic_finds_are_reported_not_guessed(tmp_path: Path):
    root = _write(
        tmp_path / "a.xacro",
        _robot(
            '<mesh filename="$(find nowhere_pkg)/x.stl"/>'
            '<xacro:include filename="$(find ${which})/y.xacro"/>'
            '<xacro:include filename="${prefix}/z.xacro"/>'
        ),
    )
    scan = literal_packages(root, find_package=lambda n: None)
    assert scan.packages == {}
    assert scan.missing == ["nowhere_pkg"]
    # the expansion still asks for those, one card at a time
    assert scan.dynamic is True


def test_the_scan_never_evaluates_and_survives_cycles(tmp_path: Path):
    _write(
        tmp_path / "b.xacro",
        f'<robot {XNS}><xacro:include filename="a.xacro"/>'
        '<link name="${python.vars(python.type)}"/></robot>',
    )
    root = _write(
        tmp_path / "a.xacro",
        _robot('<xacro:include filename="b.xacro"/><link name="$(find p)"/>'),
    )
    scan = literal_packages(root, find_package={"p": str(tmp_path / "p")}.get)
    assert list(scan.packages) == ["p"]
    assert len(scan.files) == 2


def test_the_scan_reads_only_what_the_expansion_may_read(tmp_path: Path):
    project = tmp_path / "project"
    _write(tmp_path / "elsewhere" / "x.xacro", '<robot><link name="$(find secret_pkg)"/></robot>')
    root = _write(
        project / "a.xacro",
        _robot(
            '<xacro:include filename="../elsewhere/x.xacro"/>'
            '<xacro:include filename="missing.xacro"/>'
            '<xacro:include filename="NUL"/>'
        ),
    )
    scan = literal_packages(root, find_package=lambda n: str(tmp_path / n), roots=[project])
    assert scan.packages == {}  # never read outside the project, nor a device
    assert [Path(f).name for f in scan.files] == ["a.xacro"]
