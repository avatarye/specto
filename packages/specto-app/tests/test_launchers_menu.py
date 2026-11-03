"""Seam 2 (#186): the launchers in the context menu and for the agent, headless. A fake
launcher (Python running a script that writes its argv, folder and environment to a
file) stands in for Blender: a right-click on a file tree row offers "Open with <the
default launcher>" and the "Open with" submenu (matching launchers, System default,
Show in Explorer, Edit launchers...); several files go to an ``each = false`` launcher in
one process; programs and scripts never reach the system default; the project's file
merges over the machine's; a saved file reloads with its problems as notices; the
scripted agent lists and launches, and its launch is a row in the agent thread. No real
GUI program starts: the system default, Explorer and the editor are recorders."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from web_helpers import registry

pytest.importorskip("PySide6.QtGui")

from specto_app.launchers.agent import LAUNCH_TOOLS, LaunchTools
from specto_app.launchers.book import LauncherBook, book_for
from specto_app.launchers.watch import LauncherWatch
from specto_app.menu.shell import ContextMenus
from specto_app.prompt.context import CanvasContext
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.session import sdk_tools
from specto_app.prompt.turn import TurnTools, run_turn
from specto_core import CallContext, Engine, Project
from specto_core.agent_tools import AgentTools
from specto_core.errors import InvalidArgs
from specto_core.jobs import InlineJobManager

FAKE = """\
import json, os, sys
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
data = {"argv": sys.argv[2:], "cwd": os.getcwd(), "env": os.environ.get("FAKE_ENV", "")}
tmp = os.path.join(out, f"argv-{os.getpid()}.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f)
os.replace(tmp, os.path.join(out, f"argv-{os.getpid()}.json"))
"""

FILES = (
    "scenes/a.blend",
    "scenes/b c & d.blend",
    "shots/1.jpg",
    "shots/2.jpg",
    "shots/3.jpg",
    "tools/setup.exe",
    "tools/run.ps1",
    "notes/a.md",
)


def launchers_toml(script: Path, out: Path) -> str:
    py, s, o = (Path(sys.executable).as_posix(), script.as_posix(), out.as_posix())
    return f"""\
[[launcher]]
id = "fake-blender"
label = "Fake Blender"
command = "{py}"
args = ["{s}", "{o}", "{{path}}"]
match = ["*.blend"]
cwd = "{{dir}}"
env = {{ FAKE_ENV = "{{name}}" }}
default = true

[[launcher]]
id = "fake-viewer"
label = "Fake Viewer"
command = "{py}"
args = ["{s}", "{o}", "{{paths}}"]
match = ["*.jpg"]
each = false
"""


def recorded(out: Path, n: int, timeout: float = 20.0) -> list[dict]:
    """The argv records of ``n`` launched processes (they run detached: wait for them)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = sorted(out.glob("argv-*.json"))
        if len(found) >= n:
            time.sleep(0.2)  # a stray extra process would show up now
            found = sorted(out.glob("argv-*.json"))
            return [json.loads(p.read_text(encoding="utf-8")) for p in found]
        time.sleep(0.05)
    raise AssertionError(f"{len(list(out.glob('argv-*.json')))} of {n} launches recorded")


class Opened:
    def __init__(self, menus: ContextMenus) -> None:
        self.calls: list[list[dict]] = []
        menus.openRequested.connect(lambda rows, x, y, kb: self.calls.append(rows))

    def rows(self) -> dict[str, dict]:
        return {r["name"]: r for r in self.calls[-1] if "name" in r}

    def children(self) -> dict[str, dict]:
        kids = self.rows()["launch.open_with"]["children"]
        return {r["name"].split("/", 1)[1]: r for r in kids if "name" in r}


@pytest.fixture
def s(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    user = tmp_path / "user"
    monkeypatch.setenv("SPECTO_USER_DIR", str(user))
    proj = Project.init(tmp_path / "proj", "launch")
    for rel in FILES:
        p = proj.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    script, out = tmp_path / "fake_launcher.py", tmp_path / "out"
    script.write_text(FAKE, encoding="utf-8")
    (user / "launchers.toml").parent.mkdir(parents=True)
    (user / "launchers.toml").write_text(launchers_toml(script, out), encoding="utf-8")
    engine = Engine(project=proj, jobs=InlineJobManager())
    engine.links  # noqa: B018
    reg = registry(engine)
    tree = reg.host("fege")
    tree.load_all()  # every folder listed (#231) ...
    tree.model.expanded = {p for p, n in tree.model._nodes.items() if n.is_dir}
    tree.model._set_rows(tree.model._walk())  # ... and open
    menus = ContextMenus(reg)
    reg.ctx.context_menus = menus
    book = book_for(proj.root)
    started: list[str] = []
    revealed: list[list[str]] = []
    edited: list[str] = []
    book.start, book.reveal_fn, book.edit_fn = started.append, revealed.append, edited.append
    return SimpleNamespace(
        engine=engine, proj=proj, reg=reg, tree=tree, menus=menus, book=book, out=out,
        user=user, started=started, revealed=revealed, edited=edited,
    )  # fmt: skip


def native(s: SimpleNamespace, rel: str) -> str:
    return os.path.join(str(s.proj.root), *rel.split("/"))


def right_click(s: SimpleNamespace, rel: str) -> Opened:
    opened = Opened(s.menus)
    s.tree.bridge.contextMenu(s.tree.model.row_of(rel), 40.0, 40.0)
    return opened


# -- the menu -------------------------------------------------------------------------------------
def test_a_blend_row_opens_with_its_default_launcher_one_argv_item_per_placeholder(s):
    opened = right_click(s, "scenes/b c & d.blend")
    rows = opened.rows()
    # after the tree's own open entries (SPEC-32), the launchers lead
    assert list(rows)[:4] == [
        "file_tree.open_dedicated",
        "file_tree.open_contextual",
        "launch.default",
        "launch.open_with",
    ]
    assert rows["launch.default"]["text"] == "Open with Fake Blender"
    assert list(opened.children()) == [
        "launcher:fake-blender", "system", "explorer", "edit", "edit_project",
    ]  # fmt: skip
    assert opened.children()["launcher:fake-blender"]["detail"] == "default"
    assert opened.children()["system"]["enabled"] is True

    assert s.menus.trigger("launch.default") == "launched Fake Blender on b c & d.blend"
    (rec,) = recorded(s.out, 1)
    # one argument, spaces and & included: no shell read it
    assert rec["argv"] == [native(s, "scenes/b c & d.blend")]
    assert os.path.normcase(rec["cwd"]) == os.path.normcase(native(s, "scenes"))
    assert rec["env"] == "b c & d.blend"


def test_several_jpgs_go_to_an_each_false_launcher_in_one_process(s):
    s.tree.select_paths(["shots/3.jpg", "shots/1.jpg", "shots/2.jpg"])
    opened = right_click(s, "shots/2.jpg")
    # no default launcher for jpgs: the top entry is the system default
    assert opened.rows()["launch.default"]["text"] == "Open with system default"
    assert list(opened.children())[:1] == ["launcher:fake-viewer"]
    assert "launcher:fake-blender" not in opened.children()

    assert s.menus.trigger("launch.open_with/launcher:fake-viewer").startswith(
        "launched Fake Viewer on 1.jpg and 2 more"
    )
    (rec,) = recorded(s.out, 1)
    assert rec["argv"] == [native(s, f"shots/{n}.jpg") for n in ("1", "2", "3")]

    assert s.menus.trigger("launch.default") == "launched System default on 1.jpg and 2 more"
    assert s.started == [native(s, f"shots/{n}.jpg") for n in ("1", "2", "3")]


def test_each_true_starts_one_process_per_file(s):
    s.tree.select_paths(["scenes/a.blend", "scenes/b c & d.blend"])
    right_click(s, "scenes/a.blend")
    s.menus.trigger("launch.open_with/launcher:fake-blender")
    recs = recorded(s.out, 2)
    assert sorted(r["argv"][0] for r in recs) == sorted(
        [native(s, "scenes/a.blend"), native(s, "scenes/b c & d.blend")]
    )


@pytest.mark.parametrize("rel", ["tools/setup.exe", "tools/run.ps1"])
def test_programs_and_scripts_never_reach_the_system_default(s, rel):
    opened = right_click(s, rel)
    assert "launch.default" not in opened.rows()  # no launcher, and no system default
    kids = opened.children()
    assert list(kids) == ["system", "explorer", "edit", "edit_project"]
    assert kids["system"]["enabled"] is False and kids["system"]["detail"] == "would run it"
    assert s.menus.trigger("launch.open_with/system") is None
    assert s.started == []
    # Show in Explorer is fine: it runs nothing
    s.menus.trigger("launch.open_with/explorer")
    assert s.revealed == [[native(s, rel)]]


def test_a_folder_row_and_a_mixed_selection(s):
    opened = right_click(s, "scenes")
    assert opened.rows()["launch.default"]["text"] == "Open with system default"
    assert list(opened.children()) == ["system", "explorer", "edit", "edit_project"]
    s.tree.select_paths(["scenes/a.blend", "shots/1.jpg"])
    opened = right_click(s, "shots/1.jpg")
    assert "launcher:fake-blender" not in opened.children()  # it does not open the jpg
    assert "launcher:fake-viewer" not in opened.children()


def test_edit_launchers_opens_the_machine_file_writing_an_example_first(s, tmp_path):
    right_click(s, "notes/a.md")
    assert s.menus.trigger("launch.open_with/edit") == str(s.user / "launchers.toml")
    assert s.edited == [str(s.user / "launchers.toml")]
    fresh = LauncherBook(None, machine_path=tmp_path / "new" / "launchers.toml",
                         edit=s.edited.append)  # fmt: skip
    path = fresh.edit()
    assert "[[launcher]]" in Path(path).read_text(encoding="utf-8")
    assert fresh.all() == []  # the example is all comments


def test_edit_project_launchers_opens_the_project_file_writing_an_example_first(s):
    """#204: the row next to the machine file's; the file is created from a commented
    example (all comments: nothing loads from it) under the project's .specto."""
    proj_file = s.proj.root / ".specto" / "launchers.toml"
    assert not proj_file.exists()
    opened = right_click(s, "notes/a.md")
    assert opened.children()["edit_project"]["detail"] == ".specto/launchers.toml"
    assert s.menus.trigger("launch.open_with/edit_project") == str(proj_file)
    assert s.edited == [str(proj_file)]
    text = proj_file.read_text(encoding="utf-8")
    assert "[[launcher]]" in text and "override" in text
    assert s.book.refresh() is True
    assert [lc.id for lc in s.book.all()] == ["fake-blender", "fake-viewer"]
    # the machine's book has no project row and no project file to edit
    machine = LauncherBook(None, machine_path=s.user / "launchers.toml", edit=s.edited.append)
    with pytest.raises(InvalidArgs):
        machine.edit_project()


def test_the_project_file_merges_over_the_machine_s(s):
    proj_file = s.proj.root / ".specto" / "launchers.toml"
    proj_file.write_text(
        '[[launcher]]\nid = "fake-blender"\nlabel = "Project Blender"\n\n'
        '[[launcher]]\nid = "fake-viewer"\ndefault = true\n',
        encoding="utf-8",
    )
    opened = right_click(s, "shots/1.jpg")
    assert opened.rows()["launch.default"]["text"] == "Open with Fake Viewer"
    opened = right_click(s, "scenes/a.blend")
    assert opened.rows()["launch.default"]["text"] == "Open with Project Blender"


def test_a_saved_file_reloads_and_its_problems_become_notices(s, spin):
    from specto_app.notices import Notices

    notices = Notices()
    watch = LauncherWatch(s.book, notices)
    try:
        assert notices.log == []  # a good file at the start says nothing
        machine = s.user / "launchers.toml"
        good = machine.read_text(encoding="utf-8")
        machine.write_text(good.replace('label = "Fake Viewer"', 'labl = "Fake Viewer"'),
                           encoding="utf-8")  # fmt: skip
        spin(until=lambda: notices.log, timeout=10.0)
        warn = notices.log[-1]
        assert warn["level"] == "warn"
        assert f"{machine}:13: unknown key 'labl'" in warn["message"]
        assert "label" in warn["fix"]
        assert [n["id"] for n in notices.items] == [warn["id"]]
        assert [lc.id for lc in s.book.launchers] == ["fake-blender"]

        # the fixed file: the warning goes, the reload says so (#192)
        machine.write_text(good, encoding="utf-8")
        spin(until=lambda: notices.log[-1]["level"] == "info", timeout=10.0)
        assert notices.log[-1]["message"] == "launchers reloaded: 2 launchers"
        assert [n["level"] for n in notices.items] == ["info"]
    finally:
        watch.close()
    assert watch._reloaded not in s.book.listeners


# -- the agent ------------------------------------------------------------------------------------
def turn_tools(s: SimpleNamespace) -> TurnTools:
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: s.engine.call(n, a, ctx))
    return TurnTools(agent, s.engine.live, launch=LaunchTools(s.book))


def scripted_turn(tools: TurnTools, steps: list[dict]):
    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(document=tools.live.document(), viewport={"x": 0, "y": 0, "w": 9, "h": 9})
    return asyncio.run(run_turn(session, tools, "open it in blender", ctx)), session


def test_the_scripted_agent_lists_and_launches_and_is_refused_a_program(s):
    tools = turn_tools(s)
    reply, session = scripted_turn(
        tools,
        [
            {"tool": "launchers", "args": {"paths": ["scenes/a.blend"]}},
            {"tool": "launchers", "args": {}},
            {"tool": "launch", "args": {"launcher": "fake-blender", "paths": ["scenes/a.blend"]}},
            {"tool": "launch", "args": {"launcher": "system", "paths": ["tools/setup.exe"]}},
            {"tool": "launch", "args": {"launcher": "notepad", "paths": ["notes/a.md"]}},
            {"text": "Opened a.blend in Fake Blender."},
        ],
    )
    listed, everything, launched, refused, unknown = session.calls
    assert listed.data["default"] == "fake-blender"
    assert [lc["id"] for lc in listed.data["launchers"]] == ["fake-blender"]
    assert listed.data["system_default"] == {"allowed": True}
    assert [lc["id"] for lc in everything.data["launchers"]] == ["fake-blender", "fake-viewer"]
    assert launched.is_error is False and launched.data["paths"] == ["scenes/a.blend"]
    assert refused.is_error and "refused" in refused.text and "would run it" in refused.text
    assert unknown.is_error and "no launcher 'notepad'" in unknown.text
    assert s.started == []
    # every launch is a row, a refused one with its reason (#192)
    assert [done.note for done in reply.launches] == [
        "launched Fake Blender on a.blend",
        "refused System default on setup.exe: setup.exe is a program or script (.exe): "
        "opening it would run it",
        "refused notepad on a.md: no launcher 'notepad'",
    ]
    assert [bool(done.refused) for done in reply.launches] == [False, True, True]
    assert reply.status == "answered" and reply.turn_txn is None  # no canvas change
    (rec,) = recorded(s.out, 1)
    assert rec["argv"] == [native(s, "scenes/a.blend")]


def test_the_launcher_tools_join_the_in_process_server(s):
    names = {t.name for t in sdk_tools(turn_tools(s))}
    assert set(LAUNCH_TOOLS) <= names
    ro = {t.name: t.annotations.read_only_hint for t in sdk_tools(turn_tools(s))}
    assert ro["launchers"] is True and ro["launch"] is False


# -- the agent thread's launch row ----------------------------------------------------------------
@pytest.fixture
def bar_app(qapp, aloop, spin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from specto_app.app import SpectoApp

    user = tmp_path / "user"
    monkeypatch.setenv("SPECTO_USER_DIR", str(user))
    proj = Project.init(tmp_path / "proj", "launch")
    (proj.root / "scenes").mkdir()
    (proj.root / "scenes" / "a.blend").write_bytes(b"x")
    script, out = tmp_path / "fake_launcher.py", tmp_path / "out"
    script.write_text(FAKE, encoding="utf-8")
    user.mkdir()
    (user / "launchers.toml").write_text(launchers_toml(script, out), encoding="utf-8")
    (proj.root / "setup.exe").write_bytes(b"x")
    steps = [
        {"tool": "launch", "args": {"launcher": "fake-blender", "paths": ["scenes/a.blend"]}},
        {"text": "Opened it."},
    ]
    refused = [
        {"tool": "launch", "args": {"launcher": "system", "paths": ["setup.exe"]}},
        {"text": "I can't run that."},
    ]
    a = SpectoApp(
        proj, jobs="inline", web=False, offscreen=True, serve=True, loop=aloop,
        agent=lambda tools: ScriptedAgentSession(tools, [steps, refused]),
    )  # fmt: skip
    a.start()
    a.out = out  # type: ignore[attr-defined]
    spin(50)
    yield a
    a.close()
    spin(20)


def test_an_agent_launch_is_a_row_in_the_thread(bar_app, aspin, spin):
    bar = bar_app.prompt_bar
    bar.submit("open a.blend in blender")
    aspin(until=lambda: not bar.busy)
    entries = bar.thread.thread.entries
    assert [e.kind for e in entries][-2:] == ["agent", "launch"]
    assert entries[-1].label == "launched Fake Blender on a.blend"
    bar.panel.summon()
    locate = bar_app.engine.call
    spin(until=lambda: locate("shell.locate", {"name": "replyLaunch"})["visible"])
    assert locate("shell.locate", {"name": "replyLaunchNote"})["visible"] is True
    (rec,) = recorded(bar_app.out, 1)
    assert rec["argv"] == [os.path.join(str(bar_app.project.root), "scenes", "a.blend")]
    # the thread file keeps it for the next launch
    lines = (bar_app.project.root / ".specto" / "agent-thread.jsonl").read_text("utf-8")
    assert '"kind": "launch"' in lines

    # a refused launch is a row too, in the error tone, with the reason (#192)
    bar.submit("run setup.exe")
    aspin(until=lambda: not bar.busy)
    entries = bar.thread.thread.entries
    assert [e.kind for e in entries][-2:] == ["agent", "launch"]
    assert entries[-1].status == "failed"
    assert entries[-1].label == (
        "refused System default on setup.exe: setup.exe is a program or script (.exe): "
        "opening it would run it"
    )
    spin(until=lambda: locate("shell.locate", {"name": "replyLaunchNote"})["text"]
         == entries[-1].label)  # fmt: skip
    assert len(recorded(bar_app.out, 1)) == 1  # nothing ran


# -- the shell's submenu (offscreen) ---------------------------------------------------------------
@pytest.fixture
def shell_app(qapp, spin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from specto_app.app import SpectoApp

    user = tmp_path / "user"
    monkeypatch.setenv("SPECTO_USER_DIR", str(user))
    proj = Project.init(tmp_path / "proj", "launch")
    (proj.root / "scenes").mkdir()
    (proj.root / "scenes" / "a.blend").write_bytes(b"x")
    script, out = tmp_path / "fake_launcher.py", tmp_path / "out"
    script.write_text(FAKE, encoding="utf-8")
    user.mkdir()
    (user / "launchers.toml").write_text(launchers_toml(script, out), encoding="utf-8")
    a = SpectoApp(proj, jobs="inline", web=False, offscreen=True, serve=True)
    a.start()
    a.out = out  # type: ignore[attr-defined]
    spin(50)
    yield a
    a.close()
    spin(20)


def _locate(app, name: str) -> dict:
    return app.engine.call("shell.locate", {"name": name})


def _centre(app, name: str):
    from specto_app.app import find_item

    item = find_item(app.window.contentItem(), name)
    assert item is not None, name
    return item.mapToScene(item.boundingRect().center()).toPoint()


def test_the_open_with_submenu_opens_beside_its_row_and_launches(shell_app, spin):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app = shell_app
    row = "treeRow:fege:scenes/a.blend"
    spin(until=lambda: _locate(app, "treeRow:fege:scenes")["visible"])
    app.hosts.hosts["fege"].expand_path("scenes")  # listed on first open (#231)
    spin(until=lambda: _locate(app, row)["visible"])
    QTest.mouseClick(app.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                     _centre(app, row))  # fmt: skip
    parent = "menu:launch.open_with"
    child = "menu:launch.open_with/launcher:fake-blender"
    spin(until=lambda: _locate(app, parent)["visible"])
    assert _locate(app, "menuText:launch.default")["text"] == "Open with Fake Blender"
    assert not _locate(app, child)["visible"]
    # the rows are laid out a frame after the menu shows: hover the row only then
    spin(until=lambda: _locate(app, parent)["rect"][1]
         > _locate(app, "menu:launch.default")["rect"][1])  # fmt: skip
    QTest.mouseMove(app.window, _centre(app, parent))  # hover opens it
    spin(until=lambda: _locate(app, child)["visible"])
    spin(until=lambda: _locate(app, child)["rect"][0] > _locate(app, parent)["rect"][0])
    # its rows too are laid out a frame later (a click before that hits the last row)
    spin(until=lambda: _locate(app, "menu:launch.open_with/edit")["rect"][1]
         > _locate(app, child)["rect"][1])  # fmt: skip
    for name in ("system", "explorer", "edit", "edit_project"):
        assert _locate(app, f"menu:launch.open_with/{name}")["visible"]
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _centre(app, child))  # fmt: skip
    spin(until=lambda: not _locate(app, parent)["visible"] and not _locate(app, child)["visible"])
    (rec,) = recorded(app.out, 1)
    assert rec["argv"] == [os.path.join(str(app.project.root), "scenes", "a.blend")]

    # from the keyboard: Down four times to "Open with" (a right-click's menu starts with
    # no row; the tree's two open entries of SPEC-32 lead), Right opens it with the keys, Left
    # closes it, Right again and Enter run its first row
    QTest.mouseClick(app.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                     _centre(app, row))  # fmt: skip
    spin(until=lambda: _locate(app, parent)["visible"])
    for _ in range(4):
        QTest.keyClick(app.window, Qt.Key.Key_Down)
    QTest.keyClick(app.window, Qt.Key.Key_Right)
    spin(until=lambda: _locate(app, child)["visible"])
    QTest.keyClick(app.window, Qt.Key.Key_Left)
    spin(until=lambda: not _locate(app, child)["visible"])
    assert _locate(app, parent)["visible"]
    QTest.keyClick(app.window, Qt.Key.Key_Right)
    spin(until=lambda: _locate(app, child)["visible"])
    QTest.keyClick(app.window, Qt.Key.Key_Return)
    spin(until=lambda: not _locate(app, parent)["visible"])
    assert len(recorded(app.out, 2)) == 2


def test_the_empty_canvas_hint_stays_above_the_notices(shell_app, spin):
    """#192: the notice stack at the bottom right never covers the empty canvas hint."""
    app = shell_app
    hint = "emptyCanvasHintCard"
    spin(until=lambda: _locate(app, hint)["visible"])
    alone = _locate(app, hint)["rect"]
    app.notices.warn("a long warning " * 12, fix="one fix")
    app.notices.info("and an info")
    spin(until=lambda: _locate(app, "noticeStack")["rect"][3] > 60)
    stack = _locate(app, "noticeStack")["rect"]
    spin(until=lambda: _locate(app, hint)["rect"][1] < alone[1])
    _x, y, _w, h = _locate(app, hint)["rect"]
    assert y + h <= stack[1]  # wholly above the stack's top
    app.notices.clear()
    spin(until=lambda: _locate(app, hint)["rect"] == alone)
