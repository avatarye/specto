"""Seed the project folder for the #216 video sound flow: two copies of the run-001 cam-a
video (each with its ``.timeline.json`` sidecar) under runs/run-001/video/, each given an
audio track when the test data's video has none (a quiet 220 Hz tone beeping once a
second, muxed with ffmpeg, the picture copied as is), so the footer's speaker button is
live and the flow can hear nothing but assert everything. No ``.specto/``: the app
initialises the project on open.

    uv run python tools/gui_flows/v2-216-seed.py <folder>
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))
NAMES = ("take-a", "take-b")
BEEPS = "sine=frequency=220:beep_factor=4:sample_rate=48000"


def has_audio(mp4: Path) -> bool:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(mp4)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return "audio" in r.stdout


def with_beeps(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-f", "lavfi", "-i", BEEPS, "-map", "0:v", "-map", "1:a", "-shortest",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-af", "volume=-14dB",
         "-movflags", "+faststart", str(dst)],
        check=True,
    )  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ns = ap.parse_args()
    src = TESTDATA / "runs" / "run-001" / "video"
    if not (src / "cam-a.mp4").exists():
        print(f"[ERROR] no {src / 'cam-a.mp4'}; run tools/make_testdata.py")
        return 1
    vdir = ns.folder / "runs" / "run-001" / "video"
    if vdir.exists():
        shutil.rmtree(vdir)
    vdir.mkdir(parents=True)
    silent = shutil.which("ffmpeg") is not None and not has_audio(src / "cam-a.mp4")
    for name in NAMES:
        if silent:
            with_beeps(src / "cam-a.mp4", vdir / f"{name}.mp4")
        else:
            shutil.copy2(src / "cam-a.mp4", vdir / f"{name}.mp4")
        shutil.copy2(src / "cam-a.timeline.json", vdir / f"{name}.timeline.json")
    how = "with a beep track muxed in" if silent else "as in the test data"
    print(f"[OK] seeded {ns.folder}: {len(NAMES)} copies of cam-a.mp4 {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
