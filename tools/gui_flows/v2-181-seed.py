"""Seed the project folder for the v2 #181 scripted flow: 450 small GeoJSON files under
big/ (one point each, on a grid over Seattle), more than two batches of 200 for the map.
``--shift`` rewrites them a degree east and south (the same files, new contents), so a
refresh is a data reload the map frames. No ``.specto/``: the app initialises the
project on open.

    uv run python tools/gui_flows/v2-181-seed.py <folder> [--shift]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

COUNT = 450
COLS = 30


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--shift", action="store_true", help="move every point (a reload)")
    ns = ap.parse_args()
    big = ns.folder / "big"
    big.mkdir(parents=True, exist_ok=True)
    dx, dy = (1.0, -1.0) if ns.shift else (0.0, 0.0)
    for i in range(COUNT):
        lon = -122.45 + (i % COLS) * 0.01 + dx
        lat = 47.55 + (i // COLS) * 0.01 + dy
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": f"stop {i:03d}"},
                    "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                }
            ],
        }
        (big / f"stop_{i:03d}.geojson").write_text(json.dumps(fc), encoding="utf-8")
    print(f"[OK] {COUNT} GeoJSON files under {big}" + (" (shifted)" if ns.shift else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
