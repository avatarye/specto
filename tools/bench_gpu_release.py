"""GPU memory of paused views (SPEC-3): what a view type keeps on the GPU while it is off
screen, how long it takes to come back, and whether overworld round trips leak.

    uv run --no-sync --with psutil python tools/gpu_guard.py --log <guard.log> -- \\
        uv run --no-sync --with psutil python tools/bench_gpu_release.py --work <scratch>
        [--types gallery,image,...] [--copies 3] [--toggles 20] [--out <json>]

Windows only, the GPU tier: the app in a real window that never takes the focus (the test
driver, as ``gui_qtdrive.py --platform window``). The project is the #95 seed (a file of
every view type, ``gui_flows/v2-95-seed.py``) plus the test data's cam-a and cam-b frames
(``big/``, a large gallery) and one 6000 x 4000 photo (``photo/``, a large image).

What is measured is the app's GPU memory: the dedicated plus shared usage of its process
tree (Qt WebEngine runs its GPU work inside the app process) from the Windows ``GPU
Process Memory`` counters, the median of three samples a second apart. The OpenGL driver
keeps memory the app frees in the process's own pool and reuses it for the next
allocation, so these counters do not drop when a view frees its textures: they show the
high-water mark. So the bench measures what paused views *keep*:

- **tour** (one app per view type): ``--copies`` views of the type, each on its own
  intaker, take turns on screen; the others are parked far off the canvas (paused).
  ``kept_mb`` is the growth per extra view visited: a paused view that releases leaves
  its memory to the next one (growth near 0), one that keeps it adds its whole
  footprint. ``resume_ms`` is the time from bringing the first view back until its slot
  shows the same pixels as before it left (within a small tolerance).
- **overworld** (one app, a view of every type): ``--toggles`` round trips (Tab, Tab);
  the memory after each and the drift from the first to the last.

Writes JSON to ``--out`` (default ``<work>/bench_gpu_release.json``) and prints a table.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FLOWS = HERE / "gui_flows"
sys.path.insert(0, str(HERE))
from console_utf8 import utf8_stdio  # noqa: E402
from gui_platform import kill_tree, popen_kwargs  # noqa: E402
from gui_qtdrive import Endpoint, Flow, QtFlow, app_env, open_drive, wait_for  # noqa: E402

#: how far a parked view moves (canvas px): beyond any window frame
PARK_DX = 40000
#: where the view on tour sits, and its size (canvas px)
SPOT = (320, 0)
SIZE = (1000, 760)
#: mean absolute difference (0-255, per channel) under which a slot counts as restored
SAME = 4.0
MB = 1024 * 1024
#: the view of each type in gui_flows/v2-95-canvas.json; the tour copies its intaker
SEED_VIEWS = {
    "pdf": "kobi",
    "html": "tamu",
    "markdown": "rena",
    "json": "pola",
    "3d": "mihu",
    "gallery": "sola",
    "image": "dena",
    "video": "naru",
}
#: the bench's own sources for the types that have bigger content than the seed's
SOURCES = {"gallery": ["big"], "image": ["photo"]}
#: the tour's view ids (names: two consonant-vowel syllables)
TOUR_IDS = ("tava", "teve", "tivi", "tovo", "tuvu", "dava", "deve", "divi")


# -- GPU memory (PDH) ------------------------------------------------------------------------
class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD), ("largeValue", ctypes.c_longlong)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", wt.LPWSTR), ("FmtValue", _FmtValue)]


PDH_FMT_LARGE = 0x00000400
PDH_MORE_DATA = 0x800007D2


class GpuMemory:
    """Per-process GPU memory from the ``GPU Process Memory`` counter set: instances are
    ``pid_<pid>_luid_..._phys_<n>``, one per adapter."""

    def __init__(self) -> None:
        self.pdh = ctypes.windll.pdh
        self.query = wt.HANDLE()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
            raise OSError("PdhOpenQuery failed")
        self.counters = {}
        for name in ("Dedicated Usage", "Shared Usage"):
            h = wt.HANDLE()
            path = f"\\GPU Process Memory(*)\\{name}"
            if self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(h)):
                raise OSError(f"cannot add counter {path}")
            self.counters[name] = h
        self.pdh.PdhCollectQueryData(self.query)

    def _array(self, h: wt.HANDLE) -> dict[int, int]:
        size, count = wt.DWORD(0), wt.DWORD(0)
        st = self.pdh.PdhGetFormattedCounterArrayW(
            h, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None
        )
        if st & 0xFFFFFFFF != PDH_MORE_DATA:
            return {}
        buf = (ctypes.c_byte * size.value)()
        st = self.pdh.PdhGetFormattedCounterArrayW(
            h, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf
        )
        if st:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        out: dict[int, int] = {}
        for i in range(count.value):
            name = items[i].szName or ""
            if not name.startswith("pid_"):
                continue
            pid = int(name.split("_")[1])
            out[pid] = out.get(pid, 0) + int(items[i].FmtValue.largeValue)
        return out

    def sample(self, pids: set[int]) -> float:
        """MB of dedicated plus shared GPU memory of the processes ``pids``."""
        self.pdh.PdhCollectQueryData(self.query)
        total = 0
        for h in self.counters.values():
            total += sum(v for p, v in self._array(h).items() if p in pids)
        return total / MB


def tree_pids(pid: int) -> set[int]:
    """The process and its descendants."""
    import psutil

    try:
        root = psutil.Process(pid)
        return {pid} | {c.pid for c in root.children(recursive=True)}
    except psutil.NoSuchProcess:
        return set()


def diff(a: Any, b: Any) -> float:
    """Mean absolute difference of two images (0-255, per channel)."""
    from PIL import ImageChops, ImageStat

    if a.size != b.size:
        return 255.0
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


# -- the bench ------------------------------------------------------------------------------
class Session:
    """One app process on the bench project, driven through the test driver."""

    def __init__(self, bench: Bench, name: str) -> None:
        self.bench = bench
        self.name = name
        self.app: subprocess.Popen | None = None
        self.flow: QtFlow | None = None
        self.log: Any = None

    def __enter__(self) -> Session:
        project = self.bench.project
        # every session starts on a fresh project (the app initialises .specto/ on open),
        # not on the last session's canvas
        shutil.rmtree(project / ".specto", ignore_errors=True)
        user_dir = project / ".specto" / "gui-user"
        env = app_env(project, dict(os.environ), user_dir, "window")
        self.log = open(self.bench.work / f"app-{self.name}.log", "w", encoding="utf-8")
        self.app = subprocess.Popen(
            ["uv", "run", "--no-sync", "specto-app", "--test-driver", "--project", str(project)],
            cwd=ROOT,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            env=env,
            **popen_kwargs(),
        )
        drive = open_drive(user_dir, self.app)
        self.flow = QtFlow(drive, project, self.bench.out, None)
        wait_for(project / ".specto" / "endpoint.json", self.app, "the endpoint")
        time.sleep(0.5)
        self.flow.ep = Endpoint(project)
        drive.call("resize", w=1700, h=900)
        time.sleep(1.5)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.flow is not None:
            with contextlib.suppress(Exception):
                self.flow.drive.call("quit")
                if self.app is not None:
                    self.app.wait(8)
            self.flow.close()
        if self.app is not None and self.app.poll() is None:
            kill_tree(self.app)
        if self.log is not None:
            self.log.close()

    def mem(self, n: int = 3) -> float:
        """The app's GPU memory (MB), the median of ``n`` samples a second apart."""
        assert self.app is not None
        samples = []
        for i in range(n):
            if i:
                time.sleep(1.0)
            samples.append(self.bench.gpu.sample(tree_pids(self.app.pid)))
        return round(statistics.median(samples), 1)

    def submit(self, doc: dict[str, Any], label: str) -> None:
        assert self.flow is not None
        self.flow.agent().call("canvas.submit", {"document": doc, "label": label})

    def state(self) -> dict[str, Any]:
        assert self.flow is not None
        return self.flow.endpoint().call("canvas.state", {})["document"]

    def grab(self, vid: str) -> Any:
        assert self.flow is not None
        return self.flow.grab(self.flow.rect(f"slot:{vid}"))

    def move(self, vid: str, dx: float) -> None:
        doc = self.state()
        pos = doc["views"][vid]["layout"]["position"]
        doc["views"][vid]["layout"]["position"] = [pos[0] + dx, pos[1]]
        self.submit(doc, f"bench: move {vid}")

    def fill(self, vid: str, vtype: str) -> None:
        """Let the view load; a gallery scrolls to its end and back so its atlas fills."""
        assert self.flow is not None
        time.sleep(self.bench.ns.load)
        if vtype == "gallery":
            x, y = self.flow.point(f"slot:{vid}")
            self.flow.wheel(x, y, -80, pause=0.04)
            time.sleep(2)
            self.flow.wheel(x, y, 80, pause=0.04)
            time.sleep(3)

    def toggle(self, n: int) -> float | None:
        """``n`` overworld round trips; the memory drift from the first to the last."""
        assert self.flow is not None
        after = []
        for _ in range(n):
            self.flow.key("tab")
            time.sleep(self.bench.ns.settle)
            self.flow.key("tab")
            time.sleep(self.bench.ns.settle)
            after.append(self.mem(1))
        return round(after[-1] - after[0], 1) if len(after) > 1 else None

    def pan_cycles(self, vid: str, n: int) -> float | None:
        """``n`` round trips of ``vid`` off the canvas and back; the memory drift from the
        first to the last."""
        after = []
        for _ in range(n):
            self.move(vid, PARK_DX)
            time.sleep(self.bench.ns.settle)
            self.move(vid, -PARK_DX)
            time.sleep(self.bench.ns.settle)
            after.append(self.mem(1))
            print(f"[OK] {vid} off and back {len(after)}: {after[-1]} MB", flush=True)
        return round(after[-1] - after[0], 1) if len(after) > 1 else None

    def same_again(self, vid: str, ref: Any, limit: float = 20.0) -> tuple[int | None, float]:
        """Milliseconds until the slot shows ``ref`` again (None: not within ``limit``)."""
        t0 = time.monotonic()
        last = 255.0
        while time.monotonic() - t0 < limit:
            try:
                last = diff(ref, self.grab(vid))
            except Flow:
                last = 255.0
            if last < SAME:
                return round((time.monotonic() - t0) * 1000), last
            time.sleep(0.05)
        return None, last


class Bench:
    def __init__(self, ns: argparse.Namespace) -> None:
        self.ns = ns
        self.work: Path = ns.work
        self.project = self.work / "project"
        self.out = self.work / "out"
        self.gpu = GpuMemory()
        self.seed_doc = json.loads((FLOWS / "v2-95-canvas.json").read_text(encoding="utf-8"))
        self.report: dict[str, Any] = {"tour": {}, "toggles": []}

    def seed(self) -> None:
        if self.project.exists():
            shutil.rmtree(self.project)
        subprocess.check_call(
            ["uv", "run", "--no-sync", "python", str(FLOWS / "v2-95-seed.py"), str(self.project)],
            cwd=ROOT,
        )
        testdata = Path(os.environ.get("SPECTO_TESTDATA") or ROOT / "testdata")
        for cam in ("cam-a", "cam-b"):
            src = testdata / "runs" / "run-001" / cam
            dst = self.project / "big" / cam
            dst.mkdir(parents=True, exist_ok=True)
            for p in src.iterdir():
                if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    shutil.copy2(p, dst / p.name)
        from PIL import Image

        (self.project / "photo").mkdir()
        photo = Image.effect_noise((6000, 4000), 60).convert("RGB")
        photo.save(self.project / "photo" / "big.jpg")

    def link_for(self, vtype: str) -> dict[str, Any]:
        """The seed canvas's intaker into the view of ``vtype``, with the bench's sources."""
        vid = SEED_VIEWS[vtype]
        link = next(v for v in self.seed_doc["links"].values() if v["target"]["view"] == vid)
        link = copy.deepcopy(link)
        if vtype in SOURCES:
            link["params"]["sources"] = SOURCES[vtype]
        return link

    def tour_canvas(self, vtype: str, copies: int) -> dict[str, Any]:
        doc = copy.deepcopy(self.seed_doc)
        doc["views"], doc["links"] = {}, {}
        base = self.link_for(vtype)
        for i in range(copies):
            vid = TOUR_IDS[i]
            doc["views"][vid] = {
                "type": vtype,
                "label": f"{vtype} {i}",
                "layout": {"position": [SPOT[0] + PARK_DX * (i + 1), SPOT[1]], "size": list(SIZE)},
                "params": {},
                "locked": False,
            }
            link = copy.deepcopy(base)
            link["target"]["view"] = vid
            doc["links"][f"l{i}"] = link
        return doc

    def tour(self, vtype: str) -> dict[str, Any]:
        copies = self.ns.copies
        with Session(self, vtype) as s:
            s.submit(self.tour_canvas(vtype, copies), f"bench: {copies} {vtype} views, parked")
            time.sleep(4)
            base = s.mem()
            on: list[float] = []
            parked: list[float] = []
            refs: dict[str, Any] = {}
            for i in range(copies):
                vid = TOUR_IDS[i]
                s.move(vid, -PARK_DX * (i + 1))
                s.fill(vid, vtype)
                on.append(s.mem())
                refs[vid] = s.grab(vid)
                if i == 0 and s.flow is not None:
                    s.flow.shot(f"{vtype}-t0.png")
                s.move(vid, PARK_DX * (i + 1))
                time.sleep(self.ns.settle)
                parked.append(s.mem())
                print(f"[OK] {vtype} {vid}: on {on[-1]} MB, parked {parked[-1]} MB", flush=True)
            first = TOUR_IDS[0]
            s.move(first, -PARK_DX)
            resume_ms, last = s.same_again(first, refs[first])
            if s.flow is not None:
                s.flow.shot(f"{vtype}-back.png")
            time.sleep(self.ns.settle)
            back = s.mem()
            drift = s.toggle(self.ns.type_toggles)
            pan_drift = s.pan_cycles(first, self.ns.type_pans)
        row = {
            "base_mb": base,
            "on_mb": on,
            "parked_mb": parked,
            "back_mb": back,
            "footprint_mb": round(on[0] - base, 1),
            "kept_mb": round((on[-1] - on[0]) / max(1, copies - 1), 1),
            "resume_ms": resume_ms,
            "resume_diff": round(last, 2),
            "toggle_drift_mb": drift,
            "pan_drift_mb": pan_drift,
        }
        print(f"[OK] {vtype}: {row}", flush=True)
        return row

    def overworld(self) -> None:
        doc = copy.deepcopy(self.seed_doc)
        for vtype, sources in SOURCES.items():
            for link in doc["links"].values():
                if link["target"]["view"] == SEED_VIEWS[vtype]:
                    link["params"]["sources"] = sources
        with Session(self, "overworld") as s:
            assert s.flow is not None
            s.submit(doc, "bench: a view of every type")
            time.sleep(12)
            self.report["overworld_base_mb"] = s.mem()
            for i in range(self.ns.toggles):
                s.flow.key("tab")
                time.sleep(self.ns.settle)
                ow = s.mem(1)
                s.flow.key("tab")
                time.sleep(self.ns.settle)
                after = s.mem(1)
                self.report["toggles"].append({"overworld_mb": ow, "after_mb": after})
                print(
                    f"[OK] toggle {i + 1}: {ow} MB in the overworld, {after} MB after", flush=True
                )
        t = self.report["toggles"]
        if t:
            self.report["drift_mb"] = round(t[-1]["after_mb"] - t[0]["after_mb"], 1)

    def run(self) -> int:
        self.work.mkdir(parents=True, exist_ok=True)
        self.seed()
        types = [t for t in self.ns.types.split(",") if t] if self.ns.types else list(SEED_VIEWS)
        for vtype in types:
            self.report["tour"][vtype] = self.tour(vtype)
        if self.ns.toggles:
            self.overworld()
        out = self.ns.out or self.work / "bench_gpu_release.json"
        out.write_text(json.dumps(self.report, indent=1), encoding="utf-8")
        print(f"[OK] wrote {out}")
        self.table()
        return 0

    def table(self) -> None:
        r = self.report
        print("\ntype       footprint MB  kept MB/view  resume ms  toggle drift MB")
        for vtype, row in r["tour"].items():
            print(
                f"{vtype:10} {row['footprint_mb']:12.1f} {row['kept_mb']:13.1f}  "
                f"{row['resume_ms']!s:9}  {row['toggle_drift_mb']}"
            )
        if r["toggles"]:
            print(f"overworld x{len(r['toggles'])}: drift {r.get('drift_mb')} MB")


def main() -> int:
    if sys.platform != "win32":
        sys.exit("[ERROR] Windows only (GPU Process Memory counters)")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--types", default="", help=f"comma-separated, of {', '.join(SEED_VIEWS)}")
    ap.add_argument(
        "--copies", type=int, default=3, help=f"views of each type on the tour (<= {len(TOUR_IDS)})"
    )
    ap.add_argument("--toggles", type=int, default=20)
    ap.add_argument(
        "--type-toggles", type=int, default=0, help="overworld round trips in each tour"
    )
    ap.add_argument("--type-pans", type=int, default=0, help="off-and-back trips in each tour")
    ap.add_argument("--load", type=float, default=8.0, help="seconds a view gets to load")
    ap.add_argument("--settle", type=float, default=4.0, help="seconds after each change")
    ap.add_argument("--out", type=Path, default=None)
    ns = ap.parse_args()
    utf8_stdio()
    return Bench(ns).run()


if __name__ == "__main__":
    sys.exit(main())
