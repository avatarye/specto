"""Vendor the file tree's icons (#122) from Material Icon Theme (MIT), pinned, offline.

    uv run python tools/vendor_file_icons.py            # fetch the pinned package, write
    uv run python tools/vendor_file_icons.py --check    # verify only (exit 1 on a gap)

``npm pack`` fetches ``material-icon-theme@VERSION`` into a temporary folder; its
``dist/material-icons.json`` (the VS Code icon theme manifest) gives the associations.
Only the icons in ``FILE_ICONS`` and ``FOLDER_ICONS`` (plus what
``specto_app.views.file_icons`` maps Specto's data kinds and extra extensions to) are
copied into ``qml/Specto/FileTree/icons/``, with the theme's light variants where it
has them (Specto's theme is light), the licence, a README naming the pin, and
``map.json``: the manifest's file-name, extension and folder-name tables narrowed to
those icons. The output is committed; nothing is fetched at runtime.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import specto_core  # noqa: F401  (pydantic before PySide6, as the app imports them)
from specto_app.views.file_icons import EXTRA_EXTENSIONS, ICON_DIR, icon_names

PACKAGE = "material-icon-theme"
VERSION = "5.39.0"

#: file icons: Specto's data kinds, then common source, config and document types
FILE_ICONS = {
    "file", "image", "video", "audio", "pdf", "markdown", "json", "html", "document",
    "table", "yaml", "toml", "xml", "log", "zip", "3d", "blender", "svg", "font",
    "python", "python-misc", "jupyter", "javascript", "typescript", "react", "react_ts",
    "rust", "csharp", "lua", "c", "h", "cpp", "hpp", "go", "java", "kotlin", "swift",
    "ruby", "php", "css", "sass", "shader", "webassembly", "database", "console",
    "powershell", "exe", "dll", "hex", "settings", "tune", "lock", "key", "certificate",
    "diff", "git", "tex", "word", "powerpoint", "subtitles", "pytorch", "onnx",
    "readme", "license", "changelog", "docker", "makefile", "nodejs", "uv",
}  # fmt: skip
#: folder icons (each with its ``-open`` twin)
FOLDER_ICONS = {
    "folder", "folder-images", "folder-video", "folder-audio", "folder-docs",
    "folder-src", "folder-test", "folder-config", "folder-scripts", "folder-database",
    "folder-dist", "folder-log", "folder-archive", "folder-json", "folder-markdown",
    "folder-git", "folder-github", "folder-node", "folder-python", "folder-vscode",
    "folder-resource", "folder-public", "folder-temp", "folder-tools", "folder-lib",
    "folder-packages", "folder-plugin", "folder-app", "folder-api", "folder-server",
    "folder-client", "folder-components", "folder-core", "folder-views",
    "folder-content", "folder-ci", "folder-examples", "folder-template", "folder-font",
    "folder-export", "folder-download", "folder-upload", "folder-meta", "folder-robot",
    "folder-benchmark", "folder-debug", "folder-keys", "folder-review", "folder-job",
    "folder-tasks", "folder-claude", "folder-mock", "folder-environment", "folder-shader",
}  # fmt: skip

README = """# File tree icons

A subset of [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme)
({pin}, MIT, see `LICENSE`): only the icons Specto's file tree maps (#122). `map.json`
holds the theme's own file-name, extension and folder-name associations narrowed to
these icons, light variants preferred. Regenerate with
`uv run python tools/vendor_file_icons.py`; never edit by hand.
"""


def _base(icon: str) -> str:
    """``folder-images-open`` -> ``folder-images``; ``toml_light`` -> ``toml``."""
    icon = icon.removesuffix("_light")
    return icon.removesuffix("-open") if icon.startswith("folder") else icon


def narrow(manifest: dict) -> dict:
    """The manifest's tables narrowed to the curated icons, light variants applied."""
    keep = FILE_ICONS | FOLDER_ICONS
    light = manifest.get("light", {})
    out: dict = {
        "source": f"{PACKAGE}@{VERSION}",
        "file": manifest["file"],
        "folder": manifest["folder"],
        "folderExpanded": manifest["folderExpanded"],
    }
    for table in ("fileNames", "fileExtensions", "folderNames", "folderNamesExpanded"):
        merged = {**manifest.get(table, {}), **light.get(table, {})}
        out[table] = {k.lower(): v for k, v in sorted(merged.items()) if _base(v) in keep}
    return out


def fetch(dest: Path) -> Path:
    """``npm pack`` the pinned package into ``dest`` and unpack it; returns ``package/``."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("[ERROR] npm not found")
    subprocess.run(
        [npm, "pack", f"{PACKAGE}@{VERSION}", "--pack-destination", str(dest)],
        check=True,
        capture_output=True,
    )
    (tgz,) = dest.glob("*.tgz")
    with tarfile.open(tgz) as tar:
        tar.extractall(dest, filter="data")
    return dest / "package"


def write(pkg: Path) -> None:
    manifest = json.loads((pkg / "dist" / "material-icons.json").read_text(encoding="utf-8"))
    mapping = narrow(manifest)
    if ICON_DIR.exists():
        shutil.rmtree(ICON_DIR)
    ICON_DIR.mkdir(parents=True)
    (ICON_DIR / "map.json").write_text(
        json.dumps(mapping, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    for name in icon_names():
        src = pkg / "icons" / f"{name}.svg"
        if not src.is_file():
            raise SystemExit(f"[ERROR] {PACKAGE}@{VERSION} has no icon {name!r}")
        shutil.copyfile(src, ICON_DIR / f"{name}.svg")
    shutil.copyfile(pkg / "LICENSE", ICON_DIR / "LICENSE")
    (ICON_DIR / "README.md").write_text(
        README.format(pin=f"`{PACKAGE}@{VERSION}`"), encoding="utf-8", newline="\n"
    )
    print(f"[OK] {len(icon_names())} icons from {PACKAGE}@{VERSION} into {ICON_DIR}")


def check() -> int:
    if not (ICON_DIR / "map.json").is_file():
        print(f"[ERROR] {ICON_DIR / 'map.json'} missing: run tools/vendor_file_icons.py")
        return 1
    missing = [n for n in icon_names() if not (ICON_DIR / f"{n}.svg").is_file()]
    for n in missing:
        print(f"[ERROR] icon {n}.svg missing")
    extra = {p.stem for p in ICON_DIR.glob("*.svg")} - set(icon_names())
    for n in sorted(extra):
        print(f"[WARN] icon {n}.svg is not mapped")
    if not missing:
        print(f"[OK] {len(icon_names())} icons ({len(EXTRA_EXTENSIONS)} extra extensions)")
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify only")
    args = ap.parse_args()
    if args.check:
        return check()
    with tempfile.TemporaryDirectory(prefix="specto-icons-") as tmp:
        write(fetch(Path(tmp)))
    return check()


if __name__ == "__main__":
    sys.exit(main())
