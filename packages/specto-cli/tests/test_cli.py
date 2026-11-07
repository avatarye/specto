"""The generated CLI against the in-process fake endpoint."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from specto_cli.main import CliState, build_cli, cli, main
from specto_cli.render import render_result
from specto_core import Args, CommandContext, Engine, Result
from specto_core.registry import Registry
from specto_core.registry import registry as default_registry
from specto_sdk import load_api_info
from specto_sdk.testing import InProcessEndpoint


@pytest.fixture
def ep() -> InProcessEndpoint:
    return InProcessEndpoint()


def factory(ep: InProcessEndpoint):
    """What the real CLI does: a fresh socket per invocation, one shared connection name."""
    return lambda st: ep.client(
        origin=st.origin, client="cli", connection=st.connection or "cli:test@host"
    )


@pytest.fixture
def run(ep: InProcessEndpoint):
    runner = CliRunner()

    def _run(*args: str, env: dict[str, str] | None = None):
        state = CliState(client_factory=factory(ep))
        return runner.invoke(cli, list(args), obj=state, env=env, catch_exceptions=False)

    return _run


def test_help_lists_generated_groups():
    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0
    for word in ("canvas", "describe", "list-commands", "init", "status", "watch"):
        assert word in r.output
    r = CliRunner().invoke(cli, ["canvas", "submit", "--help"])
    assert "--document" in r.output and "--label" in r.output
    r = CliRunner().invoke(cli, ["describe", "--help"])
    assert "[TARGET]" in r.output


def test_version_is_the_release():
    """``specto --version`` is the release (#230): the CLI's ``__version__`` and its
    pyproject carry the same number."""
    import tomllib
    from pathlib import Path

    import specto_cli

    r = CliRunner().invoke(cli, ["--version"])
    assert r.exit_code == 0 and r.output.strip() == f"specto, version {specto_cli.__version__}"
    pyproject = Path(specto_cli.__file__).parents[2] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"] == (
        specto_cli.__version__
    )


def test_build_from_api_info_covers_every_command():
    info = load_api_info()
    root = build_cli(info)
    for cmd in info["commands"]:
        group, _, verb = cmd["name"].partition(".")
        if verb:
            assert verb in root.commands[group].commands, cmd["name"]
        else:
            assert group.replace("_", "-") in root.commands, cmd["name"]


def test_call_and_json_output(run, ep):
    r = run("--json", "canvas", "state")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["document"]["views"]["fege"]["type"] == "file_tree"
    doc = out["document"]
    doc["views"]["fege"]["label"] = "Data"
    r = run("--json", "canvas", "submit", "--document", json.dumps(doc), "--label", "renamed")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["touched"] == ["fege"]
    assert ep.engine.live.data["views"]["fege"]["label"] == "Data"
    assert ep.engine.live.record("t1").origin == "user"


def test_human_output_and_positional(run, ep):
    r = run("describe", "view:gallery")
    assert r.exit_code == 0, r.output
    assert "target: view:gallery" in r.output and "ports" in r.output
    r = run("canvas", "undo")
    assert r.exit_code == 1 and "[ERROR]" in r.output and "nothing to undo" in r.output


def test_missing_positional_is_a_clear_error(run):
    r = run("describe")
    assert r.exit_code == 1
    assert "target is required" in r.output


def test_error_rendering_with_fix(run):
    r = run("describe", "view:nope")
    assert r.exit_code == 1
    assert "[ERROR] not_found" in r.output and "->" in r.output


class JobArgs(Args):
    label: str = ""


class JobRes(Result):
    job: str


def registry_with_job() -> Registry:
    """The built-ins plus a job-starting command, without touching the global registry."""
    reg = Registry()
    for spec in default_registry.specs():
        reg.register(spec)

    @reg.command("test.job", doc="start a job", read_only=True)
    def _test_job(ctx: CommandContext, args: JobArgs) -> JobRes:
        def work(*, progress):
            progress(0.5, "half")
            return {"n": 3}

        return JobRes(job=ctx.engine.jobs.submit("test", work).id)

    return reg


def test_job_wait():
    ep = InProcessEndpoint(Engine(project=None, registry=registry_with_job()))
    info = ep.engine.api_info()
    root = build_cli(info)
    runner = CliRunner()
    state = CliState(client_factory=lambda st: ep.client())
    r = runner.invoke(root, ["test", "job"], obj=state)
    assert r.exit_code == 0, r.output
    assert "[OK] j1 done" in r.output and "state: done" in r.output
    state = CliState(client_factory=lambda st: ep.client(), json=True)
    r = runner.invoke(root, ["--no-wait", "test", "job"], obj=state)
    assert json.loads(r.output) == {"job": "j2"}


def test_init_and_status(tmp_path, monkeypatch):
    runner = CliRunner()
    r = runner.invoke(cli, ["init", str(tmp_path / "proj"), "--name", "demo"], obj=CliState())
    assert r.exit_code == 0 and "[OK] project 'demo'" in r.output
    assert (tmp_path / "proj" / ".specto" / "project.toml").exists()
    monkeypatch.chdir(tmp_path / "proj")
    r = runner.invoke(cli, ["status"], obj=CliState())
    assert r.exit_code == 1 and "no running Specto app" in r.output
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(cli, ["status"], obj=CliState())
    assert r.exit_code == 1 and "no Specto project" in r.output


def test_main_returns_exit_codes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["--help"]) == 0
    assert main(["canvas", "state"]) == 1  # no project -> endpoint error
    assert main(["bogus"]) == 2


def test_render_result_shapes():
    txt = render_result(
        {
            "txn": "t1",
            "report": {"entries": [{"kind": "bound", "message": "ok", "count": 3}]},
            "patch": [{"op": "add", "path": "/views/kobi", "value": {}}],
            "touched": ["kobi"],
            "quotas": {"web_views": {"used": 1, "max": 8}},
            "list": [{"a": 1, "b": [1, 2]}],
            "none": None,
        }
    )
    assert txt.splitlines()[0] == "[OK] x3: ok"
    assert "touched: kobi" in txt and "add /views/kobi" in txt
    assert "web_views: used=1, max=8" in txt and "none: -" in txt and "- a: 1" in txt


SPEC = {
    "sources": [{"root": "C:/data/testdata/runs/run-001/cam-a"}],
    "keys": r"^(?P<frame>\d{5})_",
}


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(SPEC),  # proper JSON: the regex backslash doubled
        json.dumps(SPEC).replace("\\\\", "\\"),  # a shell collapsed the doubled backslash
    ],
)
def test_inline_json_accepts_lone_backslashes(text):
    from specto_cli.main import JsonParam, loads_lenient

    assert loads_lenient(text) == SPEC
    assert JsonParam({"type": "object"}).convert(text, None, None) == SPEC


def test_inline_json_spec_via_cli_in_both_quotings(run, ep):
    # the engine serves intake.preview once specto_intake is imported (its import
    # registers the intake.* commands); the CLI does not depend on it, the app does
    pytest.importorskip("specto_intake")
    info = ep.engine.api_info()
    cmd = next(c for c in info["commands"] if c["name"] == "intake.preview")
    assert "spec" in cmd["args_schema"]["properties"]
    for text in (json.dumps(SPEC), json.dumps(SPEC).replace("\\\\", "\\")):
        r = run("--json", "intake", "preview", "--spec", text, "--limit", "1")
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["key_fields"] == ["frame"] or out["errors"]  # parsed either way


def test_quotes_stripped_by_powershell_gets_a_fix_it(run):
    stripped = "{sources:[{root:C:/x/front}],keys:^(?P<frame>\\d{5})_}"
    r = run("intake", "preview", "--spec", stripped)
    assert r.exit_code == 2
    assert "removed the double quotes" in r.output and "PowerShell" in r.output
    assert '\\"' in r.output and "@file.json" in r.output
    r = run("intake", "preview", "--spec", "nonsense")
    assert r.exit_code == 2 and "not JSON (or @file)" in r.output


def test_help_documents_json_quoting():
    r = CliRunner().invoke(cli, ["--help"])
    assert "Quoting inline JSON" in r.output and "PowerShell" in r.output
    r = CliRunner().invoke(cli, ["intake", "preview", "--help"])
    assert "JSON|@FILE" in r.output and "quoting rules" in r.output
