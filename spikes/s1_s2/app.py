"""Spike S1/S2 host: PySide6 Qt Quick window on the OpenGL RHI backend with a
QtWebEngine WebEngineView beside the native C++ TextureItem, plus GUI-thread
metrics gathered from Python.

    .venv/Scripts/python spikes/s1_s2/app.py --seconds 10 --mode 0 --web both

Modes: 0 native GL mesh (zero copy), 1 render-thread CPU upload of a 1080p RGBA
frame every frame, 2 Python pushes a shared-memory 1080p RGBA frame every frame.
Prints one JSON object on stdout at the end (and writes --out if given).
"""

from __future__ import annotations

import argparse
import contextlib
import faulthandler
import json
import os
import statistics
import sys
import time
from multiprocessing import shared_memory
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
BLOB = WEB / "blob.bin"
BLOB_BYTES = 32 * 1024 * 1024

sys.path.insert(0, str(HERE))
import scheme  # noqa: E402


def ensure_blob() -> None:
    if BLOB.exists() and BLOB.stat().st_size == BLOB_BYTES:
        return
    pattern = bytes(range(256)) * 4096  # 1 MiB where byte i == i & 0xff
    with BLOB.open("wb") as fh:
        for _ in range(BLOB_BYTES // len(pattern)):
            fh.write(pattern)


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, round(p * (len(s) - 1)))]


def main() -> int:
    faulthandler.enable()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=10.0, help="measurement window")
    ap.add_argument("--warmup", type=float, default=4.0)
    ap.add_argument("--mode", type=int, default=0, choices=[0, 1, 2])
    ap.add_argument("--web", default="both", choices=["both", "webgl", "monaco", "none"])
    ap.add_argument("--backend", default="opengl", choices=["opengl", "d3d11", "d3d12", "vulkan"])
    ap.add_argument("--triangles", type=int, default=200_000)
    ap.add_argument(
        "--tex-size", default="", help="WxH offscreen texture size (default: item size)"
    )
    ap.add_argument("--render-loop", default="", choices=["", "threaded", "basic"])
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--stay", action="store_true", help="do not quit after measuring")
    ap.add_argument("--screen", type=int, default=-1, help="move the window to this screen index")
    ap.add_argument("--swap-interval", type=int, default=-1, help="QSurfaceFormat swap interval")
    ap.add_argument(
        "--static", action="store_true", help="TextureItem renders once, no per-frame update"
    )
    ap.add_argument(
        "--drive",
        default="render",
        choices=["render", "gui"],
        help="render: the item re-arms frames from the render thread (QQuickWindow::update, the "
        "QQuickFramebufferObject pattern); gui: a QML FrameAnimation calls item.update() per frame",
    )
    ap.add_argument(
        "--no-timer-period",
        action="store_true",
        help="skip winmm timeBeginPeriod(1); shows the default 15.6 ms timer granularity",
    )
    args = ap.parse_args()

    os.environ["QSG_RHI_BACKEND"] = args.backend
    os.environ.setdefault("QSG_INFO", "1")
    if args.render_loop:
        os.environ["QSG_RENDER_LOOP"] = args.render_loop

    from PySide6.QtCore import QByteArray, QElapsedTimer, QMetaObject, Qt, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication, QSurfaceFormat
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtWebEngineCore import QWebEngineProfile
    from PySide6.QtWebEngineQuick import QtWebEngineQuick

    ensure_blob()
    if not args.no_timer_period and sys.platform == "win32":
        # Qt 6 no longer raises the Windows timer resolution itself; without this a
        # 4 ms QTimer fires every ~15.6 ms. Any sub-16 ms Python tick needs it.
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
    if args.swap_interval >= 0:
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setSwapInterval(args.swap_interval)
        QSurfaceFormat.setDefaultFormat(fmt)
    scheme.register_scheme()
    QtWebEngineQuick.initialize()
    app = QGuiApplication(sys.argv)

    served: list[dict] = []
    handler = scheme.make_handler(WEB, served)
    QWebEngineProfile.defaultProfile().installUrlSchemeHandler(scheme.SCHEME, handler)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(HERE / "qml"))
    props = {
        "texMode": args.mode,
        "triangles": args.triangles,
        "showWeb": args.web != "none",
        "continuous": not args.static and args.drive == "render",
        "driveGui": not args.static and args.drive == "gui",
        "webUrl": f"specto://app/index.html?web={args.web}",
    }
    engine.setInitialProperties(props)
    engine.load(QUrl.fromLocalFile(str(HERE / "main.qml")))
    if not engine.rootObjects():
        print("[ERROR] QML failed to load", file=sys.stderr)
        return 2
    win: QQuickWindow = engine.rootObjects()[0]
    screens = app.screens()
    if 0 <= args.screen < len(screens):
        win.setScreen(screens[args.screen])
        win.setPosition(screens[args.screen].geometry().topLeft())
    # Gotcha: a QML `property alias tex: tex` comes back as QVariant<TextureItem*>
    # and PySide6 has no converter for the unknown pointer type ("Can't find
    # converter for 'TextureItem*'"). findChild by objectName works: the item is
    # wrapped as its nearest known base class, QQuickItem.
    tex = win.findChild(QQuickItem, "tex")
    web = win.findChild(QQuickItem, "web")
    print(
        f"[OK] TextureItem wrapped by PySide6 as {type(tex).__name__}; metaobject class "
        f"{tex.metaObject().className()}",
        file=sys.stderr,
    )
    if args.tex_size:
        w, h = (int(v) for v in args.tex_size.lower().split("x"))
        tex.setProperty("textureWidth", w)
        tex.setProperty("textureHeight", h)

    # ---- metrics ---------------------------------------------------------------
    result: dict = {
        "args": vars(args) | {"out": str(args.out) if args.out else None},
        "pyside6": __import__("PySide6").__version__,
        "graphics_api": None,
        "screen": None,
        "swapped_frames": 0,
        "gui_cpu_busy_pct": None,
        "timer_latency_ms": {},
        "tex": {},
        "web": {},
        "pyframe": {},
        "served": None,
    }
    swaps = {"n": 0}

    def on_swapped() -> None:
        swaps["n"] += 1

    # frameSwapped is emitted on the render thread; queue it to the GUI thread so
    # no Python ever runs on the render thread.
    win.frameSwapped.connect(on_swapped, Qt.ConnectionType.QueuedConnection)

    # 4 ms precise timer: scheduling latency is the GUI thread's responsiveness.
    latency: list[float] = []
    lat_clock = QElapsedTimer()
    lat_clock.start()
    lat_state = {"last": 0}
    LAT_MS = 4

    def on_latency() -> None:
        now = lat_clock.nsecsElapsed() / 1e6
        if lat_state["last"]:
            latency.append(max(0.0, now - lat_state["last"] - LAT_MS))
        lat_state["last"] = now

    lat_timer = QTimer()
    lat_timer.setTimerType(Qt.TimerType.PreciseTimer)
    lat_timer.setInterval(LAT_MS)
    lat_timer.timeout.connect(on_latency)

    # Mode 2: Python pushes a 1080p RGBA frame from shared memory every ~16 ms.
    FW, FH = 1920, 1080
    shm = None
    push_cost: list[float] = []
    push_cost_bytes: list[float] = []
    push_timer = QTimer()
    if args.mode == 2:
        shm = shared_memory.SharedMemory(create=True, size=FW * (FH + 256) * 4)
        rows = bytearray(FW * (FH + 256) * 4)
        for y in range(FH + 256):
            v = (y * 7) & 0xFF
            rows[y * FW * 4 : (y + 1) * FW * 4] = bytes((v, 255 - v, (y >> 1) & 0xFF, 255)) * FW
        shm.buf[:] = rows
        view = memoryview(shm.buf)
        push_state = {"n": 0}

        def on_push() -> None:
            n = push_state["n"]
            push_state["n"] += 1
            off = (n % 256) * FW * 4
            t0 = time.perf_counter()
            # PySide6 6.12.0 rejects QByteArray(memoryview) despite its stub, so the
            # shared-memory frame goes through bytes first: copy 1 (Python heap),
            # then QByteArray(bytes): copy 2 (Qt heap). setProperty itself is a
            # QVariant wrap of the implicitly shared QByteArray.
            qba = QByteArray(view[off : off + FW * FH * 4].tobytes())
            tex.setProperty("frame", qba)
            t2 = time.perf_counter()
            push_cost.append((t2 - t0) * 1000)
            if n % 30 == 0:
                # Alternative: hand setProperty the bytes object directly.
                b = view[off : off + FW * FH * 4].tobytes()
                tex.setProperty("frame", b)
                push_cost_bytes.append((time.perf_counter() - t2) * 1000)

        push_timer.setTimerType(Qt.TimerType.PreciseTimer)
        push_timer.setInterval(16)
        push_timer.timeout.connect(on_push)

    phase = {"name": "warmup"}
    clock = QElapsedTimer()
    cpu0 = {"t": 0.0, "wall": 0.0}
    tex_samples: list[dict] = []
    web_samples: list[dict] = []

    def sample() -> None:
        tex_samples.append(
            {
                k: tex.property(k)
                for k in (
                    "renderFps",
                    "renderCpuMs",
                    "renderGpuMs",
                    "uploadCpuMs",
                    "uploadGpuMs",
                    "syncMs",
                )
            }
        )
        if web is not None:
            title = web.property("title") or ""
            if title.startswith("{"):
                with contextlib.suppress(json.JSONDecodeError):
                    web_samples.append(json.loads(title))

    def start_measure() -> None:
        phase["name"] = "measure"
        tex_samples.clear()
        web_samples.clear()
        swaps["n"] = 0
        latency.clear()
        push_cost.clear()
        push_cost_bytes.clear()
        QMetaObject.invokeMethod(tex, "resetStats", Qt.ConnectionType.DirectConnection)
        cpu0["t"] = time.thread_time()
        cpu0["wall"] = time.perf_counter()
        clock.restart()
        QTimer.singleShot(int(args.seconds * 1000), finish)

    def finish() -> None:
        result["graphics_api"] = str(win.rendererInterface().graphicsApi()).rsplit(".", 1)[-1]
        scr = win.screen()
        result["screen"] = {
            "name": scr.name(),
            "refreshHz": round(scr.refreshRate(), 1),
            "dpr": scr.devicePixelRatio(),
            "size": f"{scr.size().width()}x{scr.size().height()}",
            "window": f"{win.width()}x{win.height()}",
        }
        wall = time.perf_counter() - cpu0["wall"]
        cpu = time.thread_time() - cpu0["t"]
        result["gui_cpu_busy_pct"] = round(100 * cpu / wall, 2)
        result["gui_idle_pct"] = round(100 - 100 * cpu / wall, 2)
        result["swapped_frames"] = swaps["n"]
        result["swapped_fps"] = round(swaps["n"] / wall, 2)
        result["timer_latency_ms"] = {
            "samples": len(latency),
            "p50": round(percentile(latency, 0.50), 3),
            "p95": round(percentile(latency, 0.95), 3),
            "p99": round(percentile(latency, 0.99), 3),
            "max": round(max(latency) if latency else 0.0, 3),
            "mean": round(statistics.fmean(latency), 3) if latency else 0.0,
        }
        if tex_samples:
            keys = tex_samples[0].keys()
            result["tex"] = {k: round(statistics.fmean(s[k] for s in tex_samples), 3) for k in keys}
            result["tex"]["renderFpsMin"] = round(min(s["renderFps"] for s in tex_samples), 2)
        result["tex"]["frames"] = tex.property("frames")
        result["tex"]["glRenderer"] = tex.property("glRenderer")
        result["tex"]["glVersion"] = tex.property("glVersion")
        result["tex"]["uploadBytes"] = tex.property("uploadBytes")
        if web_samples:
            fps = [s.get("webglFps", 0) for s in web_samples if s.get("webglFps")]
            edits = [
                s.get("monacoEditsPerSec", 0) for s in web_samples if s.get("monacoEditsPerSec")
            ]
            last = web_samples[-1]
            result["web"] = {
                "webglFpsMean": round(statistics.fmean(fps), 2) if fps else 0,
                "webglFpsMin": round(min(fps), 2) if fps else 0,
                "monacoEditsPerSec": round(statistics.fmean(edits), 1) if edits else 0,
                "webgl": last.get("webgl"),
                "monaco": last.get("monaco"),
                "range": last.get("range"),
                "bulk": last.get("bulk"),
                "errors": last.get("errors"),
            }
        if push_cost:
            result["pyframe"] = {
                "pushes": len(push_cost),
                "tobytes_QByteArray_setProperty_ms_mean": round(statistics.fmean(push_cost), 3),
                "p95_ms": round(percentile(push_cost, 0.95), 3),
                "bytes_path_ms_mean": round(statistics.fmean(push_cost_bytes), 3)
                if push_cost_bytes
                else None,
            }
        result["served"] = served[-6:]
        text = json.dumps(result, indent=2)
        print(text)
        if args.out:
            args.out.write_text(text)
        if not args.stay:
            app.quit()

    poll = QTimer()
    poll.setInterval(500)
    poll.timeout.connect(sample)

    def go() -> None:
        lat_timer.start()
        poll.start()
        if args.mode == 2:
            push_timer.start()
        QTimer.singleShot(int(args.warmup * 1000), start_measure)

    QTimer.singleShot(0, go)
    rc = app.exec()
    if shm is not None:
        view.release()  # the exported memoryview must go before the mmap closes
        shm.close()
        shm.unlink()
    return rc


if __name__ == "__main__":
    sys.exit(main())
