"""The Claude Code plugin shipped with Specto (#187 section 5) and its install plan.

The plugin's files are package data: ``marketplace/`` is a local Claude Code
marketplace (``.claude-plugin/marketplace.json``) holding one plugin, ``specto/``, with
the ``specto`` skill, the ``/specto`` command and the specto-mcp server declared in its
``.claude-plugin/plugin.json``. Two install paths, each run only when the user agrees:

- fleet: ``nimitz harness add skill <skill folder> --fleet F --yes``, so the fleet's
  members get the skill through the fleet harness (a harness carries rules, agents,
  skills and settings, not MCP servers: members use the ``specto`` CLI the skill names);
- Claude Code: ``claude plugin marketplace add <marketplace>`` then
  ``claude plugin install specto@specto-app``.

``install_plan`` builds the plan (exact argv per step, with the notes the user should
read first), ``render_plan`` prints it and ``run_plan`` runs it on ``--yes``. Nothing
here edits ``~/.claude`` itself: the two CLIs do, once the user said yes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MARKETPLACE = Path(__file__).resolve().parent / "marketplace"
MARKETPLACE_NAME = "specto-app"
PLUGIN_NAME = "specto"
PLUGIN = MARKETPLACE / PLUGIN_NAME
SKILL = PLUGIN / "skills" / "specto"
PLUGIN_ID = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
#: one fleet's name as nimitz writes it (``FLEET``, ``MAIN_2``): never a path, never //...
FLEET_NAME = re.compile(r"[A-Z0-9_]+")

Which = Callable[[str], str | None]


@dataclass
class Step:
    argv: list[str]
    why: str


@dataclass
class Plan:
    target: str
    steps: list[Step] = field(default_factory=list)
    #: what the user should know before saying yes (``[WARN]`` lines)
    warnings: list[str] = field(default_factory=list)
    #: why nothing can run (``[ERROR]`` lines); a plan with problems never runs
    problems: list[str] = field(default_factory=list)


def install_plan(
    *,
    fleet: str | None = None,
    claude: bool = False,
    which: Which | None = None,
) -> Plan:
    """The plan for ``specto plugin install --fleet F | --claude`` (exactly one);
    ``which`` finds a program on PATH (``shutil.which``)."""
    which = which or shutil.which
    if (fleet is None) == (not claude):
        plan = Plan("")
        plan.problems.append("name one install path: --fleet F (a nimitz fleet) or --claude")
        return plan
    if fleet is not None:
        plan = Plan(f"fleet {fleet}")
        if not FLEET_NAME.fullmatch(fleet):
            plan.problems.append(
                f"{fleet!r} is not a fleet name: one name such as FLEET (not a member path, "
                "never //...)"
            )
            return plan
        if which("nimitz") is None:
            plan.problems.append("nimitz is not on PATH: install claude-nimitz first")
        plan.steps.append(
            Step(
                ["nimitz", "harness", "add", "skill", str(SKILL), "--fleet", fleet, "--yes"],
                f"add the specto skill to fleet {fleet}'s harness: every member gets it",
            )
        )
        plan.warnings.append(
            "a fleet harness carries skills, not MCP servers: members reach Specto through "
            "the specto CLI, which must be on their PATH"
        )
        if which("specto") is None:
            plan.warnings.append("specto is not on PATH here: members will not find the CLI")
        return plan
    plan = Plan("Claude Code")
    if which("claude") is None:
        plan.problems.append("claude is not on PATH: install Claude Code first")
    plan.steps += [
        Step(
            ["claude", "plugin", "marketplace", "add", str(MARKETPLACE)],
            f"register this install's plugin folder as the local marketplace {MARKETPLACE_NAME}",
        ),
        Step(
            ["claude", "plugin", "install", PLUGIN_ID],
            "install the specto plugin: the specto skill, /specto and the specto-mcp server",
        ),
    ]
    if which("specto-mcp") is None:
        plan.warnings.append(
            "specto-mcp is not on PATH here: the plugin's MCP server will not start"
        )
    return plan


def render_plan(plan: Plan, *, yes: bool = False) -> str:
    """The plan as the user reads it: every command exactly as it would run."""
    lines = ["Plan: install the Specto plugin" + (f" for {plan.target}" if plan.target else "")]
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"  {i}. {step.why}")
        lines.append(f"     {_shell(step.argv)}")
    lines += [f"[WARN] {w}" for w in plan.warnings]
    lines += [f"[ERROR] {p}" for p in plan.problems]
    if not plan.problems and not yes:
        lines.append("[INFO] nothing was run: pass --yes to run this plan")
    return "\n".join(lines)


def run_plan(
    plan: Plan,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    which: Which | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    """Run the plan's steps in order (an argv list each, no shell; the program found on
    PATH, so a ``claude.cmd`` runs too); the first failing step stops it. Returns the
    exit code."""
    if plan.problems:
        return 1
    which = which or shutil.which
    for step in plan.steps:
        echo(f"[RUN] {_shell(step.argv)}")
        argv = [which(step.argv[0]) or step.argv[0], *step.argv[1:]]
        code = run(argv, check=False).returncode
        if code != 0:
            echo(f"[ERROR] {step.argv[0]} exited {code}; the steps after it did not run")
            return code
    echo(f"[OK] the Specto plugin is installed for {plan.target}")
    return 0


def _shell(argv: Sequence[str]) -> str:
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in argv)


__all__ = [
    "MARKETPLACE",
    "MARKETPLACE_NAME",
    "PLUGIN",
    "PLUGIN_ID",
    "PLUGIN_NAME",
    "SKILL",
    "Plan",
    "Step",
    "install_plan",
    "render_plan",
    "run_plan",
]
