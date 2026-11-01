"""Repo-wide test hygiene: no watcher thread (``specto_core.links.LinkWatch``) outlives
the test that started it. Engines opened on a project watch their file tree's folder
by default; a test that forgets ``engine.close()`` must not leave threads polling a
deleted temp folder while later tests run (that crashed the Qt tests).

Also the TEST-ONLY way to approve a python link's code headlessly (``approve_link``):
the ``link.approve`` command never approves by itself (#203, ADR 0006), it opens an
approval card in the window; tests with no window stand in for the user's click by
calling the runtime's ``approve`` directly, exactly what the view's Approve button and
the card call. There is no flag, env var or command for this: nothing outside the
process can reach it."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_watcher_outlives_the_test():
    yield
    from specto_core.links import stop_all_watches

    stop_all_watches(join=True)


@pytest.fixture
def approve_link():
    """TEST ONLY: ``approve_link(engine, "l9")`` approves the file of a python link as
    it is now, as the user's click would (``engine.links.approve``), and returns
    ``{link, path, hash}``. The ``link.approve`` command only asks (#203)."""

    def _approve(engine, link_id: str) -> dict:
        return engine.links.approve(link_id)

    return _approve
