"""The image view's channel toggles (#171), Qt Quick-free so they test headless.

**What an image offers** comes from its file, probed once per file and never decoded:
a PNG's own header (its colour type says grey, grey+alpha, RGB, RGBA or a palette, a
``tRNS`` chunk before the pixels adds alpha; Qt's decoder turns grey+alpha into ARGB, so
the decoder's format cannot tell), any other format through ``QImageReader.imageFormat``
(the header only). RGB offers R G B, RGBA adds A, grey plus alpha offers L A, plain grey
offers nothing (one channel is no choice). 16-bit and float files say their depth; what
shows is what the decoder gives.

**What shows** is ``params.channels``: the letters of the shown channels (``r``, ``g``,
``b``, ``a``, ``l``), the agent's to set and the toggles' to write. Against the image it
is normalised: letters the image lacks drop out (``r`` / ``g`` / ``b`` on a grey image
are its ``l``, ``l`` on a colour image is its ``rgb``), and nothing left, or nothing
set, is every channel it offers (the normal view). The display follows from the set:

- one colour channel (R, G, B or L): that channel as greyscale;
- A alone: the alpha as greyscale;
- several colour channels: those in colour, the others black;
- with A and a colour channel, the transparency shows over a checkerboard (the normal
  view of an RGBA image); without A the colour shows opaque.

A click solos a channel (the soloed one again is the normal view); Ctrl+click or
Alt+click (#182), or the menu, toggles it within the mix; R, G, B, A (and L) are the click's keys.
:meth:`ChannelView.uniforms` is what the GPU shader (``ImageView.qml``'s ShaderEffect,
``shaders/channels.frag``) takes: no re-decode, the zoom and pan stay a texture's.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtGui import QImage, QImageReader, QPixelFormat

#: the letters, in the order the footer shows and the param is written
ORDER = "rgbla"
COLOUR = "rgbl"
#: ``params.channels`` as the manifests describe it (the image view's and the preview's)
CHANNELS_SCHEMA: dict[str, Any] = {
    "type": "string",
    "default": "",
    "description": "Shown channels of an image, letters of r g b a (l: a grey image's "
    "luminance): one colour channel shows as greyscale, a alone the alpha as greyscale, "
    "several in colour, a with colour over a checkerboard; empty: all the image has.",
}
_PNG = b"\x89PNG\r\n\x1a\n"
_HEAD = 256 * 1024  # a container member is read this far for its header


@dataclass(frozen=True)
class ChannelInfo:
    """The channels a file offers (``""`` for plain grey), its bits per channel and
    whether they are floats."""

    offered: str = "rgb"
    depth: int = 8
    float: bool = False

    @property
    def grey(self) -> bool:
        return self.offered in ("", "la")

    @property
    def alpha(self) -> bool:
        return "a" in self.offered

    def as_dict(self) -> dict[str, Any]:
        return {"offered": self.offered, "depth": self.depth, "float": self.float}


UNKNOWN = ChannelInfo()

_DEEP = {
    QImage.Format.Format_Grayscale16: (16, False),
    QImage.Format.Format_RGBA64: (16, False),
    QImage.Format.Format_RGBA64_Premultiplied: (16, False),
    QImage.Format.Format_RGBX64: (16, False),
    QImage.Format.Format_RGBA16FPx4: (16, True),
    QImage.Format.Format_RGBA16FPx4_Premultiplied: (16, True),
    QImage.Format.Format_RGBX16FPx4: (16, True),
    QImage.Format.Format_RGBA32FPx4: (32, True),
    QImage.Format.Format_RGBA32FPx4_Premultiplied: (32, True),
    QImage.Format.Format_RGBX32FPx4: (32, True),
}
_GREY = {
    QImage.Format.Format_Grayscale8,
    QImage.Format.Format_Grayscale16,
    QImage.Format.Format_Mono,
    QImage.Format.Format_MonoLSB,
}


def probe(path: str) -> ChannelInfo:
    """The channels ``path`` offers (a plain path or a container member), from its
    header; :data:`UNKNOWN` (colour, opaque) when it cannot be read."""
    try:
        head = _head(path)
    except Exception:
        return UNKNOWN
    if head.startswith(_PNG):
        return _png(head) or UNKNOWN
    raw = QByteArray(head)
    buf = QBuffer(raw)
    buf.open(QBuffer.OpenModeFlag.ReadOnly)
    try:
        reader = QImageReader(buf)
        # a broken header: the size is unknown and the format is whatever the handler had
        # (the JPEG one returns an uninitialised value)
        fmt = reader.imageFormat() if reader.size().isValid() else QImage.Format.Format_Invalid
    finally:
        buf.close()
    return _of_format(fmt)


def _head(path: str) -> bytes:
    from specto_core.containers import open_source

    with open_source(path) as fh:
        return fh.read(_HEAD)


def _png(head: bytes) -> ChannelInfo | None:
    """IHDR's bit depth and colour type; a ``tRNS`` chunk before IDAT adds alpha."""
    if len(head) < 33 or head[12:16] != b"IHDR":
        return None
    depth, colour = head[24], head[25]
    trns, pos = False, 8
    while pos + 8 <= len(head):
        length, tag = struct.unpack(">I4s", head[pos : pos + 8])
        if tag in (b"IDAT", b"IEND"):
            break
        trns = trns or tag == b"tRNS"
        pos += 12 + length
    grey = colour in (0, 4)
    alpha = colour in (4, 6) or trns
    offered = ("la" if alpha else "") if grey else ("rgba" if alpha else "rgb")
    return ChannelInfo(offered, 16 if depth == 16 else 8)


def _of_format(fmt: QImage.Format) -> ChannelInfo:
    if not 0 < fmt.value < QImage.Format.NImageFormats.value:
        return UNKNOWN
    depth, is_float = _DEEP.get(fmt, (8, False))
    if fmt in _GREY:
        return ChannelInfo("", depth, is_float)
    alpha = QImage.toPixelFormat(fmt).alphaUsage() == QPixelFormat.AlphaUsage.UsesAlpha
    return ChannelInfo("rgba" if alpha else "rgb", depth, is_float)


class ChannelView:
    """What one image view shows of its image's channels: the param (``params.channels``
    as last set or toggled) read against :attr:`info` (the shown file's channels)."""

    def __init__(self, info: ChannelInfo = UNKNOWN, param: str = "") -> None:
        self.info = info
        self.param = ""
        self.set_param(param)

    # -- the param ---------------------------------------------------------------------
    @staticmethod
    def letters(text: Any) -> str:
        """``text``'s channel letters in footer order (case and the rest ignored)."""
        low = str(text or "").lower()
        return "".join(c for c in ORDER if c in low)

    def _on_image(self, letters: str) -> str:
        """``letters`` as this image has them: grey's r/g/b are its l, colour's l is its
        rgb, the ones it lacks drop out."""
        grey = self.info.grey
        out = set()
        for c in letters:
            if c in COLOUR:
                out.update("l" if grey else ("rgb" if c == "l" else c))
            elif c == "a":
                out.add("a")
        return "".join(c for c in ORDER if c in out and c in self.info.offered)

    def set_param(self, param: Any) -> str:
        """The agent's or the document's ``params.channels`` (kept as written, so it
        means the same on the next image)."""
        self.param = self.letters(param)
        return self.param

    def _write(self, letters: str) -> str:
        """A gesture's param: every channel the image has is the normal view, ``""``."""
        return self.set_param("" if self._on_image(letters) == self.info.offered else letters)

    # -- what shows --------------------------------------------------------------------
    @property
    def shown(self) -> str:
        return self._on_image(self.param) or self.info.offered

    @property
    def mode(self) -> str:
        """``normal`` (every channel), ``grey`` (one colour channel), ``alpha`` (A alone)
        or ``mix`` (several colour channels)."""
        shown = self.shown
        if shown == self.info.offered:
            return "normal"
        colour = [c for c in shown if c in COLOUR]
        if not colour:
            return "alpha"
        return "grey" if len(colour) == 1 else "mix"

    # -- the user's gestures (footer toggles, keys, the menu) ----------------------------
    def click(self, channel: str) -> str:
        """Solo ``channel``; the soloed one again shows every channel. Returns the param."""
        one = self._on_image(self.letters(channel))
        if len(one) != 1:
            return self.param
        return self._write("" if self.shown == one else one)

    def mix(self, channel: str) -> str:
        """Toggle ``channel`` within the shown mix (Ctrl/Alt+click, the menu); none left shows
        every channel. Returns the param."""
        one = self._on_image(self.letters(channel))
        if len(one) != 1:
            return self.param
        shown = set(self.shown) ^ {one}
        return self._write("".join(c for c in ORDER if c in shown))

    # -- the shader ---------------------------------------------------------------------
    def uniforms(self) -> dict[str, Any]:
        """``active``: the shader draws (the plain image draws itself otherwise); ``mask``
        the colour channels kept (and A); ``grey`` 1 with ``pick`` the channel shown as
        greyscale; ``checker`` 1 composites over the checkerboard by the alpha."""
        shown, mode = self.shown, self.mode
        colour = [c for c in shown if c in COLOUR]
        mask = [
            1.0 if c in shown or ("l" in shown and c != "a") else 0.0 for c in ("r", "g", "b", "a")
        ]
        pick = [0.0, 0.0, 0.0, 0.0]
        if mode == "grey":
            pick["rgb".index(colour[0]) if colour[0] != "l" else 0] = 1.0
        elif mode == "alpha":
            pick[3] = 1.0
        checker = 1.0 if "a" in shown and colour else 0.0
        return {
            "active": mode != "normal" or checker > 0,
            "mask": mask,
            "pick": pick,
            "grey": 1.0 if mode in ("grey", "alpha") else 0.0,
            "checker": checker,
        }

    def snapshot(self) -> dict[str, Any]:
        return {**self.info.as_dict(), "param": self.param, "shown": self.shown, "mode": self.mode}
