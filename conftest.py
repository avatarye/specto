"""Repo-wide test hygiene: no watcher thread (``specto_core.links.LinkWatch``) outlives
the test that started it. Engines opened on a project watch their file tree's folder
by default; a test that forgets ``engine.close()`` must not leave threads polling a
deleted temp folder while later tests run (that crashed the Qt tests).

Also the TEST-ONLY way to approve a python link's code headlessly (``approve_link``):
the ``link.approve`` command never approves by itself (#203, ADR 0006), it opens an
approval card in the window; tests with no window stand in for the user's click by
calling the runtime's ``approve`` directly, exactly what the view's Approve button and
the card call. There is no flag, env var or command for this: nothing outside the
process can reach it.

A submit off the thread that owns the canvas raises under tests (``SPECTO_THREAD_CHECK``,
``LiveCanvas.check_thread``; the app only warns), in this process and in the app
processes the GUI tests start.

Also ``no_reach``, the filesystem trap of the path-guard tests (SPEC-72, SPEC-79)."""

from __future__ import annotations

import os
import pathlib

import pytest

os.environ.setdefault("SPECTO_THREAD_CHECK", "raise")


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


#: text no local path of the tests carries: a filesystem call that reaches it reached out
SUSPECT = ("host", "\\\\.", "\\??", "/??", "PhysicalDrive")


@pytest.fixture
def no_reach(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every filesystem call keeps working on the test's own paths and fails the test on a
    path that names the fake ``host`` or a device (``SUSPECT``); returns the paths it saw.
    ``os.path.isdir``, ``isfile`` and ``lexists`` are trapped too: on Windows (3.12+) they
    are native and do not go through ``os.stat``."""
    seen: list[str] = []

    def guard(real):
        def wrapped(path, *a, **k):
            text = str(path)
            seen.append(text)
            if any(s in text for s in SUSPECT):
                raise AssertionError(f"a filesystem call reached {text!r}")
            return real(path, *a, **k)

        return wrapped

    for name in ("stat", "exists", "is_file", "is_dir", "resolve", "glob"):
        monkeypatch.setattr(pathlib.Path, name, guard(getattr(pathlib.Path, name)))
    for name in ("stat", "lstat", "listdir", "scandir"):
        monkeypatch.setattr(os, name, guard(getattr(os, name)))
    for name in ("exists", "lexists", "isdir", "isfile", "realpath"):
        monkeypatch.setattr(os.path, name, guard(getattr(os.path, name)))
    return seen
