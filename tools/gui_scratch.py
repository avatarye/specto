"""Project folders the GUI sessions may wipe (SPEC-7): only folders a session created.

A session that creates its project folder (``prepare_project`` on a path that does not
exist yet, ``session_root`` when no folder was named, or the suite's seeded projects)
marks it with ``.specto/gui-scratch``. ``--fresh`` wipes only a folder whose mark names
that folder; an existing folder without one is never marked and never wiped (an empty one
is used as it is, one with content is refused), so a typo, a reused path or a copied
folder can never delete someone's data. The mark lives under ``.specto/``, which the
app's file tree never lists.

The mark counts by its path alone: a later session (another pid) must be able to wipe a
folder an earlier one created, so the pid and nonce cannot be part of the match. They
record who made it, for the sweep (``pid``: is the creator gone?) and for tracing.

    from gui_scratch import prepare_project, session_root, remove_session_root
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from gui_procs import pid_alive

MARKER = Path(".specto") / "gui-scratch"
#: the per-session roots' folder name prefix, under the temp dir
ROOT_PREFIX = "specto-gui-"
#: a per-session root older than this whose creator is gone is swept
SWEEP_AGE_S = 24 * 3600.0


@dataclass(frozen=True)
class MarkRecord:
    """What a mark records: the folder it names, its creator and when; ``kept`` for a
    session folder left open with its app (``--keep``), swept only once that app
    (``app_pid``) is gone too."""

    path: str
    pid: int
    nonce: str
    created: float
    kept: bool = False
    app_pid: int | None = None


def owner(project: Path) -> MarkRecord | None:
    """The mark's record when it names this folder; ``None`` for no mark, an unreadable
    one, or one copied from another folder (its path is not this one's)."""
    try:
        raw = json.loads((Path(project) / MARKER).read_text(encoding="utf-8"))
        record = MarkRecord(
            path=str(raw["path"]),
            pid=int(raw["pid"]),
            nonce=str(raw["nonce"]),
            created=float(raw["created"]),
            kept=bool(raw.get("kept", False)),
            app_pid=int(raw["app_pid"]) if raw.get("app_pid") is not None else None,
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return record if record.path == str(Path(project).resolve()) else None


def is_scratch(project: Path) -> bool:
    """Whether a GUI session created ``project`` (its mark names this folder)."""
    return owner(project) is not None


def mark(project: Path, *, kept: bool = False, app_pid: int | None = None) -> None:
    """Mark ``project`` as a GUI session's own: only a folder the session itself created
    or just wiped (never one that was there before it)."""
    project = Path(project)
    marker = project / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    record = MarkRecord(
        path=str(project.resolve()),
        pid=os.getpid(),
        nonce=secrets.token_hex(4),
        created=time.time(),
        kept=kept,
        app_pid=app_pid,
    )
    marker.write_text(json.dumps(asdict(record)) + "\n", encoding="utf-8")


def prepare_project(project: Path, *, fresh: bool) -> bool:
    """Make ``project`` ready for a session; True when it is the session's own (created
    here, or a session's folder ``fresh`` just wiped), which is then marked. A folder
    that existed without a session's mark is never marked: ``fresh`` uses it as it is
    when it is empty and refuses it (``SystemExit``) when it has content."""
    project = Path(project)
    if not project.exists():
        project.mkdir(parents=True)
        mark(project)
        return True
    if not fresh:
        return is_scratch(project)
    if is_scratch(project):
        shutil.rmtree(project)
        project.mkdir(parents=True)
        mark(project)
        return True
    if any(project.iterdir()):
        raise SystemExit(
            f"[ERROR] --fresh wipes only a folder a GUI session created; {project} has "
            f"content and no {MARKER.as_posix()} mark naming it. Pick a new folder (or "
            "leave --project out for a per-session one), or empty it yourself"
        )
    return False  # someone's empty folder: used as it is, never marked


def wipe_fresh(project: Path, lock_name: str) -> bool:
    """``prepare_project(fresh=True)`` under the desktop lock, so a wipe never touches a
    folder a mouse-tier session holding it is using (it waits for that session)."""
    from desktop_lock import held

    with held(lock_name):
        return prepare_project(project, fresh=True)


def session_root(*, kept: bool = False) -> Path:
    """A new marked project folder of this session's own, under the temp dir
    (``<temp>/specto-gui-<random>/project``); ``kept`` when its app stays open."""
    project = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX)) / "project"
    project.mkdir()
    mark(project, kept=kept)
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
    """Remove the per-session roots older than ``max_age_s`` whose process is gone: the
    session that made it, or for one kept open with its app (``--keep``) that app.
    Returns them."""
    swept: list[Path] = []
    now = time.time()
    for root in Path(tempfile.gettempdir()).glob(f"{ROOT_PREFIX}*"):
        record = owner(root / "project")
        if record is None or not root.is_dir():
            continue
        if record.kept and record.app_pid is None:
            continue  # kept, but its app never recorded: leave it to the user
        keeper = record.app_pid if record.kept and record.app_pid is not None else record.pid
        if now - record.created < max_age_s or pid_alive(keeper):
            continue
        _rmtree(root)
        if not root.exists():
            swept.append(root)
    return swept


__all__ = [
    "MARKER",
    "MarkRecord",
    "is_scratch",
    "mark",
    "owner",
    "prepare_project",
    "remove_session_root",
    "session_root",
    "sweep_session_roots",
    "wipe_fresh",
]
