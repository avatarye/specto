"""A minimal GLB (glTF 2.0 binary) writer: one mesh, one primitive, positions plus
optional normals, vertex colours and triangle indices. What the 3D view's resolve step
turns a PLY mesh into, so Babylon's glTF loader (not its PLY path) draws it.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

GLB_MAGIC = b"glTF"
#: the up axis glTF 2.0 makes normative (+Y): what a glTF item's scene entry declares and
#: what a DAE is converted to (``specto_readers.scene3d`` has the list of who declares one)
GLTF_UP = "y"
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
FLOAT = 5126
UNSIGNED_BYTE = 5121
UNSIGNED_INT = 5125
TRIANGLES = 4
POINTS = 0


def _pad(data: bytes, pad: bytes = b"\0") -> bytes:
    rem = len(data) % 4
    return data + pad * (4 - rem) if rem else data


def write_glb(
    path: Path | str,
    positions: np.ndarray,
    *,
    indices: np.ndarray | None = None,
    normals: np.ndarray | None = None,
    colors: np.ndarray | None = None,
    name: str = "mesh",
) -> Path:
    """``positions`` (n, 3) float32; ``indices`` (m, 3) triangles (points when None);
    ``normals`` (n, 3) float32; ``colors`` (n, 4) uint8 RGBA (normalized)."""
    pos = np.ascontiguousarray(positions, dtype="<f4").reshape(-1, 3)
    n = len(pos)
    blobs: list[bytes] = []
    views: list[dict] = []
    accessors: list[dict] = []

    def add(data: bytes, target: int, accessor: dict) -> int:
        offset = sum(len(b) for b in blobs)
        blobs.append(_pad(data))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        accessors.append({"bufferView": len(views) - 1, **accessor})
        return len(accessors) - 1

    attributes: dict[str, int] = {}
    attributes["POSITION"] = add(
        pos.tobytes(),
        ARRAY_BUFFER,
        {
            "componentType": FLOAT,
            "count": n,
            "type": "VEC3",
            "min": [float(x) for x in pos.min(axis=0)] if n else [0.0, 0.0, 0.0],
            "max": [float(x) for x in pos.max(axis=0)] if n else [0.0, 0.0, 0.0],
        },
    )
    if normals is not None:
        nrm = np.ascontiguousarray(normals, dtype="<f4").reshape(-1, 3)
        attributes["NORMAL"] = add(
            nrm.tobytes(), ARRAY_BUFFER, {"componentType": FLOAT, "count": n, "type": "VEC3"}
        )
    if colors is not None:
        col = np.ascontiguousarray(colors, dtype=np.uint8).reshape(-1, 4)
        attributes["COLOR_0"] = add(
            col.tobytes(),
            ARRAY_BUFFER,
            {"componentType": UNSIGNED_BYTE, "count": n, "type": "VEC4", "normalized": True},
        )
    primitive: dict = {
        "attributes": attributes,
        "mode": TRIANGLES if indices is not None else POINTS,
    }
    if indices is not None:
        idx = np.ascontiguousarray(indices, dtype="<u4").reshape(-1)
        primitive["indices"] = add(
            idx.tobytes(),
            ELEMENT_ARRAY_BUFFER,
            {"componentType": UNSIGNED_INT, "count": int(idx.size), "type": "SCALAR"},
        )
    bin_chunk = b"".join(blobs)
    gltf = {
        "asset": {"version": "2.0", "generator": "specto-readers"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [primitive]}],
        "materials": [
            {
                "name": "vertex",
                "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.9},
                "doubleSided": True,
            }
        ],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    primitive["material"] = 0
    json_chunk = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = Path(path)
    with out.open("wb") as fh:
        fh.write(struct.pack("<4sII", GLB_MAGIC, 2, total))
        fh.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        fh.write(json_chunk)
        fh.write(struct.pack("<I4s", len(bin_chunk), b"BIN\0"))
        fh.write(bin_chunk)
    return out


__all__ = ["write_glb"]
