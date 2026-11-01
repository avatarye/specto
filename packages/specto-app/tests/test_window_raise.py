"""#215: ``window.raise`` without Qt: headless it raises nothing and says so; with a shell
provider it calls ``raise_window`` and names the instance."""

from __future__ import annotations

import specto_app.commands  # noqa: F401  (registers window.raise)
from specto_core import CallContext, Engine

USER = CallContext(connection="cli:1", origin="user")


class FakeShell:
    def __init__(self) -> None:
        self.raised = 0

    def raise_window(self) -> bool:
        self.raised += 1
        return True


class FakeApp:
    instance_name = "rasel"


class FakeHost:
    def __init__(self) -> None:
        self.shell = FakeShell()
        self.app = FakeApp()
        self.approvals = None


def test_headless_raise_is_false_with_a_warning():
    e = Engine(project=None)
    res = e.call("window.raise", {}, USER)
    assert res == {"raised": False, "instance": ""}


def test_raise_goes_to_the_shell_provider():
    host = FakeHost()
    e = Engine(project=None, host=host)
    res = e.call("window.raise", {}, USER)
    assert res == {"raised": True, "instance": "rasel"} and host.shell.raised == 1
