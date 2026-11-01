"""``specto plugin``: the Claude Code plugin shipped with the app (#187 section 5).

    specto plugin install --fleet FLEET          # print the plan: nimitz harness add skill ...
    specto plugin install --claude --yes        # run it: claude plugin marketplace add + install
    specto plugin path                          # where this install keeps the plugin

``install`` prints the exact plan and runs it only with ``--yes`` (``specto_plugin``):
nothing touches ``~/.claude`` or a fleet until the user says so.
"""

from __future__ import annotations

import sys

import click

import specto_plugin


@click.group("plugin")
def plugin_group() -> None:
    """The Claude Code plugin shipped with Specto (the specto skill and specto-mcp)."""


@plugin_group.command("install")
@click.option("--fleet", default=None, help="Add the specto skill to this nimitz fleet's harness.")
@click.option("--claude", is_flag=True, help="Install the plugin into Claude Code.")
@click.option("--yes", is_flag=True, help="Run the plan (without it, only print it).")
def install_cmd(fleet: str | None, claude: bool, yes: bool) -> None:
    """Print the install plan for --fleet F or --claude; run it with --yes."""
    plan = specto_plugin.install_plan(fleet=fleet, claude=claude)
    click.echo(specto_plugin.render_plan(plan, yes=yes))
    if plan.problems:
        sys.exit(1)
    if yes:
        sys.exit(specto_plugin.run_plan(plan, echo=click.echo))


@plugin_group.command("path")
def path_cmd() -> None:
    """Print the plugin's folders: the local marketplace, the plugin and the skill."""
    click.echo(f"marketplace {specto_plugin.MARKETPLACE}")
    click.echo(f"plugin      {specto_plugin.PLUGIN}")
    click.echo(f"skill       {specto_plugin.SKILL}")


__all__ = ["plugin_group"]
