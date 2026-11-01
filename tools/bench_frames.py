"""Bench the frame path (#136): the stub frame source writes frames into the ring and a
FrameItem, rendered offscreen with OpenGL (no window on screen), uploads them.

    uv run python tools/bench_frames.py [--width 1920] [--height 1080] [--seconds 5]
        [--source-fps 0 60] [--render-hz 0]

For each source rate it reports the figures below. Rate 0 is a tear stress, not a
throughput figure: the stub then rewrites the ring thousands of times a second while
holding the GIL, so its wake thread starves and few frames are shown, but every one
shown must be whole (torn counts the copies the item dropped).

- source fps: frames the stub published per second;
- shown fps: frames the item uploaded per second (each one a whole, untorn frame);
- upload ms: the item's CPU time per upload (copy into the unpack buffer +
  glTexSubImage2D), mean / p50 / p95;
- frame ms: one Qt Quick frame that uploaded (sync + render + glFinish), which includes
  the GPU transfer the upload queued, mean / p95;
- latency ms: wall time from the source stamping a frame to its upload, mean / p95;
- torn: copies the item dropped because the source rewrote the slot meanwhile.

``--render-hz 0`` renders back to back (the path's ceiling); 60 paces it like a display.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "specto-app" / "src"))

from specto_app.frames.offscreen import OffscreenQuick, prepare_environment  # noqa: E402

prepare_environment()

from PySide6.QtGui import QGuiApplication  # noqa: E402

from specto_app.frames.control import FrameSourceProcess  # noqa: E402
from specto_app.frames.ring import RingReader  # noqa: E402

QML = "import QtQuick\nimport Specto.Native\nFrameItem { anchors.fill: parent }"


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def run(app, quick: OffscreenQuick, item, args, source_fps: float) -> dict:
    w, h = args.width, args.height
    source = FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.frames.stub_source",
         "--width", str(w), "--height", str(h), "--fps", str(source_fps)]
    )  # fmt: skip
    try:
        ready = source.wait_event("ready")
        item.setProperty("ring", ready["ring"])
        item.setProperty("wake", ready["wake"])
        gl = quick.context.functions()
        # warm up: the first frames allocate the texture and the unpack buffer
        deadline = time.monotonic() + 5
        while int(item.property("frames") or 0) < 5 and time.monotonic() < deadline:
            app.processEvents()
            quick.render()
        reader = RingReader.open(ready["ring"])
        frames0 = int(item.property("frames"))
        torn0 = int(item.property("tornFrames"))
        seq0 = reader.latest().seq
        uploads, frame_ms, latency = [], [], []
        last = frames0
        period = 1.0 / args.render_hz if args.render_hz > 0 else 0.0
        start = time.perf_counter()
        next_at = start
        while time.perf_counter() - start < args.seconds:
            app.processEvents()
            t0 = time.perf_counter()
            quick.render()
            gl.glFinish()
            ms = (time.perf_counter() - t0) * 1000
            app.processEvents()   # the item's stats, posted by the render phase
            n = int(item.property("frames"))
            if n != last:
                last = n
                frame_ms.append(ms)
                uploads.append(float(item.property("uploadMs")))
                latency.append((time.time() - float(item.property("frameTimestamp"))) * 1000)
            if period:
                next_at += period
                delay = next_at - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
        elapsed = time.perf_counter() - start
        seq1 = reader.latest().seq
        reader.close()
        return {
            "source_fps": (seq1 - seq0) / elapsed,
            "shown_fps": (last - frames0) / elapsed,
            "upload": (statistics.fmean(uploads), pct(uploads, 0.5), pct(uploads, 0.95)),
            "frame": (statistics.fmean(frame_ms), pct(frame_ms, 0.95)),
            "latency": (statistics.fmean(latency), pct(latency, 0.95)) if latency else (0, 0),
            "torn": int(item.property("tornFrames")) - torn0,
        }
    finally:
        source.close()
        item.setProperty("wake", "")
        item.setProperty("ring", "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--source-fps", type=float, nargs="+", default=[60.0, 120.0, 240.0, 0.0])
    ap.add_argument("--render-hz", type=float, default=0.0)
    args = ap.parse_args()

    app = QGuiApplication([])
    quick = OffscreenQuick(args.width, args.height)
    item = quick.load(QML)
    renderer = quick.context.functions().glGetString(0x1F01)   # GL_RENDERER
    print(f"[OK] {args.width}x{args.height} RGBA8, {args.seconds:g} s per run, "
          f"render {'back to back' if not args.render_hz else f'{args.render_hz:g} Hz'}, {renderer}")
    for fps in args.source_fps:
        r = run(app, quick, item, args, fps)
        label = "max" if fps <= 0 else f"{fps:g}"
        print(
            f"source {label:>4} fps | source {r['source_fps']:6.1f} fps | shown {r['shown_fps']:6.1f} fps"
            f" | upload {r['upload'][0]:.2f} / {r['upload'][1]:.2f} / {r['upload'][2]:.2f} ms"
            f" | frame {r['frame'][0]:.2f} / p95 {r['frame'][1]:.2f} ms"
            f" | latency {r['latency'][0]:.1f} / p95 {r['latency'][1]:.1f} ms | torn {r['torn']}"
        )
    quick.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
