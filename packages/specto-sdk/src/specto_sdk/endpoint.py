"""Finding the running app: nearest ``.specto/`` project (like git), its
``endpoint.json``, or an explicit ``SPECTO_ENDPOINT`` / ``--endpoint`` url."""

from __future__ import annotations

import os
from pathlib import Path

from specto_core import Project
from specto_core.rpc import EndpointInfo, read_endpoint_file

from .errors import EndpointNotFound

ENV_ENDPOINT = "SPECTO_ENDPOINT"
ENV_PROJECT = "SPECTO_PROJECT"


def resolve_project(path: str | Path | None = None) -> Project | None:
    """``path`` (or ``SPECTO_PROJECT``) if given, else the nearest project at or above
    the cwd. ``None`` when there is none."""
    explicit = path or os.environ.get(ENV_PROJECT)
    if explicit:
        return Project.open(Path(explicit))
    return Project.find()


def find_endpoint(
    project: str | Path | Project | None = None, *, endpoint: str | None = None
) -> tuple[EndpointInfo, Project | None]:
    """The endpoint to connect to and the project it belongs to (if resolvable)."""
    url = endpoint or os.environ.get(ENV_ENDPOINT)
    proj = project if isinstance(project, Project) else resolve_project(project)
    if url:
        return EndpointInfo.from_url(url, project_id=proj.id if proj else ""), proj
    if proj is None:
        raise EndpointNotFound(
            "no Specto project here (no .specto/ at or above the current directory)",
            fix="cd into a project, pass --project <dir>, run `specto init <dir>`, "
            f"or set {ENV_ENDPOINT}=tcp://host:port",
        )
    info = read_endpoint_file(proj)
    if info is None:
        raise EndpointNotFound(
            f"no running Specto app for project {proj.name!r} ({proj.root})",
            fix=f"start `specto-app --project {proj.root}`; it writes .specto/endpoint.json",
        )
    return info, proj
