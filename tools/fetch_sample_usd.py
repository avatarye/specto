"""Fetch a public USD sample world from NVIDIA's public content bucket, for profiling a usd
view's load (SPEC-27, tools/bench_usd_load.py). Local use only: the content is under
NVIDIA's terms and is never committed.

    uv run --no-sync python tools/fetch_sample_usd.py <dest> [--textures keep|1x1]

Mirrors Isaac 4.5's ``Environments/Simple_Warehouse`` (about 500 MB) and the
``Props/Forklift`` it references into ``<dest>/Isaac/...``, keeping their relative
layout; the world to load is ``<dest>/Isaac/Environments/Simple_Warehouse/
full_warehouse.usd``. A file already there with the same size is not fetched again.
``--textures 1x1`` then replaces every PNG with a 1x1 image of the same mode and name: the
same stage and materials without the texture decode, for an A/B against the full world.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
ROOT = "Assets/Isaac/4.5/Isaac/"
FOLDERS = ("Environments/Simple_Warehouse/", "Props/Forklift/")


def keys(prefix: str):
    """``(key, size)`` of every object under ``prefix`` (the bucket's public listing)."""
    token = None
    while True:
        query = {"list-type": "2", "prefix": prefix}
        if token:
            query["continuation-token"] = token
        with urllib.request.urlopen(BUCKET + "?" + urllib.parse.urlencode(query)) as r:
            text = r.read().decode()
        yield from (
            (k, int(s)) for k, s in re.findall(r"<Key>([^<]*)</Key>.*?<Size>(\d+)</Size>", text)
        )
        found = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", text)
        if not found:
            return
        token = found.group(1)


def mirror(dest: Path) -> None:
    for folder in FOLDERS:
        n = total = 0
        for key, size in keys(ROOT + folder):
            if key.endswith("/") or "/.thumbs/" in key:
                continue
            out = dest / "Isaac" / key[len(ROOT) :]
            if out.exists() and out.stat().st_size == size:
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(BUCKET + urllib.parse.quote(key), out)
            n, total = n + 1, total + size
        print(f"[OK] {folder}: {n} files fetched, {total / 1e6:.1f} MB")


def one_pixel_textures(dest: Path) -> None:
    from PIL import Image

    n = 0
    for png in (dest / "Isaac").rglob("*.png"):
        with Image.open(png) as im:
            mode = im.mode if im.mode in ("RGB", "RGBA", "L", "LA") else "RGBA"
        Image.new(mode, (1, 1)).save(png)
        n += 1
    print(f"[OK] {n} textures replaced by 1x1 images")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--textures", choices=("keep", "1x1"), default="keep")
    ns = ap.parse_args()
    mirror(ns.dest)
    if ns.textures == "1x1":
        one_pixel_textures(ns.dest)
    world = ns.dest / "Isaac" / "Environments" / "Simple_Warehouse" / "full_warehouse.usd"
    print(f"[OK] world: {world}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
