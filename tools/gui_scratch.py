"""Project folders the GUI sessions may wipe (SPEC-7): only folders a session created.

A session that creates its project folder (``prepare_project`` on a path that does not
exist yet, or ``session_root`` when no folder was named) marks it with ``.specto/gui-scratch``;
``--fresh`` wipes a marked folder or an empty one and refuses anything else, so a typo or a
reused path can never delete someone's data. The mark lives under ``.specto/``, which the
app's file tree never lists.

    from gui_scratch import prepare_project, session_root, remove_session_root
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

MARKER = Path(".specto") / "gui-scratch"
#: the per-session roots' folder name prefix, under the temp dir
ROOT_PREFIX = "specto-gui-"


def is_scratch(project: Path) -> bool:
    """Whether a GUI session created ``project`` (it carries the mark)."""
    return (Path(project) / MARKER).is_file()


def _mark(project: Path) -> None:
    marker = project / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("created by a GUI session; its --fresh may wipe this folder\n", "utf-8")


def prepare_project(project: Path, *, fresh: bool) -> None:
    """Make ``project`` ready for a session. A folder that does not exist is created and
    marked. ``fresh`` first wipes a marked or empty folder; a folder with content and no
    mark is refused (``SystemExit``) and left as it is."""
    project = Path(project)
    if fresh and project.exists():
        if not is_scratch(project) and any(project.iterdir()):
            raise SystemExit(
                f"[ERROR] --fresh wipes only a folder a GUI session created; {project} has "
                f"content and no {MARKER.as_posix()} mark. Pick a new folder (or leave "
                "--project out for a per-session one), or empty it yourself"
            )
        shutil.rmtree(project)
    created = not project.exists()
    project.mkdir(parents=True, exist_ok=True)
    if created:
        _mark(project)


def session_root() -> Path:
    """A new marked project folder of this session's own, under the temp dir
    (``<temp>/specto-gui-<random>/project``)."""
    project = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX)) / "project"
    project.mkdir()
    _mark(project)
    return project


def remove_session_root(project: Path) -> None:
    """Remove a ``session_root`` folder and its parent, and nothing that is not one."""
    root = Path(project).parent
    if is_scratch(project) and root.name.startswith(ROOT_PREFIX):
        shutil.rmtree(root, ignore_errors=True)


__all__ = ["MARKER", "is_scratch", "prepare_project", "remove_session_root", "session_root"]
