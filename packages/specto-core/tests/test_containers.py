"""Containers: virtual paths, member listing without extraction, member streams, the
reader extension point (M2, HLD 3.6)."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from specto_core.containers import (
    Member,
    container_extensions,
    is_container,
    is_virtual,
    join_virtual,
    list_members,
    open_member,
    open_source,
    reader_for,
    real_path,
    register_container_reader,
    split_virtual,
    stat_virtual,
)
from specto_core.errors import NotFound
from specto_core.kinds import default_plugins


def make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("front/00001_a.jpg", b"\xff\xd8\xff")
        zf.writestr("front/00001_a.json", b'{"frame": 1}')
        zf.writestr("front/", b"")  # a directory entry: not a member
    return path


def make_tar(path: Path, mode: str) -> Path:
    with tarfile.open(path, mode) as tf:
        for name, data in (("rear/00002_b.jpg", b"\xff\xd8\xff"), ("rear/00002_b.json", b"{}")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 1_700_000_000
            tf.addfile(info, io.BytesIO(data))
    return path


def test_virtual_path_grammar():
    assert split_virtual("C:/x/a.zip#/front/a.jpg") == ("C:/x/a.zip", "front/a.jpg")
    assert split_virtual(r"C:\x\a.zip#\front\a.jpg") == ("C:/x/a.zip", "front/a.jpg")
    assert split_virtual("C:/x/a.jpg") is None
    assert split_virtual("#/nothing") is None
    assert join_virtual("a.zip", "/front/a.jpg") == "a.zip#/front/a.jpg"
    assert is_virtual("a.zip#/x") and not is_virtual("a.zip")
    assert real_path("a.zip#/x/y") == "a.zip" and real_path("plain.jpg") == "plain.jpg"


def test_container_detection_by_extension():
    assert is_container("drive.zip") and is_container("DRIVE.TAR.GZ") and is_container("x.tgz")
    assert not is_container("x.gz") and not is_container("x.jpg")
    assert reader_for("a.tar.bz2").name == "tar"
    assert ".zip" in container_extensions() and ".tar.gz" in container_extensions()


def test_zip_members_and_streams(tmp_path: Path):
    z = make_zip(tmp_path / "a.zip")
    members = list_members(str(z))
    assert [m.path for m in members] == ["front/00001_a.jpg", "front/00001_a.json"]
    assert members[1].size == len(b'{"frame": 1}') and members[0].mtime_ns > 0
    with open_member(join_virtual(str(z), "front/00001_a.json")) as fh:
        assert fh.seekable()
        assert fh.read() == b'{"frame": 1}'
        fh.seek(0)
        assert fh.read(1) == b"{"
    with open_source(join_virtual(str(z), "front/00001_a.jpg")) as fh:
        assert fh.read(2) == b"\xff\xd8"
    with pytest.raises(NotFound):
        open_member(join_virtual(str(z), "front/missing.jpg"))
    assert stat_virtual(join_virtual(str(z), "front/00001_a.jpg")).st_size == z.stat().st_size


@pytest.mark.parametrize("name,mode", [("a.tar", "w"), ("a.tar.gz", "w:gz"), ("a.tgz", "w:gz")])
def test_tar_members_and_streams(tmp_path: Path, name: str, mode: str):
    t = make_tar(tmp_path / name, mode)
    members = list_members(str(t))
    assert [m.path for m in members] == ["rear/00002_b.jpg", "rear/00002_b.json"]
    assert members[0].mtime_ns == 1_700_000_000 * 10**9
    with open_member(join_virtual(str(t), "rear/00002_b.jpg")) as fh:
        assert fh.read() == b"\xff\xd8\xff"
        fh.seek(0)
        assert fh.read(1) == b"\xff"


def test_plain_paths_still_open(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"hi")
    with open_source(str(p)) as fh:
        assert fh.read() == b"hi"
    with pytest.raises(NotFound):
        open_member(str(p))
    with pytest.raises(NotFound):
        list_members(str(p))


def test_reader_extension_point_for_m4():
    class FakeMcap:
        name = "mcap"
        extensions = (".mcap",)

        def members(self, archive: str):
            yield Member("camera/image/0", 1, 0)

        def open(self, archive: str, inner: str):
            return io.BytesIO(b"x")

    register_container_reader(FakeMcap())
    try:
        assert is_container("drive.mcap")
        assert [m.path for m in list_members("drive.mcap")] == ["camera/image/0"]
        with open_member("drive.mcap#/camera/image/0") as fh:
            assert fh.read() == b"x"
    finally:
        from specto_core import containers as c

        c._readers = [r for r in c._readers if r.name != "mcap"]


def test_container_manifest_has_no_kind_but_an_alias():
    plugins = default_plugins()
    assert "@archives" in plugins.aliases()
    assert ".zip" in plugins.aliases()["@archives"]
    assert plugins.kinds_for_path("a.zip") == []
    assert plugins.kinds_for_path("a.zip#/front/x.jpg") == ["image2d"]
