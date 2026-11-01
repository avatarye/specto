"""Run the S1/S2 measurement matrix sequentially (one GPU, one window at a time)
and print a markdown table. Each run writes results/<name>.json.

    .venv/Scripts/python spikes/s1_s2/run_matrix.py [--seconds 10] [--only name,name]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

# name -> extra app.py args. Screen 1 is the 1920x1080 @ 60 Hz monitor on this box.
S1 = ["--screen", "1"]  # 1920x1080 @ 60 Hz on this box
GUI = ["--drive", "gui"]
MATRIX: dict[str, list[str]] = {
    # --- frames re-armed from the render thread (QQuickFramebufferObject pattern) ---
    "gl_native_web": ["--mode", "0", "--web", "both", *S1],
    "gl_native_alone": ["--mode", "0", "--web", "none", *S1],
    "gl_native_2Mtris_web": ["--mode", "0", "--web", "both", *S1, "--triangles", "2000000"],
    "gl_upload_web": ["--mode", "1", "--web", "both", *S1, "--tex-size", "1920x1080"],
    "gl_web_only": ["--mode", "0", "--web", "both", *S1, "--triangles", "12"],
    "gl_static_web": ["--mode", "0", "--web", "both", *S1, "--static"],
    "d3d11_web_baseline": ["--backend", "d3d11", "--mode", "0", "--web", "both", *S1],
    "gl_native_web_4k120": ["--mode", "0", "--web", "both", "--screen", "0"],
    "gl_native_web_no_timer_period": ["--mode", "0", "--web", "both", *S1, "--no-timer-period"],
    "gl_native_web_basic_loop": ["--mode", "0", "--web", "both", *S1, "--render-loop", "basic"],
    "gl_native_web_swap0": ["--mode", "0", "--web", "both", *S1, "--swap-interval", "0"],
    # --- frames driven from the GUI thread (item.update() per frame) ---
    "gui_native_web": ["--mode", "0", "--web", "both", *S1, *GUI],
    "gui_native_alone": ["--mode", "0", "--web", "none", *S1, *GUI],
    "gui_native_2Mtris_web": ["--mode", "0", "--web", "both", *S1, *GUI, "--triangles", "2000000"],
    "gui_upload_web": ["--mode", "1", "--web", "both", *S1, *GUI, "--tex-size", "1920x1080"],
    "gui_pyframe_web": ["--mode", "2", "--web", "both", *S1, *GUI, "--tex-size", "1920x1080"],
    "gui_native_web_4k120": ["--mode", "0", "--web", "both", "--screen", "0", *GUI],
}


def run_one(name: str, extra: list[str], seconds: float, warmup: float) -> dict | None:
    out = RESULTS / f"{name}.json"
    log = RESULTS / f"{name}.log"
    cmd = [
        sys.executable,
        str(HERE / "app.py"),
        "--seconds",
        str(seconds),
        "--warmup",
        str(warmup),
        "--out",
        str(out),
        *extra,
    ]
    print(f"[RUN] {name}: {' '.join(extra)}", flush=True)
    with log.open("w") as fh:
        rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=fh, timeout=240)
    if rc != 0 or not out.exists():
        print(f"[ERROR] {name} exited {rc}; see {log}", flush=True)
        return None
    return json.loads(out.read_text())


def fmt(v, nd=1) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def table(rows: dict[str, dict | None]) -> str:
    head = (
        "| run | api | screen | swap fps | item fps | GUI idle % | timer p50/p95/max ms "
        "| draw cpu/gpu ms "
        "| upload cpu/gpu ms | webgl fps | monaco edits/s | py push ms |"
    )
    sep = "|" + "---|" * 12
    lines = [head, sep]
    for name, r in rows.items():
        if r is None:
            lines.append(f"| {name} | crashed/failed |" + " |" * 10)
            continue
        t = r.get("tex", {})
        w = r.get("web", {}) or {}
        lat = r.get("timer_latency_ms", {})
        pf = r.get("pyframe", {}) or {}
        scr = r.get("screen", {}) or {}
        lines.append(
            f"| {name} | {r.get('graphics_api')} "
            f"| {scr.get('size')}@{fmt(scr.get('refreshHz'), 0)} "
            f"| {fmt(r.get('swapped_fps'))} "
            f"| {fmt(t.get('renderFps'))} (min {fmt(t.get('renderFpsMin'))}) "
            f"| {fmt(r.get('gui_idle_pct'))} "
            f"| {fmt(lat.get('p50'), 2)}/{fmt(lat.get('p95'), 2)}/{fmt(lat.get('max'), 2)} "
            f"| {fmt(t.get('renderCpuMs'), 3)}/{fmt(t.get('renderGpuMs'), 3)} "
            f"| {fmt(t.get('uploadCpuMs'), 3)}/{fmt(t.get('uploadGpuMs'), 3)} "
            f"| {fmt(w.get('webglFpsMean'))} (min {fmt(w.get('webglFpsMin'))}) "
            f"| {fmt(w.get('monacoEditsPerSec'))} "
            f"| {fmt(pf.get('tobytes_QByteArray_setProperty_ms_mean'), 2)} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--only", default="", help="comma-separated run names")
    ap.add_argument("--summarize", action="store_true", help="rebuild the table from results/")
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    names = [n for n in args.only.split(",") if n] or list(MATRIX)
    if args.summarize:
        rows = {}
        for n in names:
            f = RESULTS / f"{n}.json"
            rows[n] = json.loads(f.read_text()) if f.exists() else None
    else:
        rows = {n: run_one(n, MATRIX[n], args.seconds, args.warmup) for n in names}
    md = table(rows)
    (RESULTS / "summary.md").write_text(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
