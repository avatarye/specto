"""Fetch a public USD sample world from NVIDIA's public content bucket, for profiling a usd
view's load (SPEC-27, tools/bench_usd_load.py). Local use only: the content is NVIDIA's,
under the NVIDIA Omniverse License Agreement (THIRD_PARTY_NOTICES.md section 5.1), and is
never committed; nothing in Specto, its tests or its build reads it.

    uv run --no-sync python tools/fetch_sample_usd.py <dest> [--textures keep|1x1]

Mirrors Isaac Sim 4.5's ``Environments/Simple_Warehouse`` (2047 files, about 554 MB) and
the ``Props/Forklift`` it references into ``<dest>/Isaac/...``, keeping their relative
layout; the world to load is ``<dest>/Isaac/Environments/Simple_Warehouse/
full_warehouse.usd``. Every file is checked against the bucket's listing: its size and,
for a single-part object (2037 of them), its ETag, which is the MD5 of the content; a
file already there that passes is not fetched again, one that fails is fetched again. The
world's entry file is pinned by sha256 (``ROOT_SHA256``), so a measurement names the exact
world it was made on: a different one exits 2. A per-file sha256 manifest is not kept in
the repository for 2047 files that only a developer's bench reads; the pin and the ETag
checks stand in for it. ``--textures 1x1`` then replaces every PNG with a 1x1 image of
the same mode and name: the same stage and materials without the texture decode, for an
A/B against the full world.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
ROOT = "Assets/Isaac/4.5/Isaac/"
FOLDERS = ("Environments/Simple_Warehouse/", "Props/Forklift/")
WORLD = "Environments/Simple_Warehouse/full_warehouse.usd"
#: the world's entry file as SPEC-27 measured it (6724555 bytes)
ROOT_SHA256 = "81898ea7284276130af37645a31888b3a9a2e9b8d4490518484829e15813f85d"

OBJECT = re.compile(
    r"<Key>([^<]*)</Key>.*?<ETag>&quot;([^&]*)&quot;</ETag>.*?<Size>(\d+)</Size>", re.S
)


class Entry(NamedTuple):
    key: str
    size: int
    md5: str | None  # None for a multipart object, whose ETag is not the content's MD5


def entries(prefix: str):
    """Every object under ``prefix``, from the bucket's public listing."""
    token = None
    while True:
        query = {"list-type": "2", "prefix": prefix}
        if token:
            query["continuation-token"] = token
        with urllib.request.urlopen(BUCKET + "?" + urllib.parse.urlencode(query)) as r:
            text = r.read().decode()
        for key, etag, size in OBJECT.findall(text):
            yield Entry(key, int(size), None if "-" in etag else etag)
        found = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", text)
        if not found:
            return
        token = found.group(1)


def matches(path: Path, entry: Entry) -> bool:
    """``path`` is the listed object: its size and, when known, its content's MD5."""
    if not path.exists() or path.stat().st_size != entry.size:
        return False
    return entry.md5 is None or hashlib.md5(path.read_bytes()).hexdigest() == entry.md5


def mirror(dest: Path) -> None:
    for folder in FOLDERS:
        n = total = 0
        for entry in entries(ROOT + folder):
            if entry.key.endswith("/") or "/.thumbs/" in entry.key:
                continue
            out = dest / "Isaac" / entry.key[len(ROOT) :]
            if matches(out, entry):
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(BUCKET + urllib.parse.quote(entry.key), out)
            if not matches(out, entry):
                out.unlink(missing_ok=True)
                raise SystemExit(f"[ERROR] {entry.key}: the fetched file is not the listed object")
            n, total = n + 1, total + entry.size
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
    world = ns.dest / "Isaac" / Path(WORLD)
    digest = hashlib.sha256(world.read_bytes()).hexdigest()
    if digest != ROOT_SHA256:
        print(f"[ERROR] {world}: sha256 {digest}, not the world SPEC-27 measured ({ROOT_SHA256})")
        return 2
    if ns.textures == "1x1":
        one_pixel_textures(ns.dest)
    print(f"[OK] world: {world} (sha256 {ROOT_SHA256[:12]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
