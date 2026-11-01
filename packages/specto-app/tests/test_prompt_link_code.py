"""Python link code the prompt-bar agent writes (#168, parts 4-5): ``write_link_code`` and
``read_link_code`` over ``.specto/links/<name>.py``. Writing is validated (name, size,
parses, defines the entry) and never approves (ADR 0006): the link shows needs_approval
and the code never runs until the user approved its exact content. The turn's code rows
undo in one step. Seam 1: a scripted agent, the real tools and link runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from specto_app.prompt.context import CanvasContext
from specto_app.prompt.files import ProjectFiles
from specto_app.prompt.link_code import MAX_CODE, LinkCodeStore, entry_problem
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.skills import SkillStore
from specto_app.prompt.turn import TurnTools, run_turn
from specto_core import CallContext, Engine, Project
from specto_core.agent_tools import AgentTools
from specto_core.errors import InvalidArgs, NotFound
from specto_core.jobs import InlineJobManager
from specto_core.links import Selection

CODE = """\
import json, pathlib

def resolve(items, params, root):
    trace = pathlib.Path(root) / ".specto" / "links" / "moment.ran"
    trace.write_text("ran")
    return {"00002": 1500.0}
"""


def gallery_doc() -> dict:
    return {
        "schema_version": 2,
        "project": {"label": "code"},
        "views": {
            "kobi": {"type": "gallery", "label": "Front", "layout": {"position": [0, 0]}},
        },
        "links": {
            "l2": {
                "kind": "intaker",
                "scope": "pinned",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "kobi", "port": "data"},
                "params": {"sources": ["."]},
                "summary": {"sentence": "The front gallery shows every image."},
            }
        },
    }


def python_edits(name: str = "moment") -> dict:
    return {
        "views": {"new1": {"type": "video", "label": "Video", "layout": {"position": [700, 0]}}},
        "links": {
            "new2": {
                "kind": "python",
                "source": {"view": "kobi", "port": "selection"},
                "target": {"view": "new1", "port": "time"},
                "python": {
                    "path": f".specto/links/{name}.py",
                    "entry": "resolve",
                    "resolves_to": "table",
                    "output": "time",
                },
                "summary": {"sentence": "Selecting a front image seeks the video."},
            }
        },
    }


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    return Project.init(tmp_path / "proj", "code")


@pytest.fixture
def engine(project: Project) -> Engine:
    engine = Engine(project=project, jobs=InlineJobManager())
    engine.live.submit(gallery_doc(), origin="user", label="setup")
    return engine


def turn_tools(engine: Engine) -> TurnTools:
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: engine.call(n, a, ctx))
    root = engine.project.root
    return TurnTools(
        agent,
        engine.live,
        skills=SkillStore(root),
        link_code=LinkCodeStore(root, engine=engine),
        files=ProjectFiles(root),
    )


def scripted_turn(tools: TurnTools, steps: list[dict]):
    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(document=tools.live.document(), viewport={"x": 0, "y": 0, "w": 9, "h": 9})
    return asyncio.run(run_turn(session, tools, "seek the video", ctx)), session


def ran(project: Project) -> bool:
    return (project.root / ".specto" / "links" / "moment.ran").exists()


def python_link(engine: Engine) -> str:
    return next(lid for lid, ln in engine.live.data["links"].items() if ln["kind"] == "python")


# -- validation ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, code, why",
    [
        ("geo/map", CODE, "characters"),
        ("a" * 65, CODE, "64"),
        ("", CODE, "name"),
        ("geo", "", "empty"),
        ("geo", "def resolve(items, params, root):\n    return {\n", "does not parse (line"),
        ("geo", "def other(items, params, root):\n    return {}\n", "no top-level function"),
        ("geo", "async def resolve(items, params, root):\n    return {}\n", "async"),
        ("geo", "x = 1\n" * (MAX_CODE // 6 + 1) + CODE, str(MAX_CODE)),
    ],
    ids=["folder", "long-name", "no-name", "empty", "syntax", "no-entry", "async", "size"],
)
def test_bad_link_code_is_refused_with_a_fix_it_and_nothing_is_written(
    project: Project, name: str, code: str, why: str
):
    with pytest.raises(InvalidArgs) as err:
        LinkCodeStore(project.root).write(name, code)
    assert why in str(err.value)
    assert not list((project.root / ".specto" / "links").glob("*.py"))


def test_the_entry_a_link_calls_is_the_one_checked(project: Project):
    store = LinkCodeStore(project.root)
    with pytest.raises(InvalidArgs) as err:
        store.write("geo", CODE, entry="pick")
    assert "'pick'" in str(err.value) and "resolve" in str(err.value)
    change = store.write("geo.py", CODE.replace("\n", "\r\n"))  # a .py suffix, CRLF
    assert change.rel == ".specto/links/geo.py" and "\r" not in change.after
    assert entry_problem(CODE, "resolve") is None


# -- a turn ----------------------------------------------------------------------------------
def test_written_code_never_approves_and_never_runs_until_the_user_approves(
    project: Project, engine: Engine
):
    tools = turn_tools(engine)
    reply, session = scripted_turn(
        tools,
        [
            {"tool": "write_link_code", "args": {"name": "moment", "code": "def resolve(:\n"}},
            {"tool": "write_link_code", "args": {"name": "moment", "code": CODE}},
            {"tool": "submit_canvas", "args": {"edits": python_edits(), "label": "seek video"}},
        ],
    )

    bad, wrote, submitted = session.calls
    assert bad.is_error and "does not parse" in bad.text  # a fix-it, never raised
    assert not wrote.is_error and json.loads(wrote.text)["approved"] is False
    assert "needs_approval" in json.loads(wrote.text)["note"]
    assert not submitted.is_error and reply.status == "changed"
    assert tools.failures == 0  # a code error is no failed submit
    assert [c.note for c in reply.code] == ["wrote link code moment: needs your approval"]
    lid = python_link(engine)
    assert engine.links.status(lid)["state"] == "needs_approval"
    target = engine.live.data["links"][lid]["target"]["view"]
    engine.links.emit("kobi", "selection", Selection(keys=["00002"], last="00002"))
    assert not ran(project) and engine.links.input_of(target, "time") is None

    # reading it back: the code, its hash, not approved, the link waiting on it
    _, read = tools.link_code.run("read_link_code", {"name": "moment"})
    assert read["code"] == CODE and read["approved"] is False
    assert read["links"] == [
        {
            "link": lid,
            "entry": "resolve",
            "state": "needs_approval",
            "message": ".specto/links/moment.py needs your approval before it runs",
        }
    ]

    # the user approves it: it runs; the agent then rewrites it: approval is needed again
    engine.links.approve(lid)
    moment = engine.links.input_of(target, "time")
    assert ran(project) and getattr(moment, "t", moment) == 1500.0
    _, data = tools.link_code.run("read_link_code", {"name": "moment"})
    assert data["approved"] is True and data["links"][0]["state"] == "resolved"
    reply2, _ = scripted_turn(
        tools,
        [{"tool": "write_link_code", "args": {"name": "moment", "code": CODE + "# v2\n"}}],
    )
    assert reply2.code[0].before == CODE
    assert engine.links.status(lid)["state"] == "needs_approval"

    # Undo puts the approved content back, which runs again without asking
    assert tools.undo_code(reply2) is True
    assert reply2.code == [] and reply2.code_undone
    assert (project.root / ".specto/links/moment.py").read_bytes() == CODE.encode()
    assert engine.links.status(lid)["state"] == "resolved"


def test_undo_removes_new_code_and_refuses_after_a_hand_edit(project: Project, engine: Engine):
    tools = turn_tools(engine)
    reply, _ = scripted_turn(
        tools, [{"tool": "write_link_code", "args": {"name": "moment", "code": CODE}}]
    )
    path = project.root / ".specto/links/moment.py"
    path.write_bytes((CODE + "# by hand\n").encode())
    assert tools.undo_code(reply) is False and path.exists()
    path.write_bytes(CODE.encode())
    assert tools.undo_code(reply) is True and not path.exists()


def test_reading_missing_code_lists_the_files_there_are(project: Project):
    store = LinkCodeStore(project.root)
    store.write("geo", CODE)
    with pytest.raises(NotFound) as err:
        store.read("moment")
    assert "geo" in err.value.fix


def test_the_link_code_and_file_tools_join_the_in_process_server(engine: Engine):
    from specto_app.prompt.session import sdk_tools
    from specto_core.agent_tools import AGENT_TOOL_NAMES

    names = [t.name for t in sdk_tools(turn_tools(engine))]
    assert names == [
        *AGENT_TOOL_NAMES,
        "save_skill",
        "delete_skill",
        "list_files",
        "write_link_code",
        "read_link_code",
    ]


def test_a_per_pick_link_takes_the_value_first(project: Project, engine: Engine):
    three = "def resolve(items, params, root):\n    return {}\n"
    assert "the link calls resolve(value, items, params, root)" in entry_problem(
        three, "resolve", "value"
    )
    assert entry_problem(three, "resolve", "table") is None
    assert entry_problem(three, "resolve") is None  # no link on it yet: either form
    four = "def resolve(value, items, params, root=None):\n    return None\n"
    assert entry_problem(four, "resolve", "value") is None
    assert entry_problem(four, "resolve", "table") is None  # root has a default
    assert "takes 1 arguments" in entry_problem("def resolve(v):\n    pass\n", "resolve")
    # a link on the file names the form the code must have
    tools = turn_tools(engine)
    tools.link_code.write("moment", CODE)
    engine.live.submit(edits=python_edits(), origin="agent:prompt", label="link")
    with pytest.raises(InvalidArgs) as err:
        tools.link_code.write("moment", "def resolve(value):\n    return {}\n")
    assert "resolve(items, params, root)" in str(err.value)


# -- describe link:python and the system prompt (part 5) --------------------------------------
def test_describe_link_python_says_how_to_write_per_pick_code(engine: Engine):
    from specto_core.python_link_guide import EXAMPLE_CODE

    guide = engine.call("describe", {"target": "link:python"})["info"]["guide"]
    assert "entry(value, items, params, root)" in guide["per_pick"]
    assert set(guide["value"]) >= {"files", "selection", "time"}
    assert '"paths"' in guide["value"]["files"] and '"keys"' in guide["value"]["selection"]
    assert set(guide["returns"]) >= {"files", "selection", "content", "time", "null"}
    assert "approve" in guide["code"] and "write_link_code" in guide["code"]
    example = guide["example"]
    assert example["write_link_code"]["code"] == EXAMPLE_CODE
    links = example["submit_canvas"]["edits"]["links"]
    python = next(ln for ln in links.values() if ln["kind"] == "python")
    assert python["source"]["port"] == "selected_files"
    assert python["target"]["port"] == "selection"
    assert python["python"]["resolves_to"] == "value"
    assert python["python"]["path"] == ".specto/links/clip_to_geo.py"
    assert any(ln["kind"] == "read_contents" for ln in links.values())
    json.dumps(guide)  # JSON all through: the agent reads it as the tool result


def test_the_worked_example_maps_a_recording_to_its_annotation_folder():
    from specto_core.python_link_guide import EXAMPLE_CODE

    assert entry_problem(EXAMPLE_CODE, "resolve", "value") is None
    space: dict = {}
    exec(EXAMPLE_CODE, space)
    value = {
        "root": "C:/depot",
        "paths": [
            "recordings/s01/clip1/world.usda",
            "recordings/s01/clip1/props/tree.usda",
            "notes/a.md",
        ],
    }
    assert space["resolve"](value, [], {}, "C:/depot") == ["annotations/s01/clip1/geo"]
    assert space["resolve"]({"root": "C:/depot", "paths": None}, [], {}, "C:/depot") == []


def test_the_session_prompt_says_unexpressible_links_are_python_the_user_approves():
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert "per-pick Python link that you write and the user approves" in SYSTEM_PROMPT
    assert "write_link_code" in SYSTEM_PROMPT and "read_link_code" in SYSTEM_PROMPT
    assert "Writing never approves" in SYSTEM_PROMPT
    assert "annotations/<session>/<clip>/geo" in SYSTEM_PROMPT
    assert "describe link:python" in SYSTEM_PROMPT


def test_the_agent_writes_the_worked_example_and_it_runs_once_approved(project: Project):
    """The acceptance at seam 1, as the agent does it: write the example's code, submit
    its edits; nothing runs until the user approves; then a picked recording selects its
    annotation folder in tree B."""
    engine = Engine(project=project, jobs=InlineJobManager())  # the new canvas: its tree
    from specto_core.links import FilesValue
    from specto_core.python_link_guide import EXAMPLE_CODE, EXAMPLE_NAME, example_edits

    world = "recordings/s01/clip1/world.usda"
    geo = "annotations/s01/clip1/geo"
    for rel in (world, f"{geo}/track.geojson"):
        (project.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (project.root / rel).write_bytes(b"{}")
    tree = next(v for v, d in engine.live.data["views"].items() if d["type"] == "file_tree")
    tools = turn_tools(engine)
    edits = example_edits(tree)
    _, session = scripted_turn(
        tools,
        [
            {"tool": "write_link_code", "args": {"name": EXAMPLE_NAME, "code": EXAMPLE_CODE}},
            {"tool": "submit_canvas", "args": {"edits": edits, "label": "mirror"}},
        ],
    )
    assert not any(c.is_error for c in session.calls), [c.text for c in session.calls]
    renamed = json.loads(session.calls[1].text)["renamed"]
    tree_b, lid = renamed["new1"], renamed["new4"]
    assert engine.links.status(lid)["state"] == "needs_approval"
    pick = FilesValue(root=str(project.root), paths=[world])
    engine.links.emit(tree, "selected_files", pick)
    assert engine.links.input_of(tree_b, "selection") is None

    engine.links.approve(lid)
    engine.links.emit(tree, "selected_files", pick)
    engine.links.poll()
    assert engine.links.input_of(tree_b, "selection").keys == [geo]


def test_the_guide_s_fix_it_shapes_are_the_runtime_s():
    from specto_core.python_link_guide import OUTPUT_SHAPES
    from specto_core.python_links import SHAPES

    assert OUTPUT_SHAPES is SHAPES


def test_describe_link_python_documents_the_collection_output_and_the_trace(engine: Engine):
    guide = engine.call("describe", {"target": "link:python"})["info"]["guide"]
    collection = guide["returns"]["collection"]
    assert '"parts"' in collection and "intaker" in collection and "unique" in collection
    assert "trace" in guide["status"] and "traceback" in guide["status"]
    example = guide["collection_example"]
    assert example["link"]["python"]["output"] == "collection"
    assert example["link"]["target"]["port"] == "data"
    json.dumps(guide)


def test_the_collection_example_builds_a_gallery_of_frames_once_approved(project: Project):
    """The guide's second example as the agent writes it: one item per frame number,
    the picture and its sidecar as the parts, delivered to a gallery's data."""
    from specto_core.links import FilesValue
    from specto_core.python_link_guide import (
        COLLECTION_EXAMPLE_CODE,
        COLLECTION_EXAMPLE_NAME,
        python_link_guide,
    )

    engine = Engine(project=project, jobs=InlineJobManager())
    files = []
    for i in range(2):
        for ext in ("jpg", "json"):
            rel = f"runs/cam-a/{i:05d}_1700000000000000000.{ext}"
            (project.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (project.root / rel).write_bytes(b"x")
            files.append(rel)
    tree = next(v for v, d in engine.live.data["views"].items() if d["type"] == "file_tree")
    link = python_link_guide()["collection_example"]["link"]
    link = {**link, "source": {**link["source"], "view": tree}}
    link["target"] = {"view": "new1", "port": "data"}
    tools = turn_tools(engine)
    edits = {
        "views": {"new1": {"type": "gallery", "label": "frames", "layout": {"position": [400, 0]}}},
        "links": {"new2": link},
    }
    _, session = scripted_turn(
        tools,
        [
            {
                "tool": "write_link_code",
                "args": {"name": COLLECTION_EXAMPLE_NAME, "code": COLLECTION_EXAMPLE_CODE},
            },
            {"tool": "submit_canvas", "args": {"edits": edits, "label": "frames"}},
        ],
    )
    assert not any(c.is_error for c in session.calls), [c.text for c in session.calls]
    renamed = json.loads(session.calls[1].text)["renamed"]
    gallery, lid = renamed["new1"], renamed["new2"]
    engine.links.emit(tree, "selected_files", FilesValue(root=str(project.root), paths=files))
    assert engine.links.input_of(gallery, "data") is None  # not approved: nothing ran

    engine.links.approve(lid)
    col = engine.links.input_of(gallery, "data")
    assert [it.key for it in col.items] == ["00000", "00001"]
    assert sorted(col.items[1].parts) == [".jpg", ".json"]
    assert col.items[1].meta["keys"] == {"frame": "00001"}
