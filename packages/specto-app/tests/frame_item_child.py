"""Child process of test_frame_item.py: the stub frame source feeds a FrameItem rendered
offscreen (no window on screen); for each size in the plan the item and the source are
resized, frames are rendered until the item shows that size, and the grab is saved as
``<save>-<step>-<w>x<h>.png``. Prints one ``RESULT {json}`` line with the item's
properties per step.

    python frame_item_child.py '{"sizes": [[160, 120], [96, 64]], "save": "out/grab"}'
"""

from __future__ import annotations

import json
import sys
import time

from specto_app.frames.offscreen import OffscreenQuick, prepare_environment

prepare_environment()

from PySide6.QtGui import QGuiApplication  # noqa: E402

from specto_app.frames.control import FrameSourceProcess  # noqa: E402

QML = "import QtQuick\nimport Specto.Native\nFrameItem { anchors.fill: parent }"
PROPS = ("backend", "error", "ring", "connected", "frameSeq", "frameWidth", "frameHeight",
         "frames", "tornFrames", "uploadMs")  # fmt: skip


def props(item) -> dict:
    return {p: item.property(p) for p in PROPS}


def main() -> int:
    plan = json.loads(sys.argv[1])
    sizes = [tuple(s) for s in plan["sizes"]]
    app = QGuiApplication([])
    w, h = sizes[0]
    quick = OffscreenQuick(w, h)
    item = quick.load(QML)
    source = FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.frames.stub_source",
         "--width", str(w), "--height", str(h), "--fps", "120"]
    )  # fmt: skip
    out: dict = {"steps": []}
    try:
        ready = source.wait_event("ready")
        item.setProperty("ring", ready["ring"])
        item.setProperty("wake", ready["wake"])
        for i, (w, h) in enumerate(sizes):
            if i:
                quick.resize(w, h)
                source.call("resize", width=w, height=h)
            shown = -1
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                app.processEvents()
                quick.render()
                app.processEvents()
                p = props(item)
                if (p["frameWidth"], p["frameHeight"]) == (w, h):
                    if shown < 0:
                        shown = p["frames"]
                    elif p["frames"] >= shown + 2:
                        break
                time.sleep(0.005)
            img = quick.grab()
            grab = f"{plan['save']}-{i}-{w}x{h}.png"
            img.save(grab)
            out["steps"].append({"size": [w, h], "grab": grab, **props(item)})
    finally:
        source.close()
        quick.close()
    print("RESULT " + json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
