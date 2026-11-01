"""Build OpenUSD with imaging (Storm, GL) and usdview for the Storm half of spike S1.

Everything lives outside the repo (HLD section 10 parks Storm until this build
exists); nothing here is imported by Specto:

    ~/specto-build/openusd-src     shallow clone of the release tag
    ~/specto-build/openusd-venv    uv venv, Python 3.12, PySide6 pinned to the
                                       workspace wheel (uv.lock), PyOpenGL, Jinja2
    ~/specto-build/openusd-build   build_usd.py --build (per-dependency trees)
    ~/specto-build/openusd-deps    build_usd.py --src (downloaded archives)
    ~/specto-build/openusd         install prefix (bin, lib; pxr in Lib/site-packages,
                                       lib/python3.12/site-packages elsewhere)
    ~/specto-build/openusd-build.log

``SPECTO_BUILD_ROOT`` moves the root on every platform.

Reproducible from a clean machine (Windows 11, MSVC 2022 Build Tools 17.14,
CMake >= 3.27, uv, git):

    .venv/Scripts/python tools/build_openusd.py      # stdlib only; any Python 3.10+

or on Linux (Ubuntu 24.04: g++, CMake >= 3.27, uv, git and the GL/X11 headers,
``libgl-dev libglu1-mesa-dev libx11-dev libxt-dev libxrandr-dev``; run with the ROS
environment unset if it is sourced, its CMAKE_PREFIX_PATH and PYTHONPATH leak in):

    .venv/bin/python tools/parked/build_openusd.py

or on macOS (Xcode Command Line Tools, enough without Xcode: a shim answers build_usd.py's
``xcodebuild -version``; CMake >= 3.27 from Homebrew, uv, git; Storm draws
through Metal and hands the image to the GL context; ~/specto-build may be a symlink to
a roomier volume, the build trees take several GB):

    python3 tools/parked/build_openusd.py

Run it from PowerShell or cmd, not Git Bash: bash cannot export ProgramFiles(x86),
and vcvars64.bat needs it to find vswhere.exe.

The venv is separate from the workspace .venv; the usdview Python bindings are
built against the venv's interpreter (build_usd.py reads sys.executable), so the
Python running build_usd.py must be the one that will import pxr. The interpreter
version matches Specto's runtime (3.12) so a later in-process StormItem can share
one process with PySide6 6.12.0.

build_usd.py chooses "Visual Studio 17 2022 -A x64" by finding cl.exe on PATH;
it is run from a throwaway .bat that calls vcvars64.bat first (the pattern in
spikes/s1_s2/build.py). Rerunning the script is incremental: the clone, venv and
each finished dependency are skipped.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TAG = "v26.08"
REPO = "https://github.com/PixarAnimationStudios/OpenUSD.git"
PYTHON = "3.12"
# PySide6 pinned to the workspace wheel (uv.lock); PyOpenGL and Jinja2 are what
# build_usd.py checks for before it agrees to build usdview.
VENV_PACKAGES = ["PySide6==6.12.0", "PyOpenGL", "Jinja2"]

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
DEFAULT_ROOT = str(Path.home() / "specto-build")
ROOT = Path(os.environ.get("SPECTO_BUILD_ROOT", DEFAULT_ROOT))
SRC = ROOT / "openusd-src"
VENV = ROOT / "openusd-venv"
BUILD = ROOT / "openusd-build"
DEPS = ROOT / "openusd-deps"
INST = ROOT / "openusd"
LOG = ROOT / "openusd-build.log"


def build_usd_args(jobs: int, python_info: list[str]) -> list[str]:
    # Imaging + usdview (Storm, GL), Python bindings, release, no examples /
    # tutorials / docs / tests. MaterialX stays at its default (on): Storm's
    # usdview material path uses it and it is what a user build has.
    return [
        "--build-variant",
        "release",
        "--usd-imaging",  # implies --imaging; the two flags are exclusive
        "--usdview",
        "--python",
        "--build-python-info",
        *python_info,
        "--no-examples",
        "--no-tutorials",
        "--no-docs",
        "--no-tests",
        "--no-vulkan",
        "--jobs",
        str(jobs),
        "--build",
        str(BUILD),
        "--src",
        str(DEPS),
        str(INST),
    ]


# build_usd.py's own GetPythonInfo takes sysconfig's "include" path, which in a
# venv on Windows is <venv>\Include (no Python.h). Give it the base install's
# headers and import library explicitly: EXECUTABLE INCLUDE_DIR LIBRARY VERSION.
PYTHON_INFO_SNIPPET = r"""
import sys, sysconfig, os
base = sysconfig.get_config_var("installed_base")
print(sys.executable)
print(os.path.join(base, "include"))
print(os.path.join(base, "libs", "python%s.lib" % sysconfig.get_config_var("py_version_nodot")))
print(sysconfig.get_config_var("py_version_short"))
"""
# Elsewhere sysconfig points at the base install's headers and shared libpython directly.
PYTHON_INFO_SNIPPET_POSIX = r"""
import sys, sysconfig, os
print(sys.executable)
print(sysconfig.get_config_var("INCLUDEPY"))
print(os.path.join(sysconfig.get_config_var("LIBDIR"), sysconfig.get_config_var("LDLIBRARY")))
print(sysconfig.get_config_var("py_version_short"))
"""


def python_info(python: Path) -> list[str]:
    snippet = PYTHON_INFO_SNIPPET if WINDOWS else PYTHON_INFO_SNIPPET_POSIX
    info = subprocess.check_output([str(python), "-c", snippet], text=True).split()
    for p in info[:3]:
        if not Path(p).exists():
            sys.exit(f"[ERROR] python build input missing: {p}")
    return info


def find_vcvars() -> Path:
    vswhere = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    vswhere = vswhere / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        sys.exit("[ERROR] vswhere.exe not found; install MSVC 2022 Build Tools")
    out = subprocess.check_output(
        [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
        text=True,
    ).strip()
    vcvars = Path(out) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.exists():
        sys.exit(f"[ERROR] vcvars64.bat not found at {vcvars}")
    return vcvars


def ensure_clone() -> None:
    if (SRC / "build_scripts" / "build_usd.py").exists():
        tag = subprocess.check_output(
            ["git", "-C", str(SRC), "describe", "--tags", "--exact-match"], text=True
        ).strip()
        if tag != TAG:
            sys.exit(f"[ERROR] {SRC} is at {tag}, expected {TAG}; move it aside")
        print(f"[OK] source {SRC} at {TAG}")
        return
    print(f"[RUN] git clone --depth 1 --branch {TAG} {REPO} {SRC}")
    subprocess.check_call(["git", "clone", "--depth", "1", "--branch", TAG, REPO, str(SRC)])


def ensure_venv() -> Path:
    python = VENV / "Scripts" / "python.exe" if WINDOWS else VENV / "bin" / "python"
    if not python.exists():
        print(f"[RUN] uv venv --python {PYTHON} {VENV}")
        # macOS: uv's own CPython (libpython3.12.dylib), as the workspace .venv uses; a
        # conda 3.12 on PATH ships only the static libpython3.12.a its sysconfig names
        managed = ["--python-preference", "only-managed"] if MACOS else []
        cmd = ["uv", "venv", "--no-config", *managed, "--python", PYTHON, str(VENV)]
        subprocess.check_call(cmd)
    print(f"[RUN] uv pip install {' '.join(VENV_PACKAGES)}")
    # public PyPI only: --no-config keeps a user-level uv.toml's private index out
    cmd = ["uv", "pip", "install", "--no-config", "--python", str(python), *VENV_PACKAGES]
    subprocess.check_call(cmd)
    ver = subprocess.check_output([str(python), "--version"], text=True).strip()
    print(f"[OK] venv {VENV}: {ver}")
    return python


def write_bat(vcvars: Path, python: Path, jobs: int) -> Path:
    # A throwaway .bat avoids cmd.exe's quoting rules for `cmd /c "..."`.
    BUILD.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python),
        str(SRC / "build_scripts" / "build_usd.py"),
        *build_usd_args(jobs, python_info(python)),
    ]
    quoted = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    bat = BUILD / "_build.bat"
    bat.write_text(
        "@echo off\r\n"
        f'call "{vcvars}" >nul\r\n'
        "if errorlevel 1 exit /b 1\r\n"
        f"set VIRTUAL_ENV={VENV}\r\n"
        f"set PATH={VENV / 'Scripts'};%PATH%\r\n"
        f"{quoted}\r\n"
    )
    print(f"[RUN] {quoted}")
    return bat


def xcodebuild_shim() -> Path | None:
    """build_usd.py reads ``xcodebuild -version`` (apple_utils.GetXcodeVersion), which
    fails with only the Command Line Tools installed. Then a build-only shim answers it
    with the CLT's version (the compile itself uses the CLT's clang++); None when Xcode
    is there."""
    ok = subprocess.run(["xcodebuild", "-version"], capture_output=True, check=False)
    if ok.returncode == 0:
        return None
    info = subprocess.run(
        ["pkgutil", "--pkg-info=com.apple.pkg.CLTools_Executables"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    version = next(
        (ln.split(":", 1)[1].strip() for ln in info.splitlines() if ln.startswith("version:")),
        "",
    )
    if not version:
        sys.exit("[ERROR] neither Xcode nor the Command Line Tools: xcode-select --install")
    short = ".".join(version.split(".")[:2])
    shim = BUILD / "xcshim"
    shim.mkdir(parents=True, exist_ok=True)
    (shim / "xcodebuild").write_text(
        "#!/bin/sh\n"
        "# build-only shim (build_openusd.py): Command Line Tools, no Xcode\n"
        f'if [ "$1" = "-version" ]; then echo "Xcode {short}"; echo "Build version CLT"; '
        "exit 0; fi\n"
        'echo "xcodebuild shim: only -version" >&2; exit 1\n'
    )
    (shim / "xcodebuild").chmod(0o755)
    print(f"[WARN] no Xcode: xcodebuild -version answered by {shim} (CLT {short})")
    return shim


def posix_command(python: Path, jobs: int) -> tuple[list[str], dict[str, str]]:
    """build_usd.py run directly (g++ from PATH), with the venv first on PATH; the
    environment drops CMAKE_PREFIX_PATH / PYTHONPATH a sourced ROS setup leaks in."""
    BUILD.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python),
        str(SRC / "build_scripts" / "build_usd.py"),
        *build_usd_args(jobs, python_info(python)),
    ]
    env = {k: v for k, v in os.environ.items() if k not in ("CMAKE_PREFIX_PATH", "PYTHONPATH")}
    env["VIRTUAL_ENV"] = str(VENV)
    shim = xcodebuild_shim() if MACOS else None
    first = [str(VENV / "bin"), *([str(shim)] if shim else [])]
    env["PATH"] = os.pathsep.join([*first, env.get("PATH", "")])
    print(f"[RUN] {' '.join(cmd)}")
    return cmd, env


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 8) - 4),
        help="parallel build jobs (default: cores - 4, keeps the machine usable)",
    )
    ap.add_argument("--clean", action="store_true", help="wipe build/deps/install first")
    ap.add_argument(
        "--print-only",
        action="store_true",
        help="write the .bat and print the build_usd.py command without running it",
    )
    args = ap.parse_args()

    ensure_clone()
    python = ensure_venv()
    vcvars = find_vcvars() if WINDOWS else None

    if args.clean:
        for d in (BUILD, DEPS, INST):
            if d.exists():
                print(f"[RUN] rmtree {d}")
                shutil.rmtree(d)

    if WINDOWS:
        bat = write_bat(vcvars, python, args.jobs)
        cmd, env = ["cmd.exe", "/c", str(bat)], None
    else:
        cmd, env = posix_command(python, args.jobs)
    if args.print_only:
        return 0

    print(f"[RUN] logging to {LOG}")
    t0 = time.monotonic()
    with LOG.open("ab") as log:
        log.write(f"==== build_openusd.py {TAG} jobs={args.jobs} ====\n".encode())
        log.flush()
        rc = subprocess.call(cmd, cwd=str(BUILD), env=env, stdout=log, stderr=subprocess.STDOUT)
    minutes = (time.monotonic() - t0) / 60
    status = "OK" if rc == 0 else "ERROR"
    print(f"[{status}] build_usd.py exit {rc} after {minutes:.1f} min; log {LOG}")
    if rc == 0:
        print(f"[OK] installed to {INST}")
        if WINDOWS:
            print(f"     PATH += {INST / 'bin'};{INST / 'lib'}")
        site = (
            INST / "Lib" / "site-packages"
            if WINDOWS
            else INST / "lib" / f"python{PYTHON}" / "site-packages"
        )
        print(f"     PYTHONPATH += {site}  (Specto finds it: specto_core.machine)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
