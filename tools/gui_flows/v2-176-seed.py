"""Seed the project folder for the v2 #176 scripted flow: a two-hop chain of per-pick
Python links over a two-tree layout (recordings and their annotations).

    recordings/s01/<clip>/w.usda
    annotations/s01/<clip>/geo/*.geojson + readme.txt
    annotations/s01/<clip>/frames/*.jpg        (run-001 cam-a frames)
    .specto/links/chain.py      to_geo, geo and frames: one file, one approval

Hop one: the recording picked in tree A selects its annotation folder in tree B.
Hop two, off tree B's picks: the map shows that folder's GeoJSONs (a folder for a map
stands for its GeoJSON files, the readme stays out) and a gallery shows the clip's
frames as a collection the script builds itself (#176). The link file carries a nonce:
approval is per exact content and the trust list outlives the project (ADR 0006).

    uv run python tools/gui_flows/v2-176-seed.py <folder> [--approve]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

LINK_FILE = ".specto/links/chain.py"
WORLDS = "recordings/s01"
PROPS = "annotations/s01"
TESTDATA = Path(os.environ.get("SPECTO_TESTDATA", Path(__file__).resolve().parents[2] / "testdata"))
FRONT = TESTDATA / "runs" / "run-001" / "cam-a"
#: clip -> GeoJSON layers (lon, lat at a fictional spot) and how many frames
CLIPS = {
    "clip1": ({"track": (10.335, 0.608)}, 2),
    "clip2": ({"track": (10.301, 0.655), "zone": (10.302, 0.6545)}, 3),
    "clip3": ({"track": (10.384, 0.668)}, 1),
}

CHAIN = '''\
"""A two-hop chain (#176). to_geo: a recording picked in tree A -> its
annotation geo folder, for tree B's selection. geo: tree B's picks -> their geo
folders, for the map. frames: tree B's picks -> the clip's frames, built into the
gallery's collection (one item per frame, keyed <clip>/<name>)."""

import os


def _clips(paths):
    out = []
    for p in paths or []:
        parts = p.split("/")
        if parts[:1] == ["annotations"] and len(parts) >= 3:
            clip = "/".join(parts[:3])
            if clip not in out:
                out.append(clip)
    return out


def to_geo(value, items, params, root):
    out = []
    for p in value["paths"] or []:
        parts = p.split("/")
        if parts[:1] == ["recordings"] and len(parts) >= 4:
            out.append("/".join(["annotations", *parts[1:3], "geo"]))
    return sorted(set(out))


def geo(value, items, params, root):
    return {"paths": [a + "/geo" for a in _clips(value["paths"])]}


def frames(value, items, params, root):
    out = []
    for clip in _clips(value["paths"]):
        folder = os.path.join(root, clip, "frames")
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if name.lower().endswith(".jpg"):
                key = clip.rsplit("/", 1)[1] + "/" + name[:-4]
                out.append({"key": key, "parts": [clip + "/frames/" + name]})
    return {"items": out}
'''


def square(lon: float, lat: float, d: float = 0.0006) -> dict:
    ring = [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d]]
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--approve", action="store_true", help="approve the link file now")
    ns = ap.parse_args()
    root = ns.folder
    if root.exists():
        shutil.rmtree(root)
    frames = sorted(FRONT.glob("*.jpg"))
    if len(frames) < 6:
        print(f"[ERROR] the frames come from {FRONT}; it has {len(frames)} frames")
        return 1
    n = 0
    for clip, (layers, count) in CLIPS.items():
        world = root / WORLDS / clip / "w.usda"
        world.parent.mkdir(parents=True)
        world.write_text('#usda 1.0\n(\n    defaultPrim = "World"\n)\n', encoding="utf-8")
        geo = root / PROPS / clip / "geo"
        geo.mkdir(parents=True)
        for name, (lon, lat) in layers.items():
            fc = {"type": "FeatureCollection", "features": [square(lon, lat)]}
            (geo / f"{name}.geojson").write_text(json.dumps(fc), encoding="utf-8")
        (geo / "readme.txt").write_text(f"geo layers of {clip}\n", encoding="utf-8")
        folder = root / PROPS / clip / "frames"
        folder.mkdir()
        for i in range(count):
            shutil.copy2(frames[n], folder / f"frame{i + 1}.jpg")
            n += 1
    link = root / LINK_FILE
    link.parent.mkdir(parents=True)
    link.write_text(CHAIN + f"\n# seeded {time.time_ns()}\n", encoding="utf-8")
    if ns.approve:
        from specto_core.python_links import TrustStore, sha256_of

        TrustStore().approve(sha256_of(link.read_bytes()), path=LINK_FILE)
    print(
        f"[OK] seeded {root}: {len(CLIPS)} recordings, their geo and frames, {LINK_FILE}"
        + (" (approved)" if ns.approve else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
