"""Seed the project folder for the v2 #186 launchers flow: two .blend scenes, three
jpgs, a program and a script, a note; and the machine's launchers.toml in the user dir
gui_session gives the app (``<folder>/.specto/gui-user``) with two fake launchers: this
Python running ``.specto/fake_launcher.py``, which writes its argv to
``.specto/launched/argv-<pid>.json``. No real program ever starts.

    uv run python tools/gui_flows/v2-186-seed.py <folder>               # seed (wipes it)
    uv run python tools/gui_flows/v2-186-seed.py <folder> --config bad  # a typo: labl
    uv run python tools/gui_flows/v2-186-seed.py <folder> --config good # the file again
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

FAKE = """\
import json, os, sys
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
data = {"argv": sys.argv[2:], "cwd": os.getcwd()}
tmp = os.path.join(out, f"argv-{os.getpid()}.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f)
os.replace(tmp, os.path.join(out, f"argv-{os.getpid()}.json"))
"""

FILES = (
    "scenes/a.blend",
    "scenes/b c & d.blend",
    "shots/1.jpg",
    "shots/2.jpg",
    "shots/3.jpg",
    "tools/setup.exe",
    "tools/run.ps1",
    "notes/a.md",
)


def config(root: Path) -> str:
    py = Path(sys.executable).as_posix()
    script = (root / ".specto" / "fake_launcher.py").as_posix()
    out = (root / ".specto" / "launched").as_posix()
    return f"""\
[[launcher]]
id = "fake-blender"
label = "Fake Blender"
command = "{py}"
args = ["{script}", "{out}", "{{path}}"]
match = ["*.blend"]
cwd = "{{dir}}"
default = true

[[launcher]]
id = "fake-viewer"
label = "Fake Viewer"
command = "{py}"
args = ["{script}", "{out}", "{{paths}}"]
match = ["*.jpg"]
each = false
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--config", choices=("good", "bad"))
    ns = ap.parse_args()
    root = ns.folder.resolve()
    machine = root / ".specto" / "gui-user" / "launchers.toml"
    if ns.config:
        text = config(root)
        if ns.config == "bad":
            text = text.replace('label = "Fake Viewer"', 'labl = "Fake Viewer"')
        machine.write_text(text, encoding="utf-8")
        print(f"[OK] wrote the {ns.config} launchers.toml")
        return 0
    if root.exists():
        shutil.rmtree(root)
    for rel in FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (root / ".specto").mkdir(exist_ok=True)
    (root / ".specto" / "fake_launcher.py").write_text(FAKE, encoding="utf-8")
    machine.parent.mkdir(parents=True)
    machine.write_text(config(root), encoding="utf-8")
    print(f"[OK] seeded {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
