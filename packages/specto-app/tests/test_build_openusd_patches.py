"""tools/parked/build_openusd.py applies the repository's OpenUSD patches to its clone:
each once (a rerun finds it applied and goes on), and one that no longer fits the source
stops the build instead of building without it. The patches themselves apply to the
pinned tag (checked against a real clone when one is at the default place)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "tools" / "parked"
_spec = importlib.util.spec_from_file_location("build_openusd", TOOLS / "build_openusd.py")
assert _spec is not None and _spec.loader is not None
build_openusd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_openusd)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q")
    git(src, "config", "user.email", "t@example.com")
    git(src, "config", "user.name", "t")
    (src / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    git(src, "add", ".")
    git(src, "commit", "-qm", "init")
    return src


def patch_for(repo: Path, new_text: str, patches: Path) -> Path:
    (repo / "a.txt").write_text(new_text, encoding="utf-8")
    diff = git(repo, "diff")
    git(repo, "checkout", "-q", "--", "a.txt")
    patches.mkdir(exist_ok=True)
    out = patches / "0001-a.patch"
    out.write_text(diff, encoding="utf-8")
    return out


def test_a_patch_is_applied_once_and_a_rerun_finds_it_applied(repo, tmp_path):
    patches = tmp_path / "patches"
    patch_for(repo, "one\ntwo\nthree\n", patches)
    assert build_openusd.apply_patches(repo, patches) == ["0001-a.patch"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\n"
    assert build_openusd.apply_patches(repo, patches) == []  # already in
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_a_patch_that_no_longer_fits_stops_the_build(repo, tmp_path):
    patches = tmp_path / "patches"
    patch_for(repo, "one\ntwo\nthree\n", patches)
    (repo / "a.txt").write_text("something else entirely\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"0001-a\.patch"):
        build_openusd.apply_patches(repo, patches)


def test_the_repositorys_patches_are_found():
    names = [p.name for p in sorted(build_openusd.PATCHES.glob("*.patch"))]
    assert "0001-hgimetal-release-on-destroy.patch" in names


@pytest.mark.skipif(
    not (build_openusd.SRC / ".git").exists(), reason="no OpenUSD clone at the default place"
)
def test_the_patches_fit_the_pinned_tag():
    for patch in sorted(build_openusd.PATCHES.glob("*.patch")):
        forward = subprocess.run(
            ["git", "-C", str(build_openusd.SRC), "apply", "--check", str(patch)],
            capture_output=True,
        )
        reverse = subprocess.run(
            ["git", "-C", str(build_openusd.SRC), "apply", "--reverse", "--check", str(patch)],
            capture_output=True,
        )
        assert forward.returncode == 0 or reverse.returncode == 0, patch.name
