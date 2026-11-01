"""Seed the project folder for the v2 #122 scripted flow: a mixed tree, one file of each
kind Specto shows (frames, notes, json, geojson, a pdf, a point cloud, a splat, a USD
layer, a video) plus source, config and archive files and named folders (images, docs,
scripts, data, tests), so the file tree shows its type icons. No ``.specto/``: the app
initialises the project on open.

    uv run python tools/gui_flows/v2-122-seed.py <folder>
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))
FRONT = TESTDATA / "runs" / "run-001" / "cam-a"


def pdf_bytes(title: str) -> bytes:
    """A one-page pdf saying ``title`` (hand-written objects, offsets computed)."""
    text = f"BT /F1 28 Tf 72 700 Td ({title}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if root.exists():
        shutil.rmtree(root)
    frames = sorted(FRONT.glob("*.jpg"))[:3] if FRONT.exists() else []
    files: dict[str, bytes] = {
        "docs/plan.md": b"# Plan\n\nThe preview follows the picks in the file tree.\n",
        "docs/README.md": b"# Docs\n\nNotes on the run.\n",
        "data/run.json": b'{"run": "run-001", "frames": 3}\n',
        "data/stats.csv": b"frame,speed\n0,1.2\n1,1.4\n",
        "data/route.geojson": b'{"type": "FeatureCollection", "features": []}\n',
        "papers/report.pdf": pdf_bytes("Report"),
        "scenes/room.ply": b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n",
        "scenes/room.splat": bytes(32),
        "scenes/stage.usda": b"#usda 1.0\n",
        "scripts/convert.py": b"print('convert')\n",
        "scripts/run.sh": b"echo run\n",
        "tests/test_convert.py": b"def test_it():\n    pass\n",
        "backup.zip": b"PK\x05\x06" + bytes(18),
        "config.yaml": b"speed: 1\n",
        "pyproject.toml": b'[project]\nname = "demo"\n',
        "odd.xyz": b"no renderer for this one\n",
        "videos/clip.mp4": bytes(64),
    }
    for i in range(3):
        files[f"images/{i:05d}.jpg"] = frames[i].read_bytes() if frames else b"\xff\xd8\xff\xd9"
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"[OK] seeded {root}: {len(files)} files of mixed types")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
