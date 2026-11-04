"""SPEC-34, ADR 0025: opening a file past a quota makes room, counted with the costs the
app's own view manifests declare (the registry the shell fills), for every costly view
type a dedicated or contextual open can add."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from specto_app.hosts import HostContext, HostRegistry
from specto_app.views import factories
from specto_core import Engine, gestures
from specto_core.canvas_document import file_tree_of
from specto_core.jobs import InlineJobManager


@pytest.fixture
def engine(qapp) -> Iterator[Engine]:
    """A headless engine whose registry holds the app's view manifests, registered the
    way the shell registers them; the window frame is large enough that every view
    opened beside the tree is live."""
    engine = Engine(project=None, jobs=InlineJobManager())
    reg = HostRegistry(HostContext(engine, None), load_entry_points=False)
    for factory in factories():
        reg.add_factory(factory)
    gestures.set_window_frame(engine.live, (4000.0, 8000.0))
    yield engine
    reg.dispose_all()
    engine.close()


def _open(engine: Engine, path: str, mode: str = "dedicated") -> gestures.OpenedFile:
    tree = file_tree_of(engine.live.data["views"])
    return gestures.open_file(engine.live, tree, path, mode=mode, plugins=engine.plugins)


@pytest.mark.parametrize(
    ("ext", "view_type", "quota", "ceiling"),
    [
        ("mp4", "video", "video_decoders", 4),
        ("ply", "3d", "viewports3d", 3),
        ("usda", "usd", "rtx_views", 1),
        ("md", "markdown", "web_views", 8),
        ("html", "html", "web_views", 8),
        ("geojson", "map", "web_views", 8),
    ],
)
def test_one_dedicated_view_past_a_quota_collapses_the_oldest(
    engine: Engine, ext: str, view_type: str, quota: str, ceiling: int
):
    """The dedicated view one past a quota opens and the oldest live view counting
    toward that quota collapses to make room, named with the quota."""
    opened = [_open(engine, f"f/{i}.{ext}") for i in range(ceiling + 1)]
    assert {engine.live.data["views"][o.view]["type"] for o in opened} == {view_type}
    assert [o.collapsed for o in opened[:ceiling]] == [{}] * ceiling
    assert opened[ceiling].collapsed == {opened[0].view: [quota]}


def test_a_new_contextual_preview_past_the_web_views_quota_collapses_the_oldest(
    engine: Engine,
):
    """The preview is a web view too: a Ctrl+double-click that adds it past eight live
    web views collapses the oldest of them."""
    notes = [_open(engine, f"notes/{i}.md") for i in range(8)]
    preview = _open(engine, "frames/a.jpg", mode="contextual")
    assert (preview.action, preview.mode) == ("created", "contextual")
    assert engine.live.data["views"][preview.view]["type"] == "preview"
    assert preview.collapsed == {notes[0].view: ["web_views"]}


def test_a_usd_view_is_collapsed_for_both_quotas_it_counts_toward(engine: Engine):
    """A usd view counts toward 3D viewports and RTX views. Two point clouds and one
    stage fill the 3D viewports; a second stage exceeds both quotas, and the first
    stage alone collapses (it relieves both), named with both quotas; the point clouds
    stay live."""
    clouds = [_open(engine, f"scans/{i}.ply") for i in range(2)]
    first = _open(engine, "a/one.usda")
    second = _open(engine, "b/two.usda")
    assert second.collapsed == {first.view: ["viewports3d", "rtx_views"]}
    views = engine.live.data["views"]
    assert not any(views[c.view]["layout"].get("collapsed") for c in clouds)
    assert gestures.room_notice(engine.live, second.view, second.collapsed) == (
        "one.usda · a collapsed to make room for two.usda · b: at most 3 live 3D viewports "
        "per canvas; at most 1 live RTX view per canvas"
    )
