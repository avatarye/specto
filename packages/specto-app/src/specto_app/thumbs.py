"""Gallery thumbnails for the kinds that are not images (#105; spec #103 decision 2).

Images keep their own path (the ``image`` decoder in the worker pool). Every other kind
gets a picture here, rendered off the GUI thread by :func:`render_batch` (a pool job):

- ``video_ref``: the poster frame; libmpv (no window: ``vo=null``) seeks to 10 % and
  hands the frame back (``screenshot-raw``);
- ``pdf``: the first page, rendered by QtPdf (``QPdfDocument.render``);
- ``markdown``, ``json``, ``geojson``, ``html``, ``text``: an excerpt card, the first
  lines as text on a theme-coloured card (no web view);
- 3D (``mesh``, ``pointcloud``, ``splat``) and the rest: a kind icon card (a snapshot
  later).

Video and pdf thumbnails fit a ``px`` square at the source's aspect; cards are
``CARD_ASPECT`` landscape. The result is an RGBA ``HWC`` ``TypedBuffer`` like the image
decoder's, so the gallery pushes both the same way.

Cache: per file path plus stat (size, mtime). On disk, one PNG per path and size under
the project's ``.specto/cache/thumbs/`` (the intaker never lists ``.specto/``), the stat
kept in the PNG and compared on read, so a changed file is rendered again and its PNG
replaced; in memory, :class:`ThumbMemory` per engine (GUI thread), so a tile scrolled
back into view or a second gallery over the same files needs no job at all.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import functools
import hashlib
import html
import json
import os
import re
import sys
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from specto_core.containers import is_virtual, open_source, stat_virtual
from specto_core.kinds import DataRef, TypedBuffer

THUMB_VERSION = 1  # bump when the pictures change: old cache files are rendered again
CARD_ASPECT = 1.5  # width / height of excerpt and kind cards (the gallery's default cell)
CACHE_SUBDIR = Path(".specto") / "cache" / "thumbs"
MEMORY_BUDGET = 96 * 1024 * 1024  # bytes of RGBA kept per engine
POSTER_AT = "10%"  # where the poster frame is taken
VIDEO_TIMEOUT_S = 5.0
EXCERPT_BYTES = 16 * 1024
NATIVE_DIR = Path(__file__).resolve().parent / "qml" / "Specto" / "Native"  # ships libmpv
JSON_PRETTY_MAX = 256 * 1024  # larger json files show their first raw lines

IMAGE_KINDS = ("image2d", "image_seq")
TEXT_KINDS = ("markdown", "json", "geojson", "html", "text")
SCENE_KINDS = ("mesh", "pointcloud", "splat", "usd_stage_ref")
# what a tile shows of an item with several parts: the first kind group that has one
KIND_ORDER: tuple[tuple[str, ...], ...] = (
    IMAGE_KINDS,
    ("video_ref",),
    ("pdf",),
    SCENE_KINDS,
    ("markdown",),
    ("html",),
    ("geojson", "json"),
    ("text",),
)
BADGES: dict[str, tuple[str, str]] = {  # kind -> (label, theme colour token)
    "markdown": ("MD", "accent"),
    "json": ("JSON", "changed"),
    "geojson": ("GEOJSON", "changed"),
    "html": ("HTML", "agent"),
    "text": ("TEXT", "text_muted"),
    "video_ref": ("VIDEO", "removed"),
    "pdf": ("PDF", "removed"),
    "mesh": ("3D", "added"),
    "pointcloud": ("3D", "added"),
    "splat": ("3D", "added"),
    "usd_stage_ref": ("USD", "added"),
}

ProgressFn = Callable[[float, str], None]


def is_image(kind: str) -> bool:
    return kind in IMAGE_KINDS


def palette() -> dict[str, str]:
    """The theme colours and fonts a card is drawn with (plain data: it goes to a worker)."""
    from specto_app.theme import COLORS, FONTS

    keys = ("surface", "surface_alt", "border", "text", "text_muted", "mono_bg")
    out = {k: COLORS[k] for k in keys}
    out.update({tok: COLORS[tok] for _label, tok in BADGES.values()})
    out.update({f"font_{k}": v for k, v in FONTS.items()})
    return out


def cache_dir_for(project_root: Path | str | None) -> str | None:
    return str(Path(project_root) / CACHE_SUBDIR) if project_root else None


def cache_path(cache_dir: str, uri: str, px: int, pal: dict[str, str]) -> Path:
    """The PNG for ``uri`` at ``px`` under ``cache_dir`` (one per path, size and look)."""
    look = json.dumps(pal, sort_keys=True)
    h = hashlib.sha1(f"{THUMB_VERSION}|{uri}|{px}|{look}".encode()).hexdigest()
    return Path(cache_dir) / h[:2] / f"{h}.png"


def stat_of(uri: str) -> tuple[int, int]:
    st = stat_virtual(uri)
    return int(st.st_size), int(st.st_mtime_ns)


# -- the pool job ---------------------------------------------------------------------
def render_batch(
    refs: list[dict[str, Any]],
    px: int,
    cache_dir: str | None,
    pal: dict[str, str],
    progress: ProgressFn,
) -> list[TypedBuffer | dict[str, str]]:
    """Thumbnails for ``refs`` (``DataRef`` dumps) at ``px``, from the disk cache when the
    file's stat still matches, else rendered (and cached). A failing item becomes
    ``{"error": ...}`` in its slot. ``meta.cache`` says ``disk`` or ``rendered``,
    ``meta.stat`` the (size, mtime_ns) the picture is of."""
    out: list[TypedBuffer | dict[str, str]] = []
    n = max(1, len(refs))
    for i, raw in enumerate(refs):
        ref = DataRef.model_validate(raw)
        try:
            out.append(_thumb(ref, int(px), cache_dir, pal))
        except Exception as e:
            out.append({"error": f"{type(e).__name__}: {e}", "uri": ref.uri})
        if (i + 1) % 8 == 0:
            progress((i + 1) / n, f"{i + 1}/{n}")
    return out


def _thumb(ref: DataRef, px: int, cache_dir: str | None, pal: dict[str, str]) -> TypedBuffer:
    size, mtime = stat_of(ref.uri)
    sig = f"{size}:{mtime}"
    path = cache_path(cache_dir, ref.uri, px, pal) if cache_dir else None
    arr = _read_cached(path, sig) if path is not None else None
    how = "disk"
    if arr is None:
        arr = render(ref.uri, ref.kind, px, pal)
        how = "rendered"
        if path is not None:
            _write_cached(path, arr, sig, ref.uri)
    return TypedBuffer.from_numpy(
        arr,
        layout="HWC",
        width=int(arr.shape[1]),
        height=int(arr.shape[0]),
        source=ref.uri,
        purpose="thumbnail",
        stat=[size, mtime],
        cache=how,
    )


def _read_cached(path: Path, sig: str) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            if getattr(im, "text", {}).get("specto-stat") != sig:
                return None
            return np.asarray(im.convert("RGBA"), dtype=np.uint8)
    except (OSError, ValueError):
        return None


def _write_cached(path: Path, arr: np.ndarray, sig: str, uri: str) -> None:
    info = PngInfo()
    info.add_text("specto-stat", sig)
    info.add_text("specto-source", uri)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr, "RGBA").save(tmp, "PNG", pnginfo=info, compress_level=1)
        os.replace(tmp, path)
    except OSError:  # another worker wrote it first, or the folder is read-only
        tmp.unlink(missing_ok=True)


# -- rendering --------------------------------------------------------------------------
def render(uri: str, kind: str, px: int, pal: dict[str, str]) -> np.ndarray:
    """The thumbnail of one file as RGBA ``HWC``, no cache."""
    if kind == "video_ref":
        frame = None if is_virtual(uri) else video_poster(uri, px)
        return frame if frame is not None else kind_card(uri, kind, px, pal)
    if kind == "pdf":
        return pdf_page(uri, px)
    if kind in TEXT_KINDS:
        return excerpt_card(uri, kind, px, pal)
    return kind_card(uri, kind, px, pal)


def _fit(im: Image.Image, px: int) -> np.ndarray:
    im.thumbnail((px, px), Image.Resampling.BILINEAR)
    return np.asarray(im.convert("RGBA"), dtype=np.uint8)


def pdf_page(uri: str, px: int) -> np.ndarray:
    """The first page, fitted into ``px`` x ``px``."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QImage
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument(None)
    buf = None
    if is_virtual(uri):
        with open_source(uri) as f:
            buf = QBuffer()
            buf.setData(QByteArray(f.read()))
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
        doc.load(buf)
    else:
        doc.load(uri)
    try:
        if doc.status() != QPdfDocument.Status.Ready or doc.pageCount() < 1:
            raise ValueError(f"QtPdf cannot open {uri}: {doc.error()}")
        page = doc.pagePointSize(0)
        s = px / max(1.0, page.width(), page.height())
        w, h = max(1, round(page.width() * s)), max(1, round(page.height() * s))
        img = doc.render(0, QSize(w, h)).convertToFormat(QImage.Format.Format_RGBA8888)
        bpl = img.bytesPerLine()
        raw = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h)
        arr = raw.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4).copy()
    finally:
        doc.close()
        if buf is not None:
            buf.close()
    arr[..., 3] = 255  # a page with no background is a white page
    return arr


# -- video poster frames through libmpv (no window: vo=null, screenshot-raw) -----------
class _Node(ctypes.Structure):
    pass


class _NodeList(ctypes.Structure):
    _fields_ = [
        ("num", ctypes.c_int),
        ("values", ctypes.POINTER(_Node)),
        ("keys", ctypes.POINTER(ctypes.c_char_p)),
    ]


class _ByteArray(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


class _NodeU(ctypes.Union):
    _fields_ = [
        ("string", ctypes.c_char_p),
        ("flag", ctypes.c_int),
        ("int64", ctypes.c_int64),
        ("double_", ctypes.c_double),
        ("list", ctypes.POINTER(_NodeList)),
        ("ba", ctypes.POINTER(_ByteArray)),
    ]


_Node._fields_ = [("u", _NodeU), ("format", ctypes.c_int)]


class _Event(ctypes.Structure):
    _fields_ = [
        ("event_id", ctypes.c_int),
        ("error", ctypes.c_int),
        ("reply_userdata", ctypes.c_uint64),
        ("data", ctypes.c_void_p),
    ]


#: libmpv's Lua off: no user or built-in scripts. LuaJIT unwinds every Lua error, even a
#: caught one, as an SEH exception (0xE24C4A02) that faulthandler logs from libmpv's
#: thread while walking Python's thread states unlocked, which crashed test runs (#172).
#: MpvItem (native/mpvitem.cpp) sets the same options.
NO_LUA = (
    ("load-scripts", "no"),
    ("osc", "no"),
    ("ytdl", "no"),
    ("load-stats-overlay", "no"),
    ("load-console", "no"),
    ("load-auto-profiles", "no"),
    ("load-select", "no"),
    ("load-positioning", "no"),
    ("load-commands", "no"),
    ("load-context-menu", "no"),
)

MPV_FORMAT_STRING, MPV_FORMAT_INT64, MPV_FORMAT_BYTE_ARRAY = 1, 4, 9
MPV_EVENT_SHUTDOWN, MPV_EVENT_END_FILE, MPV_EVENT_PLAYBACK_RESTART = 1, 7, 21


@functools.cache
def libmpv() -> Any | None:
    """libmpv, as the native plugin ships it (beside the plugin: tools/fetch_libmpv.py) or
    from the system; None without."""
    local = "libmpv-2.dll" if sys.platform == "win32" else "libmpv.so.2"
    names = [str(NATIVE_DIR / local)] if (NATIVE_DIR / local).exists() else []
    found = ctypes.util.find_library("mpv")
    if found:
        names.append(found)
    for name in names:
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.mpv_create.restype = ctypes.c_void_p
        lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.mpv_initialize.argtypes = [ctypes.c_void_p]
        lib.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        lib.mpv_command_ret.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(_Node),
        ]
        lib.mpv_wait_event.restype = ctypes.POINTER(_Event)
        lib.mpv_wait_event.argtypes = [ctypes.c_void_p, ctypes.c_double]
        lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        lib.mpv_free_node_contents.argtypes = [ctypes.POINTER(_Node)]
        return lib
    return None


def _argv(*args: str) -> Any:
    return (ctypes.c_char_p * (len(args) + 1))(*[a.encode("utf-8") for a in args], None)


def video_poster(path: str, px: int) -> np.ndarray | None:
    """The poster frame fitted into ``px`` x ``px``; None when libmpv is missing. Raises
    when the file has no picture."""
    lib = libmpv()
    if lib is None:
        return None
    h = lib.mpv_create()
    if not h:
        return None
    try:
        # no picture window and no sound: the poster never touches an audio device
        # (the video view's sound, #216, is the view's alone)
        for k, v in (
            ("vo", "null"),
            ("ao", "null"),
            ("audio", "no"),
            ("sub", "no"),
            ("pause", "yes"),
            ("hwdec", "no"),
            ("hr-seek", "no"),
            ("start", POSTER_AT),
            ("config", "no"),
            ("terminal", "no"),
            ("idle", "yes"),
            *NO_LUA,
        ):
            lib.mpv_set_option_string(h, k.encode(), v.encode())
        if lib.mpv_initialize(h) < 0:
            return None
        lib.mpv_command(h, _argv("loadfile", path))
        deadline = time.monotonic() + VIDEO_TIMEOUT_S
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"libmpv took over {VIDEO_TIMEOUT_S:.0f} s to open {path}")
            ev = lib.mpv_wait_event(h, left).contents
            if ev.event_id == MPV_EVENT_PLAYBACK_RESTART:
                break
            if ev.event_id in (MPV_EVENT_END_FILE, MPV_EVENT_SHUTDOWN):
                raise ValueError(f"libmpv cannot play {path}")
        node = _Node()
        if lib.mpv_command_ret(h, _argv("screenshot-raw", "video"), ctypes.byref(node)) < 0:
            raise ValueError(f"no picture in {path}")
        try:
            frame = _frame_of(node)
        finally:
            lib.mpv_free_node_contents(ctypes.byref(node))
    finally:
        lib.mpv_terminate_destroy(h)
    return _fit(Image.fromarray(frame, "RGBA"), px)


def _frame_of(node: _Node) -> np.ndarray:
    """``screenshot-raw``'s map (w, h, stride, format bgr0, data) as RGBA ``HWC``."""
    lst = node.u.list.contents
    f: dict[str, Any] = {}
    for i in range(lst.num):
        v = lst.values[i]
        key = lst.keys[i].decode()
        if v.format == MPV_FORMAT_INT64:
            f[key] = int(v.u.int64)
        elif v.format == MPV_FORMAT_STRING:
            f[key] = v.u.string.decode()
        elif v.format == MPV_FORMAT_BYTE_ARRAY:
            ba = v.u.ba.contents
            f[key] = ctypes.string_at(ba.data, ba.size)
    w, h, stride = f["w"], f["h"], f["stride"]
    rows = np.frombuffer(f["data"], dtype=np.uint8, count=stride * h).reshape(h, stride)
    bgr0 = rows[:, : w * 4].reshape(h, w, 4)
    rgba = bgr0[..., [2, 1, 0, 3]].copy()
    rgba[..., 3] = 255
    return rgba


# -- cards -------------------------------------------------------------------------------
_FONT_FILES = {
    "ui": ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc"),
    "bold": ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"),
    "mono": ("CascadiaMono.ttf", "consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"),
}


@functools.cache
def _font(face: str, size: int) -> Any:
    for name in _FONT_FILES[face]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _rgb(colour: str) -> tuple[int, int, int]:
    c = colour.lstrip("#")[-6:]  # #aarrggbb tokens: the colour part
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _card(
    kind: str, px: int, pal: dict[str, str]
) -> tuple[Image.Image, ImageDraw.ImageDraw, int, int]:
    """A blank card with the kind badge in its corner; returns the image, a drawer,
    the padding and the y below the badge."""
    w, h = px, max(1, round(px / CARD_ASPECT))
    im = Image.new("RGBA", (w, h), (*_rgb(pal["surface"]), 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w - 1, h - 1), outline=_rgb(pal["border"]))
    pad = max(4, round(px * 0.06))
    label, token = BADGES.get(kind, (kind.upper()[:6], "text_muted"))
    colour = _rgb(pal[token])
    font = _font("bold", max(7, round(px / 22)))
    tw = d.textlength(label, font=font)
    bh = max(9, round(px / 13))
    d.rounded_rectangle(
        (pad, pad, pad + tw + bh * 0.8, pad + bh),
        radius=bh // 3,
        fill=_mix(colour, _rgb(pal["surface"]), 0.82),
    )
    d.text((pad + bh * 0.4, pad + bh / 2), label, font=font, fill=colour, anchor="lm")
    return im, d, pad, pad + bh + max(3, round(px * 0.035))


def _wrap(d: ImageDraw.ImageDraw, text: str, font: Any, width: float) -> list[str]:
    words, lines, cur = text.split(" "), [], ""
    for word in words:
        trial = f"{cur} {word}" if cur else word
        if cur and d.textlength(trial, font=font) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    return lines


def _read_head(uri: str, limit: int) -> tuple[str, bool]:
    """The first ``limit`` bytes as text, and whether that was the whole file."""
    with open_source(uri) as f:
        raw = f.read(limit + 1)
    return raw[:limit].decode("utf-8", errors="replace"), len(raw) <= limit


def excerpt_lines(uri: str, kind: str) -> list[tuple[str, str]]:
    """The first lines of a text file as (style, text): style ``head`` for a title or
    heading, ``body`` for prose, ``code`` for monospaced lines."""
    text, whole = _read_head(uri, EXCERPT_BYTES)
    if kind == "markdown":
        out = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("```"):
                continue
            if s.startswith("#"):
                out.append(("head", s.lstrip("#").strip()))
            else:
                s = re.sub(r"^[-*+]\s+", "• ", s)
                s = re.sub(r"\*\*|__|`|\[\[|\]\]", "", s)
                out.append(("body", s))
        return out
    if kind == "html":
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        body = re.sub(r"<(script|style|head)\b.*?</\1>", " ", text, flags=re.I | re.S)
        body = html.unescape(re.sub(r"<[^>]+>", "\n", body))
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        out = [("head", html.unescape(title.group(1).strip()))] if title else []
        if lines and out and lines[0] == out[0][1]:
            lines = lines[1:]
        return out + [("body", ln) for ln in lines]
    if kind in ("json", "geojson") and whole and len(text) <= JSON_PRETTY_MAX:
        with contextlib.suppress(ValueError):  # not json after all: the raw lines
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    return [("code", ln.rstrip()) for ln in text.splitlines() if ln.strip()]


def excerpt_card(uri: str, kind: str, px: int, pal: dict[str, str]) -> np.ndarray:
    """The first lines of the file on a card (no web view, no layout engine)."""
    im, d, pad, y = _card(kind, px, pal)
    w, h = im.size
    body = max(7, round(px / 15))
    fonts = {
        "head": _font("bold", round(body * 1.2)),
        "body": _font("ui", body),
        "code": _font("mono", round(body * 0.92)),
    }
    colours = {"head": _rgb(pal["text"]), "body": _rgb(pal["text"]), "code": _rgb(pal["text"])}
    width = w - 2 * pad
    for style, line in excerpt_lines(uri, kind):
        font = fonts[style]
        step = round(font.size * 1.3)
        rows = [line] if style == "code" else _wrap(d, line, font, width)
        for row in rows:
            if y + font.size > h - pad // 2:
                return np.asarray(im, dtype=np.uint8)
            d.text((pad, y), row, font=font, fill=colours[style])
            y += step
        if style == "head":
            y += round(body * 0.3)
    return np.asarray(im, dtype=np.uint8)


def kind_card(uri: str, kind: str, px: int, pal: dict[str, str]) -> np.ndarray:
    """A card with a glyph for the kind and the file's extension."""
    im, d, pad, top = _card(kind, px, pal)
    w, h = im.size
    _label, token = BADGES.get(kind, ("", "text_muted"))
    colour = _rgb(pal[token])
    ext = Path(uri.rsplit("#", 1)[-1]).suffix.lstrip(".").upper() or kind
    font = _font("bold", max(7, round(px / 14)))
    text_h = font.size + pad
    cx, cy = w / 2, top + (h - top - text_h) / 2
    r = max(4.0, min(w, h - top - text_h) * 0.32)
    lw = max(1, round(px / 60))
    if kind in SCENE_KINDS:
        # an isometric cube
        dx, dy = r * 0.87, r * 0.5
        top_face = [(cx, cy - r), (cx + dx, cy - dy), (cx, cy), (cx - dx, cy - dy)]
        d.polygon(top_face, fill=_mix(colour, _rgb(pal["surface"]), 0.7))
        d.polygon(
            [(cx - dx, cy - dy), (cx, cy), (cx, cy + r), (cx - dx, cy + dy)],
            fill=_mix(colour, _rgb(pal["surface"]), 0.45),
        )
        d.polygon(
            [(cx + dx, cy - dy), (cx, cy), (cx, cy + r), (cx + dx, cy + dy)],
            fill=_mix(colour, _rgb(pal["surface"]), 0.2),
        )
        outline = [(cx, cy - r), (cx + dx, cy - dy), (cx + dx, cy + dy), (cx, cy + r)]
        outline += [(cx - dx, cy + dy), (cx - dx, cy - dy), (cx, cy - r)]
        d.line(outline, fill=colour, width=lw, joint="curve")
        d.line([(cx - dx, cy - dy), (cx, cy), (cx + dx, cy - dy)], fill=colour, width=lw)
        d.line([(cx, cy), (cx, cy + r)], fill=colour, width=lw)
    elif kind == "video_ref":
        d.regular_polygon((cx, cy, r), 3, rotation=-90, fill=colour)
    else:
        # a page with a folded corner
        x0, y0, x1, y1 = cx - r * 0.75, cy - r, cx + r * 0.75, cy + r
        f = r * 0.45
        page = [(x0, y0), (x1 - f, y0), (x1, y0 + f), (x1, y1), (x0, y1)]
        d.polygon(page, outline=colour, width=lw)
        d.line([(x1 - f, y0), (x1 - f, y0 + f), (x1, y0 + f)], fill=colour, width=lw)
    d.text((cx, h - pad), ext, font=font, fill=_rgb(pal["text_muted"]), anchor="ms")
    return np.asarray(im, dtype=np.uint8)


# -- the in-memory cache (GUI thread) ----------------------------------------------------
class ThumbMemory:
    """Rendered thumbnails by (path, px), each with the stat it was made from; a lookup
    with another stat misses. Least recently used goes first past ``budget`` bytes."""

    def __init__(self, budget: int = MEMORY_BUDGET) -> None:
        self.budget = budget
        self.bytes = 0
        self._entries: OrderedDict[tuple[str, int], tuple[Any, int, int, bytes]] = OrderedDict()

    def get(self, uri: str, px: int, stat: Any) -> tuple[int, int, bytes] | None:
        hit = self._entries.get((uri, px))
        if hit is None or hit[0] != stat:
            return None
        self._entries.move_to_end((uri, px))
        return hit[1], hit[2], hit[3]

    def put(self, uri: str, px: int, stat: Any, w: int, h: int, rgba: bytes) -> None:
        old = self._entries.pop((uri, px), None)
        if old is not None:
            self.bytes -= len(old[3])
        self._entries[(uri, px)] = (stat, w, h, rgba)
        self.bytes += len(rgba)
        while self.bytes > self.budget and self._entries:
            _k, (_s, _w, _h, data) = self._entries.popitem(last=False)
            self.bytes -= len(data)

    def __len__(self) -> int:
        return len(self._entries)


_memories: weakref.WeakKeyDictionary[Any, ThumbMemory] = weakref.WeakKeyDictionary()


def memory_for(engine: Any) -> ThumbMemory:
    """The engine's thumbnail memory, shared by its galleries."""
    mem = _memories.get(engine)
    if mem is None:
        mem = _memories[engine] = ThumbMemory()
    return mem


def ref_stat(ref: DataRef) -> tuple[int, int] | None:
    """The stat the locator saw (None when it could not stat the file)."""
    size, mtime = ref.meta.get("size"), ref.meta.get("mtime_ns")
    return (int(size), int(mtime)) if size is not None and mtime is not None else None


def submit_thumbs(
    jobs: Any,
    refs: list[DataRef],
    px: int,
    cache_dir: str | None,
    pal: dict[str, str],
    *,
    canvas: str | None = None,
    on_done: Callable[[Any], None] | None = None,
) -> Any:
    """Queue :func:`render_batch` on any ``JobManager`` (the pool in the app)."""
    return jobs.submit(
        "thumbs.batch",
        render_batch,
        [r.model_dump() for r in refs],
        int(px),
        cache_dir,
        dict(pal),
        canvas=canvas,
        on_done=on_done,
    )


__all__ = [
    "CARD_ASPECT",
    "IMAGE_KINDS",
    "KIND_ORDER",
    "THUMB_VERSION",
    "ThumbMemory",
    "cache_dir_for",
    "cache_path",
    "excerpt_card",
    "excerpt_lines",
    "is_image",
    "kind_card",
    "memory_for",
    "palette",
    "pdf_page",
    "ref_stat",
    "render",
    "render_batch",
    "submit_thumbs",
    "video_poster",
]
