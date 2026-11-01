"""Seed the project folder for the v2 #168 scripted flow: two trees (recordings and
their annotations) and the per-pick Python link that joins them.

    recordings/s01/<clip>/w.usda
    annotations/s01/<clip>/geo/*.geojson   (and a frames folder)
    .specto/links/clip_to_geo.py                            (a recording -> its geo folder)

The link file carries a nonce, so its hash is new every run: approval is per exact
content and the trust list outlives the project folder (ADR 0006), so the flow approves
it by mouse each time. ``--approve`` records the approval in the trust store instead
(a scratch project to open by hand). No canvas: the flow submits v2-168-canvas.json.

    uv run python tools/gui_flows/v2-168-seed.py <folder> [--approve]
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

LINK_FILE = ".specto/links/clip_to_geo.py"
WORLDS = "recordings/s01"
PROPS = "annotations/s01"
#: clip -> GeoJSON files in its geo folder (a lon, lat each, at a fictional spot)
CLIPS = {
    "clip1": {"track": (10.335, 0.608)},
    "clip2": {"track": (10.301, 0.655), "zone": (10.302, 0.6545)},
    "clip3": {"track": (10.384, 0.668)},
}

MIRROR = '''\
"""A recording picked in tree A -> its annotation folder (its geo), for
tree B's selection: recordings/<session>/<clip>/... ->
annotations/<session>/<clip>/geo."""


def resolve(value, items, params, root):
    out = []
    for p in value["paths"] or []:
        parts = p.split("/")
        if parts[:1] == ["recordings"] and len(parts) >= 4:
            out.append("/".join(["annotations", *parts[1:3], "geo"]))
    return sorted(set(out))
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
    for clip, layers in CLIPS.items():
        world = root / WORLDS / clip / "w.usda"
        world.parent.mkdir(parents=True)
        world.write_text('#usda 1.0\n(\n    defaultPrim = "World"\n)\n', encoding="utf-8")
        geo = root / PROPS / clip / "geo"
        geo.mkdir(parents=True)
        for name, (lon, lat) in layers.items():
            fc = {"type": "FeatureCollection", "features": [square(lon, lat)]}
            (geo / f"{name}.geojson").write_text(json.dumps(fc), encoding="utf-8")
        frames = root / PROPS / clip / "frames"
        frames.mkdir()
        (frames / "notes.txt").write_text(f"{clip}\n", encoding="utf-8")
    link = root / LINK_FILE
    link.parent.mkdir(parents=True)
    link.write_text(MIRROR + f"\n# seeded {time.time_ns()}\n", encoding="utf-8")
    if ns.approve:
        from specto_core.python_links import TrustStore, sha256_of

        TrustStore().approve(sha256_of(link.read_bytes()), path=LINK_FILE)
    print(
        f"[OK] seeded {root}: {len(CLIPS)} recordings, their geo, {LINK_FILE}"
        + (" (approved)" if ns.approve else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
