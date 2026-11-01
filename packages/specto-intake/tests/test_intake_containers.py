"""Containers in intake (M2): archives under a source expand into virtual entries, the
screener and keys see the inner path, ``explain`` understands virtual paths, and a scan
through the engine lists members as items."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from intake_helpers import ALIASES, EXT_KINDS

from specto_intake import IntakeSpec, explain, scan, walk_source


def zip_frames(path: Path, n: int, prefix: str = "front") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(n):
            zf.writestr(f"{prefix}/{i:05d}_1757.jpg", b"\xff\xd8\xff")
            zf.writestr(f"{prefix}/{i:05d}_1757.json", b"{}")
        zf.writestr("README.txt", b"notes")
    return path


def tar_frames(path: Path, n: int) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for i in range(n):
            for ext, data in ((".jpg", b"\xff\xd8\xff"), (".json", b"{}")):
                info = tarfile.TarInfo(f"rear/{i:05d}_1757{ext}")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(d))
    return d


def test_walk_expands_archives_under_a_folder(tmp_path: Path):
    zip_frames(tmp_path / "runs" / "drive.zip", 2)
    (tmp_path / "runs" / "loose.jpg").write_bytes(b"\xff\xd8\xff")
    spec = IntakeSpec.parse({"sources": ["runs"]})
    rels = sorted(f.rel for f in walk_source(0, spec.sources[0], tmp_path))
    assert rels == [
        "drive.zip#/README.txt",
        "drive.zip#/front/00000_1757.jpg",
        "drive.zip#/front/00000_1757.json",
        "drive.zip#/front/00001_1757.jpg",
        "drive.zip#/front/00001_1757.json",
        "loose.jpg",
    ]
    virt = next(f for f in walk_source(0, spec.sources[0], tmp_path) if f.rel.endswith(".txt"))
    assert virt.path.replace("\\", "/").endswith("runs/drive.zip#/README.txt")
    assert virt.size == len(b"notes")
    # the cheap snapshot (watcher) lists the archive as one file
    plain = sorted(
        f.rel for f in walk_source(0, spec.sources[0], tmp_path, expand_containers=False)
    )
    assert plain == ["drive.zip", "loose.jpg"]


def test_source_pattern_applies_to_the_virtual_path(tmp_path: Path):
    zip_frames(tmp_path / "drive.zip", 2)
    spec = IntakeSpec.parse({"sources": [{"root": ".", "pattern": "**/*.jpg"}]})
    rels = sorted(f.rel for f in walk_source(0, spec.sources[0], tmp_path))
    assert rels == ["drive.zip#/front/00000_1757.jpg", "drive.zip#/front/00001_1757.jpg"]


def test_archive_as_the_source_root(tmp_path: Path):
    z = zip_frames(tmp_path / "drive.zip", 3)
    spec = IntakeSpec.parse({"sources": [str(z)]})
    found = list(walk_source(0, spec.sources[0], tmp_path))
    assert len(found) == 7 and all(f.rel.startswith("drive.zip#/") for f in found)
    assert found[0].root == tmp_path.as_posix()


def test_scan_groups_members_and_screens_inner_paths(tmp_path: Path):
    zip_frames(tmp_path / "drive.zip", 3)
    tar_frames(tmp_path / "rear.tar.gz", 2)
    spec = {
        "sources": ["."],
        "screener": ["@images", "@json", "!README.txt"],
        "keys": r"(?P<cam>front|rear)/(?P<frame>\d{5})_",
        "group_by": ["cam", "frame"],
        "sort": "key",
    }
    r = scan(spec, base=tmp_path, aliases=ALIASES, ext_kinds=EXT_KINDS)
    assert r.files == 11 and r.item_count == 5
    assert [it.key for it in r.items] == [
        "front/00000",
        "front/00001",
        "front/00002",
        "rear/00000",
        "rear/00001",
    ]
    it = r.items[0]
    assert set(it.parts) == {".jpg", ".json"}
    assert it.parts[".jpg"].replace("\\", "/").endswith("drive.zip#/front/00000_1757.jpg")
    assert it.meta["rel"][".jpg"] == "drive.zip#/front/00000_1757.jpg"
    assert r.kinds == {"image2d": 5, "json": 5}
    assert not r.skipped


def test_explain_understands_virtual_paths(tmp_path: Path):
    z = zip_frames(tmp_path / "runs" / "drive.zip", 1)
    spec = {"sources": ["runs"], "screener": ["@images"], "keys": r"(?P<frame>\d{5})_"}
    hit = explain(spec, f"{z}#/front/00000_1757.jpg", base=tmp_path, aliases=ALIASES)
    assert hit["included"] and hit["rel"] == "drive.zip#/front/00000_1757.jpg"
    assert hit["item_key"] == "00000" and hit["role"] == ".jpg"
    miss = explain(spec, f"{z}#/front/00000_1757.json", base=tmp_path, aliases=ALIASES)
    assert not miss["included"] and "no rule matched" in miss["reason"]
    # a relative virtual path resolves against the project too
    rel = explain(spec, "runs/drive.zip#/README.txt", base=tmp_path, aliases=ALIASES)
    assert rel["source"] == 0 and rel["rel"] == "drive.zip#/README.txt" and not rel["included"]
    outside = explain(spec, f"{tmp_path}/x.zip#/a.jpg", base=tmp_path, aliases=ALIASES)
    assert outside["reason"] == "not under any source root"


def test_unreadable_archive_stays_a_plain_file(tmp_path: Path):
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    spec = IntakeSpec.parse({"sources": ["."]})
    assert [f.rel for f in walk_source(0, spec.sources[0], tmp_path)] == ["bad.zip"]
