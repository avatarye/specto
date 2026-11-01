"""Fetch libmpv (library + headers) into ~/specto-build/libmpv (SPECTO_BUILD_ROOT or SPECTO_LIBMPV_DIR move it).

The ``video`` view's native ``MpvItem`` (packages/specto-app/native/mpvitem.cpp) loads
``libmpv-2.dll`` at runtime and compiles against ``include/mpv/*.h``. Both come from the
shinchiro ``mpv-dev-x86_64-<date>-git-<hash>.7z`` release (MinGW build, plain C ABI, the
same DLL python-mpv uses). Pinned by default; ``--tag latest`` asks GitHub for the newest.

    uv run python tools/fetch_libmpv.py                     # pinned release -> LIBMPV_DIR
    uv run python tools/fetch_libmpv.py --tag latest
    uv run python tools/fetch_libmpv.py --dest D:/libmpv    # or SPECTO_LIBMPV_DIR

Layout after a run (what native/build.py and the item look for):

    <dest>/libmpv-2.dll            the library (loaded at runtime, never linked)
    <dest>/include/mpv/*.h         client.h, render.h, render_gl.h, stream_cb.h
    <dest>/libmpv.json             {tag, asset, sha256, extracted}

The archive is downloaded data: it is extracted with ``7z`` (PATH) or ``py7zr`` from a
throwaway venv; nothing from inside the directory is executed. The build copies the DLL
next to the plugin (git-ignored); the item also honours ``SPECTO_LIBMPV`` (a DLL path)
and ``SPECTO_LIBMPV_DIR`` at runtime.

Linux: the normal route is the distribution package (``sudo apt install libmpv-dev``);
the plugin then compiles against ``/usr/include/mpv`` and loads ``libmpv.so.2`` from the
loader path, and this script only reports that. Without root on Debian/Ubuntu it fetches
the distribution's own ``libmpv2`` and ``libmpv-dev`` debs (URIs and SHA512 from
``apt-get download --print-uris``, so the pool and the hashes are apt's), unpacks them
with ``dpkg-deb -x`` (no maintainer scripts run), adds the debs of any shared library
``libmpv.so.2`` needs that this machine lacks (``readelf`` NEEDED against
``ldconfig -p``), and sets ``RUNPATH=$ORIGIN`` with patchelf (the system one when
installed, else the pinned ``PATCHELF_WHEEL`` through ``uvx --no-config``, public PyPI)
so those resolve next to it:

    <dest>/libmpv.so.2             the library plus any missing deps beside it
    <dest>/include/mpv/*.h
    <dest>/libmpv.json             {source, packages: {name: {file, sha512}}}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = "shinchiro/mpv-winbuild-cmake"
PINNED_TAG = "20261013"
ASSET_PREFIX = "mpv-dev-x86_64-"  # not the -v3 (AVX2-only) build
_BUILD_ROOT = os.environ.get("SPECTO_BUILD_ROOT", str(Path.home() / "specto-build"))
DEFAULT_DEST = Path(os.environ.get("SPECTO_LIBMPV_DIR", str(Path(_BUILD_ROOT) / "libmpv")))
HEADERS = ("client.h", "render.h", "render_gl.h", "stream_cb.h")
LINUX_SONAME = "libmpv.so.2"
#: the patchelf wheel ``uvx`` runs when no system patchelf is on PATH (0.19.1, 2026-08)
PATCHELF_WHEEL = "patchelf==0.19.1.0"
DEB_PACKAGES = ("libmpv2", "libmpv-dev")
# apt may sit behind a custom transport (zim://, mirror+file://): the pool path is the
# same on the public archive, and the SHA512 apt reports is checked either way
APT_MIRROR = os.environ.get("SPECTO_APT_MIRROR", "http://archive.ubuntu.com/ubuntu")


def release(tag: str) -> dict:
    url = f"https://api.github.com/repos/{REPO}/releases/"
    url += "latest" if tag == "latest" else f"tags/{tag}"
    req = urllib.request.Request(url, headers={"User-Agent": "specto-fetch-libmpv"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def pick_asset(rel: dict) -> dict:
    for a in rel.get("assets", []):
        name = a["name"]
        if name.startswith(ASSET_PREFIX) and name.endswith(".7z") and "-v3-" not in name:
            return a
    names = [a["name"] for a in rel.get("assets", [])]
    sys.exit(f"[ERROR] no {ASSET_PREFIX}*.7z asset in release {rel.get('tag_name')}: {names}")


def download(url: str, dest: Path) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "specto-fetch-libmpv"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total:
                print(f"\r[..] {done * 100 // total:3d}% of {total / 1e6:.1f} MB", end="")
    print()
    return h.hexdigest()


def extract(archive: Path, into: Path) -> None:
    seven = shutil.which("7z") or shutil.which("7za")
    if seven:
        subprocess.check_call([seven, "x", "-y", f"-o{into}", str(archive)], cwd=str(into.parent))
        return
    # fallback: py7zr in a throwaway venv (uv), removed afterwards
    venv = Path(tempfile.mkdtemp(prefix="specto-py7zr-"))
    try:
        subprocess.check_call(["uv", "venv", "--no-config", str(venv), "-q"])
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.check_call(
            ["uv", "pip", "install", "--no-config", "--python", str(py), "-q", "py7zr"]
        )
        code = "import sys, py7zr; py7zr.SevenZipFile(sys.argv[1]).extractall(sys.argv[2])"
        subprocess.check_call([str(py), "-I", "-c", code, str(archive), str(into)])
    finally:
        shutil.rmtree(venv, ignore_errors=True)


def layout(dest: Path) -> tuple[Path, Path]:
    """Find the DLL and the include dir wherever the archive put them."""
    dll = next(dest.rglob("libmpv-2.dll"), None)
    inc = next((p.parent.parent for p in dest.rglob("mpv/client.h")), None)
    if dll is None or inc is None:
        sys.exit(
            f"[ERROR] extracted archive lacks libmpv-2.dll or include/mpv/client.h under {dest}"
        )
    return dll, inc


# --- Linux (Debian/Ubuntu) -------------------------------------------------------------


def system_sonames() -> set[str]:
    out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
    return {line.split()[0] for line in out.splitlines()[1:] if line.strip()}


def system_libmpv() -> bool:
    return Path("/usr/include/mpv/render_gl.h").exists() and LINUX_SONAME in system_sonames()


def deb_uri(pkg: str) -> tuple[str, str, str] | None:
    """(url, filename, sha512) for ``pkg`` from apt's own metadata, or None."""
    r = subprocess.run(["apt-get", "download", "--print-uris", pkg], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].startswith("SHA512:"):
            uri = parts[0].strip("'")
            if not uri.startswith(("http://", "https://")):
                if "/pool/" not in uri:
                    return None
                uri = f"{APT_MIRROR.rstrip('/')}/pool/{uri.split('/pool/', 1)[1]}"
            return uri, parts[1], parts[3].removeprefix("SHA512:")
    return None


def soname_package(soname: str) -> str:
    """Debian's library package naming: libXpresent.so.1 -> libxpresent1,
    libfoo2.so.3 -> libfoo2-3."""
    name, _, ver = soname.partition(".so.")
    name = name.lower()
    return f"{name}-{ver}" if name[-1:].isdigit() else f"{name}{ver}"


def needed(lib: Path) -> list[str]:
    out = subprocess.run(["readelf", "-d", "--wide", str(lib)], capture_output=True, text=True)
    return [
        line.rsplit("[", 1)[1].rstrip("]") for line in out.stdout.splitlines() if "(NEEDED)" in line
    ]


def fetch_deb(pkg: str, work: Path, packages: dict) -> Path:
    """Download ``pkg``'s deb into ``work`` (sha512-checked) and unpack it into a fresh
    directory; returns that directory."""
    found = deb_uri(pkg)
    if found is None:
        sys.exit(f"[ERROR] apt knows no package {pkg!r} (apt-get download --print-uris)")
    url, fname, sha512 = found
    deb = work / fname
    print(f"[..] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "specto-fetch-libmpv"})
    with urllib.request.urlopen(req, timeout=120) as r:
        deb.write_bytes(r.read())
    got = hashlib.sha512(deb.read_bytes()).hexdigest()
    if got != sha512:
        sys.exit(f"[ERROR] {fname}: sha512 {got} != apt's {sha512}")
    into = work / pkg
    into.mkdir()
    subprocess.check_call(["dpkg-deb", "-x", str(deb), str(into)])
    packages[pkg] = {"file": fname, "url": url, "sha512": sha512}
    return into


def main_linux(dest: Path, force: bool) -> int:
    if system_libmpv() and not force:
        print("[OK] system libmpv present (/usr/include/mpv, libmpv.so.2); nothing to fetch")
        return 0
    manifest = dest / "libmpv.json"
    if manifest.exists() and not force:
        print(f"[OK] libmpv already present at {dest} (use --force to refetch)")
        return 0
    if dest.exists() and any(dest.iterdir()) and not force:
        sys.exit(f"[ERROR] {dest} is not empty; pass --force or pick another --dest")
    for tool in ("apt-get", "dpkg-deb", "readelf", "ldconfig"):
        if not shutil.which(tool):
            sys.exit(
                f"[ERROR] {tool} not found: install libmpv-dev with your package manager "
                "and rebuild the native plugin"
            )
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="specto-libmpv-"))
    packages: dict = {}
    try:
        for pkg in DEB_PACKAGES:
            fetch_deb(pkg, work, packages)
        libs = {p.name: p for p in work.rglob("*.so*") if p.is_file() or p.is_symlink()}
        if LINUX_SONAME not in libs or not next(work.rglob("mpv/render_gl.h"), None):
            sys.exit(f"[ERROR] the debs lack {LINUX_SONAME} or mpv/render_gl.h")
        shutil.copy2(libs[LINUX_SONAME].resolve(), dest / LINUX_SONAME)
        (dest / "include" / "mpv").mkdir(parents=True)
        inc = next(work.rglob("mpv/render_gl.h")).parent
        for h in HEADERS:
            if (inc / h).exists():
                shutil.copy2(inc / h, dest / "include" / "mpv" / h)
        have = system_sonames()
        queue, placed = [dest / LINUX_SONAME], [dest / LINUX_SONAME]
        while queue:
            for soname in needed(queue.pop()):
                if soname in have or (dest / soname).exists():
                    continue
                pkg = soname_package(soname)
                into = fetch_deb(pkg, work, packages)
                lib = next((p for p in into.rglob(soname)), None)
                if lib is None:
                    sys.exit(f"[ERROR] {pkg} does not ship {soname}; install it yourself")
                shutil.copy2(lib.resolve(), dest / soname)
                print(f"[OK] dependency {soname} from {pkg}")
                queue.append(dest / soname)
                placed.append(dest / soname)
        patchelf = patchelf_argv()
        for lib in placed:
            subprocess.check_call([*patchelf, "--set-rpath", "$ORIGIN", str(lib)])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    manifest.write_text(
        json.dumps(
            {
                "source": "apt",
                "packages": packages,
                "library": str(dest / LINUX_SONAME),
                "include": str(dest / "include"),
            },
            indent=2,
        )
    )
    print(f"[OK] {dest / LINUX_SONAME} ({len(placed) - 1} bundled deps)")
    print(f"[OK] {dest / 'include' / 'mpv'}: {', '.join(HEADERS)}")
    print(f"[OK] manifest {manifest}")
    return 0


def patchelf_argv() -> list[str]:
    """The system ``patchelf`` when PATH has one (the distribution's build, no download),
    else the pinned wheel run through ``uvx``: never an unpinned fetch from PyPI."""
    found = shutil.which("patchelf")
    if found:
        return [found]
    return ["uvx", "--no-config", PATCHELF_WHEEL]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--tag", default=PINNED_TAG, help=f"release tag or 'latest' (default {PINNED_TAG})"
    )
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--force", action="store_true", help="refetch into a non-empty dest")
    args = ap.parse_args()
    dest: Path = args.dest
    if sys.platform.startswith("linux"):
        return main_linux(dest, args.force)
    manifest = dest / "libmpv.json"
    if manifest.exists() and not args.force:
        info = json.loads(manifest.read_text())
        print(f"[OK] libmpv already present: {info.get('tag')} at {dest} (use --force to refetch)")
        return 0
    if dest.exists() and any(dest.iterdir()) and not args.force:
        sys.exit(f"[ERROR] {dest} is not empty; pass --force or pick another --dest")
    dest.mkdir(parents=True, exist_ok=True)

    rel = release(args.tag)
    asset = pick_asset(rel)
    tag = rel["tag_name"]
    print(f"[OK] release {tag}: {asset['name']} ({asset['size'] / 1e6:.1f} MB)")
    archive = dest / asset["name"]
    if archive.exists() and archive.stat().st_size == asset["size"]:
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        print(f"[OK] archive already downloaded: {archive.name}")
    else:
        sha = download(asset["browser_download_url"], archive)
    print(f"[OK] sha256 {sha}")
    extracted = dest / "mpv-dev"
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir()
    extract(archive, extracted)
    dll, inc = layout(extracted)
    # flatten: <dest>/libmpv-2.dll and <dest>/include/mpv/*.h
    shutil.copy2(dll, dest / dll.name)
    (dest / "include" / "mpv").mkdir(parents=True, exist_ok=True)
    for h in HEADERS:
        src = inc / "mpv" / h
        if src.exists():
            shutil.copy2(src, dest / "include" / "mpv" / h)
    manifest.write_text(
        json.dumps(
            {
                "repo": REPO,
                "tag": tag,
                "asset": asset["name"],
                "url": asset["browser_download_url"],
                "sha256": sha,
                "dll": str(dest / dll.name),
                "include": str(dest / "include"),
                "extracted": str(extracted),
            },
            indent=2,
        )
    )
    print(f"[OK] {dest / dll.name}")
    print(f"[OK] {dest / 'include' / 'mpv'}: {', '.join(HEADERS)}")
    print(f"[OK] manifest {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
