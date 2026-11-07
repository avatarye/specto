"""Time a usd view's stage loads in the frame source itself (SPEC-27): where the seconds
between picking a world and its first frame go.

    uv run --no-sync --with psutil python tools/gpu_guard.py --log <guard.log> -- \\
        uv run --no-sync python tools/bench_usd_load.py --work <scratch> STAGE [STAGE ...]
        [--renderer rtx] [--fresh-renderer | --reuse-renderer] [--repeat 2]
        [--size 1280x720] [--py-spy [--py-spy-idle]]

Runs ``specto_app.usd.source`` with the command line the usd view uses
(``views.usd_view.rtx_launcher``, ``--fresh-renderer`` unless ``--reuse-renderer``, or
``storm_launcher``), with no app and no window, and loads each STAGE in turn,
``--repeat`` times over (the second round reloads worlds the process has seen). Every
event the source sends is stamped on arrival; each load's row has the time from its
``load`` request to its phases (``composing``: the stage opens with usd-core; ``loaded``:
the stage info is out; ``first frame``: the phase in which the renderer gets the stage
and draws; ``ok``: the stage's first complete frame is named) and the source's own split
of it (``load_timings``, sent once that frame is in the ring: the renderer swap, the
stage opened, the renderer's stage populated, the environment set up, from there to
the frame ``ok`` names, all of it), which the table shows.

The renderer's own start (``compiling shaders``, the first ``ready``) is timed apart.
The ovrtx log (``--log-file``) is kept in ``--work`` for its own timings. ``--py-spy``
records a native-frame profile of the source process (``py-spy record --native``,
``--idle`` with ``--py-spy-idle`` so waits show too; a speedscope JSON) into ``--work``.
Writes ``<work>/usd_load.json`` and prints a table. A public stage to use, and how to
make its texture-less variant, is in docs/setup/developer.md ("Profiling a usd load").
Use public stages only (no private depot paths in what this writes).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from console_utf8 import utf8_stdio  # noqa: E402


class Run:
    def __init__(self, ns: argparse.Namespace) -> None:
        # specto_core (pydantic) before any PySide6 module: the other way round, shiboken's
        # signature hook inspects pydantic while pydantic still imports itself
        import specto_core  # noqa: F401
        from specto_app.frames.control import FrameSourceProcess
        from specto_app.views.usd_view import rtx_launcher, storm_launcher

        self.ns = ns
        self.work: Path = ns.work
        self.work.mkdir(parents=True, exist_ok=True)
        self.log_file = self.work / "ovrtx.log"
        w, h = (int(v) for v in ns.size.split("x"))
        argv = (rtx_launcher if ns.renderer == "rtx" else storm_launcher)(w, h)
        if ns.reuse_renderer:
            argv = [a for a in argv if a != "--fresh-renderer"]
        argv += ["--log-file", str(self.log_file)]
        self.t0 = time.monotonic()
        # the source's stderr (its own and the renderer's lines) goes whole to a file
        self.stderr = open(self.work / "source-stderr.log", "w", encoding="utf-8")  # noqa: SIM115
        self.src = FrameSourceProcess.spawn(argv, stderr=self.stderr)
        self.events: list[dict[str, Any]] = []
        self.spy: subprocess.Popen | None = None

    def stamp(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def pump(self) -> list[dict[str, Any]]:
        out = []
        for name, params in self.src.poll():
            ev = {"t": self.stamp(), "event": name, **params}
            self.events.append(ev)
            out.append(ev)
        return out

    def until(self, test, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for ev in self.pump():
                if test(ev):
                    return ev
            if self.src.proc.poll() is not None:
                raise SystemExit(f"[ERROR] the source exited: {self.src.stderr_tail()[-5:]}")
            time.sleep(0.01)
        raise SystemExit(f"[ERROR] timed out after {timeout} s; last events {self.events[-5:]}")

    def start(self) -> dict[str, Any]:
        ready = self.until(lambda e: e["event"] == "ready", 120)
        if self.ns.py_spy:
            out = self.work / f"py-spy-{ready['pid']}.json"
            self.spy = subprocess.Popen(
                [
                    "uv",
                    "tool",
                    "run",
                    "py-spy",
                    "record",
                    "--native",
                    "--pid",
                    str(ready["pid"]),
                    "--format",
                    "speedscope",
                    "--output",
                    str(out),
                    "--rate",
                    "100",
                    *(["--idle"] if self.ns.py_spy_idle else []),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        return ready

    def load(self, path: str) -> dict[str, Any]:
        sent = self.stamp()
        self.src.send("load", path=path)
        marks: dict[str, float] = {}

        def done(ev: dict[str, Any]) -> bool:
            if ev["event"] == "loaded":
                marks.setdefault("loaded", ev["t"] - sent)
            elif ev["event"] == "health":
                state = ev.get("state", "")
                marks.setdefault(state, ev["t"] - sent)
                return state in ("ok", "error")
            return False

        end = self.until(done, self.ns.timeout)
        row = {"stage": Path(path).name, "sent": sent, **{k: round(v, 3) for k, v in marks.items()}}
        if end.get("state") == "error":
            row["error"] = end.get("message")
            return row
        timings = self.until(lambda e: e["event"] == "load_timings", 60)
        row["timings"] = {k: v for k, v in timings.items() if k not in ("t", "event", "path")}
        return row

    def close(self) -> None:
        self.src.close(timeout=30)  # asks for a shutdown first
        self.stderr.close()
        if self.spy is not None:
            try:
                self.spy.wait(60)
            except subprocess.TimeoutExpired:
                self.spy.terminate()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stages", nargs="+")
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--renderer", default="rtx", choices=("rtx", "storm"))
    ap.add_argument("--reuse-renderer", action="store_true", help="no --fresh-renderer (#155)")
    ap.add_argument("--repeat", type=int, default=2, help="rounds over the stages")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds a load may take")
    ap.add_argument("--py-spy", action="store_true", help="profile the source with py-spy")
    ap.add_argument("--py-spy-idle", action="store_true", help="py-spy samples waits too")
    ns = ap.parse_args()
    utf8_stdio()
    run = Run(ns)
    report: dict[str, Any] = {"args": {k: str(v) for k, v in vars(ns).items()}, "loads": []}
    try:
        ready = run.start()
        report["ready_s"] = run.stamp()
        print(f"[OK] source pid {ready['pid']} ready at {report['ready_s']} s", flush=True)
        for r in range(ns.repeat):
            for stage in ns.stages:
                row = run.load(os.path.abspath(stage))
                row["round"] = r + 1
                report["loads"].append(row)
                print(f"[OK] {row}", flush=True)
                time.sleep(2)  # let the frames settle before the next pick
    finally:
        run.close()
        report["events"] = run.events
        (ns.work / "usd_load.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    split = ("renderer_s", "open_s", "populate_s", "environment_s", "first_frame_s", "total_s")
    print(
        "\nround  stage                    to ok  renderer      open  populate  environ.  "
        "1st frame     total"
    )
    for row in report["loads"]:
        t = row.get("timings", {})
        print(
            f"{row['round']:5}  {row['stage'][:22]:22} {row.get('ok', float('nan')):6.2f}  "
            + "  ".join(f"{t.get(k, float('nan')):8.2f}" for k in split)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
