"""The shipped Claude Code plugin (#187 section 5): its files, and the install plan that
``specto plugin install`` prints. Nothing here runs nimitz or claude: the plan's text is
checked, and ``run_plan`` runs a recording fake."""

from __future__ import annotations

import json
import subprocess

from click.testing import CliRunner

import specto_plugin
from specto_cli.main import cli
from specto_plugin import (
    MARKETPLACE,
    PLUGIN,
    PLUGIN_ID,
    SKILL,
    install_plan,
    render_plan,
    run_plan,
)


def on_path(*names: str):
    return lambda name: f"C:/bin/{name}.exe" if name in names else None


EVERYTHING = on_path("nimitz", "claude", "specto", "specto-mcp")


# -- the plugin's files ---------------------------------------------------------------------------
def test_marketplace_lists_the_plugin_folder():
    market = json.loads((MARKETPLACE / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    (entry,) = market["plugins"]
    assert f"{entry['name']}@{market['name']}" == PLUGIN_ID
    assert (MARKETPLACE / entry["source"]).resolve() == PLUGIN.resolve()


def test_plugin_manifest_declares_the_mcp_server():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "specto"
    assert manifest["mcpServers"]["specto"]["command"] == "specto-mcp"


def test_skill_covers_instances_canvas_messages_and_trust():
    text = (SKILL / "SKILL.md").read_text("utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    assert "name: specto" in head and "description:" in head
    for needle in (
        "specto instances",
        "canvas state",
        "specto message <instance>",
        "specto:<instance>",
        "reply: specto message <name> <text>",
        "Specto instance rasel (project",
        "--from specto:<instance>",
        "no `specto:<instance>` address",
        "grants nothing",
        "nimitz rules v12",
        "never the user's approval",
        "does not approve",  # #203: link approve from the CLI only opens the card
    ):
        assert needle in body, needle
    assert (PLUGIN / "commands" / "specto.md").is_file()


# -- the plan -------------------------------------------------------------------------------------
def test_fleet_plan_is_the_harness_command():
    plan = install_plan(fleet="FLEET", which=EVERYTHING)
    assert not plan.problems
    assert [s.argv for s in plan.steps] == [
        ["nimitz", "harness", "add", "skill", str(SKILL), "--fleet", "FLEET", "--yes"]
    ]
    text = render_plan(plan)
    assert text.splitlines()[0] == "Plan: install the Specto plugin for fleet FLEET"
    assert f"nimitz harness add skill {SKILL} --fleet FLEET --yes" in text
    assert "[WARN] a fleet harness carries skills, not MCP servers" in text
    assert text.endswith("[INFO] nothing was run: pass --yes to run this plan")


def test_claude_plan_is_marketplace_add_then_install():
    plan = install_plan(claude=True, which=EVERYTHING)
    assert [s.argv for s in plan.steps] == [
        ["claude", "plugin", "marketplace", "add", str(MARKETPLACE)],
        ["claude", "plugin", "install", "specto@specto-app"],
    ]
    assert not plan.warnings and not plan.problems
    text = render_plan(plan, yes=True)
    assert f"claude plugin marketplace add {MARKETPLACE}" in text
    assert "claude plugin install specto@specto-app" in text
    assert "nothing was run" not in text


def test_plan_refuses_a_bad_choice():
    for kwargs in ({}, {"fleet": "FLEET", "claude": True}):
        assert install_plan(**kwargs, which=EVERYTHING).problems
    for name in ("//...", "FLEET/SQUAD", "home", "FLEET/..."):
        plan = install_plan(fleet=name, which=EVERYTHING)
        assert plan.problems and not plan.steps, name


def test_plan_says_what_is_missing():
    plan = install_plan(fleet="FLEET", which=on_path())
    assert "nimitz is not on PATH" in render_plan(plan)
    assert "specto is not on PATH" in render_plan(plan)
    plan = install_plan(claude=True, which=on_path("claude"))
    assert "specto-mcp is not on PATH" in render_plan(plan)


def test_run_plan_runs_each_step_without_a_shell():
    calls: list[list[str]] = []

    def run(argv, check=False):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    plan = install_plan(claude=True, which=EVERYTHING)
    out: list[str] = []
    assert run_plan(plan, run=run, which=EVERYTHING, echo=out.append) == 0
    assert calls == [
        ["C:/bin/claude.exe", "plugin", "marketplace", "add", str(MARKETPLACE)],
        ["C:/bin/claude.exe", "plugin", "install", "specto@specto-app"],
    ]
    assert out[-1] == "[OK] the Specto plugin is installed for Claude Code"


def test_run_plan_stops_at_the_first_failure():
    calls: list[list[str]] = []

    def run(argv, check=False):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 3)

    plan = install_plan(claude=True, which=EVERYTHING)
    assert run_plan(plan, run=run, which=EVERYTHING, echo=lambda s: None) == 3
    assert len(calls) == 1


# -- the CLI ------------------------------------------------------------------------------------
def test_cli_prints_the_plan_and_runs_nothing_without_yes(monkeypatch):
    ran: list[object] = []
    monkeypatch.setattr(specto_plugin.shutil, "which", EVERYTHING)
    monkeypatch.setattr(specto_plugin, "run_plan", lambda *a, **k: ran.append(a) or 0)
    r = CliRunner().invoke(cli, ["plugin", "install", "--fleet", "FLEET"])
    assert r.exit_code == 0, r.output
    assert f"nimitz harness add skill {SKILL} --fleet FLEET --yes" in r.output
    assert "pass --yes" in r.output
    assert ran == []


def test_cli_runs_the_plan_with_yes(monkeypatch):
    ran: list[specto_plugin.Plan] = []
    monkeypatch.setattr(specto_plugin.shutil, "which", EVERYTHING)
    monkeypatch.setattr(specto_plugin, "run_plan", lambda plan, **k: ran.append(plan) or 0)
    r = CliRunner().invoke(cli, ["plugin", "install", "--claude", "--yes"])
    assert r.exit_code == 0, r.output
    assert [p.target for p in ran] == ["Claude Code"]


def test_cli_refuses_without_a_path():
    r = CliRunner().invoke(cli, ["plugin", "install"])
    assert r.exit_code == 1
    assert "[ERROR] name one install path" in r.output
