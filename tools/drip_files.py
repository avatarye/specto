"""Drip test frames into a folder at a set rate: the demo and test generator for watch
mode (HLD 3.6 M2). Copies jpg+json pairs from the test data, one frame per tick, with
a temporary name and a rename at the end so a watcher never sees a half-written file.

    uv run python tools/drip_files.py <target folder> [--rate 2] [--count 20] [--start 0]
                                      [--src testdata/runs/run-001/cam-a] [--no-sidecars]
                                      [--reset]

Never writes into the test data itself, into a project's ``.specto/`` folder or into
the item cache; the target is created if missing. ``--reset`` empties it first.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[1] / "testdata"))
DEFAULT_SRC = TESTDATA / "runs" / "run-001" / "cam-a"


def refuse_target(target: Path) -> str | None:
    """Why ``target`` may not be written to, or None."""
    t = target.resolve()
    forbidden = [("the test data", TESTDATA), ("the repo test data", REPO / "testdata")]
    try:
        from specto_intake.cache import default_cache_root

        forbidden.append(("the item cache", default_cache_root()))
    except ImportError:
        pass
    for what, root in forbidden:
        try:
            t.relative_to(root.resolve())
            return f"{t} is inside {what} ({root})"
        except ValueError:
            continue
    if ".specto" in t.parts:
        return f"{t} is inside a project's .specto folder"
    return None


def frames_in(src: Path) -> list[Path]:
    jpgs = sorted(p for p in src.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not jpgs:
        raise SystemExit(f"[ERROR] no frames in {src}")
    return jpgs


def copy_atomic(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".part")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("target", type=Path, help="folder to drip into (created if missing)")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="folder of source frames")
    ap.add_argument("--rate", type=float, default=2.0, help="frames per second (0 = all at once)")
    ap.add_argument("--count", type=int, default=20, help="how many frames to copy")
    ap.add_argument("--start", type=int, default=0, help="index of the first source frame")
    ap.add_argument("--no-sidecars", action="store_true", help="copy only the images")
    ap.add_argument("--reset", action="store_true", help="empty the target first")
    ap.add_argument("--quiet", "-q", action="store_true")
    args = ap.parse_args(argv)

    why = refuse_target(args.target)
    if why:
        print(f"[ERROR] refusing to write: {why}")
        return 2
    if not args.src.is_dir():
        print(f"[ERROR] source folder missing: {args.src}")
        return 2
    target = args.target
    target.mkdir(parents=True, exist_ok=True)
    if args.reset:
        for p in target.iterdir():
            if p.is_file():
                p.unlink()
    jpgs = frames_in(args.src)[args.start : args.start + args.count]
    if not jpgs:
        print(f"[ERROR] no frames from index {args.start} in {args.src}")
        return 2
    period = 1.0 / args.rate if args.rate > 0 else 0.0
    t0 = time.monotonic()
    for i, jpg in enumerate(jpgs):
        copy_atomic(jpg, target / jpg.name)
        side = jpg.with_suffix(".json")
        if side.exists() and not args.no_sidecars:
            copy_atomic(side, target / side.name)
        if not args.quiet:
            print(
                f"[OK] {i + 1}/{len(jpgs)} {jpg.name}  t={time.monotonic() - t0:.1f}s", flush=True
            )
        if period and i + 1 < len(jpgs):
            time.sleep(period)
    print(f"[OK] dripped {len(jpgs)} frames into {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
