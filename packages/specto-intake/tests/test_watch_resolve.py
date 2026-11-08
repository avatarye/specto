"""Seam 1 (#48): a watched intaker (``params.watch``) follows its sources on disk. New
files arriving in the folder trigger a resolve in the background (a job, off the
calling thread); the new collection swaps in on the target port with a higher
generation. Turning watch off, or removing the link, stops the watcher."""

from __future__ import annotations

import gc
import shutil
import threading
import time
from pathlib import Path

import pytest
from intake_helpers import CAM_A, copy_frames, needs_testdata

import specto_intake  # noqa: F401  (registers the intaker resolver and watcher)
from specto_core import Engine, Project, gestures
from specto_core.jobs import ThreadJobManager
from specto_core.links import Collection

pytestmark = needs_testdata

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "drops")
    copy_frames(proj.root / "drops", 3, "cam-a")
    return proj


def watched_gallery(doc: dict, *, watch: bool = True) -> dict:
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Drops",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "dropped frames",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {
            "sources": ["drops"],
            "screener": ["@images"],
            "keys": FRAME_KEYS,
            "group_by": ["frame"],
            "watch": watch,
        },
        "summary": {"sentence": "The gallery shows every frame dropped into the folder."},
    }
    return doc


def drip(dst: Path, start: int, n: int) -> list[str]:
    """Copy ``n`` cam-a frames (jpg + json) in, each written whole then renamed, the way
    ``tools/drip_files.py`` does it."""
    names = []
    for j in sorted(CAM_A.glob("*.jpg"))[start : start + n]:
        for src in (j, j.with_suffix(".json")):
            tmp = dst / (src.name + ".part")
            shutil.copy2(src, tmp)
            tmp.replace(dst / src.name)
        names.append(j.name)
    return names


def pump_until(engine: Engine, jobs: ThreadJobManager, cond, timeout: float = 30.0) -> bool:
    """What the app's timer does: deliver finished jobs and poll the watchers. The
    ceiling is generous: a rescan is a worker thread that parallel suite copies preempt
    (#179); a test that waits for nothing passes a short one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs.poll()
        engine.links.poll()
        if cond():
            return True
        time.sleep(0.02)
    return False


def keys_of(col: Collection) -> list[str]:
    return [it.key for it in col.items]


def test_files_arriving_in_a_watched_folder_resolve_in_the_background_and_swap_in(
    project: Project,
):
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.05)
    links = engine.links
    events: list = []
    links.events.subscribe(lambda ev: events.append(ev) if ev.kind == "link.arrivals" else None)

    # the file tree auto-updates by default (#115); a gallery is off until turned on (#102)
    assert gestures.auto_update_of(engine.live.document(), "fege") is True
    engine.live.submit(watched_gallery(engine.live.document()), origin="agent:s1", label="w")
    assert pump_until(engine, jobs, lambda: isinstance(links.input_of("kobi", "data"), Collection))
    first = links.input_of("kobi", "data")
    assert keys_of(first) == ["00000", "00001", "00002"]
    assert links.watching() == ["l2"]  # the tree follows its loaded folders itself (#231)
    # the watcher has settled: its baseline and the listing after notifications armed
    # are behind it, so no listing catches the drip half-written (SPEC-83)
    assert pump_until(engine, jobs, lambda: links.watch_status("l2")["polls"] >= 2)

    drip(project.root / "drops", 3, 2)

    assert pump_until(engine, jobs, lambda: len(links.input_of("kobi", "data").items) == 5)
    second = links.input_of("kobi", "data")
    assert keys_of(second) == ["00000", "00001", "00002", "00003", "00004"]
    assert second.generation > first.generation and second.link == "l2"
    arrivals = [e for e in events if e.payload["link"] == "l2"]
    assert arrivals and arrivals[0].payload["added"] == 4  # 2 jpg + 2 json
    assert arrivals[0].payload["target"] == {"view": "kobi", "port": "data"}
    # the rescan was a job (a worker), not work on this thread: two resolves of l2 so far
    assert [j.state for j in jobs.list() if j.canvas == "l2"] == ["done", "done"]
    # watch off: a further arrival changes nothing
    engine.live.submit(
        watched_gallery(engine.live.document(), watch=False), origin="agent:s1", label="off"
    )
    assert pump_until(engine, jobs, lambda: not links.resolving("l2"))
    assert links.watching() == []
    l2_jobs = len([j for j in jobs.list() if j.canvas == "l2"])
    drip(project.root / "drops", 5, 1)
    assert not pump_until(
        engine, jobs, lambda: len(links.input_of("kobi", "data").items) == 6, timeout=0.6
    )
    assert len([j for j in jobs.list() if j.canvas == "l2"]) == l2_jobs
    engine.close()
    assert links.watching() == [] and not watcher_threads()
    jobs.shutdown()


def watcher_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("specto-watch-"))


def test_closing_the_engine_stops_and_joins_its_watcher_threads(project: Project):
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05)
    engine.live.submit(watched_gallery(engine.live.document()), origin="agent:s1", label="w")
    assert engine.links.watching() == ["l2"]
    assert pump_until(engine, jobs, lambda: "specto-watch-l2" in watcher_threads(), timeout=2)

    engine.close()

    assert engine.links.watching() == [] if engine._links else True
    assert watcher_threads() == []  # joined, not merely asked to stop
    jobs.shutdown()


def test_a_dropped_engine_takes_its_watchers_with_it(project: Project):
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs, watch_interval_s=0.05)
    engine.live.submit(watched_gallery(engine.live.document()), origin="agent:s1", label="w")
    links = engine.links
    # until the first resolve has landed: a pending on_done would hold the runtime
    assert pump_until(
        engine,
        jobs,
        lambda: (
            isinstance(links.input_of("kobi", "data"), Collection)
            and watcher_threads() == ["specto-watch-l2"]
        ),
        timeout=4,
    )

    engine = links = None  # drop our references: nothing else holds the engine
    gc.collect()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and watcher_threads():
        time.sleep(0.02)
    assert watcher_threads() == []
    jobs.shutdown()
