"""SPEC-72: an intaker's ``sources`` are judged by their text (``specto_core.pathguard``)
when the document is validated, before the submit asks the disk whether they exist. A
network, device or rooted source written by an agent is refused with a Problem and never
reaches a filesystem call (on Windows a stat of ``\\\\host\\share`` connects out over SMB
and can hand the host the user's sign-in hash)."""

from __future__ import annotations

import os
import pathlib

import pytest

from specto_core import Engine, Project
from specto_core.canvas_document import CanvasInvalid, IntakerParams
from specto_core.unknown_files import source_exists

WINDOWS = os.name == "nt"
#: text no local path of the tests carries: a call that reaches it reached out
SUSPECT = ("host", "\\\\.", "\\??", "/??", "PhysicalDrive")


@pytest.fixture
def no_reach(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every filesystem call keeps working on the project's own paths and fails the test
    on a path that names the fake host or a device; returns the paths it saw."""
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


def intaker(sources: list[str], target: str) -> dict:
    return {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": target, "port": "data"},
        "params": {"sources": sources},
        "summary": {"sentence": "The gallery shows the sources."},
    }


REFUSED = [
    pytest.param(r"\\host\share", "network", marks=pytest.mark.skipif(not WINDOWS, reason="nt")),
    pytest.param(
        r"\\host\share\**\*.jpg", "network", marks=pytest.mark.skipif(not WINDOWS, reason="nt")
    ),
    pytest.param(
        r"\??\UNC\host\share", "device", marks=pytest.mark.skipif(not WINDOWS, reason="nt")
    ),
    pytest.param(
        r"\\.\PhysicalDrive0", "device", marks=pytest.mark.skipif(not WINDOWS, reason="nt")
    ),
    pytest.param(r"\host\x", "rooted", marks=pytest.mark.skipif(not WINDOWS, reason="nt")),
    pytest.param("//host/share/**/*.jpg", "network"),
]


@pytest.mark.parametrize(("source", "why"), REFUSED)
def test_a_submit_with_such_a_source_is_refused_before_any_filesystem_call(
    tmp_path, no_reach: list[str], source: str, why: str
):
    engine = Engine(project=Project.init(tmp_path / "proj", "demo"))
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "kobi",
        "layout": {"position": [400, 0], "size": [600, 400]},
    }
    doc["links"]["l7"] = intaker([".", source], "kobi")

    with pytest.raises(CanvasInvalid) as e:
        engine.live.submit(doc, origin="agent:prompt")

    (problem,) = [p for p in e.value.problems if p["path"].startswith("/links/l7")]
    assert problem["path"] == "/links/l7/params/sources/1"
    assert why in problem["message"] and repr(source) in problem["message"]
    assert "under the project folder" in problem["fix"]
    assert "kobi" not in engine.live.data["views"]
    assert not any("host" in p for p in no_reach)


def test_plain_sources_still_pass():
    assert IntakerParams(sources=["."]).sources == ["."]
    assert IntakerParams(sources=["runs/cam-a", "runs/**/*.jpg", "../shared"]).sources
    if WINDOWS:
        assert IntakerParams(sources=[r"C:\data\runs", "D:/x/**"]).sources


def test_source_exists_never_stats_a_refused_source(tmp_path, no_reach: list[str]):
    """The backstop under the validator: asked directly, the unknown-files check answers
    without touching the disk."""
    assert source_exists(tmp_path, "//host/share") is False
    assert source_exists(tmp_path, "//host/share/**/*.jpg") is False
    assert not any("host" in p for p in no_reach)
