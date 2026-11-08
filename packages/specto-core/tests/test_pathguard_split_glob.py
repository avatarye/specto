"""SPEC-94: one glob splitter (``pathguard.split_glob``) for the submit's unknown-files
check and the intake spec, which drifted apart once (SPEC-90). Text only."""

from __future__ import annotations

import os

import pytest

from specto_core import pathguard, unknown_files
from specto_core.pathguard import split_glob

WINDOWS = os.name == "nt"
nt_only = pytest.mark.skipif(not WINDOWS, reason="drive and long-path spellings are Windows")

ANY = [
    (".", (".", None)),
    ("runs", ("runs", None)),
    ("runs/cam-a/", ("runs/cam-a/", None)),
    ("runs\\cam-a", ("runs/cam-a", None)),
    ("*.jpg", (".", "*.jpg")),
    ("runs/**/*.jpg", ("runs", "**/*.jpg")),
    ("runs/cam-[ab]/x.png", ("runs", "cam-[ab]/x.png")),
    ("a/b?/c", ("a", "b?/c")),
]
NT = [
    ("C:/data/**/*.jpg", ("C:/data", "**/*.jpg")),
    ("C:\\data\\*.jpg", ("C:/data", "*.jpg")),
    ("C:/**/*.x", ("C:/", "**/*.x")),
    ("C:/*.x", ("C:/", "*.x")),
    ("C:/data", ("C:/data", None)),
    ("\\\\?\\C:\\data\\x1.png", ("C:/data/x1.png", None)),
    ("\\\\?\\C:\\data\\*", ("C:/data", "*")),
    ("//?/C:/**/*.x", ("C:/", "**/*.x")),
]


@pytest.mark.parametrize(("text", "want"), ANY)
def test_split_glob(text: str, want: tuple[str, str | None]):
    assert split_glob(text) == want


@nt_only
@pytest.mark.parametrize(("text", "want"), NT)
def test_split_glob_keeps_the_anchor_and_strips_the_long_prefix(
    text: str, want: tuple[str, str | None]
):
    assert split_glob(text) == want


@pytest.mark.skipif(WINDOWS, reason="POSIX root")
def test_split_glob_keeps_the_posix_root():
    assert split_glob("/**/*.x") == ("/", "**/*.x")
    assert split_glob("/data/*.x") == ("/data", "*.x")


def test_the_unknown_files_check_and_the_intake_spec_use_the_one_splitter():
    from specto_intake import spec

    assert unknown_files.split_glob is pathguard.split_glob
    assert spec.split_glob is pathguard.split_glob
