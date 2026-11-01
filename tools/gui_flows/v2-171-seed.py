"""Seed the project folder for the v2 #171 flow (image channel toggles): four 320 x 200
PNGs under images/, one per channel layout, each with its own picture in every channel
so a solo shows something different: red rises left to right, green top to bottom, blue
is a disc; the alpha fades out to the right (01 and 03).

- 01_rgba.png  RGBA (the toggles R G B A, transparency over the checkerboard)
- 02_rgb.png   RGB (R G B, no A)
- 03_la.png    grey + alpha (L A; written by hand: Qt saves no grey+alpha PNG)
- 04_l.png     grey (no toggles)

No ``.specto/``: the app initialises the project on open.

    uv run python tools/gui_flows/v2-171-seed.py <folder>
"""

from __future__ import annotations

import argparse
import shutil
import struct
import zlib
from pathlib import Path

from PySide6.QtGui import QColor, QImage

W, H = 320, 200


def channels(x: int, y: int) -> tuple[int, int, int, int]:
    r = 255 * x // (W - 1)
    g = 255 * y // (H - 1)
    b = 230 if (x - 110) ** 2 + (y - 100) ** 2 < 70**2 else 20
    a = 255 if x < W // 2 else 255 - 255 * (x - W // 2) // (W // 2)
    return r, g, b, a


def colour_png(path: Path, fmt: QImage.Format, alpha: bool) -> None:
    img = QImage(W, H, fmt)
    for y in range(H):
        for x in range(W):
            r, g, b, a = channels(x, y)
            img.setPixelColor(x, y, QColor(r, g, b, a if alpha else 255))
    assert img.save(str(path)), path


def grey_png(path: Path) -> None:
    img = QImage(W, H, QImage.Format.Format_Grayscale8)
    for y in range(H):
        for x in range(W):
            r, g, b, _ = channels(x, y)
            v = (r + g + b) // 3
            img.setPixelColor(x, y, QColor(v, v, v))
    assert img.save(str(path)), path


def grey_alpha_png(path: Path) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = []
    for y in range(H):
        row = bytearray(b"\x00")
        for x in range(W):
            r, g, b, a = channels(x, y)
            row += bytes(((r + g + b) // 3, a))
        rows.append(bytes(row))
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 4, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    images = root / "images"
    images.mkdir(parents=True)
    colour_png(images / "01_rgba.png", QImage.Format.Format_ARGB32, alpha=True)
    colour_png(images / "02_rgb.png", QImage.Format.Format_RGB888, alpha=False)
    grey_alpha_png(images / "03_la.png")
    grey_png(images / "04_l.png")
    print(f"[OK] seeded {root}: images/01_rgba.png 02_rgb.png 03_la.png 04_l.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
