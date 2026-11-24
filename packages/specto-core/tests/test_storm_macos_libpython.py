"""On macOS, Storm is absent when the OpenUSD build's ``pxr`` modules link a libpython the
running interpreter does not load. uv's CPython builds libpython into the executable, so
such a module starts a second, uninitialised interpreter and the source process crashes
(exit -11) on ``import pxr``. The reason names the library and the rebuild."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from specto_core import machine

LC_LOAD_DYLIB = 0xC
LC_SEGMENT_64 = 0x19


def macho(path: Path, dylibs: list[str], fat: bool = False) -> Path:
    """A minimal 64-bit Mach-O that links ``dylibs`` (a segment command first, as real
    files have, which the parser must step over)."""
    cmds = struct.pack("<II", LC_SEGMENT_64, 72) + bytes(64)
    for name in dylibs:
        raw = name.encode() + b"\0"
        size = (24 + len(raw) + 7) // 8 * 8
        cmds += struct.pack("<IIIIII", LC_LOAD_DYLIB, size, 24, 2, 0, 0)
        cmds += raw.ljust(size - 24, b"\0")
    header = struct.pack(
        "<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 1 + len(dylibs), len(cmds), 0, 0
    )
    body = header + cmds
    if fat:  # a universal file: one arm64 slice at 4096
        head = struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(
            ">IIIII", 0x0100000C, 0, 4096, len(body), 12
        )
        body = head.ljust(4096, b"\0") + body
    path.write_bytes(body)
    return path


LIBPYTHON = "/Users/x/.local/share/uv/python/cpython-3.12/lib/libpython3.12.dylib"


@pytest.mark.parametrize("fat", [False, True])
def test_the_dylibs_a_macho_links_are_read_from_its_load_commands(tmp_path, fat):
    f = macho(tmp_path / "m.so", ["@rpath/libusd_tf.dylib", LIBPYTHON], fat=fat)
    assert machine.macho_dylibs(str(f)) == ["@rpath/libusd_tf.dylib", LIBPYTHON]


def test_anything_else_has_no_dylibs(tmp_path):
    (tmp_path / "x").write_bytes(b"not a mach-o file at all")
    assert machine.macho_dylibs(str(tmp_path / "x")) == []
    assert machine.macho_dylibs(str(tmp_path / "missing")) == []


def test_a_libpython_is_foreign_only_to_an_interpreter_that_links_none(tmp_path):
    module = macho(tmp_path / "_tf.so", ["@rpath/libusd_tf.dylib", LIBPYTHON])
    static = macho(tmp_path / "python-static", ["/usr/lib/libSystem.B.dylib"])
    shared = macho(tmp_path / "python-shared", [LIBPYTHON])
    framework = macho(tmp_path / "python-fw", ["/Library/Frameworks/Python.framework/Python"])
    assert machine.foreign_libpython(str(module), str(static)) == LIBPYTHON
    assert machine.foreign_libpython(str(module), str(shared)) is None
    assert machine.foreign_libpython(str(module), str(framework)) is None
    clean = macho(tmp_path / "_tf2.so", ["@rpath/libusd_tf.dylib"])  # dynamic lookup
    assert machine.foreign_libpython(str(clean), str(static)) is None


@pytest.fixture
def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An OpenUSD prefix with UsdImagingGL and a pxr.Tf module, as detection looks."""
    site = tmp_path / "site"
    (site / "pxr" / "UsdImagingGL").mkdir(parents=True)
    (site / "pxr" / "Tf").mkdir(parents=True)
    monkeypatch.setattr(machine, "openusd_site_packages", lambda prefix: str(site))
    static = macho(tmp_path / "python", ["/usr/lib/libSystem.B.dylib"])
    monkeypatch.setattr(sys, "executable", str(static))
    return site


def test_storm_is_absent_on_macos_when_pxr_links_a_foreign_libpython(build, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    macho(build / "pxr" / "Tf" / "_tf.so", ["@rpath/libusd_tf.dylib", LIBPYTHON])
    cap = machine.detect_storm(str(tmp_path))
    assert not cap.present
    assert LIBPYTHON in cap.reason and "PXR_PY_UNDEFINED_DYNAMIC_LOOKUP" in cap.reason


def test_a_dynamic_lookup_build_is_present_on_macos(build, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    macho(build / "pxr" / "Tf" / "_tf.so", ["@rpath/libusd_tf.dylib"])
    assert machine.detect_storm(str(tmp_path)).present


def test_other_platforms_do_not_look(build, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    macho(build / "pxr" / "Tf" / "_tf.so", [LIBPYTHON])
    assert machine.detect_storm(str(tmp_path)).present
