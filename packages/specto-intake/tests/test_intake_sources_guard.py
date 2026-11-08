"""SPEC-72: an intake spec's source roots are judged by their text before any filesystem
call: ``intake.preview`` (which takes a spec straight from its caller) answers a network,
device or rooted root with an error as data, and ``resolve_root`` refuses one as the
backstop for every walker, listing and resolver."""

from __future__ import annotations

import os
import pathlib
from pathlib import Path

import pytest
from intake_helpers import ALIASES, EXT_KINDS

import specto_intake  # noqa: F401  (registers the commands)
from specto_core import Engine, Project
from specto_core.errors import InvalidArgs
from specto_intake.preview import preview
from specto_intake.sources import resolve_root
from specto_intake.spec import IntakeSpec, SourceSpec

WINDOWS = os.name == "nt"
SUSPECT = ("host", "\\\\.", "\\??", "/??")


@pytest.fixture
def no_reach(monkeypatch: pytest.MonkeyPatch) -> list[str]:
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
    monkeypatch.setattr(os.path, "exists", guard(os.path.exists))
    monkeypatch.setattr(os.path, "realpath", guard(os.path.realpath))
    return seen


NETWORK = [
    pytest.param(r"\\host\share\**\*.jpg", marks=pytest.mark.skipif(not WINDOWS, reason="nt")),
    pytest.param(r"\\host\share", marks=pytest.mark.skipif(not WINDOWS, reason="nt")),
    "//host/share/**/*.jpg",
]


@pytest.mark.parametrize("source", NETWORK)
def test_intake_preview_answers_a_network_source_with_an_error_and_no_lookup(
    tmp_path: Path, no_reach: list[str], source: str
):
    engine = Engine(project=Project.init(tmp_path / "proj", "demo"))
    out = engine.call("intake.preview", {"spec": {"sources": [source]}})
    assert out["errors"] and "host" in out["errors"][0]
    assert "under the project folder" in out["errors"][0]
    assert out["count"] == 0 if "count" in out else True
    assert not any("host" in p for p in no_reach)


@pytest.mark.skipif(not WINDOWS, reason="nt")
def test_an_nt_object_spelling_is_a_glob_under_the_project_and_reaches_nothing(
    tmp_path: Path, no_reach: list[str]
):
    """The NT object spelling has glob magic (``?``) at its second character, so the spec
    reads it as a pattern under the project folder: it matches nothing there and never
    names a root of its own (the canvas validator refuses the same text as a device
    path)."""
    engine = Engine(project=Project.init(tmp_path / "proj", "demo"))
    out = engine.call("intake.preview", {"spec": {"sources": [r"\??\UNC\host\share"]}})
    assert out["errors"] == [] and out["sources"][0]["root"] == "."
    assert not any("host" in p for p in no_reach)


def test_preview_itself_refuses_the_spec_before_resolving_roots(tmp_path: Path, no_reach):
    out = preview(
        {"sources": ["//host/share"]}, base=tmp_path, aliases=ALIASES, ext_kinds=EXT_KINDS
    )
    assert out["errors"] and "network path" in out["errors"][0]
    assert not any("host" in p for p in no_reach)


def test_the_spec_refuses_such_a_root_as_text():
    with pytest.raises(InvalidArgs, match="network path"):
        SourceSpec.coerce("//host/share/**/*.jpg")
    with pytest.raises(InvalidArgs, match="network path"):
        IntakeSpec.parse({"sources": [{"root": "//host/share"}]})
    assert SourceSpec.coerce("runs/**/*.jpg").root == "runs"
    assert SourceSpec.coerce(".").root == "."


def test_resolve_root_is_the_backstop(tmp_path: Path, no_reach: list[str]):
    """A source that skipped validation (``model_construct``) is still refused before
    ``resolve`` or ``exists`` touch it."""
    src = SourceSpec.model_construct(
        root="//host/share", pattern=None, depth=None, follow_links=False
    )
    with pytest.raises(InvalidArgs, match="network path"):
        resolve_root(src, tmp_path)
    assert not any("host" in p for p in no_reach)
    assert resolve_root(SourceSpec(root="runs"), tmp_path) == (tmp_path / "runs").resolve()
