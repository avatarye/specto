"""The shared path guard (SPEC-61): a path's text is judged before any filesystem call,
by an allowlist (relative; a Windows drive path; a POSIX single-slash path), so a xacro
include or an items-host request can never make the machine connect out (UNC, NT object
paths, file://host: Windows would send the user's NTLM hash) or open a device or an
alternate data stream."""

from __future__ import annotations

import os

import pytest

from specto_core.pathguard import Refusal, file_uri_path, path_refusal, strip_local_long, under

WINDOWS = os.name == "nt"


@pytest.mark.skipif(not WINDOWS, reason="Windows path forms")
@pytest.mark.parametrize(
    ("text", "why"),
    [
        (r"\\host\share\robot.stl", Refusal.NETWORK),
        ("//host/share/robot.stl", Refusal.NETWORK),
        (r"\??\UNC\host\share\x.stl", Refusal.DEVICE),
        ("/??/UNC/host/share/x.stl", Refusal.DEVICE),
        (r"\??\C:\Windows\x.stl", Refusal.DEVICE),
        (r"\\?\UNC\host\share\x.stl", Refusal.DEVICE),
        ("//?/UNC/host/share/x.stl", Refusal.DEVICE),
        (r"\\.\PhysicalDrive0", Refusal.DEVICE),
        (r"\\?\GLOBALROOT\Device\x", Refusal.DEVICE),
        (r"\Device\HarddiskVolume1\x.stl", Refusal.ROOTED),
        (r"\work\robot.stl", Refusal.ROOTED),
        ("/work/robot.stl", Refusal.ROOTED),
        ("C:robot.stl", Refusal.ROOTED),
        (r"C:\work\NUL", Refusal.DEVICE_NAME),
        (r"meshes\com1.stl", Refusal.DEVICE_NAME),
        (r"C:\work\secret.txt:x.png", Refusal.STREAM),
        ("secret.txt:x.png", Refusal.STREAM),
    ],
)
def test_every_rooted_form_but_a_drive_path_is_refused(text: str, why: Refusal):
    assert path_refusal(text) == why
    assert path_refusal(text, unc_ok=True) == (None if why == Refusal.NETWORK else why)


@pytest.mark.skipif(not WINDOWS, reason="Windows path forms")
def test_relative_and_drive_paths_pass():
    for ok in (r"C:\work\robot.stl", "C:/x/y.png", "meshes/robot.stl", r"..\meshes\a.obj", "a"):
        assert path_refusal(ok) is None, ok
    assert strip_local_long(r"\\?\C:\work\a.stl") == r"C:\work\a.stl"
    assert path_refusal(r"\\?\C:\work\a.stl") is None
    assert path_refusal("//?/C:/work/a.stl") is None


@pytest.mark.skipif(WINDOWS, reason="POSIX path forms")
def test_posix_forms():
    assert path_refusal("/home/u/robot.stl") is None and path_refusal("meshes/a.stl") is None
    assert path_refusal("//host/share/x") == Refusal.NETWORK
    assert path_refusal("/dev/zero") == Refusal.DEVICE


def test_file_uris_never_name_a_host_or_an_object_path():
    if WINDOWS:
        assert file_uri_path("file:///C:/work/a.stl") == ("C:/work/a.stl", None)
        assert file_uri_path("file://localhost/C:/work/a.stl") == ("C:/work/a.stl", None)
        assert file_uri_path("file:///%3F%3F/UNC/host/share/a.stl") == (None, Refusal.DEVICE)
        assert file_uri_path("file:///%5C%5Chost/share/a.stl")[0] is None
    assert file_uri_path("file://host/share/a.stl") == (None, Refusal.NETWORK)
    assert file_uri_path("file:////host/share/a.stl") == (None, Refusal.NETWORK)
    assert file_uri_path("file://%5C%5Chost/a.stl") == (None, Refusal.NETWORK)


def test_a_reason_reads_as_words():
    assert f"{Refusal.NETWORK} is never read" == "a network path is never read"


def test_under_is_a_text_comparison():
    root = "C:/proj" if WINDOWS else "/proj"
    assert under(f"{root}/a/b.stl", [root]) and under(root, [root])
    assert not under(f"{root}x/b.stl", [root])


def test_the_guard_makes_no_filesystem_call(monkeypatch: pytest.MonkeyPatch):
    def boom(*_a, **_k):
        raise AssertionError("a filesystem call")

    import pathlib

    for name in ("stat", "resolve", "exists", "is_file", "is_dir", "open"):
        monkeypatch.setattr(pathlib.Path, name, boom)
    for name in ("stat", "lstat", "open", "listdir", "scandir"):
        monkeypatch.setattr(os, name, boom)
    monkeypatch.setattr(os.path, "exists", boom)
    for text in (
        r"\\host\share\a.stl",
        r"\??\UNC\host\a",
        "/??/UNC/host/a",
        r"\\.\x",
        "a.txt:b.png",
        "a.stl",
        "NUL",
    ):
        path_refusal(text)
    file_uri_path("file://host/share/a.stl")
    file_uri_path("file:///%3F%3F/UNC/host/a.stl")
