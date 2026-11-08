"""Locators and decoders in-process: refs, thumbnails, json, text."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from readers_helpers import GEO, NOTES, first_jpg, first_json, needs_testdata

from specto_core.errors import NotFound
from specto_core.kinds import DataRef, DecodeRequest, Item, TypedBuffer, default_plugins
from specto_readers import DecodeError, ReaderRegistry, default_readers, describe, plugin_manifest


def _item(tmp_path: Path, **parts: str) -> Item:
    return Item(key="k", parts={role: str(tmp_path / name) for role, name in parts.items()})


def test_locators_pick_by_extension_and_priority(tmp_path: Path):
    reg = default_readers()
    for name in ("a.jpg", "a.json", "a.geojson", "a.md", "a.csv"):
        (tmp_path / name).write_text("{}")
    assert reg.locator_for("x.JPG").name == "image"
    assert [lo.name for lo in reg.locators_for("x.geojson")] == ["geojson"]
    assert reg.locator_for("x.unknown") is None
    it = _item(tmp_path, **{".jpg": "a.jpg", ".json": "a.json"})
    ref = reg.locator("json").locate(it)
    assert ref.kind == "json" and ref.uri.endswith("a.json") and ref.meta["role"] == ".json"
    assert ref.meta["size"] == 2 and ref.meta["key"] == "k"
    ref = reg.locate(it, accepts=["image2d"])
    assert ref is not None and ref.kind == "image2d"
    assert reg.locate(it, accepts=["pointcloud"]) is None
    assert reg.locate(Item(key="z", parts={".bin": "z.bin"})) is None
    with pytest.raises(NotFound):
        reg.locator("image").locate(Item(key="z", parts={".json": "z.json"}))
    geo = reg.locate(_item(tmp_path, **{".geojson": "a.geojson"}))
    assert geo is not None and geo.kind == "geojson"
    assert reg.decoder_for("geojson", "full").name == "json"
    assert reg.decoder_for("markdown").name == "text"
    assert reg.decoder_for("image2d", "thumbnail").name == "image"
    assert reg.decoder_for("image2d", "range") is None


def test_manifest_matches_core_vocabulary():
    core = default_plugins()
    mine = plugin_manifest()
    assert {m.name for m in mine.locators} == {m.name for m in core.locators()}
    d = describe()
    assert set(d["registered"]["decoders"]) == {"image", "joint_track", "json", "text", "usd_info"}
    assert set(d["registered"]["locators"]) == {
        "container",
        "geojson",
        "html",
        "image",
        "joint_track",
        "json",
        "markdown",
        "pdf",
        "scene3d",
        "text",
        "urdf",
        "usd",
        "video",
    }


def _png(path: Path, w: int, h: int, color=(200, 30, 30)) -> Path:
    Image.new("RGB", (w, h), color).save(path)
    return path


def test_image_thumbnail_full_and_meta(tmp_path: Path):
    p = _png(tmp_path / "big.png", 800, 400)
    reg = default_readers()
    ref = DataRef(kind="image2d", uri=str(p))
    th = reg.decode(ref, DecodeRequest(purpose="thumbnail", max_size=200))
    arr = th.to_numpy()
    assert arr.shape == (100, 200, 4) and arr.dtype == np.uint8 and th.spec.layout == "HWC"
    assert th.spec.meta["source_width"] == 800 and th.spec.meta["width"] == 200
    assert tuple(arr[50, 100]) == (200, 30, 30, 255)
    full = reg.decode(ref, DecodeRequest(purpose="full"))
    assert full.to_numpy().shape == (400, 800, 4)
    capped = reg.decode(ref, DecodeRequest(purpose="full", max_size=400))
    assert capped.to_numpy().shape == (200, 400, 4)
    meta = reg.decode(ref, DecodeRequest(purpose="meta"))
    assert meta.spec.layout == "bytes"
    assert json.loads(meta.to_numpy().tobytes()) == {
        "width": 800,
        "height": 400,
        "mode": "RGB",
        "format": "PNG",
        "source": str(p),
    }
    # default thumbnail size and a small image that is not upscaled
    small = _png(tmp_path / "small.png", 64, 32)
    th = reg.decode(DataRef(kind="image2d", uri=str(small)), DecodeRequest(purpose="thumbnail"))
    assert th.to_numpy().shape == (32, 64, 4)


def test_image_decode_errors(tmp_path: Path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    reg = default_readers()
    with pytest.raises(DecodeError) as e:
        reg.decode(DataRef(kind="image2d", uri=str(bad)), DecodeRequest(purpose="thumbnail"))
    assert e.value.code == "decode"
    with pytest.raises(DecodeError):
        reg.decode(DataRef(kind="image2d", uri=str(tmp_path / "missing.jpg")))
    with pytest.raises(NotFound):
        reg.decode(DataRef(kind="pointcloud", uri="x.las"))


def test_json_and_text_decoders(tmp_path: Path):
    reg = default_readers()
    j = tmp_path / "a.json"
    j.write_text('{"a": 1, "b": [1, 2]}', encoding="utf-8")
    buf = reg.decode(DataRef(kind="json", uri=str(j)))
    assert buf.spec.layout == "bytes" and buf.to_numpy().tobytes() == j.read_bytes()
    assert buf.spec.meta["top"] == "dict" and buf.spec.meta["keys"] == ["a", "b"]
    pretty = reg.decode(DataRef(kind="json", uri=str(j)), DecodeRequest(options={"pretty": True}))
    assert pretty.to_numpy().tobytes().startswith(b'{\n  "a": 1')
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(DecodeError) as e:
        reg.decode(DataRef(kind="json", uri=str(bad)))
    assert "not valid JSON" in e.value.message
    t = tmp_path / "n.md"
    t.write_bytes(b"# title\nline2")
    buf = reg.decode(DataRef(kind="markdown", uri=str(t)))
    assert buf.to_numpy().tobytes() == b"# title\nline2" and buf.spec.meta["lines"] == 2
    latin = tmp_path / "l.txt"
    latin.write_bytes(b"caf\xe9\n")
    buf = reg.decode(DataRef(kind="text", uri=str(latin)))
    assert buf.spec.meta["encoding_errors"] is True
    assert buf.to_numpy().tobytes().decode("utf-8") == "caf�\n"


def test_typed_buffer_attach_round_trip():
    arr = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    tb = TypedBuffer.from_numpy(arr, layout="HWC")
    back = TypedBuffer.attach(tb.spec, arr.tobytes())
    assert np.array_equal(back.to_numpy(), arr) and tb.spec.nbytes == 24


@needs_testdata
def test_real_jpg_json_geojson_markdown():
    reg = default_readers()
    req = DecodeRequest(purpose="thumbnail", max_size=192)
    th = reg.decode(DataRef(kind="image2d", uri=str(first_jpg())), req)
    arr = th.to_numpy()
    assert arr.shape == (144, 192, 4) and arr[..., 3].min() == 255
    assert th.spec.meta["source_width"] == 640 and th.spec.meta["source_height"] == 480
    side = reg.decode(DataRef(kind="json", uri=str(first_json())))
    assert side.spec.meta["top"] == "dict" and "shape" in side.spec.meta["keys"]
    geo = reg.decode(DataRef(kind="geojson", uri=str(GEO)))
    assert geo.spec.meta["top"] == "dict"
    md = reg.decode(DataRef(kind="markdown", uri=str(NOTES)))
    assert md.spec.meta["lines"] > 1


def test_custom_registry_is_independent():
    reg = ReaderRegistry()
    assert reg.locator_for("a.jpg") is None and reg.names() == {"locators": [], "decoders": []}
