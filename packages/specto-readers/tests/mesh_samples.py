"""Small synthetic mesh files for the STL / OBJ / DAE tests (SPEC-42), written by hand so
the expected numbers are known: a box in each format, Z-up as robot meshes usually are.
Shared by the readers and intake tests, the 3D page's pixel check and the GUI flow seed
(``tools/gui_flows/v2-mesh-formats-seed.py``), which load this file by its path.

- ``write_stl``: a closed box standing on the XY plane (z up), binary or ASCII;
- ``write_obj``: the same box with an MTL file and a texture in a ``textures/`` folder
  beside the OBJ (``map_Kd textures/<name>``), so both resolve relative to the OBJ;
- ``write_dae``: a COLLADA file in centimetres (``<unit meter="0.01">``) with
  ``<up_axis>Z_UP</up_axis>``: a 40 cm box at the origin and a second one 80 cm above it
  through two nested node translations, one material a colour, the other a texture.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

#: the STL and OBJ box, in file units (metres by the documented default): 0.4 wide, 0.8 tall
BOX_LO = (-0.2, -0.2, 0.0)
BOX_HI = (0.2, 0.2, 0.8)
#: the DAE: a 40 cm box ([-20, 20] x [-20, 20] x [0, 40] cm) at the root node and the same
#: box under node "arm" (translate 0 0 40) / node "tip" (translate 0 0 40): z reaches 120 cm
DAE_BOX_CM = ((-20.0, -20.0, 0.0), (20.0, 20.0, 40.0))
#: what the converted GLB must span, in metres and Y-up: (x, y, z) -> (x, z, -y) / 100
DAE_BOUNDS_M_YUP = [[-0.2, 0.0, -0.2], [0.2, 1.2, 0.2]]
DAE_BLUE = (0.1, 0.2, 0.9, 1.0)
OBJ_KD = (0.9, 0.9, 0.9)  # near white: the green on screen is the texture
RED = (230, 30, 30)
GREEN = (40, 200, 60)


def box_faces(lo, hi) -> list[tuple[tuple[float, float, float], list[tuple[float, ...]]]]:
    """The six faces of a box as (outward normal, four corners counter-clockwise seen from
    outside)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    return [
        ((1.0, 0.0, 0.0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1.0, 0.0, 0.0), [(x0, y0, z1), (x0, y1, z1), (x0, y1, z0), (x0, y0, z0)]),
        ((0.0, 1.0, 0.0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
        ((0.0, -1.0, 0.0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
        ((0.0, 0.0, 1.0), [(x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)]),
        ((0.0, 0.0, -1.0), [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]),
    ]


def box_triangles(lo, hi) -> list[tuple[tuple[float, float, float], list[tuple[float, ...]]]]:
    """Twelve (normal, three corners) triangles, counter-clockwise seen from outside."""
    out = []
    for n, (a, b, c, d) in box_faces(lo, hi):
        out += [(n, [a, b, c]), (n, [a, c, d])]
    return out


def write_png(path: Path, rgb: tuple[int, int, int], size: int = 8) -> Path:
    """A solid-colour PNG, written without Pillow (any reader of these tests has zlib)."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png + chunk(b"IEND", b""))
    return path


def write_stl(path: Path, *, binary: bool = True, lo=BOX_LO, hi=BOX_HI) -> Path:
    tris = box_triangles(lo, hi)
    if binary:
        body = b"".join(
            struct.pack("<3f", *n) + b"".join(struct.pack("<3f", *v) for v in vs) + b"\0\0"
            for n, vs in tris
        )
        path.write_bytes(b"specto test box".ljust(80, b" ") + struct.pack("<I", len(tris)) + body)
        return path
    lines = ["solid box"]
    for n, vs in tris:
        lines.append(f"  facet normal {n[0]:g} {n[1]:g} {n[2]:g}")
        lines.append("    outer loop")
        lines += [f"      vertex {v[0]:g} {v[1]:g} {v[2]:g}" for v in vs]
        lines += ["    endloop", "  endfacet"]
    lines.append("endsolid box")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def write_obj(
    path: Path, *, lo=BOX_LO, hi=BOX_HI, texture: bool = True, texture_ref: str | None = None
) -> Path:
    """``<stem>.obj`` + ``<stem>.mtl`` (+ ``textures/<stem>.png``, green) beside it;
    ``texture_ref`` names the texture another way (``../shared/skin.png``), written there."""
    stem = path.stem
    lines = [f"mtllib {stem}.mtl", f"o {stem}"]
    faces = box_faces(lo, hi)
    for _n, vs in faces:
        lines += [f"v {v[0]:g} {v[1]:g} {v[2]:g}" for v in vs]
    lines += ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1"]
    lines += [f"vn {n[0]:g} {n[1]:g} {n[2]:g}" for n, _vs in faces]
    lines.append("usemtl skin")
    for f in range(len(faces)):
        a, b, c, d = (4 * f + i + 1 for i in range(4))
        n = f + 1
        lines.append(f"f {a}/1/{n} {b}/2/{n} {c}/3/{n} {d}/4/{n}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    mtl = ["newmtl skin", "Kd {:g} {:g} {:g}".format(*OBJ_KD)]
    if texture:
        tex = texture_ref or f"textures/{stem}.png"
        write_png((path.parent / tex).resolve(), GREEN)
        mtl.append(f"map_Kd {tex}")
    (path.parent / f"{stem}.mtl").write_text("\n".join(mtl) + "\n", encoding="ascii")
    return path


def _dae_geometry(gid: str) -> str:
    faces = box_faces(*DAE_BOX_CM)
    pos = " ".join(f"{c:g}" for _n, vs in faces for v in vs for c in v)
    nrm = " ".join(f"{c:g}" for n, _vs in faces for c in n)
    p = []
    for f in range(6):
        a, b, c, d = (4 * f + i for i in range(4))
        uv = (0, 1, 2, 0, 2, 3)
        for k, v in enumerate((a, b, c, a, c, d)):
            p.append(f"{v} {f} {uv[k]}")
    return f"""
    <geometry id="{gid}" name="{gid}"><mesh>
      <source id="{gid}-pos"><float_array id="{gid}-pos-a" count="72">{pos}</float_array>
        <technique_common><accessor source="#{gid}-pos-a" count="24" stride="3">
          <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
        </accessor></technique_common></source>
      <source id="{gid}-nrm"><float_array id="{gid}-nrm-a" count="18">{nrm}</float_array>
        <technique_common><accessor source="#{gid}-nrm-a" count="6" stride="3">
          <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
        </accessor></technique_common></source>
      <source id="{gid}-uv"><float_array id="{gid}-uv-a" count="8">0 0 1 0 1 1 0 1</float_array>
        <technique_common><accessor source="#{gid}-uv-a" count="4" stride="2">
          <param name="S" type="float"/><param name="T" type="float"/>
        </accessor></technique_common></source>
      <vertices id="{gid}-v"><input semantic="POSITION" source="#{gid}-pos"/></vertices>
      <triangles material="mat" count="12">
        <input semantic="VERTEX" source="#{gid}-v" offset="0"/>
        <input semantic="NORMAL" source="#{gid}-nrm" offset="1"/>
        <input semantic="TEXCOORD" source="#{gid}-uv" offset="2" set="0"/>
        <p>{" ".join(p)}</p>
      </triangles>
    </mesh></geometry>"""


def write_dae(path: Path, *, unit_meter: float = 0.01, up_axis: str = "Z_UP") -> Path:
    """The two-box COLLADA scene (module docstring) and ``textures/<stem>.png`` (red)."""
    stem = path.stem
    write_png(path.parent / "textures" / f"{stem}.png", RED)
    blue = " ".join(f"{c:g}" for c in DAE_BLUE)
    instance = (
        '<instance_geometry url="#box"><bind_material><technique_common>'
        '<instance_material symbol="mat" target="#{m}">'
        '<bind_vertex_input semantic="UV0" input_semantic="TEXCOORD" input_set="0"/>'
        "</instance_material></technique_common></bind_material></instance_geometry>"
    )
    text = f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset>
    <contributor><authoring_tool>specto tests</authoring_tool></contributor>
    <unit name="unit" meter="{unit_meter:g}"/>
    <up_axis>{up_axis}</up_axis>
  </asset>
  <library_images>
    <image id="img"><init_from>textures/{stem}.png</init_from></image>
  </library_images>
  <library_effects>
    <effect id="fx-blue"><profile_COMMON><technique sid="t"><phong>
      <diffuse><color>{blue}</color></diffuse></phong></technique></profile_COMMON></effect>
    <effect id="fx-tex"><profile_COMMON>
      <newparam sid="surf"><surface type="2D"><init_from>img</init_from></surface></newparam>
      <newparam sid="samp"><sampler2D><source>surf</source></sampler2D></newparam>
      <technique sid="t"><lambert><diffuse><texture texture="samp" texcoord="UV0"/></diffuse>
      </lambert></technique>
    </profile_COMMON></effect>
  </library_effects>
  <library_materials>
    <material id="m-blue" name="blue"><instance_effect url="#fx-blue"/></material>
    <material id="m-tex" name="textured"><instance_effect url="#fx-tex"/></material>
  </library_materials>
  <library_geometries>{_dae_geometry("box")}
  </library_geometries>
  <library_visual_scenes><visual_scene id="scene">
    <node id="base" name="base">{instance.format(m="m-blue")}</node>
    <node id="arm" name="arm"><translate>0 0 40</translate>
      <node id="tip" name="tip"><translate>0 0 40</translate>{instance.format(m="m-tex")}</node>
    </node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#scene"/></scene>
</COLLADA>
"""
    path.write_text(text, encoding="utf-8")
    return path
