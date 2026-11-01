"""Reading the recent projects list (HLD 5.6): ``<user dir>/recents.json``, newest
first, as the welcome screen shows it and ``specto open --list`` prints it (#215).
Qt-free and app-free; the app (``specto_app.recents``) writes the file.

    {"projects": [{"root": "...", "name": "...", "opened": 1760000000.0}, ...]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .instances import user_dir
from .project import PROJECT_FILE, SPECTO_DIR

RECENTS_FILE = "recents.json"
MAX_RECENTS = 12


def recents_path() -> Path:
    return user_dir() / RECENTS_FILE


def is_project(root: Path | str) -> bool:
    return (Path(root) / SPECTO_DIR / PROJECT_FILE).is_file()


def load_recents() -> list[dict[str, Any]]:
    """Newest first. Entries whose folder vanished are kept but flagged ``missing``."""
    try:
        data = json.loads(recents_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for e in data.get("projects", []) if isinstance(data, dict) else []:
        root = str(e.get("root", "")).strip()
        if not root:
            continue
        out.append(
            {
                "root": root,
                "name": str(e.get("name") or Path(root).name),
                "opened": float(e.get("opened") or 0),
                "missing": not is_project(root),
            }
        )
    out.sort(key=lambda e: -e["opened"])
    return out[:MAX_RECENTS]


__all__ = ["MAX_RECENTS", "RECENTS_FILE", "is_project", "load_recents", "recents_path"]
