"""The agent's scoped data files (#219): ``data_write`` / ``data_read`` / ``data_list``
/ ``data_delete`` over ``.specto/<folder>/<file>`` only (json, md, txt, csv), never
Specto's own folders, never outside ``.specto/``; a .json must parse; a turn's writes
and deletes undo as one step."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from specto_app.prompt.data import MAX_BYTES, DataStore
from specto_core import Project
from specto_core.errors import InvalidArgs, NotFound


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return Project.init(tmp_path / "proj", "proj").root


def test_write_read_list_and_delete_round_trip_under_specto(root: Path):
    store = DataStore(root)
    layout = {"views": {"kobi": {"type": "gallery"}}, "links": {}}

    change = store.write("layouts/review.json", json.dumps(layout))

    path = root / ".specto" / "layouts" / "review.json"
    assert change.path == path and change.before is None and change.rel == "layouts/review.json"
    assert change.qualified == ".specto/layouts/review.json" and change.action == "wrote"
    assert json.loads(path.read_text(encoding="utf-8")) == layout
    read = store.read(".specto/layouts/review.json")  # the .specto/ prefix is tolerated
    assert json.loads(read["text"]) == layout and read["path"] == ".specto/layouts/review.json"
    assert read["bytes"] == path.stat().st_size and read["modified"]
    assert store.list() == {"folders": ["layouts"]}  # links/, scratch/ never listed
    listed = store.list("layouts")
    assert listed["folder"] == ".specto/layouts" and listed["count"] == 1
    assert listed["files"][0]["path"] == ".specto/layouts/review.json"
    gone = store.delete("layouts/review.json")
    assert gone.before == change.after and gone.after is None and gone.action == "deleted"
    assert not path.exists() and store.list("layouts")["files"] == []
    with pytest.raises(NotFound):
        store.read("layouts/review.json")


@pytest.mark.parametrize(
    "path, why",
    [
        ("", "path"),
        ("review.json", "folder"),
        ("../review.json", "folder"),
        ("layouts/../../x.json", "leaves"),
        ("layouts/./x.json", "leaves"),
        ("/tmp/x.json", "not under"),
        ("C:/tmp/x.json", "not under"),
        ("skills/x/SKILL.md", "Specto's own"),
        ("commands/x.md", "Specto's own"),
        ("links/x.py", "not a data file"),
        ("scratch/x.md", "Specto's own"),
        (".claude-plugin/plugin.json", "plain name"),
        ("layouts/x.py", "not a data file"),
        ("layouts/x", "not a data file"),
        ("layouts/.hidden.json", "plain name"),
        ("lay outs/x.json", "plain name"),
        ("a/b/c/d/x.json", "deeper"),
    ],
)
def test_a_path_outside_the_scope_is_refused_and_nothing_is_written(
    root: Path, path: str, why: str
):
    store = DataStore(root)
    with pytest.raises(InvalidArgs) as err:
        store.write(path, "x")
    assert why in str(err.value)
    with pytest.raises(InvalidArgs):
        store.read(path)
    assert not (root / ".specto" / "layouts").exists()
    assert not (root / ".specto" / "skills").exists()


def test_a_json_file_must_parse_and_an_empty_or_huge_text_is_refused(root: Path):
    store = DataStore(root)
    with pytest.raises(InvalidArgs) as err:
        store.write("layouts/review.json", "{not json")
    assert "not JSON" in str(err.value)
    with pytest.raises(InvalidArgs):
        store.write("layouts/review.json", "   ")
    with pytest.raises(InvalidArgs) as err:
        store.write("notes/big.txt", "x" * (MAX_BYTES + 1))
    assert str(MAX_BYTES) in str(err.value)
    assert not (root / ".specto" / "layouts").exists()
    store.write("notes/a.txt", "not json, fine\n")  # only .json is parsed


def test_a_folder_junctioned_out_of_specto_is_refused(root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / ".specto" / "layouts"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    with pytest.raises(InvalidArgs) as err:
        DataStore(root).write("layouts/review.json", "{}")
    assert "outside" in str(err.value)
    assert list(outside.iterdir()) == []


def test_list_folders_skips_specto_s_own_and_dot_folders(root: Path):
    specto = root / ".specto"
    for name in ("skills", "commands", "scratch", ".claude-plugin", "layouts", "notes"):
        (specto / name).mkdir(parents=True, exist_ok=True)
    (specto / "layouts" / "review.json").write_text("{}", encoding="utf-8")
    (specto / "layouts" / "stray.py").write_text("x", encoding="utf-8")
    store = DataStore(root)
    assert store.list()["folders"] == ["layouts", "notes"]
    assert [f["path"] for f in store.list("layouts")["files"]] == [".specto/layouts/review.json"]
    with pytest.raises(InvalidArgs):
        store.list("skills")


def test_undo_restores_the_previous_file_or_removes_the_new_one_in_one_step(root: Path):
    store = DataStore(root)
    turn1 = [store.write("layouts/review.json", '{"v": 1}')]
    turn2 = [
        store.write("layouts/review.json", '{"v": 2}'),
        store.write("notes/a.md", "# a\n"),
        store.delete("notes/a.md"),
    ]

    assert store.undo(turn2) is True
    assert store.path("layouts/review.json").read_text(encoding="utf-8") == '{"v": 1}'
    assert not store.path("notes/a.md").exists()
    assert store.undo(turn1) is True
    assert not store.path("layouts/review.json").exists()


def test_undo_refuses_when_a_file_changed_since_and_changes_nothing(root: Path):
    store = DataStore(root)
    turn = [store.write("layouts/a.json", "1"), store.write("layouts/b.json", "2")]
    store.path("layouts/b.json").write_text("3", encoding="utf-8")
    assert store.undo(turn) is False
    assert store.path("layouts/a.json").read_text(encoding="utf-8") == "1"
    assert store.path("layouts/b.json").read_text(encoding="utf-8") == "3"


# -- the tools in a turn (seam 1: a scripted agent, the real tools) ---------------------------
def turn_tools(root: Path):
    from specto_app.prompt.turn import TurnTools
    from specto_core import CallContext, Engine
    from specto_core.agent_tools import AgentTools

    engine = Engine(project=None)
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: engine.call(n, a, ctx))
    return TurnTools(agent, engine.live, data=DataStore(root))


def test_a_turn_s_writes_and_deletes_are_reported_and_undo_together(root: Path):
    import asyncio

    from specto_app.prompt.context import CanvasContext
    from specto_app.prompt.scripted import ScriptedAgentSession
    from specto_app.prompt.turn import run_turn

    tools = turn_tools(root)
    steps = [
        {"tool": "data_write", "args": {"path": "layouts/review.json", "text": "{bad"}},
        {"tool": "data_write", "args": {"path": "layouts/review.json", "text": '{"ok": 1}'}},
        {"tool": "data_list", "args": {"folder": "layouts"}},
        {"tool": "data_read", "args": {"path": "layouts/review.json"}},
        {"tool": "data_write", "args": {"path": "notes/x.txt", "text": "x\n"}},
        {"tool": "data_delete", "args": {"path": "notes/x.txt"}},
        {"text": "Saved the review layout."},
    ]
    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(document=tools.live.document(), viewport={"x": 0, "y": 0, "w": 9, "h": 9})
    reply = asyncio.run(run_turn(session, tools, "remember this layout as review", ctx))

    bad, ok, listed, read, _wrote, deleted = session.calls
    assert bad.is_error and "not JSON" in bad.text
    assert json.loads(ok.text) == {
        "wrote": ".specto/layouts/review.json",
        "replaced": False,
        "bytes": 9,
    }
    assert json.loads(listed.text)["count"] == 1
    assert json.loads(read.text)["text"] == '{"ok": 1}'
    assert json.loads(deleted.text) == {"deleted": ".specto/notes/x.txt"}
    assert [c.note for c in reply.data_files] == [
        "wrote .specto/layouts/review.json",
        "wrote .specto/notes/x.txt",
        "deleted .specto/notes/x.txt",
    ]
    assert reply.status == "answered" and reply.turn_txn is None and tools.failures == 0
    assert tools.undo_data(reply) is True
    assert reply.data_files == [] and len(reply.data_files_undone) == 3
    assert not (root / ".specto" / "layouts" / "review.json").exists()
    assert not (root / ".specto" / "notes" / "x.txt").exists()


def test_the_data_tools_join_the_in_process_server_only_with_a_store(root: Path):
    from specto_app.prompt.session import sdk_tools
    from specto_core.agent_tools import AGENT_TOOL_NAMES

    sdk = {t.name: t for t in sdk_tools(turn_tools(root))}
    assert list(sdk) == [*AGENT_TOOL_NAMES, "data_write", "data_read", "data_list", "data_delete"]
    assert sdk["data_read"].annotations.read_only_hint is True
    assert sdk["data_delete"].annotations.destructive_hint is True
