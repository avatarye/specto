"""Locator for the Specto web-page assets (pages, ``bridge.js``, vendored Monaco, the 3D
view's HDRI environments).

The assets are plain files: ``packages/specto-web/web`` in a source checkout (what the
uv workspace installs, editable), or ``specto_web/web`` inside a built wheel. Monaco is
vendored offline by ``packages/specto-web/build.py`` (``npm install`` + copy into
``web/vendor/vs``); ``SPECTO_WEB_ROOT`` overrides the lookup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__version__ = "0.2.0"

ENV_VAR = "SPECTO_WEB_ROOT"
MONACO_VERSION_FILE = "vendor/monaco.version"
#: the 3D view's bundled HDRIs (#175): the committed manifest (name, label, source URL,
#: author, licence, sha256) and where build.py puts each one, prefiltered (``.env``) or,
#: when no GPU could bake it, the source ``.hdr`` as it came
ENVIRONMENTS_FILE = "3d/environments.json"
HDRI_DIR = "vendor/hdri"
HDRI_FORMATS = (".env", ".hdr")


def candidates() -> list[Path]:
    """Where the web root may be, in lookup order."""
    out: list[Path] = []
    env = os.environ.get(ENV_VAR)
    if env:
        out.append(Path(env))
    here = Path(__file__).resolve().parent
    out.append(here / "web")  # wheel layout
    out.append(here.parent.parent / "web")  # source checkout: packages/specto-web/web
    return out


def find_web_root() -> Path | None:
    """The first candidate that holds ``bridge.js``; ``None`` when none does."""
    for c in candidates():
        if (c / "bridge.js").is_file():
            return c
    return None


def web_root() -> Path:
    root = find_web_root()
    if root is None:
        raise FileNotFoundError(
            f"specto-web assets not found (set {ENV_VAR} or run from the source checkout); "
            f"tried: {', '.join(str(c) for c in candidates())}"
        )
    return root


def monaco_dir(root: Path | None = None) -> Path:
    return (root or web_root()) / "vendor" / "vs"


def monaco_ok(root: Path | None = None) -> bool:
    """True when Monaco is vendored (``vendor/vs/loader.js`` exists)."""
    return (monaco_dir(root) / "loader.js").is_file()


def monaco_version(root: Path | None = None) -> str | None:
    stamp = (root or web_root()) / MONACO_VERSION_FILE
    return stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else None


def environments_manifest(root: Path | None = None) -> dict[str, Any]:
    """The committed HDRI manifest; an empty one when the web root is not found."""
    base = root or find_web_root()
    if base is None or not (base / ENVIRONMENTS_FILE).is_file():
        return {"environments": []}
    return json.loads((base / ENVIRONMENTS_FILE).read_text(encoding="utf-8"))


def environments(root: Path | None = None) -> list[dict[str, Any]]:
    """The bundled environments in menu order: one dict per HDRI of the manifest."""
    return list(environments_manifest(root).get("environments") or [])


def environment_file(name: str, root: Path | None = None) -> Path | None:
    """The vendored file of environment ``name`` (``.env`` before ``.hdr``), or None
    while build.py has not fetched it."""
    base = root or find_web_root()
    if base is None:
        return None
    for ext in HDRI_FORMATS:
        f = base / HDRI_DIR / f"{name}{ext}"
        if f.is_file():
            return f
    return None


#: where build.py keeps each HDRI's ``.hdr`` as downloaded (sha256-checked), beside the
#: prefiltered ``.env`` it bakes: a source checkout's ``packages/specto-web/node_modules/.hdri``
HDRI_CACHE = "node_modules/.hdri"


def hdri_file_name(entry: dict[str, Any]) -> str:
    """The file name of a manifest entry's HDRI: the last segment of its URL, what build.py's
    cache and the user's folder (``specto_app.usd.skies``) both name the ``.hdr`` by."""
    return str(entry.get("url", "")).rsplit("/", 1)[-1]


def hdri_source(name: str, root: Path | None = None) -> Path | None:
    """The source ``.hdr`` of environment ``name`` (SPEC-31: the usd view lights a stage with
    it; a dome light reads ``.hdr``, not Babylon's ``.env``): the file build.py downloaded
    into its cache, named as the manifest's URL names it, at the manifest's size (a build
    killed mid-write leaves nothing at that name, SPEC-45, but an older one may have left a
    truncated file: never served); None until build.py has run, or outside a source
    checkout."""
    base = root or find_web_root()
    entry = next((e for e in environments(base) if e.get("name") == name), None)
    if base is None or entry is None:
        return None
    f = base.parent / HDRI_CACHE / hdri_file_name(entry)
    try:
        whole = f.is_file() and f.stat().st_size == int(entry.get("size", -1))
    except OSError:
        return None
    return f if whole else None


BUILD_HINT = "run: uv run python packages/specto-web/build.py  (npm install + offline copy)"

__all__ = [
    "BUILD_HINT",
    "ENVIRONMENTS_FILE",
    "ENV_VAR",
    "HDRI_CACHE",
    "HDRI_DIR",
    "HDRI_FORMATS",
    "candidates",
    "environment_file",
    "environments",
    "environments_manifest",
    "find_web_root",
    "hdri_file_name",
    "hdri_source",
    "monaco_dir",
    "monaco_ok",
    "monaco_version",
    "web_root",
]
