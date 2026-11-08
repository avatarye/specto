"""SPEC-90: an absolute local source means the same thing at submit and at run time.

The submit's unknown-files check (``specto_core.unknown_files.source_exists``) and the
intake spec (``SourceSpec.coerce``, which runtime intake builds from the same
``params.sources``) both split a source into a literal base and a glob. A long-path
spelling (``\\\\?\\C:\\...``) carries a ``?`` in its prefix that is no glob, and a
drive-root glob (``C:/**/*.x``) has the drive's root as its base, not ``C:``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from intake_helpers import ALIASES, EXT_KINDS

from specto_core.canvas_document import IntakerParams
from specto_core.unknown_files import source_exists
from specto_intake.preview import preview
from specto_intake.spec import SourceSpec

WINDOWS = os.name == "nt"
nt_only = pytest.mark.skipif(not WINDOWS, reason="drive and long-path spellings are Windows")


def long_path(p: Path) -> str:
    return "\\\\?\\" + str(p)


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / "cam").mkdir(parents=True)
    (d / "cam" / "x1.png").write_bytes(b"x")
    (d / "cam" / "x2.png").write_bytes(b"x")
    return d


@nt_only
def test_an_existing_file_named_by_its_long_path_is_found(tmp_path: Path, data: Path):
    """The ``?`` of the long-path prefix is not glob magic: the file exists."""
    assert source_exists(tmp_path, long_path(data / "cam" / "x1.png")) is True
    assert source_exists(tmp_path, long_path(data / "cam")) is True
    assert source_exists(tmp_path, long_path(data / "cam" / "nope.png")) is False


@nt_only
def test_a_long_path_glob_is_judged_by_its_base_folder(tmp_path: Path, data: Path):
    assert source_exists(tmp_path, long_path(data / "**" / "*.png")) is True
    assert source_exists(tmp_path, long_path(tmp_path / "nowhere" / "*.png")) is False


@nt_only
@pytest.mark.parametrize(
    ("source", "root", "pattern"),
    [
        ("C:/**/*.x", "C:/", "**/*.x"),
        ("C:\\**\\*.x", "C:/", "**/*.x"),
        ("C:/*.x", "C:/", "*.x"),
        ("C:/data/**/*.jpg", "C:/data", "**/*.jpg"),
        ("\\\\?\\C:\\data\\*", "C:/data", "*"),
        ("//?/C:/data/*", "C:/data", "*"),
        ("\\\\?\\C:\\**\\*.x", "C:/", "**/*.x"),
        ("\\\\?\\C:\\data", "C:/data", None),
    ],
)
def test_coerce_keeps_the_anchor_as_the_root(source: str, root: str, pattern: str | None):
    """A drive-root glob's base is the drive's root, ``C:/``; ``C:`` alone is a
    drive-relative path the guard refuses. A long path's prefix is stripped first."""
    spec = SourceSpec.coerce(source)
    assert (spec.root, spec.pattern) == (root, pattern)


@pytest.mark.skipif(WINDOWS, reason="POSIX root")
def test_coerce_keeps_the_posix_root_as_the_root():
    spec = SourceSpec.coerce("/**/*.x")
    assert (spec.root, spec.pattern) == ("/", "**/*.x")


ABSOLUTE = [
    "C:/**/*.x",
    "C:\\**\\*.x",
    "C:/*.x",
    "D:/data/**/*.jpg",
    "\\\\?\\C:\\data\\*",
    "\\\\?\\C:\\Windows\\notepad.exe",
    "//?/C:/data/*",
]


@nt_only
@pytest.mark.parametrize("source", ABSOLUTE)
def test_what_submit_accepts_the_intake_spec_accepts(source: str):
    """Runtime intake builds its spec from the same ``params.sources`` the submit
    validated; a source that passes one must pass the other."""
    IntakerParams(sources=[source])
    SourceSpec.coerce(source)


@nt_only
def test_a_long_path_glob_intakes_at_run_time(tmp_path: Path, data: Path):
    """A real intake run (``preview`` walks the sources as the runtime does) over a
    long-path glob finds the files the submit said exist."""
    source = long_path(data / "**" / "*.png")
    assert source_exists(tmp_path, source) is True
    out = preview({"sources": [source]}, base=tmp_path, aliases=ALIASES, ext_kinds=EXT_KINDS)
    assert out["errors"] == []
    assert out["files"] == 2


@nt_only
def test_a_drive_root_glob_intakes_at_run_time(tmp_path: Path, data: Path, monkeypatch):
    """A drive-root glob that passes submit walks from the drive's root at run time.
    The walk is pointed at the test's folder (``resolve_root`` maps ``C:/`` there) so it
    stays small; what is checked is that the spec is built and walked, not refused."""
    import importlib

    from specto_intake import sources

    preview_mod = importlib.import_module("specto_intake.preview")

    drive = data.drive + "/"
    real = sources.resolve_root
    seen: list[str] = []

    def to_data(src, base):
        seen.append(src.root)
        if src.root == drive:
            return data
        return real(src, base)

    monkeypatch.setattr(sources, "resolve_root", to_data)
    monkeypatch.setattr(preview_mod, "resolve_root", to_data)
    source = f"{drive}**/*.png"
    IntakerParams(sources=[source])
    assert source_exists(tmp_path, source) is True
    out = preview({"sources": [source]}, base=tmp_path, aliases=ALIASES, ext_kinds=EXT_KINDS)
    assert out["errors"] == []
    assert seen and set(seen) == {drive}
    assert out["files"] == 2
