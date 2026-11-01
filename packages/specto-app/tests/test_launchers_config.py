"""Seam 1 (#186): launcher configuration, headless and Qt-free. The machine's and the
project's TOML files parse, validate (a problem names its file and line, with a
fix-it; the other launchers still load) and merge by id; placeholders expand into an
argv list (spaces, & and % stay inside their argument); match rules pick the launchers
of a selection and its default; the system default is refused for programs and
scripts; the registry's dynamic labels and submenus carry the "Open with" rows."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from specto_app.launchers.book import LauncherBook, file_of
from specto_app.launchers.config import (
    TOML_FIX,
    batch_problem,
    load,
    placeholder_problem,
    refused_reason,
)
from specto_app.menu import ContextMenuRegistry, MenuCall, MenuEntry, MenuItemRef, MenuTarget
from specto_core.errors import InvalidArgs, NotFound

BLENDER = """\
[[launcher]]
id = "blender-4.2"
label = "Blender 4.2 LTS"
command = "D:/Tools/blender-4.2.3/blender.exe"
args = ["{path}"]
match = ["*.blend", "*.blend1"]
each = true
cwd = "{dir}"
env = { BLENDER_USER_CONFIG = "D:/Tools/blender-4.2.3/config" }
default = true

[[launcher]]
id = "blender-3.6"
label = "Blender 3.6"
command = "D:/Tools/blender-3.6/blender.exe"
match = ["*.blend"]
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    """The machine's file and the project's (neither written yet)."""
    return tmp_path / "user" / "launchers.toml", tmp_path / "proj" / ".specto" / "launchers.toml"


# -- parse, validate, merge ---------------------------------------------------------------------
def test_the_issue_s_example_parses_into_a_launcher(files):
    machine, _ = files
    launchers, problems = load(write(machine, BLENDER), None)
    assert problems == []
    b42, b36 = launchers
    assert (b42.id, b42.label, b42.command) == (
        "blender-4.2",
        "Blender 4.2 LTS",
        "D:/Tools/blender-4.2.3/blender.exe",
    )
    assert b42.args == ("{path}",) and b42.match == ("*.blend", "*.blend1")
    assert b42.each is True and b42.cwd == "{dir}" and b42.default is True
    assert b42.env == (("BLENDER_USER_CONFIG", "D:/Tools/blender-4.2.3/config"),)
    # the defaults: label = id ... args ["{path}"], not a default
    assert b36.args == ("{path}",) and b36.default is False and b36.source == "machine"


def test_no_files_no_launchers_no_problems(files):
    assert load(*files) == ([], [])


def test_a_backslash_path_in_double_quotes_names_its_line_and_the_fix(files):
    machine, _ = files
    write(machine, '[[launcher]]\nid = "b"\ncommand = "D:\\Tools\\blender.exe"\n')
    launchers, problems = load(machine, None)
    assert launchers == []
    (p,) = problems
    assert p.file == str(machine) and p.line == 3
    assert p.fix == TOML_FIX and "single quotes" in p.fix
    assert p.text().startswith(f"{machine}:3: not valid TOML")


def test_a_bad_launcher_is_left_out_with_its_line_and_the_rest_load(files):
    machine, _ = files
    write(
        machine,
        BLENDER
        + '\n[[launcher]]\nid = "viewer"\ncommand = "v.exe"\nmatchs = ["*.jpg"]\n'
        + '\n[[launcher]]\nid = "bad id!"\ncommand = "x.exe"\n',
    )
    launchers, problems = load(machine, None)
    assert [lc.id for lc in launchers] == ["blender-4.2", "blender-3.6"]
    typo, bad_id = problems
    assert typo.line == 21 and "unknown key 'matchs'" in typo.message
    assert "match" in typo.fix  # the fix lists the keys
    assert bad_id.line == 24 and "not a plain name" in bad_id.message


@pytest.mark.parametrize(
    ("table", "words"),
    [
        ('id = "x"\nmatch = ["*.a"]', "has no command"),
        ('id = "x"\ncommand = "x.exe"\neach = false\nargs = ["{path}"]', "no {paths} argument"),
        ('id = "x"\ncommand = "x.exe"\nargs = ["--file={paths}"]', "whole argument"),
        ('id = "x"\ncommand = "x.exe"\nargs = ["{file}"]', "unknown placeholder {file}"),
        ('id = "x"\ncommand = "x.exe"\nargs = ["{path"]', "stray '{'"),
        ('id = "x"\ncommand = "x.exe"\ncwd = "{paths}"', "{paths} must be a whole argument"),
        ('id = "x"\ncommand = "x.exe"\neach = "yes"', "each must be true or false"),
        ('id = "x"\ncommand = "x.exe"\nmatch = []', "match is empty"),
        ('id = "x"\ncommand = "x.exe"\nenv = { A = 1 }', "env must be a table of strings"),
    ],
)
def test_validation_says_what_is_wrong_with_a_fix(files, table, words):
    machine, _ = files
    launchers, problems = load(write(machine, f"[[launcher]]\n{table}\n"), None)
    assert launchers == []
    assert len(problems) == 1 and words in problems[0].message and problems[0].fix
    assert problems[0].line >= 1


def test_the_project_merges_over_the_machine_by_id_key_by_key(files):
    machine, project = files
    write(machine, BLENDER)
    write(
        project,
        '[[launcher]]\nid = "blender-3.6"\ndefault = true\n\n'
        '[[launcher]]\nid = "blender-4.2"\nlabel = "Blender 4.2 (project)"\n\n'
        '[[launcher]]\nid = "texed"\ncommand = "notepad.exe"\nmatch = ["*.txt"]\n',
    )
    launchers, problems = load(machine, project)
    assert problems == []
    by = {lc.id: lc for lc in launchers}
    assert list(by) == ["blender-4.2", "blender-3.6", "texed"]  # machine order, then new
    assert by["blender-4.2"].label == "Blender 4.2 (project)"
    assert by["blender-4.2"].command == "D:/Tools/blender-4.2.3/blender.exe"  # kept
    assert by["blender-4.2"].source == "machine+project" and by["texed"].source == "project"
    # both are defaults now: the project's choice ranks over the machine's
    assert by["blender-3.6"].default_rank == 2 and by["blender-4.2"].default_rank == 1
    book = LauncherBook(None, machine_path=machine)
    book.project_path = project
    book.refresh(force=True)
    assert book.default_for([("C:/x/scene.blend", False)]).id == "blender-3.6"


def test_a_project_override_of_an_unknown_id_says_so(files):
    machine, project = files
    write(project, '[[launcher]]\nid = "blender-9"\ndefault = true\n')
    _, (problem,) = load(machine, project)
    assert problem.file == str(project) and problem.line == 1
    assert "no command" in problem.message and "machine" in problem.message


def test_a_second_launcher_with_the_same_id_is_reported(files):
    machine, _ = files
    write(machine, BLENDER + '\n[[launcher]]\nid = "blender-3.6"\ncommand = "y.exe"\n')
    launchers, (problem,) = load(machine, None)
    assert [lc.id for lc in launchers] == ["blender-4.2", "blender-3.6"]
    assert problem.line == 18 and "second launcher" in problem.message
    assert launchers[1].command == "D:/Tools/blender-3.6/blender.exe"  # the first one


# -- placeholders ----------------------------------------------------------------------------
def test_placeholders_expand_into_one_argument_each_whatever_the_path_holds(files, tmp_path):
    machine, _ = files
    write(
        machine,
        '[[launcher]]\nid = "x"\ncommand = "x.exe"\n'
        'args = ["--open", "{path}", "--name={name}", "{stem}", "{{literal}}", "{project}"]\n'
        'cwd = "{dir}"\nenv = { WHERE = "{dir}/cfg" }\n',
    )
    (lc,), _ = load(machine, None)
    path = os.path.join(str(tmp_path), "my scenes", "a & b %PATH% (1).blend")
    (inv,) = lc.invocations([path], project=str(tmp_path))
    folder = os.path.join(str(tmp_path), "my scenes")
    assert inv.argv == (
        "x.exe",
        "--open",
        path,
        "--name=a & b %PATH% (1).blend",
        "a & b %PATH% (1)",
        "{literal}",
        str(tmp_path),
    )
    assert inv.cwd == folder and inv.env == (("WHERE", folder + "/cfg"),)


def test_each_false_puts_every_path_in_one_process_and_each_true_one_per_file(files):
    machine, _ = files
    write(
        machine,
        '[[launcher]]\nid = "all"\ncommand = "v.exe"\neach = false\nargs = ["-a", "{paths}"]\n'
        '[[launcher]]\nid = "one"\ncommand = "v.exe"\n',
    )
    (together, apart), _ = load(machine, None)
    paths = [os.path.join("C:\\", "p", n) for n in ("1.jpg", "2 two.jpg", "3.jpg")]
    (inv,) = together.invocations(paths)
    assert inv.argv == ("v.exe", "-a", *paths)
    assert [i.argv for i in apart.invocations(paths)] == [("v.exe", p) for p in paths]


def test_placeholder_problem_allows_paths_only_as_a_whole_argument():
    assert placeholder_problem("{paths}", whole_paths=True) is None
    assert placeholder_problem(" {paths} ", whole_paths=True) is None
    assert "whole argument" in placeholder_problem("{paths}")
    assert placeholder_problem("{{x}} {path}") is None
    assert "stray '}'" in placeholder_problem("a}b")


# -- match rules ------------------------------------------------------------------------------
def test_match_is_by_file_name_case_insensitive_and_folders_need_a_slash(files):
    machine, _ = files
    write(
        machine,
        '[[launcher]]\nid = "b"\ncommand = "b.exe"\nmatch = ["*.blend"]\n'
        '[[launcher]]\nid = "any"\ncommand = "a.exe"\n'
        '[[launcher]]\nid = "code"\ncommand = "code.exe"\nmatch = ["*/", "*.py"]\n',
    )
    blend, anyfile, code = load(machine, None)[0]
    assert blend.matches("D:/p/Scene.BLEND") and not blend.matches("D:/p/scene.blend1")
    assert anyfile.matches("D:/p/x.y") and not anyfile.matches("D:/p/folder", is_dir=True)
    assert code.matches("D:/p/folder", is_dir=True) and code.matches("D:/p/a.py")
    assert not code.matches("D:/p/x.py.folder", is_dir=False)
    # a selection: the launchers matching every item
    assert blend.matches_all([("a.blend", False), ("b.blend", False)])
    assert not blend.matches_all([("a.blend", False), ("b.png", False)])
    assert not blend.matches_all([])


def test_a_gallery_item_inside_a_container_opens_its_container(tmp_path):
    z = tmp_path / "frames.zip"
    z.write_bytes(b"PK")
    assert file_of(f"{z}#inner/1.jpg") == str(z)
    odd = tmp_path / "a#b.blend"
    odd.write_bytes(b"x")
    assert file_of(str(odd)) == str(odd)  # a real file with '#' in its name stays


# -- refused types ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name", ["setup.exe", "RUN.BAT", "x.cmd", "a.ps1", "s.py", "w.pyw", "v.vbs", "l.lnk",
             "m.msi", "j.js", "h.hta", "r.reg", "u.url", "s.scr"],
)  # fmt: skip
def test_the_system_default_is_refused_for_programs_and_scripts(name, tmp_path):
    assert "would run it" in refused_reason(name)
    f = tmp_path / name
    f.write_bytes(b"x")
    opened: list[str] = []
    book = LauncherBook(tmp_path, machine_path=tmp_path / "none.toml", start=opened.append)
    with pytest.raises(InvalidArgs) as e:
        book.open_default([name])
    assert "refused" in e.value.message and e.value.fix and opened == []


@pytest.mark.parametrize("name", ["scene.blend", "notes.md", "a.jpg", "data.json", "x.txt"])
def test_data_files_go_to_the_system_default(name, tmp_path):
    assert refused_reason(name) is None
    (tmp_path / name).write_bytes(b"x")
    opened: list[str] = []
    book = LauncherBook(tmp_path, machine_path=tmp_path / "none.toml", start=opened.append)
    assert book.open_default([name]).note == f"launched System default on {name}"
    assert opened == [str(tmp_path / name)]


def test_a_batch_command_refuses_arguments_cmd_would_act_on():
    assert batch_problem("C:/t/run.bat", ["C:/p/a b.blend"]) is None
    assert "cmd.exe" in batch_problem("C:/t/run.CMD", ["C:/p/a&calc.blend"])
    assert "%" in batch_problem("C:/t/run.bat", ["%PATH%"])
    assert batch_problem("C:/t/blender.exe", ["a&b"]) is None  # no cmd.exe in between


# -- the book: reload on edit, launching ---------------------------------------------------------
def test_the_book_reloads_when_a_file_changes_and_tells_its_listeners(files):
    machine, _ = files
    book = LauncherBook(None, machine_path=machine)
    heard: list[int] = []
    book.listeners.append(lambda b: heard.append(len(b.launchers)))
    assert book.all() == [] and heard == []
    write(machine, BLENDER)
    assert [lc.id for lc in book.all()] == ["blender-4.2", "blender-3.6"]
    assert heard == [2]
    assert book.refresh() is False and heard == [2]  # unchanged: no reload
    write(machine, BLENDER.replace("id = ", "idd = ", 1))
    os.utime(machine, ns=(1, 1))  # a save in the same mtime tick still differs by size
    assert [lc.id for lc in book.all()] == ["blender-3.6"]
    assert heard == [2, 1] and "unknown key 'idd'" in book.problems[0].message


def test_a_missing_command_is_named_with_its_path_and_a_fix(files, tmp_path):
    machine, _ = files
    missing = tmp_path / "Tools" / "blender.exe"
    write(machine, f'[[launcher]]\nid = "b"\ncommand = "{missing.as_posix()}"\n')
    (tmp_path / "s.blend").write_bytes(b"x")
    spawned: list = []
    book = LauncherBook(tmp_path, machine_path=machine, spawn=lambda *a: spawned.append(a))
    with pytest.raises(NotFound) as e:
        book.launch("b", ["s.blend"])
    assert str(missing) in e.value.message.replace("/", os.sep)
    assert f"{machine}:1" in e.value.fix and spawned == []
    with pytest.raises(NotFound) as e:
        book.launch("nope", ["s.blend"])
    assert "b" in e.value.fix and "system" in e.value.fix
    with pytest.raises(NotFound):
        book.launch("system", ["nothing-here.txt"])


def test_a_launcher_is_refused_for_files_it_does_not_match(files, tmp_path):
    machine, _ = files
    write(machine, f'[[launcher]]\nid = "b"\ncommand = "{Path(os.sys.executable).as_posix()}"\n'
                   'match = ["*.blend"]\n')  # fmt: skip
    (tmp_path / "a.png").write_bytes(b"x")
    book = LauncherBook(tmp_path, machine_path=machine, spawn=lambda *a: 1)
    with pytest.raises(InvalidArgs) as e:
        book.launch("b", ["a.png"])
    assert "does not open a.png" in e.value.message


# -- the registry's submenus and dynamic labels ------------------------------------------------
def _target(*paths: str) -> MenuTarget:
    return MenuTarget(
        view_id="fege", view_type="file_tree", role="browser", kind="items",
        items=tuple(MenuItemRef(key=p, path=p) for p in paths),
    )  # fmt: skip


def test_a_submenu_row_carries_its_children_and_a_child_runs_by_its_path():
    ran: list[str] = []

    def kids(t: MenuTarget) -> list[MenuEntry]:
        return [
            MenuEntry(id="a", label=f"A for {len(t.items)}", group="x",
                      handler=lambda c: ran.append("a") or "a"),
            MenuEntry(id="off", label="Off", group="y", enabled=lambda t: False,
                      handler=lambda c: ran.append("off")),
        ]  # fmt: skip

    reg = ContextMenuRegistry()
    reg.add(MenuEntry(id="p.top", label="Top", text=lambda t: f"Top {t.items[0].key}",
                      group="open", handler=lambda c: "top"))  # fmt: skip
    reg.add(MenuEntry(id="p.with", label="With", group="open", order=1, submenu=kids))
    reg.add(MenuEntry(id="p.none", label="Empty", group="open", order=2, submenu=lambda t: []))
    reg.add(MenuEntry(id="p.boom", label="Boom", group="open", order=3, text=lambda t: 1 / 0,
                      handler=lambda c: None))  # fmt: skip
    t = _target("a.md", "b.md")
    rows = reg.rows(t)
    assert [r["text"] for r in rows] == ["Top a.md", "With", "Boom"]  # empty submenu hidden
    assert rows[1]["children"] == [
        {"name": "p.with/a", "text": "A for 2", "icon": "", "detail": "", "enabled": True},
        {"separator": True},
        {"name": "p.with/off", "text": "Off", "icon": "", "detail": "", "enabled": False},
    ]
    assert reg.run("p.with/a", MenuCall(target=t)) == "a"
    assert reg.run("p.with/off", MenuCall(target=t)) is None  # disabled
    assert reg.run("p.with/zz", MenuCall(target=t)) is None
    assert reg.run("p.with", MenuCall(target=t)) is None  # the parent itself runs nothing
    assert ran == ["a"]
    with pytest.raises(ValueError):
        reg.add(MenuEntry(id="a/b", label="x", handler=lambda c: None))
