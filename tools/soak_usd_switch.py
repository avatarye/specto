"""Soak a usd frame source's stage switches (SPEC-78, docs/setup/developer.md "Soaking
stage switches"). Dev-only; nothing in packages/ imports it.

    python <board>/tools/heavy_slot.py -- \\
      uv run --no-sync --with psutil python tools/gpu_guard.py --log <guard.log> -- \\
      uv run --no-sync --with psutil python tools/soak_usd_switch.py --work <scratch> \\
        --plan baseline|interactive|close|isolate [--op OP] [--fresh] [--rounds 15] \\
        --big STAGE --big-alt STAGE --prop STAGE --small STAGE

One run at a time on the GPU tier: the heavy-run slot (the board repository's
``tools/heavy_slot.py``) around ``tools/gpu_guard.py``. Runs ``specto_app.usd.source``
with the usd view's explicit-rtx command line (``rtx_launcher``, so an RTX GPU is needed;
``--fresh`` adds ``--fresh-renderer``), or with ``--renderer storm`` its Storm one
(``storm_launcher``: the OpenUSD imaging build, any GPU, macOS), no app and no window.
The stages are roles: a large world (``--big``), a second large one (``--big-alt``,
its 1x1-texture copy does), a prop and a small stage. Plans:

- ``baseline``: ``--big`` alone, ``--rounds`` loads, each settled (``ok``, then 2 s).
- ``interactive``: per round, in one process: big settled, then a camera cut, wireframe,
  lit, sky, stage; big again and, at its ``first frame`` + U(0.3, 4) s, small (a switch mid
  first frame); big-alt then at once prop (a pending load replaced); a malformed
  ``.usda`` (an error) then big; hdri, a camera move, prop, unlit, small, stage.
- ``close``: ``--rounds`` new sources, each closed at big's ``first frame`` + U(0.3, 3) s.
- ``isolate``: ``--rounds`` times one ``--op`` (``switch``, ``sky``, ``hdri``, ``mode``,
  ``mid``, ``failed``, ``camera``), then small, sampled after 3 s: the small stage's
  memory floor, to tell what one operation keeps.

A source is closed the way the view closes it (SPEC-87): ``close_in_background`` asks it
to stop and kills it after ``--close-timeout`` s (the view's is 5 s); the row says how
long the caller waited (``returned_s``) and how long the source took (``close_s``).
Each load is sampled: the source's private bytes (with psutil) and, on Windows, its
dedicated GPU memory (the GPU Process Memory counter; nvidia-smi says N/A under WDDM).
A fault is the source exiting, an error where none is expected, a call failing, or a
close that times out or leaves any of the source's tree running (the tool kills what is
left). A load that sees neither ``ok`` nor an error in ``--timeout`` s is a hang: the
tool records the source's Python stacks (``py-spy dump --native`` with the ``--py-spy``
command; empty: none) and, with psutil, how many threads spin, then kills the tree.
Ctrl+C kills the source too. Writes ``<work>/soak.json`` and prints one line per load;
exits 1 on any fault. Use public stages only (no private depot paths in what this
writes).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from console_utf8 import utf8_stdio  # noqa: E402

try:
    import psutil
except ImportError:  # the samples and the post-mortem say -1 without it
    psutil = None

BROKEN_USDA = (
    '#usda 1.0\n(\n    defaultPrim = "World"\n)\ndef Xform "World" {\n    def Cube "Box" {\n'
)
CAMERAS = [
    {"position": [12.0, -14.0, 6.0], "target": [0.0, 0.0, 1.0], "up": [0, 0, 1]},
    {"position": [-8.0, 9.0, 3.0], "target": [2.0, 0.0, 0.5], "up": [0, 0, 1]},
]
OPS = ("switch", "sky", "hdri", "mode", "mid", "failed", "camera")


def gpu_mb(pid: int) -> float:
    """The process's dedicated GPU memory in MB (Windows), -1 when unknown."""
    if sys.platform != "win32":
        return -1.0
    ps = (
        f"$s = Get-Counter '\\GPU Process Memory(pid_{pid}_*)\\Dedicated Usage' "
        "-ErrorAction SilentlyContinue; if ($s) { ($s.CounterSamples | "
        "Measure-Object -Property CookedValue -Sum).Sum } else { -1 }"
    )
    try:
        run = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30
        )
        value = float(run.stdout.strip())
        return -1.0 if value < 0 else round(value / 2**20, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1.0


class Source:
    """One ``specto_app.usd.source`` process and the events it sends."""

    def __init__(
        self, work: Path, fresh: bool, tag: str, timeout: float, renderer: str = "rtx"
    ) -> None:
        import specto_core  # noqa: F401  (pydantic before PySide6, as the bench does)
        from specto_app.frames.control import FrameSourceProcess
        from specto_app.views.usd_view import rtx_launcher, storm_launcher

        launcher = storm_launcher if renderer == "storm" else rtx_launcher
        argv = launcher(1280, 720)
        if fresh and "--fresh-renderer" not in argv:
            argv.append("--fresh-renderer")
        argv += ["--log-file", str(work / f"{renderer}-{tag}.log")]
        self.timeout = timeout
        self.stderr = open(work / f"source-stderr-{tag}.log", "w", encoding="utf-8")  # noqa: SIM115
        self.t0 = time.monotonic()
        try:
            self.src = FrameSourceProcess.spawn(argv, stderr=self.stderr)
        except BaseException:
            self.stderr.close()
            raise
        self.pending: list[dict[str, Any]] = []
        self.pid = self.src.proc.pid

    def stamp(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def until(self, test, timeout: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            for name, params in self.src.poll():
                self.pending.append({"t": self.stamp(), "event": name, **params})
            while self.pending:
                ev = self.pending.pop(0)
                if test(ev):
                    return ev
            if self.src.proc.poll() is not None:
                raise RuntimeError(f"the source exited, rc {self.src.proc.returncode}")
            time.sleep(0.01)
        raise TimeoutError(f"no answer in {timeout or self.timeout} s")

    def start(self) -> None:
        ready = self.until(lambda e: e["event"] == "ready", 300)
        self.pid = int(ready.get("pid") or self.pid)

    def send(self, method: str, **params: Any) -> None:
        self.src.send(method, **params)

    def at_first_frame(self) -> None:
        self.until(lambda e: e["event"] == "health" and e.get("state") == "first frame")

    def wait_loaded(self, path: str) -> dict[str, Any]:
        """Wait for ``path``'s ``loaded`` and then its ``ok`` (or any error) and timings."""
        seen = {"loaded": False}

        def done(ev: dict[str, Any]) -> bool:
            if ev["event"] == "loaded" and ev.get("path") == path:
                seen["loaded"] = True
            if ev["event"] == "health" and ev.get("state") == "error":
                seen["error"] = ev.get("message", "")
                return True
            return seen["loaded"] and ev["event"] == "health" and ev.get("state") == "ok"

        sent = self.stamp()
        self.until(done)
        row: dict[str, Any] = {"stage": Path(path).name, "wait_s": round(self.stamp() - sent, 3)}
        if "error" in seen:
            row["error"] = seen["error"]
            return row
        timings = self.until(lambda e: e["event"] == "load_timings" and e.get("path") == path, 60)
        for key in ("first_frame_s", "total_s", "renderer_s"):
            row[key] = timings.get(key)
        return row

    def load(self, path: str) -> dict[str, Any]:
        self.send("load", path=path)
        return self.wait_loaded(path)

    def call(self, method: str, **params: Any) -> str | None:
        """None when the call answers, else its error (an answered error is data)."""
        try:
            self.src.call(method, timeout=120, **params)
            return None
        except Exception as e:
            return str(e) or type(e).__name__

    def sample(self) -> dict[str, float]:
        private = -1.0
        if psutil is not None:
            try:
                mem = psutil.Process(self.pid).memory_info()
                private = round(getattr(mem, "private", mem.rss) / 2**20, 1)
            except psutil.Error:
                pass
        return {"private_mb": private, "gpu_mb": gpu_mb(self.pid)}

    def post_mortem(self, work: Path, tag: str, py_spy: list[str]) -> str:
        """A hung source: its stacks and spinning threads written down, then its tree
        killed (whatever happens meanwhile). Returns a one-line summary."""
        dump = work / f"hang-{tag}-{self.pid}.txt"
        spinning = -1
        try:
            if not py_spy:
                dump.write_text("no stacks: --py-spy is empty\n", encoding="utf-8")
            else:
                try:
                    run = subprocess.run(
                        [*py_spy, "dump", "--native", "--pid", str(self.pid)],
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    dump.write_text(run.stdout + run.stderr, encoding="utf-8")
                except (OSError, subprocess.SubprocessError) as e:
                    dump.write_text(f"py-spy failed: {e}\n", encoding="utf-8")
            if psutil is not None:
                try:
                    proc = psutil.Process(self.pid)
                    before = {t.id: t.user_time + t.system_time for t in proc.threads()}
                    time.sleep(3)
                    after = {t.id: t.user_time + t.system_time for t in proc.threads()}
                    spinning = sum(1 for k, v in after.items() if v - before.get(k, 0.0) > 1.5)
                except psutil.Error:
                    pass
        finally:
            self.kill()
        return f"hung: {spinning} threads spinning, stacks in {dump.name}"

    def tree(self) -> list[int]:
        """The source's pids: the one spawned (on Windows a venv's python.exe may be a
        launcher) and the interpreter that said ``ready``."""
        return list(dict.fromkeys([self.src.proc.pid, self.pid]))

    def kill(self) -> None:
        """The source's tree (a launcher, the source and anything under them), never
        anything else; psutil not needed."""
        from specto_app.prompt.procs import kill_tree
        from specto_core.instances import pid_alive

        for pid in self.tree():
            running = self.src.proc.poll() is None if pid == self.src.proc.pid else pid_alive(pid)
            if running:
                kill_tree(pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.src.proc.wait(30)
        self.stderr.close()

    def close(self, timeout: float) -> dict[str, Any]:
        """Close it as the view does (SPEC-87): ``close_in_background`` asks the source to
        stop and kills the process it spawned at ``timeout``. Anything of the tree still
        running after that is killed here and reported as ``left_running``."""
        from specto_core.instances import pid_alive

        tree = self.tree()
        started = time.monotonic()
        try:
            reaper = self.src.close_in_background(timeout)
            returned = time.monotonic() - started
            reaper.join(timeout + 30)
            took = time.monotonic() - started
            left = [pid for pid in tree if pid_alive(pid)]
        except BaseException:
            self.kill()
            raise
        if left:
            self.kill()
        else:
            self.stderr.close()
        return {
            "rc": self.src.proc.returncode,
            "returned_s": round(returned, 3),
            "close_s": round(took, 3),
            "timed_out": took >= timeout,
            "left_running": bool(left),
        }


class Soak:
    def __init__(self, ns: argparse.Namespace) -> None:
        self.ns = ns
        self.rng = random.Random(ns.seed)
        self.rows: list[dict[str, Any]] = []
        self.faults: list[str] = []
        self.broken = ns.work / "broken.usda"
        self.broken.write_text(BROKEN_USDA, encoding="utf-8", newline="\n")
        self.py_spy: list[str] = ns.py_spy.split()

    def record(
        self, src: Source, rnd: int, case: str, row: dict, expect_error=False, sample=True
    ) -> None:
        row = {"round": rnd, "case": case, **row, **(src.sample() if sample else {})}
        ok = ("error" in row) == expect_error
        if not ok:
            self.faults.append(f"round {rnd} {case}: {row}")
        self.rows.append(row)
        print(f"[{'OK' if ok else 'FAULT'}] {row}", flush=True)

    def closed(self, src: Source, label: str, row: dict[str, Any]) -> None:
        """Close ``src``; a close that fails, times out or leaves its tree running is a fault."""
        row.update(src.close(self.ns.close_timeout), pid=src.pid)
        self.rows.append(row)
        ok = row["rc"] == 0 and not row["timed_out"] and not row["left_running"]
        if not ok:
            self.faults.append(f"{label}: {row}")
        print(f"[{'OK' if ok else 'FAULT'}] {label}: {row}", flush=True)

    def hung(self, src: Source, tag: str, e: TimeoutError) -> TimeoutError:
        return TimeoutError(f"{e}; {src.post_mortem(self.ns.work, tag, self.py_spy)}")

    def call(self, src: Source, rnd: int, case: str, method: str, **params: Any) -> None:
        error = src.call(method, **params)
        if error is not None:
            self.faults.append(f"round {rnd} {case} {method}: {error}")
            print(f"[FAULT] round {rnd} {case} {method}: {error}", flush=True)

    def camera(self, src: Source, k: int, cut: bool = True) -> None:
        src.send("set_camera", **CAMERAS[k % 2], fov_y=60.0, near=0.1, far=1000.0, cut=cut)

    def switch_mid_first_frame(self, src: Source) -> float:
        ns = self.ns
        src.send("load", path=ns.big)
        src.at_first_frame()
        wait = round(self.rng.uniform(0.3, 4.0), 2)
        time.sleep(wait)
        src.send("load", path=ns.small)
        return wait

    def round_interactive(self, src: Source, rnd: int) -> None:
        ns = self.ns
        self.record(src, rnd, "settled", src.load(ns.big))
        self.camera(src, rnd)
        self.call(src, rnd, "settled", "set_view_mode", mode="wireframe")
        self.call(src, rnd, "settled", "set_view_mode", mode="lit")
        self.call(src, rnd, "settled", "set_environment", environment="sky")
        self.call(src, rnd, "settled", "set_environment", environment="stage")
        wait = self.switch_mid_first_frame(src)
        self.record(src, rnd, f"mid+{wait}", src.wait_loaded(ns.small))
        src.send("load", path=ns.big_alt)
        src.send("load", path=ns.prop)
        self.record(src, rnd, "pending", src.wait_loaded(ns.prop))
        self.record(src, rnd, "failed", src.load(str(self.broken)), expect_error=True)
        self.record(src, rnd, "after-failed", src.load(ns.big))
        self.call(src, rnd, "between", "set_environment", environment="hdri")
        self.camera(src, rnd + 1, cut=False)
        self.record(src, rnd, "between", src.load(ns.prop))
        self.call(src, rnd, "between", "set_view_mode", mode="unlit")
        self.record(src, rnd, "between", src.load(ns.small))
        self.call(src, rnd, "between", "set_environment", environment="stage")

    def round_isolate(self, src: Source, rnd: int) -> None:
        """One ``--op`` between two loads; those loads are rows too (unsampled, so the
        floor stays the only sample), so an unexpected error in them is a fault."""
        ns, op = self.ns, self.ns.op
        if op == "mid":
            self.switch_mid_first_frame(src)
            self.record(src, rnd, "mid small", src.wait_loaded(ns.small), sample=False)
        else:
            if op == "failed":
                self.record(src, rnd, "failed", src.load(str(self.broken)), expect_error=True)
            else:
                self.record(src, rnd, f"{op} prop", src.load(ns.prop), sample=False)
            if op in ("sky", "hdri"):
                self.call(src, rnd, op, "set_environment", environment=op)
                self.call(src, rnd, op, "set_environment", environment="stage")
            elif op == "mode":
                self.call(src, rnd, op, "set_view_mode", mode="wireframe")
                self.call(src, rnd, op, "set_view_mode", mode="lit")
            elif op == "camera":
                for k in range(10):
                    self.camera(src, k)
                    time.sleep(0.2)
            self.record(src, rnd, f"{op} small", src.load(ns.small), sample=False)
        time.sleep(3)
        self.record(src, rnd, op, {"stage": Path(ns.small).name, "floor": True})

    def run_close(self) -> None:
        ns = self.ns
        for rnd in range(1, ns.rounds + 1):
            tag = f"close{rnd}"
            src = Source(ns.work, ns.fresh, tag, ns.timeout, ns.renderer)
            try:
                src.start()
                src.send("load", path=ns.big)
                src.at_first_frame()
                wait = round(self.rng.uniform(0.3, 3.0), 2)
                time.sleep(wait)
            except TimeoutError as e:
                raise self.hung(src, tag, e) from e
            except BaseException:
                src.kill()
                raise
            self.closed(src, f"close round {rnd}", {"round": rnd, "case": "close", "after_s": wait})

    def run_one_source(self) -> None:
        ns = self.ns
        src = Source(ns.work, ns.fresh, ns.plan, ns.timeout, ns.renderer)
        try:
            src.start()
            for rnd in range(1, ns.rounds + 1):
                if ns.plan == "baseline":
                    self.record(src, rnd, "settled", src.load(ns.big))
                    time.sleep(2)
                elif ns.plan == "isolate":
                    self.round_isolate(src, rnd)
                else:
                    self.round_interactive(src, rnd)
                self.write()
        except TimeoutError as e:
            raise self.hung(src, ns.plan, e) from e
        except BaseException:
            src.kill()
            raise
        self.closed(src, "final close", {"case": "final-close"})

    def write(self) -> None:
        args = {k: str(v) for k, v in vars(self.ns).items()}
        out = {"args": args, "rows": self.rows, "faults": self.faults}
        (self.ns.work / "soak.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument(
        "--plan", choices=("baseline", "interactive", "close", "isolate"), required=True
    )
    ap.add_argument("--op", choices=OPS, help="isolate: the operation repeated")
    ap.add_argument("--fresh", action="store_true", help="a new renderer per stage")
    ap.add_argument(
        "--renderer",
        choices=("rtx", "storm"),
        default="rtx",
        help="the usd view's explicit command line for this renderer (storm: macOS, any GPU)",
    )
    ap.add_argument("--rounds", type=int, default=15)
    ap.add_argument("--seed", type=int, default=78)
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds before a load is a hang")
    ap.add_argument(
        "--close-timeout",
        type=float,
        default=60.0,
        help="seconds a close may take before the source is killed (the view's: 5)",
    )
    ap.add_argument(
        "--py-spy",
        default="uv tool run py-spy",
        help="the py-spy command a hang's stacks are taken with; empty: none",
    )
    for role in ("big", "big-alt", "prop", "small"):
        ap.add_argument(f"--{role}", required=True)
    ns = ap.parse_args()
    if ns.plan == "isolate" and ns.op is None:
        ap.error("--plan isolate needs --op")
    ns.work.mkdir(parents=True, exist_ok=True)
    soak = Soak(ns)
    try:
        if ns.plan == "close":
            soak.run_close()
        else:
            soak.run_one_source()
    except (Exception, KeyboardInterrupt) as e:  # the source is killed already
        soak.faults.append(f"aborted: {type(e).__name__}: {e}")
        print(f"[FAULT] aborted: {type(e).__name__}: {e}", flush=True)
    finally:
        soak.write()
        print(f"faults: {len(soak.faults)}", flush=True)
    return 1 if soak.faults else 0


if __name__ == "__main__":
    sys.exit(main())
