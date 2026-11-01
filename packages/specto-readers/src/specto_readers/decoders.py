"""Built-in decoders (HLD 3.7): run in a worker process, return a ``TypedBuffer``.

- ``image``: thumbnail / full frame as ``uint8`` RGBA ``HWC``; ``meta`` purpose returns
  the header only (size, mode, format) as JSON bytes without decoding pixels.
- ``json``: validates the document and returns its UTF-8 bytes (``layout="bytes"``);
  ``meta.top`` says what the root is, ``meta.pretty`` is set when the text was re-indented.
- ``text``: UTF-8 bytes (invalid sequences replaced; ``meta.encoding_errors`` counts them).
- ``usd_info``: a USD file's root-layer header as JSON bytes (``usd_info.py``).

Every decoder opens its source through ``specto_core.containers.open_source``, so a
virtual ``archive#/member`` ref is read as a stream out of the archive (M2 containers).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from specto_core.containers import open_source
from specto_core.errors import SpectoError
from specto_core.kinds import DataRef, DecodeRequest, TypedBuffer

DEFAULT_THUMB = 192


class DecodeError(SpectoError):
    code = "decode"


def _bytes_buffer(data: bytes, **meta: Any) -> TypedBuffer:
    arr = np.frombuffer(data, dtype=np.uint8)
    return TypedBuffer.from_numpy(arr, layout="bytes", **meta)


class ImageDecoder:
    name = "image"
    kinds = ("image2d",)
    purposes = ("thumbnail", "full", "meta")

    def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer:
        try:
            with open_source(ref.uri) as fh, Image.open(fh) as im:
                if request.purpose == "meta":
                    meta = {
                        "width": im.width,
                        "height": im.height,
                        "mode": im.mode,
                        "format": im.format,
                        "source": ref.uri,
                    }
                    return _bytes_buffer(json.dumps(meta).encode("utf-8"), **meta)
                src_w, src_h = im.size
                if request.purpose == "thumbnail":
                    size = int(request.max_size or DEFAULT_THUMB)
                    # draft() lets the JPEG decoder scale in the DCT domain: 2-8x faster
                    im.draft("RGB", (size, size))
                    im.thumbnail((size, size), Image.Resampling.BILINEAR)
                elif request.max_size and max(im.size) > request.max_size:
                    im.thumbnail((request.max_size, request.max_size), Image.Resampling.LANCZOS)
                if request.options.get("exif_transpose", True):
                    im = ImageOps.exif_transpose(im)
                rgba = im.convert("RGBA")
                arr = np.asarray(rgba, dtype=np.uint8)
        except (OSError, ValueError, SpectoError) as e:
            raise DecodeError(
                f"cannot decode {ref.uri}: {e}", fix="check the file; Pillow could not read it"
            ) from e
        return TypedBuffer.from_numpy(
            arr,
            layout="HWC",
            width=int(arr.shape[1]),
            height=int(arr.shape[0]),
            source_width=src_w,
            source_height=src_h,
            source=ref.uri,
            purpose=request.purpose,
        )


class JsonDecoder:
    name = "json"
    kinds = ("json", "geojson")
    purposes = ("full",)

    def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer:
        try:
            with open_source(ref.uri) as f:
                raw = f.read()
        except (OSError, SpectoError) as e:
            raise DecodeError(f"cannot read {ref.uri}: {e}") from e
        try:
            doc = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise DecodeError(f"{ref.uri} is not valid JSON: {e}", fix="fix the file") from e
        meta: dict[str, Any] = {"source": ref.uri, "top": type(doc).__name__}
        if isinstance(doc, dict):
            meta["keys"] = list(doc)[:64]
            meta["size"] = len(doc)
        elif isinstance(doc, list):
            meta["size"] = len(doc)
        if request.options.get("pretty"):
            raw = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
            meta["pretty"] = True
        return _bytes_buffer(raw, **meta)


class TextDecoder:
    name = "text"
    kinds = ("text", "markdown", "html")
    purposes = ("full",)

    def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer:
        try:
            with open_source(ref.uri) as f:
                raw = f.read()
        except (OSError, SpectoError) as e:
            raise DecodeError(f"cannot read {ref.uri}: {e}") from e
        meta: dict[str, Any] = {"source": ref.uri}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            meta["encoding_errors"] = True
            raw = text.encode("utf-8")
        meta["lines"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        return _bytes_buffer(raw, **meta)


def builtin_decoders() -> list[Any]:
    from .usd_info import UsdInfoDecoder  # it imports DecodeError from here

    return [ImageDecoder(), JsonDecoder(), TextDecoder(), UsdInfoDecoder()]
