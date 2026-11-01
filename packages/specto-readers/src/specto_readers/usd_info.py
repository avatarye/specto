"""The ``usd_info`` decoder (#132 design section 4, #135): what a USD file's root layer
says about the stage, read without pxr and without composing anything.

Stage metadata (``defaultPrim``, ``upAxis``, ``metersPerUnit``, the time codes) lives on
the root layer only, so its header is the whole answer for those fields. A text layer
(``.usda``, or ``.usd`` that starts ``#usda``) is parsed up to the end of its layer
metadata block; the rest of the file is never read. A binary crate layer (``.usdc``, or
``.usd`` that starts ``PXR-USDC``) needs pxr to read, so its fields come back null and
``unavailable`` says why; a ``.usdz`` is a zip whose first entry is the root layer and
goes by that entry's format. What only the composed stage knows (prim count, bounds,
cameras) is always in ``unavailable``: the view's source process reads those.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

import numpy as np

from specto_core.containers import open_source
from specto_core.errors import SpectoError
from specto_core.kinds import DataRef, DecodeRequest, TypedBuffer

from .decoders import DecodeError

TEXT_MAGIC = b"#usda"
CRATE_MAGIC = b"PXR-USDC"
FIRST_READ = 64 * 1024
#: a header longer than this is reported as unreadable rather than read whole
MAX_HEADER = 16 * 1024 * 1024

#: what USD answers when the root layer does not author a field (UsdGeom's up axis and
#: linear units fallbacks; 24 time codes and frames per second)
FALLBACKS: dict[str, Any] = {
    "up_axis": "Y",
    "meters_per_unit": 0.01,
    "time_codes_per_second": 24.0,
    "frames_per_second": 24.0,
}
#: what needs the composed stage, which only the source process opens
NEEDS_STAGE = "needs the composed stage (the view's source process reads it)"
STAGE_FIELDS = ("prim_count", "bounds", "cameras")
NEEDS_PXR = "binary crate layer (usdc): its header needs pxr to read"
HEADER_FIELDS = (
    "default_prim",
    "up_axis",
    "meters_per_unit",
    "start_time_code",
    "end_time_code",
    "time_codes_per_second",
    "frames_per_second",
    "kit_camera",
    "sublayers",
)


class _Incomplete(Exception):
    """The text ran out inside the layer metadata block: read more."""


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+|\#[^\n]*)
    | (?P<str3>\"\"\"(?:[^\\]|\\.)*?\"\"\"|'''(?:[^\\]|\\.)*?''')
    | (?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
    | (?P<asset>@@@.*?@@@|@[^@\n]*@)
    | (?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![\w.]))
    | (?P<name>[A-Za-z_][\w:.]*(?:\[\])?|-?inf|nan)
    | (?P<punct>[()\[\]{}=,;])
    """,
    re.VERBOSE | re.DOTALL,
)
#: a string or asset whose closing quote may lie past the end of what was read
_OPEN_QUOTE = re.compile(r"\"\"\"|'''|\"|'|@@@|@")


def _unescape(body: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


class _Parser:
    """Recursive descent over the layer metadata's value grammar: strings, numbers,
    assets, names, tuples, lists and typed dictionaries."""

    def __init__(self, text: str, pos: int) -> None:
        self.text = text
        self.pos = pos

    def _next(self) -> tuple[str, str]:
        while True:
            if self.pos >= len(self.text):
                raise _Incomplete
            m = _TOKEN.match(self.text, self.pos)
            if m is None:
                if _OPEN_QUOTE.match(self.text, self.pos):
                    raise _Incomplete  # a string cut off by the read
                raise ValueError(f"unexpected {self.text[self.pos : self.pos + 20]!r}")
            if m.end() == len(self.text) and m.lastgroup in ("num", "name", "ws"):
                raise _Incomplete  # the token may go on past the read
            self.pos = m.end()
            if m.lastgroup != "ws":
                return m.lastgroup or "", m.group()

    def _peek(self) -> tuple[str, str]:
        pos = self.pos
        try:
            return self._next()
        finally:
            self.pos = pos

    def _expect(self, value: str) -> None:
        _, tok = self._next()
        if tok != value:
            raise ValueError(f"expected {value!r}, found {tok!r}")

    def value(self) -> Any:
        kind, tok = self._next()
        if kind == "str3":
            return _unescape(tok[3:-3])
        if kind == "str":
            return _unescape(tok[1:-1])
        if kind == "asset":
            return {"asset": tok.strip("@")}
        if kind == "num":
            return float(tok) if any(c in tok for c in ".eE") else int(tok)
        if kind == "name":
            return {"true": True, "false": False, "None": None}.get(tok, tok)
        if tok == "(":
            return self._sequence(")")
        if tok == "[":
            return self._sequence("]")
        if tok == "{":
            return self._dictionary()
        raise ValueError(f"unexpected {tok!r}")

    def _sequence(self, close: str) -> list[Any]:
        out: list[Any] = []
        while True:
            if self._peek()[1] == close:
                self._next()
                return out
            out.append(self.value())
            if close == "]" and self._peek()[1] == "(":
                self._next()
                self.metadata()  # a sublayer's (offset = ...; scale = ...)
            if self._peek()[1] == ",":
                self._next()

    def _dictionary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            _, tok = self._next()
            if tok == "}":
                return out
            if tok in (",", ";"):
                continue
            _, key = self._next()  # after the value type (double3, dictionary, string)
            if key.startswith(("'", '"')):
                key = _unescape(key[1:-1])
            self._expect("=")
            out[key] = self.value()

    def metadata(self) -> dict[str, Any]:
        """A ``( ... )`` metadata block whose ``(`` was just read: ``key = value``
        entries (with an optional list op), a bare string being the doc."""
        out: dict[str, Any] = {}
        while True:
            kind, tok = self._next()
            if tok == ")":
                return out
            if tok == ";":
                continue
            if kind in ("str", "str3"):
                out["doc"] = _unescape(tok[3:-3] if kind == "str3" else tok[1:-1])
                continue
            if tok in ("add", "append", "prepend", "delete", "reorder"):
                _, tok = self._next()
            self._expect("=")
            out[tok] = self.value()


def parse_text_header(text: str, *, complete: bool = True) -> dict[str, Any] | None:
    """The layer metadata of a ``#usda`` text (``{}`` when it authors none). With
    ``complete`` false the text is a prefix of the file: None when it ends inside the
    block (read more)."""
    if not text.startswith("#usda"):
        raise ValueError("not a usda text layer (no #usda header)")
    p = _Parser(text, text.find("\n") + 1 if "\n" in text else len(text))
    opened = False
    try:
        opened = p._next()[1] == "("
        return p.metadata() if opened else {}
    except _Incomplete:
        if not complete:
            return None
        if opened:
            raise ValueError("the file ends inside its layer metadata") from None
        return {}


def _number(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _vec3(v: Any) -> list[float] | None:
    if isinstance(v, list) and len(v) == 3 and all(_number(c) is not None for c in v):
        return [float(c) for c in v]
    return None


def _kit_camera(meta: dict[str, Any]) -> dict[str, list[float]] | None:
    """Kit's saved perspective camera (``customLayerData.cameraSettings.Perspective``)."""
    persp = ((meta.get("customLayerData") or {}).get("cameraSettings") or {}).get(
        "Perspective"
    ) or {}
    pos, target = _vec3(persp.get("position")), _vec3(persp.get("target"))
    return {"position": pos, "target": target} if pos and target else None


def header_info(meta: dict[str, Any]) -> dict[str, Any]:
    """The stage fields from a root layer's metadata, USD's fallbacks where it authors
    none (named in ``defaulted``)."""
    tcps, fps = _number(meta.get("timeCodesPerSecond")), _number(meta.get("framesPerSecond"))
    prim = meta.get("defaultPrim")
    info: dict[str, Any] = {
        "default_prim": prim if isinstance(prim, str) else None,
        "up_axis": meta.get("upAxis") if meta.get("upAxis") in ("Y", "Z") else None,
        "meters_per_unit": _number(meta.get("metersPerUnit")),
        "start_time_code": _number(meta.get("startTimeCode")),
        "end_time_code": _number(meta.get("endTimeCode")),
        # USD: an unauthored timeCodesPerSecond is the authored framesPerSecond, else 24
        "time_codes_per_second": tcps if tcps is not None else fps,
        "frames_per_second": fps,
        "kit_camera": _kit_camera(meta),
        "sublayers": [
            s["asset"] for s in meta.get("subLayers") or [] if isinstance(s, dict) and "asset" in s
        ],
    }
    defaulted = []
    for field, fallback in FALLBACKS.items():
        if info[field] is None:
            info[field] = fallback
            defaulted.append(field)
    info["defaulted"] = defaulted
    return info


def _read_text_header(fh: Any) -> dict[str, Any]:
    data, size = b"", FIRST_READ
    while True:
        more = fh.read(size)
        data += more
        meta = parse_text_header(data.decode("utf-8", errors="replace"), complete=not more)
        if meta is not None:
            return meta
        if len(data) >= MAX_HEADER:
            raise ValueError(f"layer metadata runs past {MAX_HEADER // (1024 * 1024)} MB")
        size *= 2


def _layer_info(fh: Any, fmt: str) -> dict[str, Any]:
    """Format, header fields and what is unavailable for one root layer stream."""
    head = fh.read(len(CRATE_MAGIC))
    rest = io.BufferedReader(_Prefixed(head, fh)) if head else fh
    unavailable = {f: NEEDS_STAGE for f in STAGE_FIELDS}
    if head.startswith(CRATE_MAGIC):
        info: dict[str, Any] = dict.fromkeys(HEADER_FIELDS) | {"defaulted": []}
        unavailable |= {f: NEEDS_PXR for f in HEADER_FIELDS}
        return {"format": fmt, "root_format": "usdc"} | info | {"unavailable": unavailable}
    if not head.startswith(TEXT_MAGIC):
        raise ValueError(f"neither a usda nor a usdc layer (starts {head!r})")
    info = header_info(_read_text_header(rest))
    return {"format": fmt, "root_format": "usda"} | info | {"unavailable": unavailable}


class _Prefixed(io.RawIOBase):
    """A stream with its first bytes (already read to sniff the format) put back."""

    def __init__(self, head: bytes, fh: Any) -> None:
        self._head = head
        self._fh = fh

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        if self._head:
            n = min(len(b), len(self._head))
            b[:n], self._head = self._head[:n], self._head[n:]
            return n
        data = self._fh.read(len(b))
        b[: len(data)] = data
        return len(data)


def usd_info(path: str) -> dict[str, Any]:
    """What the file's root layer says about its stage (see the module doc)."""
    fmt = path.rsplit(".", 1)[-1].lower() if "." in path else "usd"
    with open_source(path) as fh:
        if fmt != "usdz":
            return _layer_info(fh, fmt)
        src = fh if fh.seekable() else io.BytesIO(fh.read())
        with zipfile.ZipFile(src) as zf:
            names = zf.namelist()
            if not names:
                raise ValueError("an empty usdz")
            with zf.open(names[0]) as root:
                info = _layer_info(root, fmt)
            return info | {"root_layer": names[0]}


class UsdInfoDecoder:
    name = "usd_info"
    kinds = ("usd_stage_ref",)
    purposes = ("meta",)

    def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer:
        try:
            info = usd_info(ref.uri)
        except (OSError, ValueError, zipfile.BadZipFile, SpectoError) as e:
            raise DecodeError(
                f"cannot read the USD header of {ref.uri}: {e}",
                fix="check the file: usd_info reads usda text headers, sniffs usdc and "
                "opens usdz archives",
            ) from e
        info["source"] = ref.uri
        arr = np.frombuffer(json.dumps(info).encode("utf-8"), dtype=np.uint8)
        return TypedBuffer.from_numpy(arr, layout="bytes", **info)
