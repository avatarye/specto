"""SPEC-72: an intake spec's source roots are judged by their text before any filesystem
call: ``intake.preview`` (which takes a spec straight from its caller) answers a network,
device or rooted root with an error as data, and ``resolve_root`` refuses one as the
backstop for every walker, listing and resolver."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from intake_helpers import ALIASES, EXT_KINDS
from pydantic import ValidationError

import specto_intake  # noqa: F401  (registers the commands)
from specto_core import Engine, Project
from specto_core.canvas_document import IntakerParams
from specto_core.errors import InvalidArgs
from specto_intake.preview import preview
from specto_intake.sources import resolve_root
from specto_intake.spec import IntakeSpec, SourceSpec

WINDOWS = os.name == "nt"


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
    assert out["ok"] is False and out["files"] == out["item_count"] == 0
    assert not any("host" in p for p in no_reach)


@pytest.mark.skipif(not WINDOWS, reason="nt")
@pytest.mark.parametrize("source", [r"\??\UNC\host\share", "/??/UNC/host/share"])
def test_an_nt_object_spelling_is_refused_as_the_canvas_validator_refuses_it(
    tmp_path: Path, no_reach: list[str], source: str
):
    """The NT object spelling has glob magic (``?``) at its second character; the whole
    text is judged before a glob is split out of it, so the spec refuses it as the same
    device path the canvas validator calls it, not a pattern under the project folder
    (SPEC-79)."""
    with pytest.raises(ValidationError, match="a device path"):
        IntakerParams(sources=[source])
    engine = Engine(project=Project.init(tmp_path / "proj", "demo"))
    out = engine.call("intake.preview", {"spec": {"sources": [source]}})
    assert out["errors"] and "is a device path" in out["errors"][0]
    assert out["ok"] is False and out["files"] == out["item_count"] == 0
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
