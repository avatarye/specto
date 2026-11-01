"""Seam 1 (#176): per-pick Python links under stress. A failing script's traceback
reaches the link status (what the agent reads in canvas.state); a slow one times out
(``python_timeout_s``) without holding up the others, even when it left a child
process behind; 10k paths in and out; returns that are not JSON, None or empty;
bursts of picks on real threads; a file edited, deleted or never approved; concurrent
links; Unicode and Windows paths; heavy imports; ``python -I`` isolation."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from test_python_hardening import (
    approve,
    engine_on,
    make_project,
    pick,
    submit_link,
    write_link,
)

import specto_intake  # noqa: F401  (registers the intaker and read contents resolvers)
from specto_core import Project
from specto_core.jobs import ThreadJobManager
from specto_core.links import FilesValue, Selection
from specto_core.python_links import ScriptFailed, build_python_value, sha256_of

PATHS = "def resolve(value, items, params, root):\n    return value['paths']\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    return make_project(tmp_path, monkeypatch)


def settle(engine, jobs: ThreadJobManager, until, timeout: float = 20.0) -> None:
    """Pump the jobs and the debounce as the app's timer does, until ``until()``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs.poll(0.02)
        engine.links.poll()
        if until():
            return
    raise AssertionError("timed out")


# -- a script that fails --------------------------------------------------------------------------
BOOM = """\
def helper(n):
    return 10 // n

def resolve(value, items, params, root):
    return helper(0)
"""


def test_a_failing_script_puts_its_own_traceback_on_the_link_status(project: Project):
    rel = write_link(project, "boom", BOOM)
    approve(project, rel)
    engine, _ = engine_on(project)
    events = []
    engine.links.events.subscribe(lambda ev: events.append((ev.kind, ev.payload)))
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")

    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert (
        status["message"] == f"{rel}:resolve: ZeroDivisionError: integer division or modulo by zero"
    )
    # the script's frames, innermost last, and nothing of the runner or the app
    trace = status["trace"]
    assert f'File "{rel}", line 5, in resolve' in trace
    assert f'File "{rel}", line 2, in helper' in trace
    assert trace.rstrip().endswith("ZeroDivisionError: integer division or modulo by zero")
    assert "_python_link_runner" not in trace and "importlib" not in trace
    failed = [d for k, d in events if k == "link.failed"]
    assert failed[-1]["trace"] == trace

    # fixed, it resolves and the trace is gone
    (project.root / rel).write_text(PATHS, encoding="utf-8")
    approve(project, rel)
    engine.links.clock.now += 1
    pick(engine, project, "geo/b.geojson")
    assert engine.links.status("l9")["state"] == "resolved"
    assert "trace" not in engine.links.status("l9")


def test_a_script_that_breaks_on_import_is_reported_with_its_line(project: Project):
    rel = write_link(project, "syntax", "def resolve(value, items, params, root)\n    return 1\n")
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")

    status = engine.links.status("l9")
    assert status["state"] == "failed" and "SyntaxError" in status["message"]
    assert f'File "{rel}", line 1' in status["trace"]


# -- a script that takes too long ---------------------------------------------------------------
SLOW = """\
import time

def resolve(value, items, params, root):
    time.sleep(30)
    return value["paths"]
"""
#: leaves a child process holding the pipes, then hangs
LINGERING = """\
import subprocess, sys, time

def resolve(value, items, params, root):
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(30)
"""


@pytest.mark.parametrize("body", [SLOW, LINGERING], ids=["slow", "lingering child"])
def test_a_slow_script_times_out_with_a_fixit(project: Project, body):
    rel = write_link(project, "slow", body)
    approve(project, rel)
    engine, _ = engine_on(project, python_timeout_s=1.0)
    submit_link(engine, rel)
    start = time.monotonic()
    pick(engine, project, "geo/a.geojson")

    # the script sleeps 30 s: the 1 s timeout fired, with room for a slow spawn under
    # parallel load (#179)
    assert time.monotonic() - start < 15.0
    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert status["message"] == f"{rel}:resolve did not finish in 1 s; fix it to return sooner"


def test_a_slow_script_does_not_hold_up_another_link(project: Project):
    slow = write_link(project, "slow", SLOW)
    fast = write_link(project, "fast", PATHS)
    approve(project, slow)
    approve(project, fast)
    jobs = ThreadJobManager()
    engine, _ = engine_on(project, jobs, python_timeout_s=8.0)
    submit_link(engine, slow, "selection", lid="l9")
    submit_link(engine, fast, "content", lid="l8")
    pick(engine, project, "geo/a.geojson")

    settle(engine, jobs, lambda: engine.links.status("l8")["state"] == "resolved", timeout=6)
    assert engine.links.status("l9")["state"] == "resolving"
    assert [e.path for e in engine.links.input_of("mapa", "features").entries] == ["geo/a.geojson"]
    settle(engine, jobs, lambda: engine.links.status("l9")["state"] == "failed", timeout=15)
    assert not engine.links.resolving("l9")  # not run again for the same pick


def test_a_failed_run_is_not_retried_until_the_code_or_the_value_changes(project: Project):
    rel = write_link(
        project,
        "fails",
        "import pathlib\n\ndef resolve(value, items, params, root):\n"
        "    t = pathlib.Path(root) / '.specto' / 'links' / 'fails.ran'\n"
        "    t.write_text((t.read_text() if t.exists() else '') + 'x')\n"
        "    raise ValueError('no')\n",
    )
    approve(project, rel)
    engine, clock = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")
    ran = project.root / ".specto" / "links" / "fails.ran"
    assert ran.read_text() == "x" and engine.links.status("l9")["state"] == "failed"

    # new data lands on the target tree: the same pick does not run the code again
    doc = engine.live.document()
    listing = engine.links.input_of("tora", "data").generation
    doc["links"]["l5"]["params"]["sources"] = ["geo"]
    engine.live.submit(doc, origin="agent:s1", label="tree B shows geo")
    assert engine.links.input_of("tora", "data").generation > listing
    assert ran.read_text() == "x"
    status = engine.links.status("l9")
    assert status["state"] == "failed" and "ValueError: no" in status["message"]
    assert "trace" in status

    clock.now += 1
    pick(engine, project, "geo/b.geojson")  # another pick: it runs again
    assert ran.read_text() == "xx"


def test_a_refresh_runs_the_code_again_as_an_intaker_scans_again(project: Project):
    # the script reads the disk; the manual rescan of its target must see what changed
    rel = write_link(
        project,
        "ls",
        "import os\n\ndef resolve(value, items, params, root):\n"
        "    return sorted('geo/' + n for n in os.listdir(os.path.join(root, 'geo')))\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")
    assert len(engine.links.input_of("tora", "selection").keys) == 3

    (project.root / "geo" / "c.geojson").write_text("{}", encoding="utf-8")
    assert "l9" in engine.links.refresh("tora")
    assert engine.links.input_of("tora", "selection").keys[2] == "geo/c.geojson"


# -- large values ---------------------------------------------------------------------------------
def test_ten_thousand_paths_in_and_out(project: Project):
    rel = write_link(project, "many", PATHS)
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    paths = [f"big/{i // 100:03d}/f{i:05d}.txt" for i in range(10_000)]
    start = time.monotonic()
    pick(engine, project, *paths)
    took = time.monotonic() - start

    assert engine.links.status("l9")["state"] == "resolved"
    sel = engine.links.input_of("tora", "selection")
    assert len(sel.keys) == 10_000 and sel.last == paths[-1]
    assert took < 5.0, f"10k paths took {took:.2f} s"


def test_a_collection_of_two_thousand_files(project: Project):
    folder = project.root / "many"
    folder.mkdir()
    for i in range(2000):
        (folder / f"f{i:04d}.txt").write_bytes(b"x")
    rel = write_link(
        project,
        "col",
        "import os\n\ndef resolve(value, items, params, root):\n"
        "    return ['many/' + n for n in sorted(os.listdir(os.path.join(root, 'many')))]\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel, "collection")
    pick(engine, project, "geo/a.geojson")

    col = engine.links.input_of("kobi", "data")
    assert len(col.items) == 2000 and col.items[-1].key == "many/f1999"


# -- odd returns ----------------------------------------------------------------------------------
def test_a_return_that_is_not_json_says_what_to_return(project: Project):
    rel = write_link(
        project, "set", "def resolve(value, items, params, root):\n    return {1, 2}\n"
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")

    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert status["message"] == (
        f"{rel}:resolve returned a set, which is not JSON; return a dict, list, str, "
        "number, bool or None"
    )


def test_none_delivers_nothing_and_the_target_keeps_what_it_has(project: Project):
    rel = write_link(
        project,
        "maybe",
        "def resolve(value, items, params, root):\n"
        "    return None if value['paths'] == ['geo/b.geojson'] else value['paths']\n",
    )
    approve(project, rel)
    engine, clock = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")
    assert engine.links.input_of("tora", "selection").keys == ["geo/a.geojson"]

    clock.now += 1
    pick(engine, project, "geo/b.geojson")
    status = engine.links.status("l9")
    assert status["state"] == "resolved"
    assert status["message"] == f"{rel}:resolve returned None: nothing delivered"
    assert engine.links.input_of("tora", "selection").keys == ["geo/a.geojson"]

    clock.now += 1
    pick(engine, project)  # nothing picked: the script returns [], which clears
    assert engine.links.input_of("tora", "selection") == Selection(keys=[], last=None)


def test_prints_in_the_script_do_not_break_its_reply(project: Project):
    rel = write_link(
        project,
        "chatty",
        "import sys\n\ndef resolve(value, items, params, root):\n"
        "    print('looking at', value['paths'], end='')\n"
        "    print('Tōkyō ✓')\n"
        "    sys.stdout.write('no newline')\n"
        "    return value['paths']\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")

    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    assert engine.links.input_of("tora", "selection").keys == ["geo/a.geojson"]


# -- bursts on real threads -----------------------------------------------------------------------
def test_a_burst_of_picks_on_real_threads_lands_only_the_last(project: Project):
    rel = write_link(
        project,
        "count",
        "import json, pathlib\n\ndef resolve(value, items, params, root):\n"
        "    t = pathlib.Path(root) / '.specto' / 'links' / 'count.ran'\n"
        "    runs = json.loads(t.read_text()) if t.exists() else []\n"
        "    t.write_text(json.dumps(runs + [value['paths']]))\n"
        "    return value['paths']\n",
    )
    approve(project, rel)
    jobs = ThreadJobManager()
    engine, _ = engine_on(project, jobs)
    engine.links.clock = time.monotonic
    submit_link(engine, rel)
    landed = []
    engine.links.attach("tora", lambda port, v: landed.append(v) if port == "selection" else None)
    for i in range(20):
        pick(engine, project, f"geo/{i}.geojson")
        jobs.poll(0.005)
        engine.links.poll()

    last = Selection(keys=["geo/19.geojson"], last="geo/19.geojson")
    settle(engine, jobs, lambda: engine.links.input_of("tora", "selection") == last)
    settle(engine, jobs, lambda: not engine.links.resolving("l9"))
    ran = json.loads((project.root / ".specto" / "links" / "count.ran").read_text())
    assert len(ran) < 20 and ran[-1] == ["geo/19.geojson"]
    assert landed[-1] == last
    # whatever landed, it landed in pick order (a stale result never overtakes)
    order = [int(v.keys[0].split("/")[1].split(".")[0]) for v in landed if v and v.keys]
    assert order == sorted(order)


# -- approval -------------------------------------------------------------------------------------
def test_a_file_edited_after_approval_needs_approval_again_and_does_not_run(
    project: Project, approve_link
):
    rel = write_link(project, "echo", PATHS)
    approve(project, rel)
    engine, clock = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")
    assert engine.links.status("l9")["state"] == "resolved"

    (project.root / rel).write_text(PATHS.replace("value['paths']", "['geo/b.geojson']"))
    clock.now += 1
    pick(engine, project, "geo/readme.md")
    status = engine.links.status("l9")
    assert status["state"] == "needs_approval"
    assert status["hash"] == sha256_of((project.root / rel).read_bytes())
    assert engine.links.input_of("tora", "selection").keys == ["geo/a.geojson"]

    approve_link(engine, "l9")
    assert engine.links.input_of("tora", "selection").keys == ["geo/b.geojson"]


def test_a_file_changed_between_the_check_and_the_run_is_refused(project: Project):
    rel = write_link(project, "echo", PATHS)
    old = sha256_of((project.root / rel).read_bytes())
    (project.root / rel).write_text("def resolve(value, items, params, root):\n    return 1\n")
    link = {"python": {"path": rel, "entry": "resolve", "output": "selection"}, "params": {}}
    failed = build_python_value(link, old, {"paths": []}, [], root=str(project.root))
    assert isinstance(failed, ScriptFailed)
    assert failed.message == (
        f"{rel}:resolve: PermissionError: the file changed since it was approved; approve it again"
    )


def test_a_deleted_file_fails_with_a_fixit_and_an_unapproved_one_waits(project: Project):
    rel = write_link(project, "echo", PATHS)
    approve(project, rel)
    engine, clock = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")
    (project.root / rel).unlink()
    clock.now += 1
    pick(engine, project, "geo/b.geojson")
    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert status["message"] == (
        f"{rel} is missing; write it with def resolve(value, items, params, root)"
    )

    (project.root / rel).write_text(PATHS + "# new\n", encoding="utf-8")
    clock.now += 1
    pick(engine, project, "geo/readme.md")
    assert engine.links.status("l9")["state"] == "needs_approval"
    assert engine.links.input_of("tora", "selection").keys == ["geo/a.geojson"]


# -- concurrent links -----------------------------------------------------------------------------
def test_two_links_off_one_pick_run_side_by_side(project: Project):
    # each nap records when it slept, next to its script: side by side means the two
    # spans overlap, however long the spawns took under parallel load (#179)
    nap = (
        "import pathlib, time\n\ndef resolve(value, items, params, root):\n"
        "    t0 = time.time()\n    time.sleep(1.5)\n"
        "    span = pathlib.Path(root) / '.specto' / 'links' / 'NAME.span'\n"
        "    span.write_text(f'{t0} {time.time()}')\n    return value['paths']\n"
    )
    a = write_link(project, "a", nap.replace("NAME", "a"))
    b = write_link(project, "b", nap.replace("NAME", "b"))
    approve(project, a)
    approve(project, b)
    jobs = ThreadJobManager()
    engine, _ = engine_on(project, jobs)
    submit_link(engine, a, "selection", lid="l9")
    submit_link(engine, b, "content", lid="l8")
    pick(engine, project, "geo/a.geojson")
    settle(
        engine,
        jobs,
        lambda: all(engine.links.status(lid)["state"] == "resolved" for lid in ("l8", "l9")),
    )

    def span(name: str) -> tuple[float, float]:
        t0, t1 = (project.root / ".specto" / "links" / f"{name}.span").read_text().split()
        return float(t0), float(t1)

    (a0, a1), (b0, b1) = span("a"), span("b")
    assert a0 < b1 and b0 < a1, f"one after the other: a {a0}-{a1}, b {b0}-{b1}"
    assert engine.links.input_of("tora", "selection").keys == ["geo/a.geojson"]
    assert [e.path for e in engine.links.input_of("mapa", "features").entries] == ["geo/a.geojson"]


# -- Unicode and Windows paths --------------------------------------------------------------------
def test_unicode_paths_and_text_files_without_an_encoding(project: Project):
    folder = project.root / "Données" / "東京"
    folder.mkdir(parents=True)
    fc = {"type": "FeatureCollection", "features": [], "name": "Zürich ✓"}
    (folder / "é.geojson").write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    rel = write_link(
        project,
        "uni",
        "import json, os\n\ndef resolve(value, items, params, root):\n"
        "    p = value['paths'][0]\n"
        "    data = json.loads(open(os.path.join(root, p)).read())\n"  # no encoding given
        "    print(data['name'])\n"
        "    return [p] if data['name'] == 'Zürich ✓' else []\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "Données/東京/é.geojson")

    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    assert engine.links.input_of("tora", "selection").keys == ["Données/東京/é.geojson"]


def test_a_file_tree_selection_takes_windows_and_absolute_paths(project: Project):
    rel = write_link(
        project,
        "win",
        "import os\n\ndef resolve(value, items, params, root):\n"
        "    return [os.path.join('geo', 'b.geojson'), os.path.join(root, 'geo', 'a.geojson')]\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/readme.md")

    sel = engine.links.input_of("tora", "selection")
    assert sel == Selection(keys=["geo/b.geojson", "geo/a.geojson"], last="geo/a.geojson")


# -- the interpreter ------------------------------------------------------------------------------
def test_a_heavy_import_costs_time_on_every_run(project: Project):
    rel = write_link(
        project,
        "heavy",
        "import numpy\n\ndef resolve(value, items, params, root):\n"
        "    return [str(int(numpy.arange(3).sum()))]\n",
    )
    approve(project, rel)
    engine, clock = engine_on(project)
    submit_link(engine, rel, "selection")
    took = []
    for p in ("geo/a.geojson", "geo/b.geojson"):
        clock.now += 1
        start = time.monotonic()
        pick(engine, project, p)
        took.append(time.monotonic() - start)
    assert engine.links.input_of("tora", "selection").keys == ["3"]
    assert max(took) < 10.0, took  # numpy imports afresh per run: no warm interpreter


def test_the_script_runs_isolated_from_planted_modules_and_pythonpath(
    project: Project, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    planted = "raise ImportError('planted module ran')\n"
    for where in (project.root / ".specto" / "links", project.root, tmp_path / "pp"):
        where.mkdir(parents=True, exist_ok=True)
        (where / "json.py").write_text(planted, encoding="utf-8")
        (where / "geohelpers.py").write_text(planted, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "pp"))
    rel = write_link(
        project,
        "iso",
        "import json, sys\n\ndef resolve(value, items, params, root):\n"
        "    try:\n        import geohelpers  # noqa\n        found = 'yes'\n"
        "    except ImportError as e:\n        found = str(e)\n"
        "    return [json.dumps(found), str(sys.flags.isolated)]\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")

    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    keys = engine.links.input_of("tora", "selection").keys
    assert keys == [json.dumps("No module named 'geohelpers'"), "1"]
    assert os.environ["PYTHONPATH"]  # the app's environment is left alone


def test_a_value_runs_out_of_process(project: Project):
    rel = write_link(
        project,
        "pid",
        "import os\n\ndef resolve(value, items, params, root):\n    return [str(os.getpid())]\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    pick(engine, project, "geo/a.geojson")
    assert engine.links.input_of("tora", "selection").keys != [str(os.getpid())]
    assert isinstance(engine.links.output_of("fege", "selected_files"), FilesValue)
