"""Recents and the welcome controller (the human way into a project), offscreen."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app import recents
from specto_app.launcher import (
    WelcomeController,
    open_project,
    relaunch,
    relaunch_argv,
    resolve_folder,
)


def test_recents_remember_forget_and_missing(tmp_path):
    assert Path(os.environ["SPECTO_USER_DIR"]) == recents.user_data_dir()
    a = tmp_path / "a"
    b = tmp_path / "b"
    from specto_core import Project

    Project.init(a, "alpha")
    recents.remember(a, "alpha")
    recents.remember(b, "beta")  # not a project (yet): kept, flagged missing
    got = recents.load_recents()
    assert [e["name"] for e in got][:2] == ["beta", "alpha"]
    assert got[0]["missing"] and not got[1]["missing"]
    recents.remember(a, "alpha")  # moves to the top, no duplicate
    got = recents.load_recents()
    assert got[0]["name"] == "alpha" and [e["root"] for e in got].count(str(a.resolve())) == 1
    recents.forget(b)
    assert all(e["name"] != "beta" for e in recents.load_recents())


def test_welcome_controller_opens_or_creates(tmp_path, qapp):
    w = WelcomeController()
    plain = tmp_path / "plain"
    plain.mkdir()
    chosen: list[str] = []
    w.projectChosen.connect(chosen.append)
    # #217: a plain folder is refused without create (a recent-list row that lost its
    # .specto/) with a fix-it naming the one button; "Open folder..." and the path field
    # pass create, and the folder becomes a project named after itself
    assert not w.openPath(str(plain), False)
    assert "not a Specto project" in w.error and "Open folder..." in w.error
    assert w.openPath(str(plain), True)
    assert w.error == "" and chosen == [str(plain.resolve())]
    assert recents.is_project(plain)
    from specto_core import Project

    assert Project.open(plain).name == "plain"
    # the same call on a project folder opens it (no second init, the name stays)
    assert w.openPath(str(plain), True) and Project.open(plain).name == "plain"
    assert w.recents[0]["root"] == str(plain.resolve())
    # a file:// url (the native folder dialog) and an empty field
    assert w.openPath(Path(plain).resolve().as_uri(), False)
    assert not w.openPath("   ", True) and "folder" in w.error
    w.forget(str(plain))
    assert w.recents == [] or all(e["root"] != str(plain.resolve()) for e in w.recents)


def test_open_project_and_relaunch_argv(tmp_path):
    p = open_project(tmp_path / "new one", create=True)
    assert p.root.name == "new one" and recents.is_project(p.root)
    with pytest.raises(Exception, match="not a Specto project"):
        open_project(tmp_path / "nope", create=False)
    argv = relaunch_argv(p.root, ["--inline-jobs", "--no-web"])
    assert argv[1:3] == ["-m", "specto_app"] and argv[3:5] == ["--project", str(p.root)]
    assert argv[-2:] == ["--inline-jobs", "--no-web"]
    assert resolve_folder("") is None and resolve_folder('"C:/x"') == Path("C:/x")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process creation flags")
def test_relaunch_starts_the_app_with_a_hidden_console(tmp_path, monkeypatch):
    """SPEC-28: a relaunched app gets a hidden console (``CREATE_NO_WINDOW``), never
    none (``DETACHED_PROCESS``): under a console-less app every console child spawned
    without flags (the agent CLI, the workers) opens a visible console window."""
    import subprocess

    from specto_core import detached

    calls: list[dict] = []

    class FakePopen:
        pid = 4242
        _handle = None
        returncode = None

        def __init__(self, argv, **kw):
            calls.append({"argv": argv, **kw})

    monkeypatch.setattr(detached.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("SPECTO_PROJECT", str(tmp_path / "elsewhere"))
    assert relaunch(tmp_path, ["--no-web"]) == 4242
    (call,) = calls
    flags = call["creationflags"]
    assert flags & subprocess.CREATE_NO_WINDOW and flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not flags & subprocess.DETACHED_PROCESS
    assert call["argv"] == relaunch_argv(tmp_path, ["--no-web"])
    assert "SPECTO_PROJECT" not in call["env"]  # the child serves the project it is given


def test_a_project_opened_from_the_welcome_answers_its_prompt_bar(tmp_path, qapp, spin):
    """Spec #35 stories 1 and 9: the app the welcome card starts runs the prompt bar's
    agent on the loop the welcome owns (the session must not land on a loop nobody
    runs)."""
    import asyncio

    from specto_app.launcher import start_app
    from specto_app.prompt.scripted import ScriptedAgentSession

    proj = open_project(tmp_path / "proj", create=True)
    (proj.root / "runs").mkdir()
    (proj.root / "runs" / "00001_1.jpg").write_bytes(b"x")
    apps: list = []

    def factory(tools):
        doc = apps[0].live.document()
        doc["views"]["kobi"] = {
            "type": "gallery",
            "label": "Front",
            "layout": {"position": [320, 0], "size": [640, 400]},
        }
        pin(doc, "kobi")
        return ScriptedAgentSession(
            tools,
            [
                [
                    {"tool": "submit_canvas", "args": {"document": doc, "label": "front gallery"}},
                    {"text": "Added a front gallery."},
                ]
            ],
        )

    loop = asyncio.new_event_loop()
    opts = {"jobs": "inline", "web": False, "offscreen": True, "serve": False, "agent": factory}
    app = start_app(proj.root, opts, loop)
    apps.append(app)
    try:
        bar = app.prompt_bar
        assert bar.submit("show the front frames") is True
        deadline = 200
        while bar.busy and deadline:
            loop.run_until_complete(asyncio.sleep(0.01))
            qapp.processEvents()
            deadline -= 1
        assert not bar.busy, "the turn never ran: the agent is on a loop nobody runs"
        assert "kobi" in app.live.data["views"] and bar.status == "changed"
    finally:
        app.close()
        loop.close()


def test_the_welcome_card_shows_the_s_character(qapp, spin):
    """#170: the brand next to "Specto" is the S character on the agent's tile."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from specto_app.app import find_item
    from specto_app.launcher import WELCOME_QML
    from specto_app.qt_setup import install_qml

    engine = QQmlApplicationEngine()
    theme = install_qml(engine)
    welcome = WelcomeController()
    engine.rootContext().setContextProperty("welcome", welcome)
    engine.load(QUrl.fromLocalFile(str(WELCOME_QML)))
    window = engine.rootObjects()[0]
    try:
        spin(60)
        mark = find_item(window.contentItem(), "welcomeMark")
        assert mark is not None and mark.isVisible()
        assert find_item(mark, "sBody") is not None
        # #217: one "Open folder..." (a project opens, a plain folder becomes one); no
        # separate "New project..."
        open_btn = find_item(window.contentItem(), "openFolder")
        assert open_btn is not None and open_btn.isVisible()
        assert open_btn.property("text") == "Open folder..."
        assert find_item(window.contentItem(), "newProject") is None
    finally:
        window.close()
        engine.deleteLater()
        spin(20)
    del theme
