"""PLY reading for the 3D view (research #31: Babylon's PLY path is binary little-endian
only, triangles only, drops normals and is 4x slower than a pre-decoded buffer, so
Specto decodes PLY itself at resolve time).

``read_header`` parses the header; ``classify`` says whether a file is a Gaussian splat
(INRIA layout, left to Babylon's SPLAT loader), a mesh (has faces) or a point cloud;
``read_ply`` returns the vertex table and the triangulated faces for ASCII, binary
little-endian and binary big-endian files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

PlyFormat = Literal["ascii", "binary_little_endian", "binary_big_endian"]
SceneKind = Literal["pointcloud", "mesh", "splat"]

_TYPES: dict[str, str] = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "i2",
    "int16": "i2",
    "ushort": "u2",
    "uint16": "u2",
    "int": "i4",
    "int32": "i4",
    "uint": "u4",
    "uint32": "u4",
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
}
MAX_HEADER = 64 * 1024
#: the properties every INRIA 3DGS export carries beside x y z
SPLAT_PROPS = ("f_dc_0", "opacity", "scale_0", "rot_0")
COLOR_SETS: tuple[tuple[str, str, str], ...] = (
    ("red", "green", "blue"),
    ("r", "g", "b"),
    ("diffuse_red", "diffuse_green", "diffuse_blue"),
)
ALPHA_NAMES = ("alpha", "a", "diffuse_alpha")
INTENSITY_NAMES = ("intensity", "scalar_intensity", "scalar_Intensity")


@dataclass
class Property:
    name: str
    type: str  # numpy type code ("f4")
    count_type: str | None = None  # list properties: the type of the length prefix


@dataclass
class Element:
    name: str
    count: int
    props: list[Property] = field(default_factory=list)

    @property
    def has_list(self) -> bool:
        return any(p.count_type for p in self.props)

    def dtype(self, order: str) -> np.dtype:
        return np.dtype([(p.name, order + p.type) for p in self.props])


@dataclass
class Header:
    format: PlyFormat
    elements: list[Element]
    size: int  # bytes up to and including the end_header line
    comments: list[str] = field(default_factory=list)

    def element(self, name: str) -> Element | None:
        return next((e for e in self.elements if e.name == name), None)

    @property
    def order(self) -> str:
        return ">" if self.format == "binary_big_endian" else "<"


class PlyError(ValueError):
    pass


def read_header(path: Path | str) -> Header:
    with Path(path).open("rb") as fh:
        head = fh.read(MAX_HEADER)
    if not head.startswith(b"ply"):
        raise PlyError(f"{path}: not a PLY file")
    end = head.find(b"end_header")
    if end < 0:
        raise PlyError(f"{path}: no end_header within {MAX_HEADER} bytes")
    nl = head.find(b"\n", end)
    if nl < 0:
        raise PlyError(f"{path}: end_header line is not terminated")
    size = nl + 1
    fmt: PlyFormat | None = None
    elements: list[Element] = []
    comments: list[str] = []
    for raw in head[:end].decode("ascii", errors="replace").splitlines()[1:]:
        words = raw.split()
        if not words:
            continue
        tag = words[0]
        if tag == "format":
            if words[1] not in ("ascii", "binary_little_endian", "binary_big_endian"):
                raise PlyError(f"{path}: unknown format {words[1]!r}")
            fmt = words[1]  # type: ignore[assignment]
        elif tag in ("comment", "obj_info"):
            comments.append(raw[len(tag) :].strip())
        elif tag == "element":
            elements.append(Element(words[1], int(words[2])))
        elif tag == "property":
            if not elements:
                raise PlyError(f"{path}: property before any element")
            if words[1] == "list":
                count_t, item_t, name = words[2], words[3], words[4]
                elements[-1].props.append(Property(name, _type(item_t, path), _type(count_t, path)))
            else:
                elements[-1].props.append(Property(words[2], _type(words[1], path)))
    if fmt is None:
        raise PlyError(f"{path}: no format line")
    return Header(fmt, elements, size, comments)


def _type(word: str, path: Path | str) -> str:
    try:
        return _TYPES[word]
    except KeyError:
        raise PlyError(f"{path}: unknown property type {word!r}") from None


def classify(header: Header) -> SceneKind:
    vertex = header.element("vertex")
    names = {p.name for p in vertex.props} if vertex else set()
    if all(n in names for n in SPLAT_PROPS):
        return "splat"
    face = header.element("face")
    if face is not None and face.count > 0:
        return "mesh"
    return "pointcloud"


@dataclass
class PlyData:
    header: Header
    vertex: np.ndarray  # structured, one field per vertex property
    faces: np.ndarray | None  # (m, 3) int32 triangles, fanned from polygons; None: no faces

    def positions(self) -> np.ndarray:
        v = self.vertex
        return np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)

    def normals(self) -> np.ndarray | None:
        names = self.vertex.dtype.names or ()
        if all(n in names for n in ("nx", "ny", "nz")):
            v = self.vertex
            return np.stack([v["nx"], v["ny"], v["nz"]], axis=1).astype(np.float32)
        return None

    def colors(self) -> np.ndarray | None:
        """``uint8`` RGBA per vertex from a colour triple (uchar, or float 0-1), or from
        an intensity scalar as grey; None when the file carries neither."""
        names = self.vertex.dtype.names or ()
        v = self.vertex
        n = len(v)
        for rgb in COLOR_SETS:
            if all(c in names for c in rgb):
                out = np.empty((n, 4), dtype=np.uint8)
                for i, c in enumerate(rgb):
                    out[:, i] = _to_u8(v[c])
                alpha = next((a for a in ALPHA_NAMES if a in names), None)
                out[:, 3] = _to_u8(v[alpha]) if alpha else 255
                return out
        inten = next((a for a in INTENSITY_NAMES if a in names), None)
        if inten:
            x = v[inten].astype(np.float64)
            lo, hi = float(x.min()), float(x.max())
            g = np.full(n, 255, dtype=np.uint8) if hi <= lo else _to_u8((x - lo) / (hi - lo))
            out = np.empty((n, 4), dtype=np.uint8)
            out[:, 0] = out[:, 1] = out[:, 2] = g
            out[:, 3] = 255
            return out
        return None


def _to_u8(a: np.ndarray) -> np.ndarray:
    if a.dtype == np.uint8:
        return a
    if a.dtype.kind == "f":
        return np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)
    if a.dtype.kind in "iu" and a.dtype.itemsize > 1:
        return np.clip(a >> (8 * (a.dtype.itemsize - 1)), 0, 255).astype(np.uint8)
    return np.clip(a, 0, 255).astype(np.uint8)


def read_ply(path: Path | str, header: Header | None = None) -> PlyData:
    path = Path(path)
    header = header or read_header(path)
    vertex_el = header.element("vertex")
    if vertex_el is None or vertex_el.has_list:
        raise PlyError(f"{path}: no plain vertex element")
    if header.format == "ascii":
        return _read_ascii(path, header, vertex_el)
    return _read_binary(path, header, vertex_el)


def _read_binary(path: Path, header: Header, vertex_el: Element) -> PlyData:
    order = header.order
    data = np.memmap(path, mode="r", dtype=np.uint8, offset=header.size)
    off = 0
    vertex: np.ndarray | None = None
    faces: np.ndarray | None = None
    for el in header.elements:
        if not el.has_list:
            dt = el.dtype(order)
            block = np.frombuffer(data, dtype=dt, count=el.count, offset=off)
            off += el.count * dt.itemsize
            if el is vertex_el:
                vertex = _native(block)
            continue
        tris, off = _read_list_binary(data, off, el, order)
        if el.name == "face":
            faces = tris
    assert vertex is not None
    return PlyData(header, vertex, faces)


def _native(block: np.ndarray) -> np.ndarray:
    """A copy in native byte order (big-endian files are read once, then native)."""
    dt = np.dtype([(n, block.dtype[n].newbyteorder("=")) for n in block.dtype.names or ()])
    return block.astype(dt)


def _read_list_binary(
    data: np.ndarray, off: int, el: Element, order: str
) -> tuple[np.ndarray | None, int]:
    """A list element (faces). The common all-triangles layout is read in one go; any
    other polygon mix falls back to a face-by-face parse and a fan triangulation."""
    list_props = [p for p in el.props if p.count_type]
    plain_after = [p for p in el.props if not p.count_type]
    if len(list_props) != 1 or el.props[0] is not list_props[0]:
        # unusual layout: parse generically, keep only the first list as the face
        return _read_list_slow(data, off, el, order)
    lp = list_props[0]
    cdt = np.dtype(order + lp.count_type)  # type: ignore[operator]
    idt = np.dtype(order + lp.type)
    tail = sum(np.dtype(order + p.type).itemsize for p in plain_after)
    stride = cdt.itemsize + 3 * idt.itemsize + tail
    if off + el.count * stride <= len(data):
        dt = np.dtype([("n", cdt), ("i", idt, 3), ("tail", f"V{tail}" if tail else "V0")])
        fixed = np.frombuffer(data, dtype=dt, count=el.count, offset=off)
        if bool((fixed["n"] == 3).all()):
            return fixed["i"].astype(np.int32), off + el.count * stride
    return _read_list_slow(data, off, el, order)


def _read_list_slow(
    data: np.ndarray, off: int, el: Element, order: str
) -> tuple[np.ndarray | None, int]:
    raw = memoryview(data)
    tris: list[np.ndarray] = []
    for _ in range(el.count):
        first_list: np.ndarray | None = None
        for p in el.props:
            if p.count_type:
                cdt = np.dtype(order + p.count_type)
                n = int(np.frombuffer(raw, dtype=cdt, count=1, offset=off)[0])
                off += cdt.itemsize
                idt = np.dtype(order + p.type)
                vals = np.frombuffer(raw, dtype=idt, count=n, offset=off).astype(np.int64)
                off += n * idt.itemsize
                if first_list is None:
                    first_list = vals
            else:
                off += np.dtype(order + p.type).itemsize
        if first_list is not None and len(first_list) >= 3:
            tris.append(_fan(first_list))
    faces = np.concatenate(tris).astype(np.int32) if tris else None
    return faces, off


def _fan(poly: np.ndarray) -> np.ndarray:
    k = len(poly)
    out = np.empty((k - 2, 3), dtype=np.int64)
    out[:, 0] = poly[0]
    out[:, 1] = poly[1 : k - 1]
    out[:, 2] = poly[2:k]
    return out


def _read_ascii(path: Path, header: Header, vertex_el: Element) -> PlyData:
    with path.open("rb") as fh:
        fh.seek(header.size)
        tokens = fh.read().split()
    pos = 0
    vertex: np.ndarray | None = None
    faces: np.ndarray | None = None
    for el in header.elements:
        if not el.has_list:
            k = len(el.props)
            chunk = tokens[pos : pos + el.count * k]
            pos += el.count * k
            cols = np.array(chunk, dtype=np.float64).reshape(el.count, k) if el.count else None
            block = np.empty(el.count, dtype=el.dtype("="))
            for i, p in enumerate(el.props):
                block[p.name] = cols[:, i] if cols is not None else []
            if el is vertex_el:
                vertex = block
            continue
        tris: list[np.ndarray] = []
        for _ in range(el.count):
            first_list: np.ndarray | None = None
            for p in el.props:
                if p.count_type:
                    n = int(tokens[pos])
                    vals = np.array(tokens[pos + 1 : pos + 1 + n], dtype=np.int64)
                    pos += 1 + n
                    if first_list is None:
                        first_list = vals
                else:
                    pos += 1
            if first_list is not None and len(first_list) >= 3:
                tris.append(_fan(first_list))
        if el.name == "face":
            faces = np.concatenate(tris).astype(np.int32) if tris else None
    assert vertex is not None
    return PlyData(header, vertex, faces)


__all__ = [
    "Element",
    "Header",
    "PlyData",
    "PlyError",
    "Property",
    "classify",
    "read_header",
    "read_ply",
]
