"""Summarise a carb CPU profiler Chrome trace of a usd load (SPEC-65, docs/setup/developer.md
"Profiling a usd load"). Dev-only; nothing in packages/ imports it.

    python -I tools/ovrtx_trace_top.py PROFILE.json [--top 25]

carb.profiler-cpu writes one event per line and may leave the file cut short; both are
fine. The fiber scheduler's own zones ("Running fiber") are dropped. Prints:

- the zone names with the most total time (summed over threads, so parallel work adds up);
- the first-frame step (the longest ``ovrtx_step_with_stage_execute``), and inside it each
  streaming ``RenderQualityLoop`` of 50 ms or more in order with what the other threads
  did meanwhile (overlap time per zone name), then the other loops as one line;
- every counter's highest value.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys

NOISE = {"Running fiber", "processing threads"}
#: a streaming loop shorter than this goes in the "other loops" line
LONG_LOOP_US = 50_000
#: zones that wrap other work for its whole length; left out of a window's top list
WRAPPERS = {"Executing task", "finishLoad", "Dispatch Event: progress.beginevent"}


def read(path: str):
    """(threads {tid: name}, zones [(tid, name, ts_us, dur_us)], counters {name: max})."""
    threads: dict[int, str] = {}
    zones: list[tuple[int, str, float, float]] = []
    counters: dict[str, float] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue  # the cut-short tail
            ph, name = e.get("ph"), e.get("name", "?")
            if ph == "M" and name == "thread_name":
                threads[e.get("tid")] = e.get("args", {}).get("name", "")
            elif ph == "X" and name not in NOISE:
                zones.append(
                    (e.get("tid"), sys.intern(name), float(e["ts"]), float(e.get("dur", 0)))
                )
            elif ph == "C":
                value = float(e.get("args", {}).get("value", 0))
                counters[name] = max(counters.get(name, value), value)
    return threads, zones, counters


def short(name: str) -> str:
    name = re.sub(r"file:/\S+", "file:<path>", name)
    return re.sub(r"\d+", "#", name)[:90]


def overlap(zones, tid_out: int, a: float, b: float) -> dict[str, float]:
    over: dict[str, float] = collections.defaultdict(float)
    for tid, name, ts, dur in zones:
        x, y = max(ts, a), min(ts + dur, b)
        if y > x and tid != tid_out and name not in WRAPPERS:
            over[short(name)] += y - x
    return over


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trace")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    threads, zones, counters = read(args.trace)
    print(f"{len(zones)} zones, {len(threads)} threads, {len(counters)} counters")

    total: dict[str, list[float]] = collections.defaultdict(lambda: [0, 0.0])
    for _, name, _, dur in zones:
        total[short(name)][0] += 1
        total[short(name)][1] += dur
    print("\n-- zone names by total time (summed over threads) --")
    for name, (count, dur) in sorted(total.items(), key=lambda kv: -kv[1][1])[: args.top]:
        print(f"{dur / 1e6:10.3f} s {count:8d}  {name}")

    steps = [z for z in zones if z[1].startswith("ovrtx_step_with_stage_execute")]
    if steps:
        tid, _, s0, sd = max(steps, key=lambda z: z[3])
        s1 = s0 + sd
        print(
            f"\nfirst frame step: {sd / 1e6:.3f} s at {s0 / 1e6:.3f} s on {threads.get(tid, tid)}"
        )
        loops = sorted(
            (
                z
                for z in zones
                if z[0] == tid and z[1].startswith("RenderQualityLoop") and s0 <= z[2] < s1
            ),
            key=lambda z: z[2],
        )
        streaming = [z for z in loops if "streaming" in z[1] and z[3] >= LONG_LOOP_US]
        for i, (_, _, ts, dur) in enumerate(streaming, 1):
            at = (ts - s0) / 1e6
            print(f"\n  streaming loop {i}: {dur / 1e6:.3f} s at +{at:.3f} s; other threads:")
            over = overlap(zones, tid, ts, ts + dur)
            for name, t in sorted(over.items(), key=lambda kv: -kv[1])[:8]:
                print(f"    {t / 1e6:8.3f} s  {name}")
        rest = [z for z in loops if z not in streaming]
        if rest:
            print(f"\n  other loops: {len(rest)}, {sum(z[3] for z in rest) / 1e6:.3f} s")
    else:
        print("\nno ovrtx_step_with_stage_execute zone in this trace")

    print("\n-- counters, highest value --")
    for name, value in sorted(counters.items()):
        print(f"{value:14.0f}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
