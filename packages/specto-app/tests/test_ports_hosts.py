"""Seam 2 (#39): view hosts on ports. The file tree receives a collection and emits all
files and selected files; the gallery receives a collection and emits a selection; the
host registry builds hosts from the canvas document and wires them to the link runtime,
so a submitted file tree -> intaker -> gallery composition shows up headless."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest
from app_doc_helpers import pin
from gallery_helpers import FakeGallery, copy_frames, needs_testdata
from PySide6.QtCore import QCoreApplication, Qt

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.views.file_tree import FileTreeFactory, FileTreeHost
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_core import Engine, Project
from specto_core.gestures import OpenedFile
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, FilesValue, Selection

CTRL = int(Qt.KeyboardModifier.ControlModifier.value)
SHIFT = int(Qt.KeyboardModifier.ShiftModifier.value)
FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


def tree_collection(root: Path, files: list[str]) -> Collection:
    """A collection of single-part items, one per file (what the project intaker gives
    the file tree), keyed by path without extension so sidecars group."""
    items: dict[str, Item] = {}
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():  # a test may have written the file with a size and time of its own
            p.write_bytes(b"x")
        stem = rel.rsplit(".", 1)[0]
        it = items.setdefault(stem, Item(key=stem, meta={"rel": {}, "root": str(root), "stat": {}}))
        role = "." + rel.rsplit(".", 1)[1]
        it.parts[role] = str(p)
        it.meta["rel"][role] = rel
        st = p.stat()
        it.meta["stat"][role] = [st.st_size, st.st_mtime_ns]  # as the intaker records them
    return Collection(items=list(items.values()), root=str(root))


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager())


def emitted(ctx: HostContext, view_id: str, port: str):
    return ctx.links.output_of(view_id, port)


# -- file tree -------------------------------------------------------------------------
def test_file_tree_emits_the_files_the_user_picks(engine: Engine, tmp_path: Path):
    col = tree_collection(
        tmp_path / "data",
        [
            "runs/cam-a/00000.jpg",
            "runs/cam-a/00000.json",
            "runs/cam-a/00001.jpg",
            "notes/readme.md",
        ],
    )
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    assert host.rows() == []

    host.receive("data", col)

    # every file it shows, as paths relative to the collection root (all files as such
    # come from the project source, #83: the tree emits only the picks)
    assert emitted(ctx, "fege", "all_files") is None
    assert host.model.all_files() == [
        "notes/readme.md",
        "runs/cam-a/00000.jpg",
        "runs/cam-a/00000.json",
        "runs/cam-a/00001.jpg",
    ]
    assert emitted(ctx, "fege", "selected_files") == FilesValue(
        root=str(tmp_path / "data"), paths=[], stats={}
    )
    # everything starts collapsed (#231): the two top folders and nothing under them
    assert [(r["path"], r["depth"], r["is_dir"]) for r in host.rows()] == [
        ("notes", 0, True),
        ("runs", 0, True),
    ]
    host.expand_path("notes")
    host.expand_path("runs/cam-a")  # and the folders above it
    assert [(r["path"], r["depth"], r["is_dir"]) for r in host.rows()] == [
        ("notes", 0, True),
        ("notes/readme.md", 1, False),
        ("runs", 0, True),
        ("runs/cam-a", 1, True),
        ("runs/cam-a/00000.jpg", 2, False),
        ("runs/cam-a/00000.json", 2, False),
        ("runs/cam-a/00001.jpg", 2, False),
    ]

    host.click(6)  # a plain click: just that file
    assert emitted(ctx, "fege", "selected_files").paths == ["runs/cam-a/00001.jpg"]
    host.click(4, SHIFT)  # shift: the visible range from the anchor
    assert emitted(ctx, "fege", "selected_files").paths == [
        "runs/cam-a/00000.jpg",
        "runs/cam-a/00000.json",
        "runs/cam-a/00001.jpg",
    ]
    host.click(1, CTRL)  # ctrl: toggle one more in
    assert emitted(ctx, "fege", "selected_files").paths == [
        "notes/readme.md",
        "runs/cam-a/00000.jpg",
        "runs/cam-a/00000.json",
        "runs/cam-a/00001.jpg",
    ]
    host.click(1, CTRL)  # ... and out again
    assert "notes/readme.md" not in emitted(ctx, "fege", "selected_files").paths
    host.click(0)  # a folder stands for everything under it
    assert emitted(ctx, "fege", "selected_files").paths == ["notes/readme.md"]
    assert [r["path"] for r in host.rows() if r["selected"]] == ["notes"]

    host.toggle(3)  # collapse runs/cam-a: the rows shrink, nothing is emitted
    before = emitted(ctx, "fege", "selected_files")
    assert [r["path"] for r in host.rows()] == ["notes", "notes/readme.md", "runs", "runs/cam-a"]
    assert emitted(ctx, "fege", "selected_files") is before
    host.toggle(3)
    assert len(host.rows()) == 7

    snap = host.snapshot()
    assert snap["files"] == 4 and snap["selected"] == ["notes/readme.md"] and snap["rows"] == 7


def test_file_tree_picks_carry_the_stat_it_already_holds(engine: Engine, tmp_path: Path):
    """#76: a folder pick hands on each file's stat from the tree's collection, so the
    intaker downstream does not stat the files again."""
    files = ["runs/cam-a/00000.jpg", "runs/cam-a/00001.jpg", "notes/readme.md"]
    col = tree_collection(tmp_path / "data", files)
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    host.receive("data", col)
    host.expand_path("runs")

    host.click(host.model.row_of("runs/cam-a"))

    picks = emitted(ctx, "fege", "selected_files")
    assert picks.paths == ["runs/cam-a/00000.jpg", "runs/cam-a/00001.jpg"]
    held = {it.meta["rel"][r]: st for it in col.items for r, st in it.meta["stat"].items()}
    assert picks.stats == {p: held[p] for p in picks.paths}


def test_file_tree_keeps_a_selection_across_a_new_collection_and_clears_on_none(
    engine: Engine, tmp_path: Path
):
    root = tmp_path / "data"
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    host.receive("data", tree_collection(root, ["a/1.jpg", "a/2.jpg", "b/3.jpg"]))
    host.expand_path("a")
    host.click(1)
    host.click(2, CTRL)
    assert emitted(ctx, "fege", "selected_files").paths == ["a/1.jpg", "a/2.jpg"]
    # a rescan drops a/2.jpg (watch mode later): the selection keeps what still exists
    host.receive("data", tree_collection(root, ["a/1.jpg", "b/3.jpg", "b/4.jpg"]))
    assert host.model.all_files() == ["a/1.jpg", "b/3.jpg", "b/4.jpg"]
    assert emitted(ctx, "fege", "selected_files").paths == ["a/1.jpg"]
    host.receive("data", None)
    assert host.rows() == [] and emitted(ctx, "fege", "all_files") is None
    assert ctx.health_of("fege")["status"] == "paused"


def test_file_tree_remembers_its_picks_and_a_reopened_tree_emits_them_again(
    engine: Engine, tmp_path: Path
):
    """Spec #35 story 4: the files the user picked are back after a relaunch (the
    galleries fed from them fill again), with no undo step for a pick."""
    root = tmp_path / "data"
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    host.receive("data", tree_collection(root, ["a/1.jpg", "a/2.jpg", "b/3.jpg"]))
    host.expand_path("a")
    host.click(1)
    host.click(2, CTRL)
    assert engine.live.data["views"]["fege"]["params"]["selected"] == ["a/1.jpg", "a/2.jpg"]
    assert engine.live.history == []
    # a new host over the same document (a relaunch): the picks come back when its data lands
    again = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    again.receive("data", tree_collection(root, ["a/1.jpg", "b/3.jpg"]))
    assert emitted(ctx, "fege", "selected_files").paths == ["a/1.jpg"]
    again.clear_selection()
    assert engine.live.data["views"]["fege"]["params"]["selected"] == []


# -- gallery ---------------------------------------------------------------------------
@needs_testdata
def test_gallery_shows_the_received_collection_and_emits_its_selection(
    engine: Engine, tmp_path: Path
):
    from specto_intake.resolve import resolve_intaker

    root = copy_frames(tmp_path / "front", 6)
    link = {"params": {"sources": ["."], "screener": ["@images"], "keys": FRAME_KEYS}}
    col = resolve_intaker(
        link, FilesValue(root=str(root)), root=str(root), env={"aliases": {"@images": [".jpg"]}}
    )
    ctx = HostContext(engine, None)
    fake = FakeGallery(window=4)
    host = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake, batch=3)
    assert fake.count() == 0 and ctx.health_of("kobi")["status"] == "paused"

    host.receive("data", col)
    host.pump()

    assert fake.count() == 6 and fake.captions[0] == [f"0000{i}" for i in range(6)]
    assert sorted(fake.pushed) == [0, 1, 2, 3]  # visible + margin only, decoded inline
    assert ctx.health_of("kobi")["status"] == "ok" and host.decoded == 4
    assert emitted(ctx, "kobi", "selection") is None

    fake.user_click(3)
    assert emitted(ctx, "kobi", "selection") == Selection(keys=["00003"], last="00003")
    assert fake.caption.startswith("00003")
    fake.user_key(1)
    assert emitted(ctx, "kobi", "selection").last == "00004"

    # a selection arriving on the input port is shown, never re-emitted
    host.receive("selection", Selection(keys=["00001"], last="00001"))
    assert fake.selected() == 1 and fake.revealed[-1] == 1
    assert emitted(ctx, "kobi", "selection").last == "00004"
    host.receive("selection", Selection(keys=["99999"], last="99999"))
    assert fake.selected() == 1  # an unknown key changes nothing

    snap = host.snapshot()
    assert snap["count"] == 6 and snap["selected"]["key"] == "00001"
    host.receive("data", None)
    assert fake.count() == 0 and ctx.health_of("kobi")["status"] == "paused"


@needs_testdata
def test_gallery_remembers_the_picked_frame_and_a_reopened_gallery_shows_and_emits_it(
    engine: Engine, tmp_path: Path
):
    """Spec #35 story 4: the frame picked in a gallery is back after a relaunch, and goes
    out on its selection port again so the views following it catch up."""
    from specto_intake.resolve import resolve_intaker

    root = copy_frames(tmp_path / "front", 6)
    link = {"params": {"sources": ["."], "screener": ["@images"], "keys": FRAME_KEYS}}
    col = resolve_intaker(
        link, FilesValue(root=str(root)), root=str(root), env={"aliases": {"@images": [".jpg"]}}
    )
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Cam A",
        "layout": {"position": [320, 0], "size": [640, 400]},
    }
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="cam-a gallery")
    ctx = HostContext(engine, None)
    host = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=FakeGallery(), batch=3)
    host.receive("data", col)
    host.gallery.user_click(3)
    assert engine.live.data["views"]["kobi"]["params"]["selected"] == "00003"
    assert engine.live.cursor == 1  # the pick added no undo step

    fake = FakeGallery()
    again = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake, batch=3)
    again.receive("data", col)
    assert fake.selected() == 3
    assert emitted(ctx, "kobi", "selection") == Selection(keys=["00003"], last="00003")


@needs_testdata
def test_gallery_size_is_a_param_the_agent_sets_and_a_zoom_remembers(
    engine: Engine, tmp_path: Path
):
    """#173: ``params.tile`` is the thumbnail size. The agent sets it with a submit; a
    zoom gesture remembers the user's size once it ends (no undo step); a pick writes only
    ``params.selected`` and leaves the size alone."""
    from specto_intake.resolve import resolve_intaker

    root = copy_frames(tmp_path / "front", 6)
    link = {"params": {"sources": ["."], "screener": ["@images"], "keys": FRAME_KEYS}}
    col = resolve_intaker(
        link, FilesValue(root=str(root)), root=str(root), env={"aliases": {"@images": [".jpg"]}}
    )
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Cam A",
        "layout": {"position": [320, 0], "size": [640, 400]},
        "params": {"tile": 192},
    }
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="cam-a gallery")
    ctx = HostContext(engine, None)
    fake = FakeGallery()
    host = GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake, batch=3)
    engine.live.on_patch(host.on_patch)  # what the registry relays to its hosts
    host.receive("data", col)
    assert fake.tile() == 192

    doc = engine.live.document()
    doc["views"]["kobi"]["params"]["tile"] = 300
    engine.live.submit(doc, origin="agent:s1", label="bigger thumbnails")
    assert fake.tile() == 300
    cursor = engine.live.cursor

    fake.user_drag(2, 0.0)  # a zoom gesture: two columns
    assert engine.live.data["views"]["kobi"]["params"]["tile"] == 300  # not per notch
    fake.user_release()
    zoomed = fake.tile()
    assert engine.live.data["views"]["kobi"]["params"]["tile"] == round(zoomed, 1)
    fake.user_click(4)
    assert engine.live.data["views"]["kobi"]["params"]["selected"] == "00004"
    assert fake.tile() == zoomed and fake.view()[0] == 2
    assert engine.live.cursor == cursor  # zoom and pick are navigation


# -- the registry over the document ---------------------------------------------------
@needs_testdata
def test_registry_wires_a_submitted_file_tree_intaker_gallery_dataflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 6)
    engine = Engine(project=proj, jobs=InlineJobManager())
    clear_pending()
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())

    tree = reg.host("fege")
    assert isinstance(tree, FileTreeHost) and len(tree.rows()) > 0  # fed on attach
    assert ctx.links.project_files is None  # the tree walked nothing for it (#231)
    assert tree.rows()[0]["path"] == "runs"  # the chain opened down to the frames
    assert any(r["path"].startswith("runs/cam-a/00000") for r in tree.rows())

    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {"size": [600, 400]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery shows the cam-a frames picked in the file tree."},
    }
    engine.live.submit(doc, origin="agent:s1", label="gallery of the picked frames")

    gallery = reg.host("kobi")
    assert isinstance(gallery, GalleryHost)
    fake = fakes["kobi"]
    assert fake.count() == 0  # nothing picked yet: an empty collection, not an error
    assert ctx.health_of("kobi")["status"] == "paused"

    rows = tree.rows()
    frame2 = next(
        i
        for i, r in enumerate(rows)
        if r["path"].startswith("runs/cam-a/00002") and r["path"].endswith(".jpg")
    )
    tree.click(frame2)
    gallery.pump()
    assert fake.count() == 1 and fake.captions[0] == ["00002"]
    assert ctx.health_of("kobi")["status"] == "ok"
    tree.click(len(rows) - 1, SHIFT)  # down to the last file: frames 2..5
    gallery.pump()
    assert fake.captions[0] == ["00002", "00003", "00004", "00005"]

    # a label change reaches the host as a patch without reloading; closing disposes
    doc = engine.live.document()
    doc["views"]["kobi"]["label"] = "Front frames"
    clears = fake.clears
    engine.live.submit(doc, origin="agent:s1", label="rename")
    assert fake.clears == clears and fake.captions[0][0] == "00002"
    del doc["views"]["kobi"]
    del doc["links"]["l2"]
    engine.live.submit(doc, origin="user", label="close")
    assert "kobi" not in reg.hosts and gallery.disposed
    assert ctx.links.input_of("kobi", "data") is None


def test_missing_host_is_a_card_with_health_error(engine: Engine):
    clear_pending()
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "3d", "label": "scene", "layout": {}}
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="3d")
    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    host = reg.host("kobi")
    assert type(host).__name__ == "MissingHost"
    assert ctx.health_of("kobi")["status"] == "error" and "3d" in ctx.health_of("kobi")["message"]


# -- #48: arrivals, progressive resolve ------------------------------------------------
class HeldJobs(InlineJobManager):
    """Holds every job until ``run_next``: the 'resolving' state stays observable."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list = []

    def submit(self, kind, fn, *args, canvas=None, on_done=None, **kwargs):
        job = self._new(kind, canvas)
        self.held.append((job, fn, args, kwargs, on_done))
        return job

    def run_next(self):
        job, fn, args, kwargs, on_done = self.held.pop(0)
        result = fn(*args, progress=lambda p, m="": None, **kwargs)
        self._finish(job, result=result)
        if on_done is not None:
            on_done(job)
        return job


def _gallery_from_folder(doc: dict, sources: list[str], *, watch: bool) -> dict:
    doc["views"]["kobi"] = {"type": "gallery", "label": "Drops", "layout": {"size": [600, 400]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "dropped frames",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": sources, "screener": ["@images"], "keys": FRAME_KEYS, "watch": watch},
        "summary": {"sentence": "The gallery shows every frame dropped into the folder."},
    }
    return doc


@needs_testdata
def test_a_watched_gallery_marks_arrivals_as_resolving_then_swaps_the_new_collection_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "drops")
    copy_frames(proj.root / "drops", 3)
    jobs = HeldJobs()
    engine = Engine(project=proj, jobs=jobs, watch_interval_s=0.05, watch_debounce_s=0.0)
    clear_pending()
    fake = FakeGallery(window=0)  # nothing on screen wants a thumbnail: no decodes

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            return GalleryHost(ctx, view_id, manifest, gallery=fake)

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(Factory())
    ctx.links  # the runtime starts: l1 is held  # noqa: B018
    engine.live.submit(
        _gallery_from_folder(engine.live.document(), ["drops"], watch=True),
        origin="agent:s1",
        label="w",
    )
    assert reg.host("kobi") is not None and ctx.health_of("kobi")["status"] == "loading"
    jobs.run_next()  # l2 lands: three frames (the gallery then reports its own decoding)
    assert fake.count() == 3 and "resolving" not in ctx.health_of("kobi")["message"]

    clears = fake.clears
    copy_frames(proj.root / "drops", 5)  # two more frames (plus sidecars) arrive
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "l2" not in ctx.links.poll():
        time.sleep(0.02)
    # resolving in the background: the view says so, the old collection stays on screen
    health = ctx.health_of("kobi")
    assert health["status"] == "loading" and "4 arrived" in health["message"], health
    assert fake.count() == 3
    jobs.run_next()  # l1 or l2 (both watch), until the gallery's own lands
    while fake.count() == 3 and jobs.held:
        jobs.run_next()
    assert fake.count() == 5 and fake.captions[0] == [f"0000{i}" for i in range(5)]
    assert fake.clears == clears  # a collection that grew keeps what it had (swap, not reset)
    assert "resolving" not in ctx.health_of("kobi")["message"]
    engine.close()  # joins the watcher threads of l1 and l2


def test_a_10k_image_intake_resolves_off_the_gui_thread_and_arrives_progressively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The GUI thread (this one) only ever delivers results; the walk, screening and
    grouping of 10 000 files runs on a worker, and no single delivery costs this thread
    more than a bounded slice of its own CPU time."""

    from specto_core.jobs import ThreadJobManager

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "big")
    big = proj.root / "big"
    big.mkdir()
    for i in range(10_000):
        (big / f"{i:05d}_1700000000000000000.jpg").write_bytes(b"\xff\xd8\xff")
    jobs = ThreadJobManager()
    engine = Engine(project=proj, jobs=jobs)
    clear_pending()
    fake = FakeGallery(window=0)

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            return GalleryHost(ctx, view_id, manifest, gallery=fake)

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())
    tree = reg.host("fege")
    engine.live.submit(
        _gallery_from_folder(engine.live.document(), ["big"], watch=False),
        origin="agent:s1",
        label="big",
    )
    gallery = reg.host("kobi")
    assert ctx.links.resolving("l2") and fake.count() == 0  # nothing waited for here
    assert ctx.health_of("kobi")["status"] == "loading"

    stalls: list[float] = []  # wall time this thread spent inside each delivery
    busy: list[float] = []  # and the CPU time it burned there (what the bound is on)
    deadline = time.monotonic() + 180  # the walk of 10k files, with load headroom (#179)
    qt_app = QCoreApplication.instance()
    while time.monotonic() < deadline and (fake.count() < 10_000 or not tree.model.complete):
        jobs.wait(timeout=1.0)
        t0, c0 = time.perf_counter(), time.thread_time()
        jobs.poll()
        if qt_app is not None:
            qt_app.processEvents()  # the tree's own listings land through the Qt loop (#231)
        dt, dc = time.perf_counter() - t0, time.thread_time() - c0
        if dt > 0.001:
            stalls.append(dt)
            busy.append(dc)
    assert fake.count() == 10_000 and gallery.keys[-1] == "09999"
    assert tree.snapshot()["files"] == 10_000
    print(
        "[OK] deliveries on the GUI thread: wall "
        f"{[round(s, 3) for s in stalls]} cpu {[round(b, 3) for b in busy]}"
    )
    # one delivery of 10k items costs this thread under a second of its own CPU time;
    # the wall time is only printed: under parallel load it belongs to the scheduler (#179)
    assert max(busy) < 1.0, (busy, stalls)
    engine.close()
    jobs.shutdown()


# -- follow links between galleries (#41) -----------------------------------------------
@needs_testdata
def test_a_click_in_one_gallery_selects_the_matching_frame_in_the_other_without_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "run-001" / "cam-a", 6, "cam-a")
    copy_frames(proj.root / "runs" / "run-001" / "cam-b", 4, "cam-b")
    engine = Engine(project=proj, jobs=InlineJobManager())
    clear_pending()
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())
    reg.host("fege")  # the file tree emits all files on attach

    doc = engine.live.document()
    for vid, lid, cam, x in (("kobi", "l2", "cam-a", 300), ("tamu", "l3", "cam-b", 960)):
        doc["views"][vid] = {"type": "gallery", "label": cam, "layout": {"position": [x, 0]}}
        doc["links"][lid] = {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": vid, "port": "data"},
            "params": {
                "sources": ["runs/run-001"],
                "screener": ["@images"],
                "keys": rf"(?P<cam>{cam})/(?P<frame>\d{{5}})_\d+\.jpg$",
                "group_by": ["cam", "frame"],
            },
            "summary": {"sentence": f"The {cam} gallery shows the {cam} frames of run-001."},
        }
    doc["links"]["l7"] = {
        "kind": "follow",
        "two_way": True,
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "params": {"match": "frame"},
        "summary": {"sentence": "Selecting a frame in one gallery selects it in the other."},
    }
    engine.live.submit(doc, origin="agent:s1", label="cam-a and cam-b linked on frame")
    front, rear = reg.host("kobi"), reg.host("tamu")
    front.pump(), rear.pump()
    assert fakes["kobi"].captions[0][:2] == ["cam-a/00000", "cam-a/00001"]
    assert fakes["tamu"].captions[0] == [f"cam-b/0000{i}" for i in range(4)]

    fakes["kobi"].user_click(2)  # cam-a/00002

    assert fakes["tamu"].selected() == 2 and fakes["tamu"].revealed[-1] == 2
    assert emitted(ctx, "kobi", "selection") == Selection(keys=["cam-a/00002"], last="cam-a/00002")
    assert emitted(ctx, "tamu", "selection") is None  # following is not selecting

    fakes["tamu"].user_click(1)  # cam-b/00001: the other way round
    assert fakes["kobi"].selected() == 1
    assert emitted(ctx, "kobi", "selection").last == "cam-a/00002"  # the front never re-spoke
    assert ctx.links.input_of("tamu", "selection") == Selection(
        keys=["cam-b/00002"], last="cam-b/00002"
    )  # nothing came back to the cam-b gallery after its own click

    fakes["kobi"].user_click(5)  # cam-a/00005: the cam-b run stopped at 00003
    assert fakes["tamu"].selected() == 1  # an unmatched frame leaves the cam-b selection alone


def test_file_tree_offers_a_thumbnail_url_for_image_rows_on_hover(engine: Engine, tmp_path: Path):
    """Story 34: hovering an image row shows its thumbnail; other rows show none."""
    col = tree_collection(tmp_path / "data", ["runs/cam-a/00000.jpg", "runs/cam-a/00000.json"])
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    host.receive("data", col)
    url = host.bridge.thumbUrl("runs/cam-a/00000.jpg")
    assert url.startswith("file:///") and url.endswith("/runs/cam-a/00000.jpg")
    assert host.bridge.thumbUrl("runs/cam-a/00000.json") == ""
    assert host.bridge.thumbUrl("runs/cam-a") == ""


# -- a view on a selection intaker says what to do until something is picked -------------
@needs_testdata
def test_a_view_on_a_selection_intaker_hints_to_pick_files_then_fills_on_a_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 3)
    copy_frames(proj.root / "runs" / "cam-b", 2, "cam-b")
    engine = Engine(project=proj, jobs=InlineJobManager())
    clear_pending()
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())
    tree = reg.host("fege")

    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Picked", "layout": {"size": [600, 400]}}
    doc["views"]["tamu"] = {"type": "gallery", "label": "Cam B", "layout": {"position": [0, 420]}}
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The gallery shows the images picked in the file tree."},
    }
    doc["links"]["l3"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "tamu", "port": "data"},
        "params": {"sources": ["runs/cam-b"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The cam-b gallery always shows the cam-b frames."},
    }
    engine.live.submit(doc, origin="agent:s1", label="a selection gallery and a pinned one")

    picked, rear = reg.host("kobi"), reg.host("tamu")
    assert isinstance(picked, GalleryHost) and isinstance(rear, GalleryHost)
    # nothing picked yet: the selection gallery is empty with the hint, the pinned one is full
    assert fakes["kobi"].count() == 0
    assert ctx.health_of("kobi") == {
        "status": "paused",
        "message": "nothing picked",
        "hint": "Pick files in the file tree",
    }
    assert fakes["tamu"].count() == 2 and ctx.health_of("tamu")["hint"] == ""

    rows = tree.rows()
    front = next(i for i, r in enumerate(rows) if r["path"] == "runs/cam-a")
    tree.click(front)  # a folder pick expands to its files
    picked.pump()
    assert fakes["kobi"].captions[0] == ["00000", "00001", "00002"]
    assert ctx.health_of("kobi")["status"] == "ok" and ctx.health_of("kobi")["hint"] == ""
    assert fakes["tamu"].captions[0] == ["00000", "00001"]  # the pinned gallery did not move

    tree.clear_selection()
    picked.pump()
    assert (
        fakes["kobi"].count() == 0
        and ctx.health_of("kobi")["hint"] == "Pick files in the file tree"
    )


def test_file_tree_details_give_each_rows_time_and_size_and_its_age(engine: Engine, tmp_path: Path):
    """Details mode (#115): under each name the file's local modified time to the second
    and its size (``2026-10-22 18:49:03`` and ``1.5 MB``), from the stat values the
    intaker already recorded (no stat on the GUI thread); a folder of a tree handed whole
    (no folder stat) gives its newest file, its item count and the total under it (#38).
    The age (0: under 10 min, 1: under an hour, 2: under a day,
    3: older) is judged against the tree's clock, read again on ``refresh_ages`` (the
    view's once-a-minute timer), which tells the list the ages changed and nothing else."""
    data = tmp_path / "data"
    (data / "runs").mkdir(parents=True)
    now = datetime(2026, 10, 22, 18, 49, 3).timestamp()
    files = {  # path: (size, seconds old)
        "runs/a.bin": (1536, 5 * 60),
        "runs/b.bin": (3 * 1024 * 1024 + 200 * 1024, 30 * 60),
        "c.txt": (12, 5 * 3600),
        "old.md": (2 * 1024**3, 3 * 86400),
    }
    for rel, (size, age) in files.items():
        with open(data / rel, "wb") as f:
            f.truncate(size)  # sparse: the size without the bytes
        os.utime(data / rel, (now - age, now - age))
    col = tree_collection(data, list(files))
    ctx = HostContext(engine, None)
    host = FileTreeHost(ctx, "fege", ctx.manifest_for("file_tree"))
    clock = [now]
    host.model.clock = lambda: clock[0]
    host.receive("data", col)
    host.expand_path("runs")

    rows = {r["path"]: (r["stamp"], r["size"], r["age"]) for r in host.rows()}
    assert rows["runs/a.bin"] == ("2026-10-22 18:44:03", "1.5 KB", 0)
    assert rows["runs/b.bin"] == ("2026-10-22 18:19:03", "3.2 MB", 1)
    assert rows["c.txt"] == ("2026-10-22 13:49:03", "12 B", 2)
    assert rows["old.md"] == ("2026-10-19 18:49:03", "2.0 GB", 3)
    assert rows["runs"] == ("2026-10-22 18:44:03", "2 items · 3.2 MB", 0)  # newest, count, total

    changed: list[list[int]] = []
    host.model.dataChanged.connect(lambda _a, _b, roles: changed.append(list(roles)))
    clock[0] = now + 10 * 60  # ten minutes on: a.bin is no longer fresh
    host.model.refresh_ages()
    ages = {r["path"]: r["age"] for r in host.rows()}
    assert (ages["runs/a.bin"], ages["runs/b.bin"], ages["runs"]) == (1, 1, 1)
    assert changed == [[host.model.AgeRole]]


def _tree_with_every_view(engine: Engine, tmp_path: Path, files: list[str]):
    """A file tree host on a registry that knows every built-in view type (so a file's
    kind resolves to its view type as in the app), fed ``files``, every folder open."""
    from specto_app.views import install

    col = tree_collection(tmp_path / "data", files)
    ctx = HostContext(engine, None)
    clear_pending()
    install()
    reg = HostRegistry(ctx, load_entry_points=False)
    clear_pending()
    host = reg.host("fege")
    assert isinstance(host, FileTreeHost)
    host.receive("data", col)
    for folder in sorted({p.rsplit("/", 1)[0] for p in files if "/" in p}):
        host.expand_path(folder)
    rows = {r["path"]: i for i, r in enumerate(host.rows())}
    return ctx, reg, host, rows


def test_double_click_on_a_file_row_opens_its_dedicated_view_or_reveals_it(
    engine: Engine, tmp_path: Path
):
    """Seam 2 (SPEC-32): a double-click on a file row asks ``gestures.open_file`` for the
    file's dedicated view: a view of the type that shows its kind, fed that one file by a
    pinned link, beside the tree; the tree's picks are not touched. A second double-click
    on the same file reveals that view through the shell (``ctx.reveal_view``) instead of
    adding another. A file no view shows opens the "Not supported" text view. Folders
    keep expand/collapse."""
    ctx, _reg, host, rows = _tree_with_every_view(
        engine, tmp_path, ["runs/cam-a/00000.jpg", "notes/readme.md", "notes/blob.bin"]
    )
    revealed: list[str] = []
    ctx.reveal_view = revealed.append

    assert host.open_row(rows["notes"]) is None  # a folder: the double-click toggles it
    assert len(engine.live.data["views"]) == 1

    opened = host.open_row(rows["notes/readme.md"])
    assert opened is not None and (opened.action, opened.mode) == ("created", "dedicated")
    doc = engine.live.document()
    md = doc["views"][opened.view]
    assert md["type"] == "markdown" and md["label"] == "readme.md · notes"
    assert md["layout"]["position"] == [380.0, 0.0]  # beside the tree (280 wide at 80,0)
    link = next(ln for ln in doc["links"].values() if ln["target"]["view"] == opened.view)
    assert (link["kind"], link["scope"]) == ("intaker", "pinned")
    assert link["source"] == {"view": "project", "port": "all_files"}
    assert link["params"]["sources"] == ["notes/readme.md"]
    assert engine.live.history[-1].origin == "user"
    assert emitted(ctx, "fege", "selected_files").paths == []  # no pick was made
    assert host.model.selected == set() and revealed == []

    # again: the same view, focused and revealed, nothing submitted
    steps = len(engine.live.history)
    again = host.open_row(rows["notes/readme.md"])
    assert again == OpenedFile(view=opened.view, action="focused", submit=None, mode="dedicated")
    assert revealed == [opened.view] and len(engine.live.history) == steps

    # a file no view type shows: the text view saying so, labelled the same way
    odd = host.open_row(rows["notes/blob.bin"])
    view = engine.live.document()["views"][odd.view]
    assert view["type"] == "text" and view["label"] == "blob.bin · notes"
    assert view["params"]["text"] == "Not supported: blob.bin (.bin)"
    image = host.open_row(rows["runs/cam-a/00000.jpg"])
    assert engine.live.document()["views"][image.view]["type"] == "image"


def test_ctrl_double_click_opens_the_contextual_preview_and_leaves_the_file_selected(
    engine: Engine, tmp_path: Path
):
    """Seam 2 (SPEC-32, #106, #122): Ctrl+double-click on a file row asks for the
    contextual preview: new beside the tree on the tree's picks when none exists, the
    file picked in the tree, even though the Ctrl click before it toggled the row out of
    the selection; a preview on another link (the shell's selected one first) is
    re-pointed at the picks; one on the picks already only takes the pick."""
    ctx, _reg, host, rows = _tree_with_every_view(
        engine,
        tmp_path,
        ["runs/cam-a/00000.jpg", "runs/cam-a/00001.jpg", "notes/readme.md", "notes/todo.md"],
    )
    host.click(rows["notes/readme.md"])
    host.click(rows["notes/readme.md"], CTRL)  # the Ctrl tap of a Ctrl+double-click
    assert host.model.selected == set()

    opened = host.open_row(rows["notes/readme.md"], CTRL)
    assert opened is not None and (opened.action, opened.mode) == ("created", "contextual")
    doc = engine.live.document()
    pv = doc["views"][opened.view]
    assert pv["type"] == "preview" and pv["label"] == "Files preview"
    link = next(ln for ln in doc["links"].values() if ln["target"]["view"] == opened.view)
    assert link["scope"] == "selection" and link["source"]["view"] == "fege"
    assert engine.live.history[-1].origin == "user"
    assert emitted(ctx, "fege", "selected_files").paths == ["notes/readme.md"]
    assert host.model.selected == {"notes/readme.md"}

    # a second preview the shell says is selected, pinned to notes/: re-pointed
    doc["views"]["lito"] = {"type": "preview", "label": "other", "layout": {"size": [300, 300]}}
    pin(doc, "lito", sources=["notes"])
    engine.live.submit(doc, origin="agent:s1", label="another preview")
    ctx.selected_views = lambda: ["lito"]
    again = host.open_row(rows["notes/todo.md"], CTRL)
    assert (again.view, again.action) == ("lito", "repointed")
    assert engine.live.document()["links"]["l3"]["source"] == {
        "view": "fege",
        "port": "selected_files",
    }
    assert engine.live.document()["views"]["lito"]["label"] == "other"  # written by hand: kept

    # on the picks already: the Ctrl+double-click is a pick in the tree
    steps = len(engine.live.history)
    picked = host.open_row(rows["runs/cam-a/00001.jpg"], CTRL)
    assert (picked.view, picked.action) == ("lito", "selected")
    assert emitted(ctx, "fege", "selected_files").paths == ["runs/cam-a/00001.jpg"]
    assert host.snapshot()["selected_rows"] == ["runs/cam-a/00001.jpg"]
    assert len(engine.live.history) == steps  # a pick is navigation, not an undo step
