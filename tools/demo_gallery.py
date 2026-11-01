"""Demo: two linked native galleries over testdata/runs/run-001/cam-a and cam-b, with the
real engine, the intaker links and the worker pool, no shell.

    uv run python tools/demo_gallery.py [--cam-a DIR] [--cam-b DIR] [--workers N]
                                        [--screen I] [--seconds S] [--snapshot PNG]
                                        [--select KEY] [--tile PX]

What the gallery does with input (the M1.5 interaction map):

    wheel                 scroll (eased); a touchpad's pixel deltas scroll directly
    Ctrl+wheel, pinch     zoom: one column per notch, the tile under the cursor stays put
    drag                  pan, with inertia on release
    click                 select; the other gallery follows (a two-way follow link on
                          the selection ports)
    arrows, PgUp/PgDn     move the selection (and reveal it); Home/End first/last
    Space                 quick look (Esc closes)
    + / -                 zoom in / out around the selection (or the centre)
    hover ~400 ms         a larger preview near the cursor

Plain ASCII output; --seconds closes the window by itself (CI, screenshots).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _gallery_app as ga  # noqa: E402  (sets QSG_RHI_BACKEND before Qt loads)
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cam-a", default=str(ga.CAM_A))
    ap.add_argument("--cam-b", default=str(ga.CAM_B))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--screen", type=int, default=None)
    ap.add_argument("--seconds", type=float, default=0, help="auto-close after S seconds")
    ap.add_argument("--snapshot", default="", help="save a PNG of the window before closing")
    ap.add_argument("--select", default="", help="select this key in the cam-a gallery at start")
    ap.add_argument("--tile", type=int, default=0, help="initial thumbnail width px (0: 192)")
    args = ap.parse_args()

    for d in (args.cam_a, args.cam_b):
        if not Path(d).is_dir():
            sys.exit(f"[ERROR] not a directory: {d}")
    ga.require_plugin()
    ga.raise_timer_resolution()
    app = QGuiApplication(sys.argv)

    engine, pool = ga.make_engine(args.workers)
    print(f"[OK] pool: {len(pool.workers)} workers")
    views = ga.build_canvas(
        engine, pool, [("cam-a", [args.cam_a]), ("cam-b", [args.cam_b])], tile=args.tile
    )
    win = ga.GalleryWindow(
        app,
        engine,
        pool,
        views,
        title="Specto gallery demo: cam-a | cam-b",
        screen=ga.pick_screen(app, args.screen),
    )
    win.window.show()
    first = win.hosts[views[0]]
    t0 = time.perf_counter()

    if args.select:

        def do_select() -> None:
            i = first.select_key(args.select)
            if i < 0:
                print(f"[WARN] key {args.select!r} not in {views[0]}")
                return
            print(f"[OK] selected {args.select} (index {i}) in {views[0]}")

        QTimer.singleShot(400, do_select)

    def status() -> None:
        parts = []
        for v in list(win.hosts):
            h = win.hosts[v]
            parts.append(f"{v}: {h.decoded}/{len(h.items)} decoded, {h.inflight} jobs")
        print(f"[..] {time.perf_counter() - t0:5.1f} s  " + "; ".join(parts))

    status_timer = QTimer()
    status_timer.setInterval(1000)
    status_timer.timeout.connect(status)
    status_timer.start()

    def finish() -> None:
        status_timer.stop()
        for v in list(win.hosts):
            snap = win.registry.snapshot(v)
            keys = snap["visible"]["keys"]
            shown = keys[:3] + (["..."] if len(keys) > 6 else []) + keys[-3:] if keys else []
            print(
                f"[OK] snapshot {v}: health={snap['health']['status']} "
                f"selected={snap['selected']['key']} visible={snap['visible']['first']}.."
                f"{snap['visible']['last']} {shown} columns={snap['view']['columns']} "
                f"aspect={snap['view']['cell_aspect']:.2f} "
                f"decoded={snap['decode']['decoded']} failed={snap['decode']['failed']}"
            )
        if args.snapshot:
            img = win.window.grabWindow()
            Path(args.snapshot).parent.mkdir(parents=True, exist_ok=True)
            img.save(args.snapshot)
            print(f"[OK] wrote {args.snapshot}")
        hist = engine.live.history
        print(
            f"[OK] journal: {len(hist)} submits; last: {json.dumps([r.label for r in hist[-4:]])}"
        )
        win.close()
        app.quit()

    if args.seconds > 0:
        QTimer.singleShot(int(args.seconds * 1000), finish)
    else:
        win.window.closing.connect(lambda _e: finish())
    rc = app.exec()
    return rc


if __name__ == "__main__":
    sys.exit(main())
