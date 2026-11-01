"""Compile the QML ShaderEffect shaders (``qml/Specto/**/shaders/*.frag``) to ``.qsb``
next to their source with the ``qsb`` tool in the PySide6 wheel (``pyside6-qsb``), for
every Qt Quick backend (OpenGL / GLES, D3D11, Metal). The ``.qsb`` files are committed:
run this after changing a shader and commit both.

    uv run python tools/build_qml_shaders.py [--check]

``--check`` compiles into a temporary folder and fails when a committed ``.qsb`` differs.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QML = ROOT / "packages" / "specto-app" / "src" / "specto_app" / "qml" / "Specto"
FLAGS = ["--glsl", "100es,120,150", "--hlsl", "50", "--msl", "12"]


def qsb_tool() -> str:
    """This interpreter's ``pyside6-qsb`` first: one on PATH may belong to another Python
    (another PySide6, or a dead interpreter)."""
    own = shutil.which("pyside6-qsb", path=str(Path(sys.executable).parent))
    return own or shutil.which("pyside6-qsb") or "pyside6-qsb"


def sources() -> list[Path]:
    return sorted(QML.glob("**/shaders/*.frag")) + sorted(QML.glob("**/shaders/*.vert"))


def compile_one(src: Path, out: Path) -> None:
    subprocess.check_call([qsb_tool(), *FLAGS, "-o", str(out), str(src)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when a .qsb is stale")
    ns = ap.parse_args()
    stale = []
    with tempfile.TemporaryDirectory() as tmp:
        for src in sources():
            target = src.with_name(src.name + ".qsb")
            out = Path(tmp) / target.name if ns.check else target
            compile_one(src, out)
            if ns.check and (not target.exists() or target.read_bytes() != out.read_bytes()):
                stale.append(target)
            elif not ns.check:
                print(f"[OK] {target.relative_to(ROOT).as_posix()}")
    for t in stale:
        print(f"[ERROR] stale: {t.relative_to(ROOT).as_posix()} (run tools/build_qml_shaders.py)")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
