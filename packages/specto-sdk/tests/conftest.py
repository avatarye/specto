from __future__ import annotations

import pytest

from specto_core import Engine
from specto_sdk.testing import InProcessEndpoint


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
def endpoint(clock: FakeClock) -> InProcessEndpoint:
    return InProcessEndpoint(Engine(project=None, clock=clock))


def relabelled(engine: Engine, label: str) -> dict:
    """The live document with the file tree relabelled: the smallest real submit."""
    doc = engine.live.document()
    doc["views"]["fege"]["label"] = label
    return doc
