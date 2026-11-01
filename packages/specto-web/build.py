"""Vendor the web-page third-party assets offline (no CDN at runtime).

    python packages/specto-web/build.py            # npm install + copy + HDRIs
    python packages/specto-web/build.py --check    # verify only (exit 1 if missing)

Every package in ``VENDORS`` is pinned in ``package.json`` and copied from
``node_modules`` into ``web/vendor/<name>`` (git-ignored); ``web/vendor/<name>.version``
records what was copied. Pages under ``web/`` load from ``/vendor/...`` on their own
specto:// host. Monaco keeps its historical location ``web/vendor/vs``.

The 3D view's HDRI environments (#175) are not npm packages: ``web/3d/environments.json``
pins each one (Poly Haven URL, sha256, author, CC0). They download once into
``node_modules/.hdri`` (checked against the sha256), are prefiltered into
``web/vendor/hdri/<name>.env`` by ``bake_env.py`` (Babylon in headless Qt WebEngine) and
``web/vendor/hdri.version`` records the manifest they came from. Where no WebGL2 context
can bake them the .hdr itself is vendored and the page filters it on load.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
VENDOR = WEB / "vendor"
NODE_MODULES = HERE / "node_modules"
HDRI_MANIFEST = WEB / "3d" / "environments.json"
HDRI_CACHE = NODE_MODULES / ".hdri"
HDRI_VENDOR = VENDOR / "hdri"
HDRI_STAMP = VENDOR / "hdri.version"

#: name -> (npm package, [(source under the package, destination under web/vendor)])
#: The first destination is the one ``--check`` probes.
VENDORS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "monaco": ("monaco-editor", [("min/vs", "vs")]),
    "markdown-it": (
        "markdown-it",
        [("dist/browser/markdown-it.umd.min.js", "markdown-it/markdown-it.umd.min.js")],
    ),
    "github-markdown-css": (
        "github-markdown-css",
        [("github-markdown-light.css", "github-markdown-css/github-markdown-light.css")],
    ),
    "mermaid": ("mermaid", [("dist/mermaid.min.js", "mermaid/mermaid.min.js")]),
    "katex": (
        "katex",
        [
            ("dist/katex.min.js", "katex/katex.min.js"),
            ("dist/katex.min.css", "katex/katex.min.css"),
            ("dist/fonts", "katex/fonts"),
        ],
    ),
    "maplibre-gl": (
        "maplibre-gl",
        [
            ("dist/maplibre-gl.js", "maplibre-gl/maplibre-gl.js"),
            ("dist/maplibre-gl.css", "maplibre-gl/maplibre-gl.css"),
        ],
    ),
    # the 3D view: Babylon.js UMD core + loaders (glTF, SPLAT) and fflate, which the
    # SPLAT loader would otherwise fetch from unpkg for SOG files; the Apache-2.0 NOTICE
    # travels next to the files. Draco/meshopt/KTX2 transcoders are not vendored: a
    # compressed glTF fails loud offline (Tools.ScriptBaseUrl points at this folder).
    "babylon": (
        "babylonjs",
        [
            ("babylon.js", "babylon/babylon.js"),
            ("NOTICE.md", "babylon/NOTICE.md"),
        ],
    ),
    "babylon-loaders": (
        "babylonjs-loaders",
        [("babylonjs.loaders.min.js", "babylon/babylonjs.loaders.min.js")],
    ),
    "fflate": ("fflate", [("umd/index.js", "babylon/fflate.js")]),
    # the SPZ decoder the SPLAT loader imports for v4+ (NGSP) splats through
    # `spzLibraryUrl` (its default is unpkg): one ES module, WebAssembly embedded (#66)
    "spz": ("@adobe/spz", [("dist/spz.js", "babylon/spz.js")]),
}


def pinned_version(package: str) -> str:
    pkg = json.loads((HERE / "package.json").read_text(encoding="utf-8"))
    return pkg["dependencies"][package]


def vendored_version(name: str) -> str | None:
    stamp = VENDOR / f"{name}.version"
    return stamp.read_text(encoding="utf-8").strip() if stamp.exists() else None


def check(names: list[str] | None = None) -> bool:
    ok = True
    for name in names or VENDORS:
        package, files = VENDORS[name]
        want = pinned_version(package)
        have = vendored_version(name)
        probe = VENDOR / files[0][1]
        if probe.exists() and have == want:
            print(f"[OK] {package} {have} vendored at {probe}")
        else:
            print(f"[ERROR] {package} {want} not vendored (have {have}); run {Path(__file__).name}")
            ok = False
    return ok


# -- the 3D view's HDRI environments (#175) -------------------------------------------------
def hdri_manifest() -> dict:
    return json.loads(HDRI_MANIFEST.read_text(encoding="utf-8"))


def hdri_stamp(manifest: dict | None = None) -> str:
    """What ``hdri.version`` holds: the manifest's pins and cube size, hashed (a changed
    HDRI, size or list re-bakes)."""
    m = manifest or hdri_manifest()
    pins = [[e["name"], e["sha256"]] for e in m["environments"]] + [m.get("cube_size", 256)]
    return hashlib.sha256(json.dumps(pins).encode("utf-8")).hexdigest()[:16]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_hdri(manifest: dict | None = None) -> dict[str, Path]:
    """Each manifest HDRI in the download cache, verified: name -> .hdr path."""
    out: dict[str, Path] = {}
    HDRI_CACHE.mkdir(parents=True, exist_ok=True)
    for e in (manifest or hdri_manifest())["environments"]:
        dst = HDRI_CACHE / Path(e["url"]).name
        if not dst.is_file() or sha256_of(dst) != e["sha256"]:
            print(f"[..] fetch {e['url']}")
            req = urllib.request.Request(e["url"], headers={"User-Agent": "specto-build"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                dst.write_bytes(resp.read())
            got = sha256_of(dst)
            if got != e["sha256"]:
                dst.unlink()
                raise SystemExit(f"[ERROR] {e['url']}: sha256 {got}, pinned {e['sha256']}")
        out[e["name"]] = dst
    return out


def bake_hdri(sources: dict[str, Path], manifest: dict | None = None) -> None:
    """``vendor/hdri/<name>.env`` from each .hdr (bake_env.py); the .hdr itself when it
    cannot bake. Writes the credits and the stamp."""
    m = manifest or hdri_manifest()
    if HDRI_VENDOR.is_dir():
        shutil.rmtree(HDRI_VENDOR)
    HDRI_VENDOR.mkdir(parents=True)
    babylon = VENDOR / VENDORS["babylon"][1][0][1]
    args = [str(a) for name, src in sources.items() for a in (src, HDRI_VENDOR / f"{name}.env")]
    size = str(m.get("cube_size", 256))
    bake = [sys.executable, str(HERE / "bake_env.py"), "--babylon", str(babylon), "--size", size]
    rc = subprocess.run([*bake, *args], check=False).returncode
    if rc != 0:
        print("[WARN] could not prefilter the HDRIs (no WebGL2 here?): vendoring the .hdr")
        print("       files instead; the page filters them on every load")
        for name, src in sources.items():
            (HDRI_VENDOR / f"{name}.env").unlink(missing_ok=True)
            shutil.copy2(src, HDRI_VENDOR / f"{name}.hdr")
    lines = [
        "# HDRI environments of the 3D view",
        "",
        f"From Poly Haven, licence {m.get('licence', 'CC0-1.0')} ({m.get('licence_url', '')}).",
        "",
    ]
    for e in m["environments"]:
        lines.append(
            f"- {e['name']}: {e['asset']} by {e['author']}, {e['page']} (sha256 {e['sha256']})"
        )
    (HDRI_VENDOR / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    HDRI_STAMP.write_text(hdri_stamp(m) + "\n", encoding="utf-8")
    print(f"[OK] {len(sources)} HDRI environments -> {HDRI_VENDOR}")


def check_hdri() -> bool:
    m = hdri_manifest()
    have = HDRI_STAMP.read_text(encoding="utf-8").strip() if HDRI_STAMP.exists() else None
    missing = [
        e["name"]
        for e in m["environments"]
        if not any((HDRI_VENDOR / f"{e['name']}{ext}").is_file() for ext in (".env", ".hdr"))
    ]
    if have == hdri_stamp(m) and not missing:
        print(f"[OK] {len(m['environments'])} HDRI environments vendored at {HDRI_VENDOR}")
        return True
    print(
        f"[ERROR] HDRI environments not vendored (missing {missing or 'none'}, stamp {have});"
        f" run {Path(__file__).name} --hdri-only"
    )
    return False


def npm_install() -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("[ERROR] npm not found on PATH; install Node.js (>= 20)")
    print("[..] npm install (" + ", ".join(p for p, _ in VENDORS.values()) + ")")
    subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=HERE, check=True)


def copy_one(name: str) -> None:
    package, files = VENDORS[name]
    src_root = NODE_MODULES / package
    if not src_root.is_dir():
        raise SystemExit(f"[ERROR] {src_root} missing after npm install")
    for src_rel, dst_rel in files:
        src = src_root / src_rel
        dst = VENDOR / dst_rel
        if not src.exists():
            raise SystemExit(f"[ERROR] {src} missing in {package}")
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    meta = json.loads((src_root / "package.json").read_text(encoding="utf-8"))
    (VENDOR / f"{name}.version").write_text(meta["version"] + "\n", encoding="utf-8")
    for lic_name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "license", "license.md"):
        lic = src_root / lic_name
        if lic.exists():
            shutil.copy(lic, VENDOR / f"{name}.LICENSE")
            break
    print(f"[OK] copied {package} {meta['version']} -> {VENDOR / files[0][1]}")


def copy_all() -> None:
    for name in VENDORS:
        copy_one(name)


def copy_monaco() -> None:  # kept for callers of the old name
    copy_one("monaco")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="verify the vendored assets only")
    ap.add_argument("--no-install", action="store_true", help="skip npm install, copy only")
    ap.add_argument("--no-hdri", action="store_true", help="skip the HDRI environments")
    ap.add_argument("--hdri-only", action="store_true", help="fetch and bake the HDRIs only")
    args = ap.parse_args(argv)
    if args.hdri_only:
        bake_hdri(fetch_hdri())
        return 0 if check_hdri() else 1
    if args.check:
        return 0 if check() and (args.no_hdri or check_hdri()) else 1
    if not args.no_install:
        npm_install()
    copy_all()
    if not args.no_hdri:
        bake_hdri(fetch_hdri())
    return 0 if check() and (args.no_hdri or check_hdri()) else 1


if __name__ == "__main__":
    sys.exit(main())
