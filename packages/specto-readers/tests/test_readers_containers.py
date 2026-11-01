"""Readers over container members (M2): locate stats the archive, decoders read the
member by stream (image thumbnail, json, text), in-process and through the pool entry
points."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from specto_core.containers import join_virtual
from specto_core.jobs import InlineJobManager
from specto_core.kinds import DataRef, DecodeRequest, Item
from specto_readers import DecodeError, default_readers, submit_decode_batch


def make_zip(path: Path) -> Path:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 30, 30)).save(buf, "JPEG")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("front/00001_a.jpg", buf.getvalue())
        zf.writestr("front/00001_a.json", json.dumps({"frame": 1, "ok": True}))
        zf.writestr("notes.md", "# hi\n")
    return path


def test_locate_a_member_item_stats_the_archive(tmp_path: Path):
    z = make_zip(tmp_path / "a.zip")
    jpg = join_virtual(str(z), "front/00001_a.jpg")
    item = Item(
        key="00001", parts={".jpg": jpg, ".json": join_virtual(str(z), "front/00001_a.json")}
    )
    reg = default_readers()
    ref = reg.locate(item, accepts=["image2d"])
    assert ref is not None and ref.kind == "image2d" and ref.uri == jpg
    assert ref.meta["size"] == z.stat().st_size and "missing" not in ref.meta
    # the container manifest itself handles nothing: intake expands it, members have kinds
    assert reg.locator_for("a.zip") is None


def test_decode_members_by_stream(tmp_path: Path):
    z = make_zip(tmp_path / "a.zip")
    reg = default_readers()
    thumb = reg.decode(
        DataRef(kind="image2d", uri=join_virtual(str(z), "front/00001_a.jpg")),
        DecodeRequest(purpose="thumbnail", max_size=64),
    )
    assert thumb.spec.layout == "HWC" and max(thumb.spec.shape[:2]) == 64
    assert thumb.spec.meta["source_width"] == 640
    meta = reg.decode(
        DataRef(kind="image2d", uri=join_virtual(str(z), "front/00001_a.jpg")),
        DecodeRequest(purpose="meta"),
    )
    assert meta.spec.meta["width"] == 640
    js = reg.decode(DataRef(kind="json", uri=join_virtual(str(z), "front/00001_a.json")))
    assert json.loads(bytes(js.to_numpy())) == {"frame": 1, "ok": True}
    md = reg.decode(DataRef(kind="markdown", uri=join_virtual(str(z), "notes.md")))
    assert bytes(md.to_numpy()) == b"# hi\n"
    with pytest.raises(DecodeError):
        reg.decode(DataRef(kind="json", uri=join_virtual(str(z), "front/missing.json")))


def test_decode_batch_entry_point_over_members(tmp_path: Path):
    z = make_zip(tmp_path / "a.zip")
    jobs = InlineJobManager()
    refs = [
        DataRef(kind="image2d", uri=join_virtual(str(z), "front/00001_a.jpg")),
        DataRef(kind="image2d", uri=join_virtual(str(z), "front/nope.jpg")),
    ]
    job = submit_decode_batch(jobs, refs, DecodeRequest(purpose="thumbnail", max_size=32))
    assert job.state == "done"
    ok, bad = job.result
    assert ok.spec.shape[2] == 4 and isinstance(bad, dict) and "error" in bad
