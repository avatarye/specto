"""Small 3D test files for the 3D view tests (headless): a five-point PLY cloud, an
ASCII PLY quad mesh, a one-splat INRIA PLY, a raw .splat and a GLB stub."""

from __future__ import annotations

import struct
from pathlib import Path

POINTS = [
    (-1.0, 0.0, 1.0),
    (0.0, 1.0, -2.0),
    (1.5, 2.0, 0.0),
    (3.0, 4.0, 2.5),
    (2.0, 0.5, 1.0),
]
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (10, 20, 30), (200, 200, 200)]
ALIASES = {"@3d": [".dae", ".glb", ".gltf", ".obj", ".ply", ".sog", ".splat", ".spz", ".stl"]}
#: the synthetic STL / OBJ / DAE writers (SPEC-42), shared with the readers tests
MESH_SAMPLES = Path(__file__).parents[2] / "specto-readers" / "tests" / "mesh_samples.py"


def write_cloud_ply(path: Path) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(POINTS)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    body = b"".join(struct.pack("<fffBBB", *p, *c) for p, c in zip(POINTS, COLORS, strict=True))
    path.write_bytes(header.encode("ascii") + body)


def write_quad_ply(path: Path) -> None:
    path.write_text(
        "ply\nformat ascii 1.0\n"
        "element vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
        "element face 2\nproperty list uchar int vertex_indices\nend_header\n"
        "0 0 0\n1 0 0\n1 1 0\n0 1 0\n"
        "3 0 1 2\n4 0 1 2 3\n",
        encoding="ascii",
    )


def write_splat_ply(path: Path) -> None:
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity"]
    names += ["scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
    header += "".join(f"property float {n}\n" for n in names) + "end_header\n"
    path.write_bytes(header.encode("ascii") + struct.pack("<17f", *([0.5] * 17)))


def mesh_samples():
    """``specto-readers/tests/mesh_samples.py``, loaded by its path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("mesh_samples_for_app", MESH_SAMPLES)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed_scans(root: Path) -> Path:
    scans = root / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    write_cloud_ply(scans / "cloud.ply")
    write_quad_ply(scans / "quad.ply")
    write_splat_ply(scans / "gauss.ply")
    (scans / "raw.splat").write_bytes(bytes(64))
    (scans / "model.glb").write_bytes(b"glTF" + bytes(8))
    return scans


def scans_link(sources: list[str] | None = None) -> dict:
    """The intaker link entry as the link runtime hands it to the resolver."""
    return {
        "kind": "intaker",
        "scope": "pinned",
        "label": "scans",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "target_type": "3d",
        "params": {"sources": sources or ["scans"], "screener": ["@3d"]},
        "summary": {"sentence": "The 3D view shows every scan under scans/."},
    }


#: (name, centre, radius, base colour, metallic, roughness): a material swatch row that
#: shows what an HDRI does to PBR (#175): chrome, gold, glossy and rough dielectrics
PBR_SPHERES = [
    ("chrome", (-4.4, 1.0, 0.0), 1.0, (0.95, 0.95, 0.95), 1.0, 0.05),
    ("gold", (-2.2, 1.0, 0.0), 1.0, (1.0, 0.77, 0.34), 1.0, 0.3),
    ("white", (0.0, 1.0, 0.0), 1.0, (0.9, 0.9, 0.9), 0.0, 0.35),
    ("red", (2.2, 1.0, 0.0), 1.0, (0.8, 0.1, 0.08), 0.0, 0.85),
    ("blue", (4.4, 1.0, 0.0), 1.0, (0.1, 0.25, 0.8), 0.0, 0.1),
]


def _sphere(centre, radius, rings: int = 24, segments: int = 48):
    import math

    pos, nrm, idx = [], [], []
    for r in range(rings + 1):
        th = math.pi * r / rings
        for s in range(segments + 1):
            ph = 2 * math.pi * s / segments
            n = (math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph))
            nrm.append(n)
            pos.append(tuple(c + radius * v for c, v in zip(centre, n, strict=True)))
    for r in range(rings):
        for s in range(segments):
            a, b = r * (segments + 1) + s, (r + 1) * (segments + 1) + s
            idx += [a, a + 1, b, b, a + 1, b + 1]
    return pos, nrm, idx


def write_pbr_spheres_glb(path: Path, spheres=None) -> Path:
    """A GLB of UV spheres, one glTF metallic-roughness material each (``PBR_SPHERES``
    by default): what lights by an environment and not by unlit points."""
    import json as _json

    blob = b""
    views, accessors, meshes, nodes, materials = [], [], [], [], []
    for name, centre, radius, colour, metallic, roughness in spheres or PBR_SPHERES:
        pos, nrm, idx = _sphere(centre, radius)
        first = len(accessors)
        for data, count, kind, comp, target in (
            (b"".join(struct.pack("<3f", *p) for p in pos), len(pos), "VEC3", 5126, 34962),
            (b"".join(struct.pack("<3f", *n) for n in nrm), len(nrm), "VEC3", 5126, 34962),
            (struct.pack(f"<{len(idx)}H", *idx), len(idx), "SCALAR", 5123, 34963),
        ):
            views.append(
                {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target}
            )
            acc = {
                "bufferView": len(views) - 1,
                "componentType": comp,
                "count": count,
                "type": kind,
            }
            if first == len(accessors):  # positions carry their bounds
                acc["min"] = [min(p[i] for p in pos) for i in range(3)]
                acc["max"] = [max(p[i] for p in pos) for i in range(3)]
            accessors.append(acc)
            blob += data + b"\0" * (-len(data) % 4)
        materials.append(
            {
                "name": name,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [*colour, 1.0],
                    "metallicFactor": metallic,
                    "roughnessFactor": roughness,
                },
            }
        )
        meshes.append(
            {
                "name": name,
                "primitives": [
                    {
                        "attributes": {"POSITION": first, "NORMAL": first + 1},
                        "indices": first + 2,
                        "material": len(materials) - 1,
                    }
                ],
            }
        )
        nodes.append({"name": name, "mesh": len(meshes) - 1})
    gltf = {
        "asset": {"version": "2.0", "generator": "specto scene3d_helpers"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    js = _json.dumps(gltf).encode("utf-8")
    js += b" " * (-len(js) % 4)
    body = (
        struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return path
