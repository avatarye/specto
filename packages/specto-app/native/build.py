"""Build the native Qt Quick module `Specto.Native` (packages/specto-app/native) against
the Qt SDK that matches the PySide6 wheel in this venv, and install it to
packages/specto-app/src/specto_app/qml/Specto/Native (git-ignored).

Reproducible from a clean machine (Windows 11, MSVC 2022 Build Tools, CMake >= 3.21,
Ninja, uv), adapted from spikes/s1_s2/build.py:

    uv sync                                             # PySide6 into .venv
    uv tool run --from git+https://github.com/miurahr/aqtinstall aqt \
        install-qt windows desktop 6.12.0 win64_msvc2022_64 \
        -O ~/specto-build/Qt --archives qtbase qtdeclarative
    uv run python packages/specto-app/native/build.py

Linux (Ubuntu 24.04, g++ >= 11, CMake >= 3.21, Ninja optional - Makefiles otherwise, the
OpenGL headers from libgl-dev; the SDK goes to ~/specto-build/Qt; SPECTO_BUILD_ROOT moves it):

    uv sync
    uv tool run --no-config --from git+https://github.com/miurahr/aqtinstall aqt \
        install-qt linux desktop 6.12.0 linux_gcc_64 \
        -O ~/specto-build/Qt --archives qtbase qtdeclarative icu
    sudo apt install libmpv-dev        # or, without root: uv run python tools/fetch_libmpv.py
    uv run python packages/specto-app/native/build.py

On Linux the plugin's RUNPATH is the PySide6 wheel's ``Qt/lib`` (never the SDK's), so it
binds to the very Qt libraries the Python process loads. A shell that exports another
toolchain's CMAKE_PREFIX_PATH (ROS, conda) is ignored: cmake runs without it.

Shaders (native/shaders/*.vert, *.frag) are compiled to .qsb with the `qsb` tool that
ships inside the PySide6 wheel, so the SDK's qtshadertools archive is not required.

The script refuses to build if the SDK version differs from the PySide6 wheel's Qt
version: a QML plugin must match the Qt that will load it.

libmpv (the video view's MpvItem) is fetched by ``tools/fetch_libmpv.py`` into
~/specto-build/libmpv (or SPECTO_LIBMPV_DIR); only its headers are compiled in and
the DLL is copied next to the plugin, where mpvapi.cpp loads it at runtime. On Linux a
fetched ~/specto-build/libmpv wins over the system's /usr/include/mpv and is copied
the same way (libmpv.so.2 with the libraries the fetch bundled, RUNPATH $ORIGIN); a
system libmpv stays where apt put it and is found on the loader path.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
BUILD = HERE / "build"
QSB_DIR = BUILD / "shaders"
INSTALL_QML = PKG / "src" / "specto_app" / "qml"
MODULE_DIR = INSTALL_QML / "Specto" / "Native"
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
BUILD_ROOT = Path(os.environ.get("SPECTO_BUILD_ROOT", str(Path.home() / "specto-build")))
DEFAULT_QT_ROOT = Path(os.environ.get("SPECTO_QT_ROOT", str(BUILD_ROOT / "Qt")))
DEFAULT_LIBMPV = Path(os.environ.get("SPECTO_LIBMPV_DIR", str(BUILD_ROOT / "libmpv")))
# libmpv-dev: /usr/include/mpv, libmpv.so.2 on the loader path; Homebrew's mpv on macOS:
# include/mpv and lib/libmpv.2.dylib under its prefix (mpvapi.cpp also tries <dir>/lib)
SYSTEM_LIBMPV = Path("/opt/homebrew" if MACOS else "/usr")
LIBMPV_DLL = "libmpv-2.dll" if WINDOWS else "libmpv.2.dylib" if MACOS else "libmpv.so.2"
QT_ARCH = "msvc2022_64" if WINDOWS else "macos" if MACOS else "gcc_64"
FETCH_HINT = "uv run python tools/fetch_libmpv.py"
SHADERS = ["tile.vert", "tile.frag"]


def pyside_qt_version() -> str:
    from PySide6.QtCore import qVersion

    return qVersion()


def find_qt_sdk(qt_root: Path, version: str) -> Path:
    sdk = qt_root / version / QT_ARCH
    if not (sdk / "lib" / "cmake" / "Qt6" / "Qt6Config.cmake").exists():
        sys.exit(
            f"[ERROR] Qt SDK {version} {QT_ARCH} not found under {qt_root}.\n"
            "        Install it with aqtinstall (see module docstring)."
        )
    return sdk


def find_libmpv(libmpv: Path) -> Path:
    header = Path("include") / "mpv" / "render_gl.h"
    fetched = (libmpv / header).exists()
    if not WINDOWS and not fetched and (SYSTEM_LIBMPV / header).exists():
        return SYSTEM_LIBMPV  # the loader finds libmpv.so.2 at runtime
    if not fetched:
        system = "; or brew install mpv)" if MACOS else "; or sudo apt install libmpv-dev)"
        sys.exit(
            f"[ERROR] libmpv headers not found under {libmpv}.\n"
            f"        Fetch them with: {FETCH_HINT}  (or set SPECTO_LIBMPV_DIR"
            + (")" if WINDOWS else system)
        )
    if not (libmpv / LIBMPV_DLL).exists():
        print(f"[WARN] {libmpv / LIBMPV_DLL} missing: the video view will report an error")
    return libmpv


def find_qsb() -> Path:
    import PySide6

    base = Path(PySide6.__file__).resolve().parent
    for cand in (base / "qsb.exe", base / "qsb"):
        if cand.exists():
            return cand
    sys.exit(f"[ERROR] qsb not found in the PySide6 wheel at {base}")


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


def pyside_qt_lib() -> Path:
    """The wheel's own Qt libraries (Linux, macOS): the plugin's RUNPATH / LC_RPATH."""
    import PySide6

    lib = Path(PySide6.__file__).resolve().parent / "Qt" / "lib"
    core = "QtCore.framework" if MACOS else "libQt6Core.so.6"
    if not (lib / core).exists():
        sys.exit(f"[ERROR] {core} not found in the PySide6 wheel at {lib}")
    return lib


def run_plain(cmd: list[str], cwd: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "CMAKE_PREFIX_PATH"}
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(cwd), env=env)


def run_in_vs_env(vcvars: Path, cmd: list[str], cwd: Path) -> None:
    # A throwaway .bat avoids cmd.exe's quoting rules for `cmd /c "..."`.
    quoted = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    bat = BUILD / "_run.bat"
    bat.write_text(
        f'@echo off\r\ncall "{vcvars}" >nul\r\nif errorlevel 1 exit /b 1\r\n{quoted}\r\n'
    )
    print(f"[RUN] {quoted}")
    subprocess.check_call(["cmd.exe", "/c", str(bat)], cwd=str(cwd))


def compile_shaders(qsb: Path) -> None:
    QSB_DIR.mkdir(parents=True, exist_ok=True)
    for name in SHADERS:
        src = HERE / "shaders" / name
        out = QSB_DIR / f"{name}.qsb"
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            continue
        cmd = [str(qsb), "--glsl", "100es,120,150", "--hlsl", "50", "--msl", "12"]
        cmd += ["-o", str(out), str(src)]
        print(f"[RUN] qsb {name}")
        subprocess.check_call(cmd)
    print(f"[OK] shaders: {', '.join(SHADERS)} -> {QSB_DIR}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--qt-root", type=Path, default=DEFAULT_QT_ROOT)
    ap.add_argument("--libmpv", type=Path, default=DEFAULT_LIBMPV, help="fetch_libmpv.py dir")
    ap.add_argument("--config", default="Release", choices=["Release", "RelWithDebInfo", "Debug"])
    ap.add_argument("--clean", action="store_true", help="wipe the build dir first")
    args = ap.parse_args()

    version = pyside_qt_version()
    sdk = find_qt_sdk(args.qt_root, version)
    if WINDOWS:
        vcvars = find_vcvars()

        def run(cmd: list[str], cwd: Path) -> None:
            run_in_vs_env(vcvars, cmd, cwd)

        generator = "Ninja"
        extra: list[str] = []
    else:
        run = run_plain
        generator = "Ninja" if shutil.which("ninja") else "Unix Makefiles"
        extra = [f"-DSPECTO_QT_RUNTIME_LIB={pyside_qt_lib().as_posix()}"]
    qsb = find_qsb()
    libmpv = find_libmpv(args.libmpv)
    print(f"[OK] PySide6 Qt {version}; SDK {sdk}; qsb {qsb}; libmpv {libmpv}")

    if args.clean and BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True, exist_ok=True)
    compile_shaders(qsb)

    run(
        [
            "cmake",
            "-G",
            generator,
            "-S",
            str(HERE),
            "-B",
            str(BUILD),
            f"-DCMAKE_BUILD_TYPE={args.config}",
            f"-DCMAKE_PREFIX_PATH={sdk.as_posix()}",
            f"-DSPECTO_QSB_DIR={QSB_DIR.as_posix()}",
            f"-DSPECTO_LIBMPV_DIR={libmpv.as_posix()}",
            *extra,
        ],
        cwd=HERE,
    )
    build = ["cmake", "--build", str(BUILD), "--config", args.config]
    run(build if WINDOWS else [*build, "--parallel"], cwd=HERE)

    # Install just the module tree: specto_app/qml/Specto/Native/{spectoqt.dll,qmldir,...}
    # (libspectoqt.so on Linux)
    src = BUILD / "qml" / "Specto" / "Native"
    if MODULE_DIR.exists():
        shutil.rmtree(MODULE_DIR)
    shutil.copytree(src, MODULE_DIR)
    if WINDOWS and (libmpv / LIBMPV_DLL).exists():
        shutil.copy2(libmpv / LIBMPV_DLL, MODULE_DIR / LIBMPV_DLL)
    elif libmpv != SYSTEM_LIBMPV and (libmpv / LIBMPV_DLL).exists():
        # the no-root fetch: libmpv.so.2 (RUNPATH $ORIGIN) and the libraries it bundles
        # sit next to the plugin, as the DLL does on Windows; a system libmpv stays put
        for lib in sorted(libmpv.glob("*.so*")):
            shutil.copy2(lib, MODULE_DIR / lib.name)
    print("[OK] installed:", MODULE_DIR)
    print("[OK] files:", ", ".join(p.name for p in sorted(MODULE_DIR.iterdir())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
