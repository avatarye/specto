"""Benchmark for M1 exit criterion 6 (kept in M1.5): 10k thumbnails scroll at 60 Hz with
decoding in workers.

    uv run python tools/bench_gallery.py [--min-items 10000] [--seconds 30] [--warmup 3]
        [--workers N] [--screen I] [--tile-px 96] [--rows-per-second 8]
        [--cam-a DIR] [--cam-b DIR] [--out out/verify/m1_5/bench_gallery.json]
        [--mixed DIR]

One native gallery over >= 10,000 items (two image folders, by default the test data's
``runs/run-001/cam-a`` and ``cam-b``, read-only, repeated as extra intake sources until
the count is reached; ``--cam-a`` / ``--cam-b`` name any folders). The tile width is set
to --tile-px (the zoom level is the thumbnail size; columns reflow to the width), and
the view auto-scrolls through the grid at --rows-per-second (the wheel scrolls), so new
thumbnails are requested continuously while the worker pool decodes them. Measured over the run:

- window frame rate: QQuickWindow.frameSwapped (queued to the GUI thread), count / s
- item frame times: GalleryItem.frames and syncMs (render-thread sync cost per frame)
- GUI-thread idle: 100 - time.thread_time() / wall; plus 4 ms timer scheduling latency
- decode throughput: thumbnails pushed into the item per second, pool size, respawns

Writes JSON to --out and a markdown summary next to it. Item lists go to the platform
cache dir (SPECTO_CACHE_DIR); nothing is written into the image folders.

``--mixed DIR`` measures a folder of every kind instead (tools/make_mixed_gallery.py
writes one: images, videos, pdfs, markdown, json, html, 3D), every file one item.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _gallery_app as ga  # noqa: E402  (sets QSG_RHI_BACKEND before Qt loads)
from PySide6.QtCore import QElapsedTimer, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

#: the two image folders, read only: the test data's frame series unless ``--cam-a`` /
#: ``--cam-b`` name others (a folder of thousands of images reaches the count sooner)
CAM_A = ga.CAM_A
CAM_B = ga.CAM_B
DEFAULT_OUT = ROOT / "out" / "verify" / "m1_5" / "bench_gallery.json"  # git-ignored


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, max(0, round(p * (len(s) - 1))))
    return s[k]


def count_images(d: Path) -> int:
    return sum(1 for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def choose_roots(cam_a: Path, cam_b: Path, min_items: int) -> tuple[list[str], int]:
    pool = [(d, count_images(d)) for d in (cam_a, cam_b) if d.is_dir()]
    if not pool:
        sys.exit(f"[ERROR] no image folders at {cam_a} or {cam_b}; run tools/make_testdata.py")
    roots: list[str] = []
    total = 0
    while total < min_items:
        for d, n in pool:
            roots.append(str(d))
            total += n
            if total >= min_items:
                break
    return roots, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cam-a", default=str(CAM_A))
    ap.add_argument("--cam-b", default=str(CAM_B))
    ap.add_argument("--min-items", type=int, default=10_000)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--screen", type=int, default=None)
    ap.add_argument("--tile-px", type=float, default=96.0, help="target tile width while scrolling")
    ap.add_argument("--rows-per-second", type=float, default=8.0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--snapshot", default="", help="PNG of the window at the end")
    ap.add_argument("--stay", action="store_true", help="keep the window open after measuring")
    ap.add_argument("--mixed", default="", help="a mixed-kind folder instead of the frames")
    args = ap.parse_args()

    ga.require_plugin()
    ga.raise_timer_resolution()
    app = QGuiApplication(sys.argv)
    if args.mixed:
        roots = [str(Path(args.mixed))]
        expected = sum(1 for p in Path(args.mixed).iterdir() if p.is_file())
    else:
        roots, expected = choose_roots(Path(args.cam_a), Path(args.cam_b), args.min_items)
    print(f"[OK] {len(roots)} intake sources, ~{expected} items")

    # a mixed folder is read as a project (its parent): intakers take project files
    engine, pool = ga.make_engine(args.workers, Path(args.mixed).parent if args.mixed else None)
    print(f"[OK] pool: {len(pool.workers)} workers")
    t_scan = time.perf_counter()
    views = ga.build_canvas(
        engine, pool, [("bench", roots)], link_selection=False, screener=[] if args.mixed else None
    )
    scan_s = time.perf_counter() - t_scan
    vid = views[0]
    count = len(engine.links.input_of(vid, "data").items)
    if count < args.min_items:
        print(f"[WARN] only {count} items (< {args.min_items})")

    screen = ga.pick_screen(app, args.screen)
    win = ga.GalleryWindow(
        app,
        engine,
        pool,
        views,
        title="Specto gallery bench",
        screen=screen,
        size=(1600, 900),
    )
    win.window.show()
    host = win.hosts[vid]
    item = win.gallery_item(vid)
    # the zoom level is the tile size; the item derives the columns from its width
    item.setProperty("captionMode", 1)
    item.setProperty("tileWidth", float(args.tile_px))
    columns = int(item.property("effectiveColumns"))
    rows = int(item.property("rows"))

    # -- metrics -------------------------------------------------------------------
    swaps = {"n": 0, "last": 0.0}
    swap_gaps: list[float] = []
    clock = QElapsedTimer()
    clock.start()

    def on_swapped() -> None:
        now = clock.nsecsElapsed() / 1e6
        if swaps["last"]:
            swap_gaps.append(now - swaps["last"])
        swaps["last"] = now
        swaps["n"] += 1

    win.window.frameSwapped.connect(on_swapped, Qt.ConnectionType.QueuedConnection)

    latency: list[float] = []
    lat = {"last": 0.0}
    LAT_MS = 4

    def on_latency() -> None:
        now = clock.nsecsElapsed() / 1e6
        if lat["last"]:
            latency.append(max(0.0, now - lat["last"] - LAT_MS))
        lat["last"] = now

    lat_timer = QTimer()
    lat_timer.setTimerType(Qt.TimerType.PreciseTimer)
    lat_timer.setInterval(LAT_MS)
    lat_timer.timeout.connect(on_latency)
    lat_timer.start()

    # -- auto scroll -------------------------------------------------------------------
    pan = {"cy": 0.0, "dir": 1.0, "last": 0.0, "bounces": 0}

    def max_rows() -> float:
        pitch = float(item.property("rowPitch"))
        return float(item.property("maxScroll")) / pitch if pitch > 0 else 0.0

    def start_pan() -> None:
        pan["cy"] = 0.0
        item.setProperty("scrollRows", 0.0)
        pan["last"] = clock.nsecsElapsed() / 1e9

    def on_pan() -> None:
        now = clock.nsecsElapsed() / 1e9
        dt = now - pan["last"]
        pan["last"] = now
        top = max_rows()
        cy = pan["cy"] + pan["dir"] * args.rows_per_second * dt
        if cy > top:
            cy, pan["dir"] = top, -1.0
            pan["bounces"] += 1
        elif cy < 0:
            cy, pan["dir"] = 0.0, 1.0
            pan["bounces"] += 1
        pan["cy"] = cy
        item.setProperty("scrollRows", cy)

    pan_timer = QTimer()
    pan_timer.setTimerType(Qt.TimerType.PreciseTimer)
    pan_timer.setInterval(16)
    pan_timer.timeout.connect(on_pan)

    # -- phases ----------------------------------------------------------------------
    samples: list[dict] = []
    base = {"swaps": 0, "frames": 0, "decoded": 0, "uploads": 0, "t": 0.0}
    cpu0 = {"cpu": 0.0, "wall": 0.0}
    result: dict = {}

    def sample() -> None:
        samples.append(
            {
                "t": round(time.perf_counter() - cpu0["wall"], 2),
                "swaps": swaps["n"],
                "frames": item.property("frames"),
                "syncMs": round(float(item.property("syncMs")), 3),
                "decoded": host.decoded,
                "inflight": host.inflight,
                "uploads": item.property("uploads"),
                "evictions": item.property("evictions"),
                "residentFull": item.property("residentFull"),
                "residentMini": item.property("residentMini"),
                "residentLarge": item.property("residentLarge"),
                "staged": item.property("staged"),
                "quads": item.property("quads"),
                "captions": item.property("captions"),
                "centerY": round(pan["cy"], 2),
            }
        )
        s = samples[-1]
        if len(samples) >= 2:
            p = samples[-2]
            swap_s, item_s = s["swaps"] - p["swaps"], s["frames"] - p["frames"]
            dec_s = s["decoded"] - p["decoded"]
            print(
                f"[..] {s['t']:5.1f} s  swap {swap_s:3d}/s  item {item_s:3d}/s  "
                f"sync {s['syncMs']:.2f} ms  decoded {dec_s:4d}/s "
                f"(total {s['decoded']}, inflight {s['inflight']})  "
                f"resident full {s['residentFull']} mini {s['residentMini']}  "
                f"row {s['centerY']:.0f}/{rows}"
            )

    sample_timer = QTimer()
    sample_timer.setInterval(1000)
    sample_timer.timeout.connect(sample)

    def start_measure() -> None:
        item.resetStats()
        swaps["n"] = 0
        swap_gaps.clear()
        latency.clear()
        samples.clear()
        base.update(
            swaps=0,
            frames=0,
            decoded=host.decoded,
            uploads=item.property("uploads"),
        )
        cpu0["cpu"] = time.thread_time()
        cpu0["wall"] = time.perf_counter()
        sample()
        print(f"[OK] measuring for {args.seconds:.0f} s")
        QTimer.singleShot(int(args.seconds * 1000), finish)

    def finish() -> None:
        sample_timer.stop()
        pan_timer.stop()
        lat_timer.stop()
        wall = time.perf_counter() - cpu0["wall"]
        cpu = time.thread_time() - cpu0["cpu"]
        scr = win.window.screen()
        frames = int(item.property("frames"))
        decoded = host.decoded - base["decoded"]
        uploads = int(item.property("uploads"))
        swapped_fps = swaps["n"] / wall
        idle = 100 - 100 * cpu / wall
        criterion = {
            "items_ge_10k": count >= 10_000,
            "swapped_fps_ge_58": swapped_fps >= 58.0,
            "gui_idle_ge_80": idle >= 80.0,
            "decoding_during_pan": decoded > 0 and host.inflight >= 0,
        }
        result.update(
            {
                "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                "machine": {
                    "platform": platform.platform(),
                    "cpu_count": os.cpu_count(),
                    "pyside6": __import__("PySide6").__version__,
                    "qt": __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion(),
                    "graphics_api": str(win.window.rendererInterface().graphicsApi()).rsplit(
                        ".", 1
                    )[-1],
                    "screen": {
                        "name": scr.name(),
                        "refreshHz": round(scr.refreshRate(), 1),
                        "dpr": scr.devicePixelRatio(),
                        "size": f"{scr.size().width()}x{scr.size().height()}",
                        "window": f"{win.window.width()}x{win.window.height()}",
                    },
                },
                "items": count,
                "sources": roots,
                "scan_s": round(scan_s, 2),
                "pool": {"workers": len(pool.workers), "respawns": pool.respawns},
                "grid": {
                    "columns": columns,
                    "rows": rows,
                    "tile_px": round(float(item.property("cellWidth")), 1),
                    "cell_aspect": round(float(item.property("cellAspect")), 3),
                    "caption_px": float(item.property("captionHeight")),
                    "want_tier": int(item.property("wantTier")),
                },
                "pan": {
                    "rows_per_second": args.rows_per_second,
                    "bounces": pan["bounces"],
                    "rows_covered": round(args.rows_per_second * wall, 1),
                },
                "measure_s": round(wall, 2),
                "window": {
                    "swapped_frames": swaps["n"],
                    "swapped_fps": round(swapped_fps, 2),
                    "swap_gap_ms": {
                        "p50": round(percentile(swap_gaps, 0.5), 2),
                        "p95": round(percentile(swap_gaps, 0.95), 2),
                        "p99": round(percentile(swap_gaps, 0.99), 2),
                        "max": round(max(swap_gaps) if swap_gaps else 0.0, 2),
                    },
                },
                "item": {
                    "frames": frames,
                    "fps": round(frames / wall, 2),
                    "sync_ms_mean": round(float(item.property("syncMs")), 3),
                    "sync_ms_samples": [s["syncMs"] for s in samples],
                    "uploads": uploads,
                    "evictions": item.property("evictions"),
                    "resident_full": item.property("residentFull"),
                    "resident_mini": item.property("residentMini"),
                    "resident_large": item.property("residentLarge"),
                    "quads_last": item.property("quads"),
                    "draw_calls_last": item.property("drawCalls"),
                    "captions_last": item.property("captions"),
                },
                "gui_thread": {
                    "cpu_busy_pct": round(100 * cpu / wall, 2),
                    "idle_pct": round(idle, 2),
                    "timer_latency_ms": {
                        "samples": len(latency),
                        "p50": round(percentile(latency, 0.5), 3),
                        "p95": round(percentile(latency, 0.95), 3),
                        "p99": round(percentile(latency, 0.99), 3),
                        "max": round(max(latency) if latency else 0.0, 3),
                        "mean": round(statistics.fmean(latency), 3) if latency else 0.0,
                    },
                },
                "decode": {
                    "thumbnails": decoded,
                    "per_second": round(decoded / wall, 1),
                    "total_decoded": host.decoded,
                    "failed": host.failed,
                    "bytes_through_shm": host.pushed_bytes,
                    "mb_per_second": round(host.pushed_bytes / 1e6 / wall, 1),
                },
                "samples": samples,
                "criterion": criterion,
                "pass": all(criterion.values()),
            }
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        md = args.out.with_suffix(".md")
        md.write_text(summary_md(result), encoding="utf-8")
        print(f"[OK] wrote {args.out} and {md}")
        print(
            f"[{'OK' if result['pass'] else 'WARN'}] {count} items: window {swapped_fps:.1f} fps, "
            f"item {frames / wall:.1f} fps, sync {result['item']['sync_ms_mean']:.2f} ms, GUI idle "
            f"{idle:.1f} %, decode {decoded / wall:.0f} thumbs/s ({host.decoded} total, "
            f"{host.failed} failed), respawns {pool.respawns}"
        )
        if args.snapshot:
            img = win.window.grabWindow()
            Path(args.snapshot).parent.mkdir(parents=True, exist_ok=True)
            img.save(args.snapshot)
            print(f"[OK] wrote {args.snapshot}")
        if not args.stay:
            win.close()
            app.quit()

    def go() -> None:
        start_pan()
        pan_timer.start()
        sample_timer.start()
        print(f"[OK] {count} items, {columns} columns x {rows} rows; warmup {args.warmup:.0f} s")
        QTimer.singleShot(int(args.warmup * 1000), start_measure)

    QTimer.singleShot(300, go)
    app.exec()
    return 0 if result.get("pass") else 1


def summary_md(r: dict) -> str:
    w, it, g, d = r["window"], r["item"], r["gui_thread"], r["decode"]
    m = r["machine"]
    sg, tl = w["swap_gap_ms"], g["timer_latency_ms"]
    swap_q = f"{sg['p50']} / {sg['p95']} / {sg['p99']} / {sg['max']} ms"
    lat_q = f"{tl['p50']} / {tl['p95']} / {tl['p99']} / {tl['max']} ms"
    decode_s = (
        f"{d['per_second']} thumbnails/s ({d['thumbnails']} in the window, "
        f"{d['total_decoded']} total, {d['failed']} failed), "
        f"{d['mb_per_second']} MB/s RGBA through shared memory"
    )
    gr, sc, po = r["grid"], m["screen"], r["pool"]
    resident = f"{it['resident_full']} / {it['resident_mini']} / {it['resident_large']}"
    grid = (
        f"{gr['tile_px']:.0f} px tiles with captions, {gr['columns']} columns x {gr['rows']} rows"
    )
    screen = f"{sc['size']} @ {sc['refreshHz']} Hz, window {sc['window']}"
    crit = "\n".join(f"| {k} | {'yes' if v else 'NO'} |" for k, v in r["criterion"].items())
    verdict = "PASS" if r["pass"] else "FAIL"
    return f"""# Gallery benchmark (M1 exit criterion 6, M1.5 scroll model)

`tools/bench_gallery.py`: one native gallery over {r["items"]} items, auto-scrolling at
{r["pan"]["rows_per_second"]} rows/s ({grid}) for {r["measure_s"]} s while
{po["workers"]} worker processes decode thumbnails. Screen {screen},
{m["graphics_api"]} RHI, PySide6 {m["pyside6"]} / Qt {m["qt"]}.

| Measure | Value |
|---|---|
| Window frame rate (frameSwapped) | {w["swapped_fps"]} fps ({w["swapped_frames"]} frames) |
| Swap interval p50 / p95 / p99 / max | {swap_q} |
| Item frames (render-thread sync) | {it["fps"]} fps, sync {it["sync_ms_mean"]} ms mean |
| Uploads / evictions during the run | {it["uploads"]} / {it["evictions"]} |
| Resident tiles at the end (full / mini / large) | {resident} |
| Captions drawn in the last frame | {it["captions_last"]} |
| GUI thread idle | {g["idle_pct"]} % (busy {g["cpu_busy_pct"]} %) |
| 4 ms timer latency p50 / p95 / p99 / max | {lat_q} |
| Decode throughput | {decode_s} |
| Pool | {po["workers"]} workers, {po["respawns"]} respawns; intake scan {r["scan_s"]} s |

| Criterion | Met |
|---|---|
{crit}

**Result: {verdict}.** Raw numbers and per-second samples: `bench_gallery.json`.
"""


if __name__ == "__main__":
    sys.exit(main())
