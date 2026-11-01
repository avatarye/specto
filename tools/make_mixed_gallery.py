"""Generate a mixed collection for the gallery benchmark (#105): images, videos, pdfs,
markdown, json, html and 3D files, one item per file (``NNNNN_<kind>.<ext>``).

    uv run python tools/make_mixed_gallery.py [--count 1000] [--out DIR]

Images are copied from the run-001 test frames; videos are short ffmpeg test patterns
(ffmpeg on PATH; skipped with a warning without it); pdfs are Pillow pages; the text
kinds are written here; 3D files are stubs (the gallery shows a kind icon for them).
Nothing is downloaded. The default output is ``<temp>/specto-mixed-gallery``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[1] / "testdata"))
FRAMES = [TESTDATA / "runs" / "run-001" / c for c in ("cam-a", "cam-b")]
DEFAULT_OUT = Path(tempfile.gettempdir()) / "specto-mixed-gallery"

# share of each kind in the collection (the rest are images)
MIX = {"mp4": 0.10, "pdf": 0.10, "md": 0.15, "json": 0.10, "html": 0.10, "glb": 0.05}
HUES = ["red", "green", "blue", "orange", "purple", "teal", "yellow", "gray"]


def markdown(i: int) -> str:
    return (
        f"# Run note {i}\n\ncam-a saw **{i % 17} discs** and a square.\n\n"
        "## Findings\n\n- pose drift under 4 cm\n- exposure stable\n- one dropped frame\n\n"
        "```\nframe  stamp_ns\n00012  1700000000000000000\n```\n"
    )


def json_doc(i: int) -> str:
    return json.dumps(
        {
            "frame": i,
            "stamp_ns": 1700000000000000000 + i * 33_000_000,
            "pose": {"t": [i * 0.1, 0.0, 1.6], "q": [0, 0, 0, 1]},
            "tags": ["cam-a", "shape"],
        },
        indent=2,
    )


def html_doc(i: int) -> str:
    return (
        f"<!doctype html><html><head><title>Report {i}</title><style>body{{font:14px sans-serif}}"
        f"</style></head><body><h1>Report {i}</h1><p>Coverage <b>{60 + i % 40}%</b> of the "
        "route.</p><ul><li>north loop</li><li>south loop</li></ul></body></html>\n"
    )


def pdf_doc(path: Path, i: int) -> None:
    im = Image.new("RGB", (420, 594), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 420, 90), fill=HUES[i % len(HUES)])
    d.text((24, 30), f"Page one of report {i}", fill="white")
    for y in range(130, 560, 22):
        d.line((24, y, 396 - (y * 7 % 120), y), fill="#888888", width=6)
    im.save(path, "PDF", resolution=72)


def clips(out: Path) -> list[Path]:
    """Eight short test-pattern clips, one per hue; reused across the video items."""
    if shutil.which("ffmpeg") is None:
        print("[WARN] no ffmpeg on PATH: no videos in the mixed collection")
        return []
    src = out / "_clips"
    src.mkdir(parents=True, exist_ok=True)
    made = []
    for k, hue in enumerate(HUES):
        p = src / f"{hue}.mp4"
        if not p.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=s=320x180:d=3:r=15,hue=h={k * 45}",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(p),
                ],
                check=True,
            )
        made.append(p)
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    frames = sorted(p for d in FRAMES if d.is_dir() for p in d.glob("*.jpg"))
    if not frames:
        sys.exit(f"[ERROR] no frames under {TESTDATA}")
    out: Path = args.out
    data = out / "data"
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    videos = clips(out)
    plan: list[str] = []
    for ext, share in MIX.items():
        if ext == "mp4" and not videos:
            continue
        plan += [ext] * round(args.count * share)
    plan += ["jpg"] * (args.count - len(plan))
    # shuffle the kinds (the same way every run) so every screenful is mixed
    order = list(range(len(plan)))
    random.Random(105).shuffle(order)
    for i, j in enumerate(order):
        ext = plan[j]
        p = data / f"{i:05d}_{ext}.{ext}"
        if ext == "jpg":
            shutil.copy2(frames[i % len(frames)], p)
        elif ext == "mp4":
            shutil.copy2(videos[i % len(videos)], p)
        elif ext == "pdf":
            pdf_doc(p, i)
        elif ext == "md":
            p.write_text(markdown(i), encoding="utf-8")
        elif ext == "json":
            p.write_text(json_doc(i), encoding="utf-8")
        elif ext == "html":
            p.write_text(html_doc(i), encoding="utf-8")
        else:
            p.write_bytes(b"glTF\x02\x00\x00\x00" + bytes(64))
    counts = {e: plan.count(e) for e in sorted(set(plan))}
    print(f"[OK] {len(plan)} files in {data}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
