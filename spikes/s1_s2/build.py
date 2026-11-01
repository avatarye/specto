"""Build the native Qt Quick module (spikes/s1_s2/native) against the Qt SDK that
matches the PySide6 wheel in this venv, and install it to spikes/s1_s2/qml/.

Reproducible from a clean machine (Windows 11, MSVC 2022 Build Tools 17.14,
CMake >= 3.21, uv):

    uv sync                                             # PySide6 6.12.0 into .venv
    uv tool run --from git+https://github.com/miurahr/aqtinstall aqt \
        install-qt windows desktop 6.12.0 win64_msvc2022_64 \
        -O ~/specto-build/Qt --archives qtbase qtdeclarative qtshadertools
    .venv/Scripts/python spikes/s1_s2/build.py

aqtinstall 3.3.0 (PyPI) does not know the 6.12 repository layout
(qt6_6120/qt6_6120_msvc2022_64); git master (3.3.1.dev166 at the time) does.

The script refuses to build if the SDK version differs from the PySide6 wheel's
Qt version: a QML plugin must match the Qt that will load it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NATIVE = HERE / "native"
BUILD = HERE / "build"
INSTALL_QML = HERE / "qml"
DEFAULT_QT_ROOT = Path(os.environ.get("SPECTO_QT_ROOT", str(Path.home() / "specto-build" / "Qt")))


def pyside_qt_version() -> str:
    from PySide6.QtCore import qVersion

    return qVersion()


def find_qt_sdk(qt_root: Path, version: str) -> Path:
    sdk = qt_root / version / "msvc2022_64"
    if not (sdk / "lib" / "cmake" / "Qt6" / "Qt6Config.cmake").exists():
        sys.exit(
            f"[ERROR] Qt SDK {version} msvc2022_64 not found under {qt_root}.\n"
            "        Install it with aqtinstall (see module docstring)."
        )
    return sdk


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


def run_in_vs_env(vcvars: Path, cmd: list[str], cwd: Path) -> None:
    # A throwaway .bat avoids cmd.exe's quoting rules for `cmd /c "..."`.
    quoted = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    bat = BUILD / "_run.bat"
    bat.write_text(
        f'@echo off\r\ncall "{vcvars}" >nul\r\nif errorlevel 1 exit /b 1\r\n{quoted}\r\n'
    )
    print(f"[RUN] {quoted}")
    subprocess.check_call(["cmd.exe", "/c", str(bat)], cwd=str(cwd))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--qt-root", type=Path, default=DEFAULT_QT_ROOT)
    ap.add_argument("--config", default="Release", choices=["Release", "RelWithDebInfo"])
    ap.add_argument("--clean", action="store_true", help="wipe the build dir first")
    args = ap.parse_args()

    version = pyside_qt_version()
    sdk = find_qt_sdk(args.qt_root, version)
    vcvars = find_vcvars()
    print(f"[OK] PySide6 Qt {version}; SDK {sdk}")

    if args.clean and BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True, exist_ok=True)

    run_in_vs_env(
        vcvars,
        [
            "cmake",
            "-G",
            "Ninja",
            "-S",
            str(NATIVE),
            "-B",
            str(BUILD),
            f"-DCMAKE_BUILD_TYPE={args.config}",
            f"-DCMAKE_PREFIX_PATH={sdk.as_posix()}",
            f"-DCMAKE_INSTALL_PREFIX={HERE.as_posix()}",
        ],
        cwd=HERE,
    )
    run_in_vs_env(vcvars, ["cmake", "--build", str(BUILD), "--config", args.config], cwd=HERE)

    # Install just the QML module tree: spikes/s1_s2/qml/Specto/Spike/{spectospike.dll,qmldir,...}
    src = BUILD / "qml"
    if INSTALL_QML.exists():
        shutil.rmtree(INSTALL_QML)
    shutil.copytree(src, INSTALL_QML)
    mod = INSTALL_QML / "Specto" / "Spike"
    print("[OK] installed:", ", ".join(p.name for p in sorted(mod.iterdir())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
