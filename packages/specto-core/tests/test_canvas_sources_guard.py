"""SPEC-72: an intaker's ``sources`` are judged by their text (``specto_core.pathguard``)
when the document is validated, before the submit asks the disk whether they exist. A
network, device or rooted source written by an agent is refused with a Problem and never
reaches a filesystem call (on Windows a stat of ``\\\\host\\share`` connects out over SMB
and can hand the host the user's sign-in hash)."""

from __future__ import annotations

import os

import pytest

from specto_core import Engine, Project
from specto_core.canvas_document import SOURCE_FIX, CanvasInvalid, IntakerParams
from specto_core.unknown_files import source_exists

WINDOWS = os.name == "nt"


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
    plain = ["runs/cam-a", "runs/**/*.jpg", "../shared"]
    assert IntakerParams(sources=plain).sources == plain
    if WINDOWS:
        drive = [r"C:\data\runs", "D:/x/**"]
        assert IntakerParams(sources=drive).sources == drive


def test_the_fix_and_the_description_name_a_local_drive_path_as_accepted():
    """The validator accepts a local drive path, so the fix-it and the agent-facing
    description say so (SPEC-79)."""
    assert "local drive path" in SOURCE_FIX
    assert "local drive" in IntakerParams.model_fields["sources"].description


def test_an_absolute_drive_glob_source_submits(tmp_path, no_reach: list[str]):
    """``Path.glob`` refuses a non-relative pattern with ``NotImplementedError``; an
    absolute glob source is looked up from its own anchor instead, so the submit neither
    raises nor reports a matching glob as missing (SPEC-79)."""
    data = tmp_path / "data" / "cam"
    data.mkdir(parents=True)
    (data / "a.jpg").write_bytes(b"x")
    engine = Engine(project=Project.init(tmp_path / "proj", "demo"))
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "kobi",
        "layout": {"position": [400, 0], "size": [600, 400]},
    }
    found = f"{tmp_path.as_posix()}/data/**/*.jpg"
    doc["links"]["l7"] = intaker([found], "kobi")

    engine.live.submit(doc, origin="agent:prompt")

    assert "kobi" in engine.live.data["views"]
    assert source_exists(tmp_path, found) is True
    assert source_exists(tmp_path, f"{tmp_path.as_posix()}/data/**/*.png") is False
    assert source_exists(tmp_path, str(tmp_path / "data" / "**" / "*.jpg")) is True


def test_source_exists_never_stats_a_refused_source(tmp_path, no_reach: list[str]):
    """The backstop under the validator: asked directly, the unknown-files check answers
    without touching the disk."""
    assert source_exists(tmp_path, "//host/share") is False
    assert source_exists(tmp_path, "//host/share/**/*.jpg") is False
    assert not any("host" in p for p in no_reach)
