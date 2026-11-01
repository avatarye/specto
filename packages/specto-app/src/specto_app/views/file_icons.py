"""Per-type file tree icons (#122), like VS Code's: a vendored, pinned subset of Material
Icon Theme (MIT) in ``qml/Specto/FileTree/icons/``, written by
``tools/vendor_file_icons.py``: the SVGs the mapping names, the theme's licence, and
``map.json``, the theme's own name, extension and folder-name associations (its light
variants) narrowed to those icons.

A file's icon is looked up by its name (``README.md``), then by its extension, longest
first (``tar.gz`` before ``gz``), then by Specto's data kind of the extension (the
built-in locators: a ``.splat`` the theme does not know is a 3D file here), else the
plain file. A folder's is looked up by its name, open or closed, else the plain folder.
Names are matched lower-case. The QML shows ``icons/<icon>.svg`` at 16 px.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from specto_core.kinds import BUILTIN_LOCATORS

from . import gallery_native

ICON_DIR = gallery_native.QML_DIR / "Specto" / "FileTree" / "icons"
MAP_FILE = ICON_DIR / "map.json"

#: Specto's data kinds -> icon, for extensions the theme has no icon of its own for
KIND_ICONS: dict[str, str] = {
    "image2d": "image",
    "image_seq": "image",
    "video_ref": "video",
    "pointcloud": "3d",
    "mesh": "3d",
    "splat": "3d",
    "usd_stage_ref": "3d",
    "geojson": "json",
    "json": "json",
    "text": "document",
    "markdown": "markdown",
    "html": "html",
    "pdf": "pdf",
    "table": "table",
    "message_stream": "log",
    "pose_track": "json",
}
#: extensions of the data Specto is for that neither the theme nor a built-in locator
#: names: USD layers, lidar scans, OpenDRIVE / OpenSCENARIO files, robot logs
EXTRA_EXTENSIONS: dict[str, str] = {
    "usda": "3d",
    "usdc": "3d",
    "las": "3d",
    "laz": "3d",
    "e57": "3d",
    "pcd": "3d",
    "xodr": "xml",
    "xosc": "xml",
    "mcap": "database",
}


@cache
def icon_map() -> dict[str, Any]:
    """``map.json``: ``file``, ``folder``, ``folderExpanded`` and the ``fileNames``,
    ``fileExtensions``, ``folderNames`` and ``folderNamesExpanded`` tables."""
    return json.loads(MAP_FILE.read_text(encoding="utf-8"))


@cache
def _kind_extensions() -> dict[str, str]:
    """Extension (no dot, lower-case) -> icon, by the first kind of the built-in locator
    that declares it."""
    out: dict[str, str] = {}
    for loc in BUILTIN_LOCATORS:
        icon = next((KIND_ICONS[k] for k in loc.kinds if k in KIND_ICONS), None)
        if icon is None:
            continue
        for ext in loc.extensions:
            out.setdefault(ext.lower().lstrip("."), icon)
    return out


def icon_for(name: str, *, is_dir: bool, expanded: bool = False) -> str:
    """The icon (an SVG's stem in ``ICON_DIR``) of a file or folder called ``name``."""
    m = icon_map()
    low = name.lower()
    if is_dir:
        if expanded:
            return m["folderNamesExpanded"].get(low) or m["folderExpanded"]
        return m["folderNames"].get(low) or m["folder"]
    hit = m["fileNames"].get(low)
    if hit:
        return hit
    dot = low.find(".", 1)  # a leading dot names a dotfile, not an extension
    while dot >= 0:
        ext = low[dot + 1 :]
        hit = m["fileExtensions"].get(ext) or EXTRA_EXTENSIONS.get(ext)
        hit = hit or _kind_extensions().get(ext)
        if hit:
            return hit
        dot = low.find(".", dot + 1)
    return m["file"]


def icon_names() -> list[str]:
    """Every icon the mapping can name: what ``ICON_DIR`` must hold."""
    m = icon_map()
    names = {m["file"], m["folder"], m["folderExpanded"]}
    for table in ("fileNames", "fileExtensions", "folderNames", "folderNamesExpanded"):
        names.update(m[table].values())
    names.update(KIND_ICONS.values())
    names.update(EXTRA_EXTENSIONS.values())
    return sorted(names)


__all__ = ["EXTRA_EXTENSIONS", "ICON_DIR", "KIND_ICONS", "icon_for", "icon_map", "icon_names"]
