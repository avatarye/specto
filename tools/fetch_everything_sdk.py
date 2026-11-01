"""Fetch the Everything SDK (voidtools) and place its DLL beside the file search (#232).

The file tree's type-to-search on Windows queries the running Everything service over
the SDK's ``Everything64.dll`` (``specto_intake.everything``). The SDK zip ships the DLL,
its C source and header under an MIT permission notice (``include/Everything.h``,
copyright David Carpenter: use, copy, modify, distribute and sell, keeping the notice),
so the DLL may be redistributed with a packaged Specto (#200) with that notice beside it.

    uv run python tools/fetch_everything_sdk.py                  # the default destination
    uv run python tools/fetch_everything_sdk.py --dest D:/evsdk  # or SPECTO_EVERYTHING_SDK_DIR
    uv run python tools/fetch_everything_sdk.py --no-copy        # fetch only

The default destination is ``~/specto-build/everything-sdk`` (``SPECTO_BUILD_ROOT`` moves
the root, as for the other fetched builds); the search also looks for the DLL under the
destination's ``dll/`` when nothing was copied.

Layout after a run:

    <dest>/Everything-SDK.zip        the archive (sha256 in the manifest)
    <dest>/dll/Everything64.dll      the DLL the search loads (also 32-bit and ARM builds)
    <dest>/include/Everything.h      the API and the permission notice
    <dest>/everything-sdk.json       {url, sha256, dll, notice}
    packages/specto-intake/src/specto_intake/vendor/Everything64.dll   (git-ignored copy)
    packages/specto-intake/src/specto_intake/vendor/EVERYTHING-SDK-NOTICE.txt

The zip is downloaded data: it is read with ``zipfile`` and nothing in it is executed.
Everything itself (the service) is the user's own install from voidtools.com; without it
the search falls back to a walk of the folder. Not needed on Linux or macOS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.voidtools.com/Everything-SDK.zip"
#: the zip as fetched on 2026-10-31 (the SDK is not versioned; a changed hash is reported,
#: not refused)
KNOWN_SHA256 = "f5716d9513cce6b462b5170a0a2e7e081e191d9bcac6774f5decc061497df443"
DLLS = ("Everything64.dll", "Everything32.dll")
_BUILD_ROOT = os.environ.get("SPECTO_BUILD_ROOT", str(Path.home() / "specto-build"))
DEFAULT_DEST = Path(
    os.environ.get("SPECTO_EVERYTHING_SDK_DIR", str(Path(_BUILD_ROOT) / "everything-sdk"))
)
VENDOR = (
    Path(__file__).resolve().parent.parent
    / "packages"
    / "specto-intake"
    / "src"
    / "specto_intake"
    / "vendor"
)


def download(url: str, dest: Path) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "specto-fetch-everything-sdk"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
    return h.hexdigest()


def notice_of(header: str) -> str:
    """The permission notice at the top of ``Everything.h`` (its leading comment)."""
    lines = []
    for line in header.lstrip().splitlines():
        if not line.startswith("//"):
            break
        lines.append(line[2:].strip())
    text = "\n".join(lines).strip()
    if "Permission is hereby granted" not in text:
        sys.exit("[ERROR] Everything.h carries no permission notice; not redistributing the DLL")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--force", action="store_true", help="refetch into a non-empty dest")
    ap.add_argument("--no-copy", action="store_true", help="do not copy the DLL beside the search")
    args = ap.parse_args()
    dest: Path = args.dest
    manifest = dest / "everything-sdk.json"
    if manifest.exists() and not args.force:
        info = json.loads(manifest.read_text(encoding="utf-8"))
        print(f"[OK] Everything SDK already present at {dest} ({info.get('sha256', '')[:12]}...)")
    else:
        if dest.exists() and any(dest.iterdir()) and not args.force:
            sys.exit(f"[ERROR] {dest} is not empty; pass --force or pick another --dest")
        dest.mkdir(parents=True, exist_ok=True)
        archive = dest / "Everything-SDK.zip"
        print(f"[..] {URL}")
        sha = download(URL, archive)
        note = "" if sha == KNOWN_SHA256 else f"  (not the known {KNOWN_SHA256[:12]}...: a new SDK)"
        print(f"[OK] sha256 {sha}{note}")
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            wanted = [f"dll/{d}" for d in DLLS if f"dll/{d}" in names] + ["include/Everything.h"]
            missing = [w for w in wanted if w not in names]
            if missing or "dll/Everything64.dll" not in names:
                sys.exit(f"[ERROR] the SDK zip lacks {missing or ['dll/Everything64.dll']}")
            for member in wanted:
                target = dest / member
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        notice = notice_of(
            (dest / "include" / "Everything.h").read_text(encoding="utf-8", errors="replace")
        )
        (dest / "EVERYTHING-SDK-NOTICE.txt").write_text(notice + "\n", encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "url": URL,
                    "sha256": sha,
                    "dll": str(dest / "dll" / "Everything64.dll"),
                    "include": str(dest / "include"),
                    "notice": str(dest / "EVERYTHING-SDK-NOTICE.txt"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[OK] {dest / 'dll' / 'Everything64.dll'}")
        print(f"[OK] notice {dest / 'EVERYTHING-SDK-NOTICE.txt'}")
    if not args.no_copy:
        VENDOR.mkdir(parents=True, exist_ok=True)
        for d in DLLS:
            src = dest / "dll" / d
            if src.exists():
                shutil.copy2(src, VENDOR / d)
        shutil.copy2(dest / "EVERYTHING-SDK-NOTICE.txt", VENDOR / "EVERYTHING-SDK-NOTICE.txt")
        print(f"[OK] copied to {VENDOR} (git-ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
