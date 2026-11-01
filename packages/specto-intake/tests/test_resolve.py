"""Seam 1 (#39): a submitted canvas document's intaker links resolve to collections on
the link runtime, as jobs off the calling thread, latest value per input. The worked
values come from the run-001 frames (one item per frame, jpg + json sidecar)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from intake_helpers import copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager, JobInfo, JobManager, ThreadJobManager
from specto_core.links import Collection, FilesValue

pytestmark = needs_testdata

FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "cam-a", 6, "cam-a")
    copy_frames(proj.root / "runs" / "cam-b", 4, "cam-b")
    (proj.root / "notes" / "readme.md").parent.mkdir()
    (proj.root / "notes" / "readme.md").write_text("# notes\n", encoding="utf-8")
    return proj


def gallery_fed_from(port: str, doc: dict) -> dict:
    """The file tree plus a gallery whose intaker takes the cam-a frames off ``port``."""
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Cam A",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection" if port == "selected_files" else "pinned",
        "label": "cam-a frames",
        "source": {"view": "project" if port == "all_files" else "fege", "port": port},
        "target": {"view": "kobi", "port": "data"},
        "params": {
            "sources": ["runs/cam-a"],
            "screener": ["@images"],
            "keys": FRAME_KEYS,
            "group_by": ["frame"],
        },
        "summary": {"sentence": "The gallery shows the cam-a frames the file tree offers."},
    }
    return doc


class HeldJobs(JobManager):
    """Holds every job until the test runs it: makes 'resolving' and the latest-value
    rule observable without timing."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list[tuple[JobInfo, object, tuple, dict, object]] = []

    def submit(self, kind, fn, *args, canvas=None, on_done=None, **kwargs) -> JobInfo:
        job = self._new(kind, canvas)
        self.held.append((job, fn, args, kwargs, on_done))
        return job

    def run_next(self) -> JobInfo:
        job, fn, args, kwargs, on_done = self.held.pop(0)
        try:
            result = fn(*args, progress=lambda p, m="": None, **kwargs)
            self._finish(job, result=result)
        except Exception as e:  # pragma: no cover - a failing resolver is a test failure
            self._finish(job, error=f"{type(e).__name__}: {e}")
        if on_done is not None:
            on_done(job)
        return job

    def cancel(self, job_id: str) -> JobInfo:
        return self.status(job_id)


def keys_of(value: Collection) -> list[str]:
    return [it.key for it in value.items]


# -- the project folder feeds the file tree ---------------------------------------------
def test_opening_a_project_hands_the_file_tree_its_root_without_a_walk(
    project: Project, monkeypatch: pytest.MonkeyPatch
):
    """#231: the intaker into the file tree is never walked. The tree gets a
    collection with the root alone, at once, and lists its folders itself."""
    from specto_intake import resolve as resolve_module

    def no_walk(*a, **k):
        raise AssertionError("the project was walked for the file tree")

    monkeypatch.setattr(resolve_module, "walk_source", no_walk)
    jobs = ThreadJobManager()
    engine = Engine(project=project, jobs=jobs)
    links = engine.links

    col = links.input_of("fege", "data")
    assert isinstance(col, Collection) and not links.resolving("l1")
    assert col.items == [] and col.root == str(project.root) and col.link == "l1"
    assert links.status("l1")["state"] == "resolved"
    assert [j for j in jobs.list() if j.canvas == "l1"] == []  # no job, no walk
    assert links.project_files is None  # nothing counted the folder
    # what a pick of the tree's root stands for: every file, the project's own folder hidden
    rels = tree_files(project.root).paths
    assert rels[:2] == ["notes/readme.md", "runs/cam-a/00000_1700000000000000000.jpg"]
    assert len(rels) == 6 * 2 + 4 * 2 + 1
    assert not any(r.startswith(".specto") for r in rels)
    jobs.shutdown()
    jobs.shutdown()


def test_thread_jobs_run_off_the_submitting_thread_and_deliver_on_poll():
    jobs = ThreadJobManager()
    seen: dict[str, int] = {}

    def work(progress):
        seen["worker"] = threading.get_ident()
        return 7

    done: list[JobInfo] = []
    job = jobs.submit("probe", work, on_done=done.append)
    assert jobs.wait([job.id], timeout=5) and not done  # finished, not yet delivered here
    assert jobs.poll() == 1 and done[0].result == 7 and done[0].state == "done"
    assert seen["worker"] != threading.get_ident()
    jobs.shutdown()


# -- submit: a gallery fed by an intaker off the file tree's output ---------------------
def test_submitted_intaker_resolves_what_the_file_tree_emits(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    tree_col = links.input_of("fege", "data")
    assert isinstance(tree_col, Collection) and tree_col.items == []  # the root alone (#231)

    res = engine.live.submit(
        gallery_fed_from("all_files", engine.live.document()), origin="agent:s1", label="gallery"
    )
    assert res.links["added"] == ["l2"]
    # the project source means the whole folder (#83): the gallery scans its sources at once
    assert links.output_of("project", "all_files") == FilesValue(root=str(project.root))
    assert links.project_files is None  # a narrowed intaker never stands for the folder

    col = links.input_of("kobi", "data")
    assert isinstance(col, Collection) and col.link == "l2"
    assert keys_of(col) == ["00000", "00001", "00002", "00003", "00004", "00005"]
    assert set(col.items[0].parts) == {".jpg"}  # the screener kept the images only
    assert Path(col.items[0].parts[".jpg"]).is_absolute()
    assert links.status("l2")["state"] == "resolved"

    # fewer files on the project source narrow the same intaker: the latest value counts
    rels = tree_files(project.root).paths
    links.emit(
        "project",
        "all_files",
        FilesValue(root=str(project.root), paths=[r for r in rels if "00002" in r]),
    )
    assert keys_of(links.input_of("kobi", "data")) == ["00002"]


def test_latest_value_wins_while_a_resolve_is_in_flight(project: Project):
    jobs = HeldJobs()
    engine = Engine(project=project, jobs=jobs)
    links = engine.links
    engine.live.submit(
        gallery_fed_from("selected_files", engine.live.document()), origin="agent:s1", label="g"
    )
    root = str(project.root)
    jpgs = [
        p
        for p in tree_files(project.root).paths
        if p.startswith("runs/cam-a/") and p.endswith(".jpg")
    ]
    pick = jpgs[1]
    links.emit("fege", "selected_files", FilesValue(root=root, paths=[pick]))
    assert links.resolving("l2") and len(jobs.held) == 1
    # two more selections arrive while the first resolve runs: only the last one counts
    links.emit("fege", "selected_files", FilesValue(root=root, paths=[]))
    links.emit("fege", "selected_files", FilesValue(root=root, paths=[pick, jpgs[4]]))
    assert len(jobs.held) == 1
    jobs.run_next()  # the first resolve lands, then the pending latest input is queued once
    assert keys_of(links.input_of("kobi", "data")) == ["00001"]
    assert len(jobs.held) == 1 and links.resolving("l2")
    jobs.run_next()
    assert keys_of(links.input_of("kobi", "data")) == ["00001", "00004"]
    assert not jobs.held and not links.resolving("l2")


def test_removing_the_link_clears_the_input_and_the_collection_generation_counts(
    project: Project,
):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    doc = gallery_fed_from("selected_files", engine.live.document())
    engine.live.submit(doc, origin="agent:s1", label="g")
    root = str(project.root)
    got: list = []
    links.attach("kobi", lambda port, value: got.append((port, value)))
    third = next(
        p
        for p in tree_files(project.root).paths
        if p.startswith("runs/cam-a/00003") and p.endswith(".jpg")
    )
    links.emit("fege", "selected_files", FilesValue(root=root, paths=[third]))
    links.emit("fege", "selected_files", FilesValue(root=root, paths=[]))
    assert [p for p, _ in got] == ["data", "data"]
    assert keys_of(got[0][1]) == ["00003"] and got[1][1].items == []
    assert got[1][1].generation == got[0][1].generation + 1

    # a view cannot stay without its link (#83): closing the view takes the link with it
    del doc["links"]["l2"]
    del doc["views"]["kobi"]
    engine.live.submit(doc, origin="agent:s1", label="close")
    assert links.input_of("kobi", "data") is None and "kobi" not in links.receivers
    assert links.status("l2") is None


# -- scope: a selection intaker follows the picks, a pinned one ignores them -------------
def test_a_selection_intaker_follows_the_picked_files_and_a_pinned_one_ignores_them(
    project: Project,
):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    rels = tree_files(project.root).paths
    root = str(project.root)

    doc = gallery_fed_from("selected_files", engine.live.document())
    doc["links"]["l2"]["scope"] = "selection"
    del doc["links"]["l2"]["params"]["sources"]  # whatever the user picks, images only
    doc["views"]["tamu"] = {"type": "gallery", "label": "Cam B", "layout": {"position": [300, 420]}}
    doc["links"]["l3"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "tamu", "port": "data"},
        "params": {"sources": ["runs/cam-b"], "screener": ["@images"], "keys": FRAME_KEYS},
        "summary": {"sentence": "The cam-b gallery always shows the cam-b frames."},
    }
    engine.live.submit(doc, origin="agent:s1", label="a selection gallery and a pinned one")

    # the file tree lists everything and nothing is picked yet
    links.emit("project", "all_files", FilesValue(root=root, paths=rels))
    links.emit("fege", "selected_files", FilesValue(root=root, paths=[]))
    rear = links.input_of("tamu", "data")
    assert keys_of(rear) == ["00000", "00001", "00002", "00003"]
    picked = links.input_of("kobi", "data")
    assert isinstance(picked, Collection) and picked.items == []  # empty, not an error

    # picking front files fills the selection gallery; the pinned one does not move
    front2 = [r for r in rels if "cam-a/00002" in r]
    links.emit("fege", "selected_files", FilesValue(root=root, paths=front2))
    assert keys_of(links.input_of("kobi", "data")) == ["00002"]
    assert links.input_of("tamu", "data") is rear

    # a new pick re-resolves: rear files now, and the note is screened out
    rear_and_note = [r for r in rels if "runs/cam-b" in r or r.endswith(".md")]
    links.emit("fege", "selected_files", FilesValue(root=root, paths=rear_and_note))
    assert keys_of(links.input_of("kobi", "data")) == ["00000", "00001", "00002", "00003"]
    assert links.input_of("tamu", "data") is rear
