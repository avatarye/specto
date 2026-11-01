"""Recent projects (HLD 5.6, M1.5 project open/new): a small JSON list in the user data
dir, newest first. Qt-free; the welcome screen and the project menu read it, the app
writes it whenever a project is opened. Reading lives in ``specto_core.recents`` so the
CLI lists them without the app (``specto open --list``, #215).

    ~/.local/share/specto/recents.json  (platformdirs.user_data_dir("specto"))
    {"projects": [{"root": "...", "name": "...", "opened": 1760000000.0}, ...]}
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from specto_core.instances import user_dir as user_data_dir
from specto_core.recents import MAX_RECENTS, is_project, load_recents, recents_path

APP_NAME = "specto"


def _save(entries: list[dict[str, Any]]) -> None:
    path = recents_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = [{"root": e["root"], "name": e["name"], "opened": e["opened"]} for e in entries]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"projects": slim}, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def remember(root: Path | str, name: str | None = None) -> list[dict[str, Any]]:
    """Put ``root`` at the top of the list (added or moved)."""
    root_s = str(Path(root).resolve())
    entries = [e for e in load_recents() if not _same(e["root"], root_s)]
    entries.insert(
        0,
        {
            "root": root_s,
            "name": name or Path(root_s).name,
            "opened": time.time(),
            "missing": not is_project(root_s),
        },
    )
    entries = entries[:MAX_RECENTS]
    _save(entries)
    return entries


def forget(root: Path | str) -> list[dict[str, Any]]:
    root_s = str(Path(root).resolve())
    entries = [e for e in load_recents() if not _same(e["root"], root_s)]
    _save(entries)
    return entries


def _same(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (OSError, ValueError):
        return a == b


__all__ = [
    "MAX_RECENTS",
    "forget",
    "is_project",
    "load_recents",
    "recents_path",
    "remember",
    "user_data_dir",
]
