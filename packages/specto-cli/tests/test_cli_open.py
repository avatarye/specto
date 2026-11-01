"""#215: `specto open` resolves the app home, launches a detached app (a fake one here)
and reports its instance, raises an already-open project's window, and lists recents."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import venv
from pathlib import Path

import pytest
from click.testing import CliRunner

from specto_cli import open as opener
from specto_cli.main import CliState, cli
from specto_core import Args, CommandContext, Engine, Project, Result
from specto_core.instances import Instance, list_instances, pid_alive, register
from specto_core.registry import Registry
from specto_core.registry import registry as default_registry
from specto_sdk.testing import InProcessEndpoint

#: what the fake app does: register itself like SpectoApp does, then wait to be killed
FAKE_APP = textwrap.dedent(
    '''
    """A stand-in specto_app: registers an instance for --project and idles."""
    import json, os, sys, time
    from pathlib import Path

    args = sys.argv[1:]
    print("[OK] fake app argv", args, flush=True)
    if "--exit" in args:
        print("[ERROR] fake app asked to exit", flush=True)
        sys.exit(3)
    if "--project" in args:
        root = args[args.index("--project") + 1]
        user = Path(os.environ["SPECTO_USER_DIR"])
        (user / "instances").mkdir(parents=True, exist_ok=True)
        entry = {"name": "fakap", "project": root, "project_name": "fake", "pid": os.getpid(),
                 "endpoint": {"kind": "tcp", "address": "127.0.0.1:1"}, "started": time.time()}
        (user / "instances" / "fakap.json").write_text(json.dumps(entry), encoding="utf-8")
        print("[OK] instance fakap", flush=True)
    time.sleep(120)
    '''
)


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    monkeypatch.delenv(opener.ENV_APP_HOME, raising=False)
    monkeypatch.delenv("SPECTO_PROJECT", raising=False)
    monkeypatch.delenv("SPECTO_ENDPOINT", raising=False)
    return tmp_path / "user"


def make_home(where: Path, *, with_venv: bool = True) -> Path:
    """A folder shaped like a Specto checkout: packages/specto-app and a .venv."""
    (where / "packages" / "specto-app").mkdir(parents=True)
    (where / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    if with_venv:
        venv.create(where / ".venv", with_pip=False, symlinks=sys.platform != "win32")
    return where


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A home whose venv python runs the fake ``specto_app`` module."""
    home = make_home(tmp_path / "checkout")
    pkg = tmp_path / "fakesrc" / "specto_app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(FAKE_APP, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(pkg.parent))
    monkeypatch.setenv(opener.ENV_APP_HOME, str(home))
    return home


def kill(pid: int, *, tree: bool = False) -> None:
    """End a fake app. On Windows the launched pid is the venv launcher, whose child is
    the interpreter that registered: ``tree`` ends both."""
    if sys.platform == "win32":
        flags = ["/T", "/F"] if tree else ["/F"]
        subprocess.run(["taskkill", "/PID", str(pid), *flags], capture_output=True)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    for _ in range(50):
        if not pid_alive(pid):
            return
        time.sleep(0.1)


def kill_tree(pid: int) -> None:
    kill(pid, tree=True)


def run(*args: str, factory=None):
    return CliRunner().invoke(cli, list(args), obj=CliState(client_factory=factory))


# -- resolving the app home (seam 1) ------------------------------------------------------------
def test_app_home_precedence_env_settings_checkout(tmp_path, monkeypatch):
    a = make_home(tmp_path / "a")
    b = make_home(tmp_path / "b")
    # nothing set: the checkout this CLI runs from (the repo, which has a venv)
    home = opener.resolve_app_home()
    assert home.source == "checkout" and (home.root / "packages" / "specto-cli").is_dir()
    assert opener.checkout_of() == home.root
    # a setting beats the checkout
    opener.save_app_home(b)
    assert opener.resolve_app_home().root == b.resolve()
    assert json.loads(opener.settings_path().read_text())["app_home"] == str(b.resolve())
    # the environment beats both
    monkeypatch.setenv(opener.ENV_APP_HOME, str(a))
    home = opener.resolve_app_home()
    assert (home.root, home.source) == (a.resolve(), "env")
    assert home.python == opener.venv_python(a.resolve()) and home.python.is_file()


def test_a_broken_home_is_an_error_naming_the_fix(tmp_path, monkeypatch):
    monkeypatch.setenv(opener.ENV_APP_HOME, str(tmp_path / "nowhere"))
    r = run("open", str(tmp_path))
    assert r.exit_code == 1 and "SPECTO_APP_HOME" in r.output and "setup-cli" in r.output
    no_venv = make_home(tmp_path / "unsynced", with_venv=False)
    monkeypatch.setenv(opener.ENV_APP_HOME, str(no_venv))
    r = run("open", str(tmp_path))
    assert r.exit_code == 1 and "uv sync" in r.output
    assert "not a Specto checkout" in opener.home_problem(tmp_path)


def test_setup_cli_keeps_the_checkout_and_show_reports_it(tmp_path):
    home = make_home(tmp_path / "co")
    r = run("setup-cli", str(home))
    assert r.exit_code == 0, r.output
    assert "[OK] specto open launches from" in r.output and str(home.resolve()) in r.output
    r = run("setup-cli", "--show")
    assert r.exit_code == 0 and "settings" in r.output and str(home.resolve()) in r.output
    r = run("setup-cli", str(tmp_path / "plain"))
    assert r.exit_code == 1 and "not a folder" in r.output


def test_launch_argv_and_child_env():
    home = opener.AppHome(root=Path("/co"), source="env")
    argv = opener.launch_argv(home, Path("/p"), ["--no-web"])
    assert argv[1:] == ["-m", "specto_app", "--project", str(Path("/p")), "--no-web"]
    assert "--project" not in opener.launch_argv(home, None)
    env = opener.child_env({"SPECTO_PROJECT": "x", "SPECTO_ENDPOINT": "y", "PATH": "p"})
    assert env == {"PATH": "p"}


# -- launching (seam 2) ---------------------------------------------------------------------
def test_open_launches_detached_and_reports_the_instance(tmp_path, fake_home, user_dir):
    proj = Project.init(tmp_path / "proj", "alpha")
    log = tmp_path / "app.log"
    r = run("open", str(proj.root), "--log", str(log), "--wait", "20")
    pid = None
    try:
        assert r.exit_code == 0, r.output
        line = next(ln for ln in r.output.splitlines() if ln.startswith("[OK] fakap"))
        pid = int(line.rsplit("pid", 1)[1])
        assert "fake" in line and str(proj.root) in line and f"log {log}" in r.output
        assert pid_alive(pid)  # it outlived the CLI call
        live = list_instances()
        assert [(i.name, i.pid) for i in live] == [("fakap", pid)]
        assert "[OK] fake app argv" in log.read_text(encoding="utf-8")
        # a second open does not start another: it raises the open one (here: no endpoint)
        r2 = run("open", str(proj.root))
        assert r2.exit_code == 0 and "is open already as fakap" in r2.output
        assert len(list_instances()) == 1
        # --new starts another; the fake registers under the same name, so the entry
        # changes hands but the CLI still reports the new process, not the old one
        r3 = run("--json", "open", str(proj.root), "--new", "--log", str(log), "--wait", "20")
        out = json.loads(r3.output)
        assert r3.exit_code == 0 and out["instance"] == "fakap" and out["instance_pid"] != pid
        kill(out["instance_pid"])
        kill(out["pid"])
    finally:
        if pid:
            kill(pid)


def test_open_json_and_no_wait(tmp_path, fake_home):
    proj = Project.init(tmp_path / "proj", "alpha")
    r = run("open", str(proj.root), "--wait", "0", "--log", str(tmp_path / "l.log"), "--json")
    out = json.loads(r.output)
    try:
        assert r.exit_code == 0 and out["instance"] is None and out["project"] == str(proj.root)
        assert out["app_home"] == str(fake_home.resolve()) and pid_alive(out["pid"])
    finally:
        kill_tree(out["pid"])


def test_open_takes_a_leading_app_flag_as_an_app_arg(tmp_path, fake_home, monkeypatch):
    """`specto open --no-web` means the project here plus an app flag, not PATH=--no-web."""
    proj = Project.init(tmp_path / "proj", "alpha")
    monkeypatch.chdir(proj.root)
    log = tmp_path / "l.log"
    r = run("--json", "open", "--no-web", "--wait", "0", "--log", str(log))
    out = json.loads(r.output)
    try:
        assert r.exit_code == 0 and out["project"] == str(proj.root)
        for _ in range(50):
            if log.exists() and "argv" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        assert "'--no-web'" in log.read_text(encoding="utf-8")
    finally:
        kill_tree(out["pid"])


def test_open_reports_an_app_that_dies_with_its_log(tmp_path, fake_home):
    proj = Project.init(tmp_path / "proj", "alpha")
    log = tmp_path / "l.log"
    r = run("open", str(proj.root), "--exit", "--log", str(log), "--wait", "20")
    assert r.exit_code == 1
    assert "exited before registering" in r.output and "asked to exit" in r.output


def test_open_welcome_passes_no_project(tmp_path, fake_home):
    log = tmp_path / "l.log"
    r = run("--json", "open", "--welcome", "--log", str(log))
    out = json.loads(r.output)
    try:
        assert r.exit_code == 0 and out["project"] is None
        for _ in range(50):
            if log.exists() and "argv" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        assert "--project" not in log.read_text(encoding="utf-8")
    finally:
        kill_tree(out["pid"])


def test_open_makes_a_plain_folder_a_project_and_opens_the_root_from_inside(tmp_path, fake_home):
    """#217: a folder in no project becomes one (named after the folder); a path inside a
    project opens that project; a path that is no folder is an error."""
    plain = tmp_path / "day3"
    plain.mkdir()
    r = run("--json", "open", str(plain), "--wait", "0")
    out = json.loads(r.output)
    try:
        assert r.exit_code == 0 and (plain / ".specto").is_dir() and out["created"]
        assert Project.open(plain).name == "day3"
    finally:
        kill_tree(out["pid"])
    sub = plain / "runs" / "a"
    sub.mkdir(parents=True)
    r = run("open", str(sub), "--wait", "0")
    assert r.exit_code == 0 and "initialised" not in r.output and not (sub / ".specto").exists()
    assert f"app starting on {plain.resolve()}" in r.output
    kill_tree(int(r.output.split("(pid ")[1].split(",")[0]))
    other = tmp_path / "day4"
    other.mkdir()
    r = run("open", str(other), "--wait", "0")
    assert r.exit_code == 0 and "[OK] initialised project 'day4'" in r.output
    kill_tree(int(r.output.split("(pid ")[1].split(",")[0]))
    r = run("open", str(tmp_path / "typo"), "--wait", "0")
    assert r.exit_code == 1 and "is not a folder" in r.output


# -- an open project: window.raise over the endpoint ---------------------------------------
class RaiseRes(Result):
    raised: bool
    instance: str = ""


def endpoint_with_raise(calls: list) -> InProcessEndpoint:
    reg = Registry()
    for spec in default_registry.specs():
        if spec.meta.name != "window.raise":
            reg.register(spec)

    @reg.command("window.raise", doc="stand-in")
    def _raise(ctx: CommandContext, args: Args) -> RaiseRes:
        calls.append(1)
        return RaiseRes(raised=True, instance="rasel")

    return InProcessEndpoint(Engine(project=None, registry=reg))


def test_open_on_an_open_project_raises_its_window(tmp_path, monkeypatch):
    proj = Project.init(tmp_path / "a", "alpha")
    register(
        Instance(
            name="rasel",
            project=str(proj.root),
            project_name="alpha",
            pid=os.getpid(),
            endpoint={"kind": "tcp", "address": "127.0.0.1:9"},
        )
    )
    calls: list = []
    ep = endpoint_with_raise(calls)
    seen: list = []

    def factory(st):
        seen.append((st.project, st.endpoint))
        return ep.client()

    monkeypatch.setenv(opener.ENV_APP_HOME, str(make_home(tmp_path / "co")))
    r = run("open", str(proj.root), factory=factory)
    assert r.exit_code == 0, r.output
    assert "alpha is open already as rasel" in r.output and "in front" in r.output
    assert calls == [1] and seen == [(str(proj.root), "tcp://127.0.0.1:9")]
    r = run("--json", "open", str(proj.root), factory=factory)
    assert json.loads(r.output) == {
        "instance": "rasel",
        "pid": os.getpid(),
        "project": str(proj.root),
        "raised": True,
    }


# -- recents -----------------------------------------------------------------------------------
def test_open_list_prints_the_recents_with_open_and_missing_marks(tmp_path, user_dir):
    proj = Project.init(tmp_path / "a", "alpha")
    gone = tmp_path / "gone"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "recents.json").write_text(
        json.dumps(
            {
                "projects": [
                    {"root": str(proj.root), "name": "alpha", "opened": 2.0},
                    {"root": str(gone), "name": "beta", "opened": 1.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    register(Instance(name="rasel", project=str(proj.root), pid=os.getpid()))
    r = run("open", "--list")
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0].startswith("alpha") and lines[0].endswith("[rasel]")
    assert lines[1].startswith("beta") and lines[1].endswith("[missing]")
    r = run("--json", "open", "--list")
    got = json.loads(r.output)
    assert [e["name"] for e in got] == ["alpha", "beta"] and got[1]["missing"]
