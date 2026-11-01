from __future__ import annotations

from pathlib import Path

import pytest

from specto_core import CallContext, Engine, Project


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def engine(clock: FakeClock) -> Engine:
    return Engine(project=None, clock=clock)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.init(tmp_path / "proj", "demo")


@pytest.fixture
def project_engine(project: Project, clock: FakeClock) -> Engine:
    return Engine(project=project, clock=clock)


AGENT = CallContext(connection="mcp:1", origin="agent:s1")
USER = CallContext(connection="gui", origin="user")
