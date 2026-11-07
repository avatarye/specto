"""Project folders the GUI sessions may wipe (SPEC-7): only folders a session created.

A session that creates its project folder (``prepare_project`` on a path that does not
exist yet, ``session_root`` when no folder was named, or the suite's seeded projects)
marks it with ``.specto/gui-scratch``: its owner, the folder's resolved path, the
creating pid and a nonce. ``--fresh`` wipes a folder whose mark names that same path, or
an empty folder, and refuses anything else, so a typo, a reused path or a copied folder
can never delete someone's data. The mark lives under ``.specto/``, which the app's file
tree never lists.

    from gui_scratch import prepare_project, session_root, remove_session_root
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from desktop_lock import pid_alive

MARKER = Path(".specto") / "gui-scratch"
#: the per-session roots' folder name prefix, under the temp dir
ROOT_PREFIX = "specto-gui-"
#: a per-session root older than this whose creator is gone is swept
SWEEP_AGE_S = 24 * 3600.0


def owner(project: Path) -> dict | None:
    """The mark's record (``path``, ``pid``, ``nonce``, ``created``), when it names this
    folder; ``None`` for no mark, an unreadable one, or one copied from another folder."""
    try:
        info = json.loads((Path(project) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("path") != str(Path(project).resolve()):
        return None
    return info


def is_scratch(project: Path) -> bool:
    """Whether a GUI session created ``project`` (its mark names this folder)."""
    return owner(project) is not None


def mark(project: Path) -> None:
    """Mark ``project`` as a GUI session's own (a folder it created or just wiped)."""
    project = Path(project)
    marker = project / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "path": str(project.resolve()),
        "pid": os.getpid(),
        "nonce": secrets.token_hex(4),
        "created": time.time(),
    }
    marker.write_text(json.dumps(record) + "\n", encoding="utf-8")


def prepare_project(project: Path, *, fresh: bool) -> None:
    """Make ``project`` ready for a session. A folder that does not exist is created and
    marked. ``fresh`` first wipes a folder marked as this one, or an empty folder; any
    other folder with content is refused (``SystemExit``) and left as it is."""
    project = Path(project)
    if fresh and project.exists():
        if not is_scratch(project) and any(project.iterdir()):
            raise SystemExit(
                f"[ERROR] --fresh wipes only a folder a GUI session created; {project} has "
                f"content and no {MARKER.as_posix()} mark naming it. Pick a new folder (or "
                "leave --project out for a per-session one), or empty it yourself"
            )
        shutil.rmtree(project)
    created = not project.exists()
    project.mkdir(parents=True, exist_ok=True)
    if created:
        mark(project)


def session_root() -> Path:
    """A new marked project folder of this session's own, under the temp dir
    (``<temp>/specto-gui-<random>/project``)."""
    project = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX)) / "project"
    project.mkdir()
    mark(project)
    return project


def _rmtree(root: Path) -> str:
    """Remove ``root`` as far as it goes; the first failure's text, ``""`` when all went."""
    try:
        shutil.rmtree(root)
        return ""
    except OSError as e:
        shutil.rmtree(root, ignore_errors=True)  # the rest of it
        return str(e)


def remove_session_root(project: Path) -> None:
    """Remove a ``session_root`` folder and its parent, and nothing that is not one; say
    so when something is left behind (a file the app still held)."""
    root = Path(project).parent
    if not (is_scratch(project) and root.name.startswith(ROOT_PREFIX)):
        return
    why = _rmtree(root)
    if root.exists():
        print(f"[WARN] left the session folder {root} behind ({why}); a later session sweeps it")


def sweep_session_roots(max_age_s: float = SWEEP_AGE_S) -> list[Path]:
    """Remove the per-session roots older than ``max_age_s`` whose creating process is
    gone (a session that was killed, or could not remove its root). Returns them."""
    swept: list[Path] = []
    now = time.time()
    for root in Path(tempfile.gettempdir()).glob(f"{ROOT_PREFIX}*"):
        info = owner(root / "project")
        if info is None or not root.is_dir():
            continue
        if now - float(info.get("created", now)) < max_age_s:
            continue
        if isinstance(info.get("pid"), int) and pid_alive(info["pid"]):
            continue
        _rmtree(root)
        if not root.exists():
            swept.append(root)
    return swept


__all__ = [
    "MARKER",
    "is_scratch",
    "mark",
    "owner",
    "prepare_project",
    "remove_session_root",
    "session_root",
    "sweep_session_roots",
]
