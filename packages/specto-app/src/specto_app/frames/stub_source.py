"""A frame source that serves a moving test pattern (#136): the stand-in for ovrtx and
Storm in seam-2 tests and the bench. Standard library only.

    python -m specto_app.frames.stub_source [--width 640] [--height 360] [--fps 60]
        [--name specto-frames-<pid>] [--max-width W] [--max-height H]

The pattern, for a frame of ``w x h`` with number ``seq``:

- rows ``[0, h // 8)``: a grey (64, 64, 64) band with a white bar ``max(1, w // 16)``
  wide whose left edge is at ``(seq * max(1, w // 64)) % w``;
- below the band, quadrants split at ``w // 2`` and ``h // 2``: red | green over
  blue | yellow, all opaque.

``--fps 0`` serves frames as fast as the ring takes them (the bench). The control
methods are the protocol's lifecycle set (see :mod:`.control`); ``set_camera`` and
``set_time`` are acknowledged and remembered, nothing more.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .control import ControlServer
from .server import FrameServer

GREY = bytes((64, 64, 64, 255))
WHITE = bytes((255, 255, 255, 255))
RED = bytes((255, 0, 0, 255))
GREEN = bytes((0, 255, 0, 255))
BLUE = bytes((0, 0, 255, 255))
YELLOW = bytes((255, 255, 0, 255))


def band_rows(height: int) -> int:
    return height // 8


def bar_span(seq: int, width: int) -> tuple[int, int]:
    """The bar's [left, right) columns in frame ``seq``, clipped to the frame."""
    left = (seq * max(1, width // 64)) % width
    return left, min(width, left + max(1, width // 16))


def base_frame(width: int, height: int) -> bytes:
    """The pattern without the bar."""
    half_w, half_h, band = width // 2, height // 2, band_rows(height)
    grey = GREY * width
    top = RED * half_w + GREEN * (width - half_w)
    bottom = BLUE * half_w + YELLOW * (width - half_w)
    return grey * band + top * (half_h - band) + bottom * (height - half_h)


class StubSource:
    def __init__(self, args: argparse.Namespace) -> None:
        self.width, self.height = args.width, args.height
        self.fps = args.fps
        self.paused = False
        self.camera: dict = {}
        self.time = 0.0
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._base: tuple[int, int, bytes] | None = None
        self.control = ControlServer(self.handle, on_eof=self.stop.set)
        self.frames = FrameServer(
            args.name or f"specto-frames-{os.getpid()}",
            args.max_width or args.width,
            args.max_height or args.height,
            on_ring=lambda name: self.control.emit("ring", name=name),
        )

    def handle(self, method: str, params: dict) -> dict:
        if method == "resize":
            w, h = int(params["width"]), int(params["height"])
            if w < 1 or h < 1:
                raise ValueError(f"bad size {w}x{h}")
            with self._lock:
                self.width, self.height = w, h
            return {"width": w, "height": h}
        if method == "set_camera":
            self.camera = dict(params)
            return {}
        if method == "set_time":
            self.time = float(params.get("t", 0.0))
            return {}
        if method in ("pause", "resume"):
            self.paused = method == "pause"
            return {}
        if method == "shutdown":
            self.stop.set()
            return {}
        raise ValueError(f"unknown method {method!r}")

    def draw(self) -> None:
        with self._lock:
            w, h = self.width, self.height
        if self._base is None or self._base[:2] != (w, h):
            self._base = (w, h, base_frame(w, h))
        base = self._base[2]
        seq = self.frames.ring.seq + 1
        left, right = bar_span(seq, w)
        stride = w * 4
        bar = WHITE * (right - left)
        with self.frames.frame(w, h, timestamp=time.time()) as dst:
            dst[: len(base)] = base
            for row in range(band_rows(h)):
                at = row * stride
                dst[at + left * 4 : at + right * 4] = bar

    def run(self) -> int:
        self.control.start()
        self.draw()
        self.control.emit(
            "ready",
            ring=self.frames.ring_name,
            wake=self.frames.wake,
            width=self.width,
            height=self.height,
            pid=os.getpid(),
        )
        period = 1.0 / self.fps if self.fps > 0 else 0.0
        next_at = time.perf_counter()
        while not self.stop.is_set():
            if not self.paused:
                self.draw()
            if period:
                next_at += period
                delay = next_at - time.perf_counter()
                if delay > 0:  # not stop.wait: on Windows it rounds up to the 15.6 ms tick
                    time.sleep(delay)
                else:
                    next_at = time.perf_counter()
            else:  # unthrottled: still let the wake thread take the GIL
                time.sleep(0.01 if self.paused else 0)
        self.frames.close()
        self.control.wait_eof()
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Specto stub frame source (test pattern)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--name", default="")
    ap.add_argument("--max-width", type=int, default=0)
    ap.add_argument("--max-height", type=int, default=0)
    return StubSource(ap.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
