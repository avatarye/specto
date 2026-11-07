"""Test documents: every view must hang off the project source by links (#83), so a
test that adds a view by hand pins it from ``project.all_files`` with ``pin``."""

from __future__ import annotations

from typing import Any

from specto_core.canvas_document import next_id
from specto_core.kinds import PluginManifest, PluginRegistry, ViewManifest, default_plugins
from specto_core.ports import file_input_of


def pin(
    doc: dict[str, Any],
    vid: str,
    *,
    lid: str | None = None,
    sources: list[str] | None = None,
) -> str:
    """Add a pinned link from the project source into ``vid``'s file input (an intaker,
    or read_contents for a map). Returns the link id."""
    port, kind = file_input_of(doc["views"][vid]["type"]) or ("data", "intaker")
    lid = lid or next_id(doc["links"], "l")
    link: dict[str, Any] = {
        "kind": kind,
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": vid, "port": port},
        "params": {"sources": sources or ["."]},
        "summary": {"sentence": f"The {vid} view always shows its sources."},
    }
    if kind == "intaker":
        link["scope"] = "pinned"
    doc["links"][lid] = link
    return lid


def app_plugins(reg: PluginRegistry | None = None) -> PluginRegistry:
    """The registry as the app fills it (SPEC-32): core's built-in views plus the typed
    views the app hosts (what each accepts, as its manifest says) and the context
    viewer, so a headless test resolves a file's kind to its view type as the app does.
    ``reg`` (an engine's) is filled in place; otherwise a fresh one."""
    if reg is None:
        reg = default_plugins()
    views = [
        ViewManifest(type="image", label="Image", host="quick", accepts=["image2d"]),
        ViewManifest(type="video", label="Video", host="quick", accepts=["video_ref"]),
        ViewManifest(type="markdown", label="Markdown", host="web", accepts=["markdown"]),
        ViewManifest(type="html", label="HTML", host="web", accepts=["html"]),
        ViewManifest(type="pdf", label="PDF", host="quick", accepts=["pdf"]),
        ViewManifest(type="map", label="Map", host="web", accepts=["geojson"]),
        ViewManifest(type="3d", label="3D", host="web", accepts=["pointcloud", "mesh", "splat"]),
        ViewManifest(type="urdf", label="URDF", host="web", accepts=["urdf"]),
        ViewManifest(
            type="preview",
            label="Preview",
            host="quick",
            accepts=[
                "geojson",
                "html",
                "image2d",
                "json",
                "markdown",
                "mesh",
                "pdf",
                "pointcloud",
                "splat",
                "text",
                "video_ref",
            ],
        ),
        ViewManifest(type="text", label="Text", host="quick", accepts=[]),
    ]
    reg.register(PluginManifest(name="app-like", version="0.0.0", views=views))
    return reg
