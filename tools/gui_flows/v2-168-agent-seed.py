"""Seed the project folder for the v2 #168 agent link code flow: two recordings under
recordings/s01/<clip>/ and their annotations under
annotations/s01/<clip>/geo/ with one GeoJSON each. No ``.specto/``
and no link code: the (scripted) agent writes it.

    uv run python tools/gui_flows/v2-168-agent-seed.py <folder>
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    root = ap.parse_args().folder
    if root.exists():
        shutil.rmtree(root)
    for i, clip in enumerate(("clip1", "clip2")):
        world = root / "recordings" / "s01" / clip / "world.usda"
        world.parent.mkdir(parents=True)
        world.write_bytes(b"#usda 1.0\n")
        geo = root / "annotations" / "s01" / clip / "geo"
        geo.mkdir(parents=True)
        point = {"type": "Point", "coordinates": [10.3 + i * 0.01, 0.6 + i * 0.01]}
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": point}]}
        (geo / "track.geojson").write_bytes(json.dumps(fc).encode("utf-8"))
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
