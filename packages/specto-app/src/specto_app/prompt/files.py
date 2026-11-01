"""``list_files`` for the prompt-bar agent (#169): the project folder by folder or by glob
(``specto_core.project_listing``), so the agent finds a path the context's summary
folded away instead of guessing one. The same listing feeds the context's summary, so
both always describe the whole project folder. No Qt here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.links import FilesValue
from specto_core.ports import PROJECT_ID
from specto_core.project_listing import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ProjectListing,
    folder_summary,
    list_files,
)

FILE_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "list_files",
            "List the project folder (project.all_files): one folder's subfolders, each with "
            "how many files its subtree holds, and its files; or, with glob, the files below "
            "path that match. Use it to find a path the context's files summary folded away "
            "(its deeper map) rather than guessing one. Paths are relative to the project "
            "folder, POSIX slashes.",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The folder to list, relative to the project folder; "
                        "default '.', the project folder itself.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Only files whose path below path matches: *.geojson "
                        "(no slash: the file name, at any depth), */gis/*, "
                        "**/gis/*.geojson (** spans folders). Case-insensitive.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Folder levels below path to list (default 1; with "
                        "glob: any depth).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"At most this many entries (default {DEFAULT_LIMIT}, "
                        f"at most {MAX_LIMIT}).",
                    },
                },
            },
        ),
    )
}


class ProjectFiles:
    """The project folder's listing for one window: the context's summary and the
    ``list_files`` tool read the same listing. It is the link runtime's whole-folder
    listing (``LinkRuntime.project_files``: what the file tree's whole-folder intaker
    found, kept current by its watch and the refresh) whenever there is one; else one
    walk of the project root, cached until an intaker off the project source resolves
    again (a refresh, watched arrivals) so a large depot is never walked per prompt."""

    def __init__(self, root: Path, *, engine: Any = None) -> None:
        self.root = Path(root)
        self.engine = engine
        self.listing = ProjectListing(self.root, ttl_s=None)
        self._off = (
            engine.live.events.subscribe(self._event) if engine is not None else lambda: None
        )

    def _event(self, ev: Any) -> None:
        if ev.kind not in ("link.resolved", "link.arrivals"):
            return
        link = self.engine.live.data["links"].get(ev.payload.get("link"))
        if link is not None and link["source"]["view"] == PROJECT_ID:
            self.listing.forget()

    def paths(self) -> list[str]:
        if self.engine is not None:
            whole = self.engine.links.project_files
            if isinstance(whole, FilesValue) and whole.paths is not None:
                return whole.paths
        return self.listing.paths()

    def summary(self) -> dict[str, Any]:
        """``{root, count, folders, ...}`` (``folder_summary``) of the whole folder."""
        return {"root": str(self.root), **folder_summary(self.paths())}

    def run(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool != "list_files":
            raise KeyError(tool)
        return list_files(
            self.paths(),
            args.get("path"),
            glob=args.get("glob"),
            depth=args.get("depth"),
            limit=args.get("limit"),
        )

    def close(self) -> None:
        self._off()


__all__ = ["FILE_TOOLS", "ProjectFiles"]
