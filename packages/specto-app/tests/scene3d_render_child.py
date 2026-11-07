"""Child process of test_scene3d_render.py: the real 3D page (web/3d/main.js, the shared
chrome, vendored Babylon) in headless Qt WebEngine (offscreen platform, GPU compositing
off), with a stand-in ``Specto`` bridge that the test drives. It loads a GLB of PBR
spheres, then renders each step of the plan (view params) and saves a grab of the
canvas as ``<out>/<step>.png``. Prints one ``RESULT {json}`` line: per step the page's
report (environment state, canvas rect) and the grab's path; or ``{"skip": reason}``
when there is no WebGL2 here.

    python scene3d_render_child.py '{"glb": ..., "out": ..., "envs": [...], "steps": [...]}'

or, in place of ``glb``, ``entries`` (each ``{id, kind, file, ext, ...}``) with an optional
``camera`` and ``params`` (the SPEC-42 mesh formats).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
# the test process's software Qt Quick backend would take WebGL away from Chromium
for var in ("QT_QUICK_BACKEND", "QSG_RHI_BACKEND"):
    os.environ.pop(var, None)
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " --disable-gpu-compositing --in-process-gpu"
).strip()

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import specto_web  # noqa: E402

WEB = specto_web.web_root()
SCRIPTS = [
    "icons.js", "sidepanel.js", "footer.js", "wheel.js", "viewnav.js",
    "vendor/babylon/fflate.js", "vendor/babylon/babylon.js",
    "vendor/babylon/babylonjs.loaders.min.js",
]  # fmt: skip
# the page's bridge, as far as the 3D page and its chrome use it; the test fires the
# notifications (window.__fire) and reads the last report (window.__report)
FAKE_BRIDGE = """
window.__handlers = {}; window.__report = null; window.__health = [];
window.Specto = {
  on: function (k, fn) {
    (window.__handlers[k] = window.__handlers[k] || []).push(fn);
    return function () {};
  },
  connect: function () { return Promise.resolve(); },
  emit: function () { return Promise.resolve({ ok: true, result: { links: [] } }); },
  setParams: function () { return Promise.resolve({ ok: true }); },
  send: function () {},
  log: function (level, msg) { console.log("[" + level + "] " + msg); },
  setHealth: function (status, msg) { window.__health.push([status, msg || ""]); },
  report: function (state) { window.__report = state; },
  rectOf: function (el) {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  },
  config: null,
};
window.__fire = function (k, p) { (window.__handlers[k] || []).forEach(function (fn) { fn(p); }); };
"""


def page_html() -> str:
    index = (WEB / "3d" / "index.html").read_text(encoding="utf-8")
    body = index[index.index("<body>") + 6 : index.index("</body>")]
    body = body.replace('<script src="main.js"></script>', "")
    uri = WEB.as_uri()
    head = [
        '<meta charset="utf-8">',
        f'<link rel="stylesheet" href="{uri}/style.css">',
        f'<link rel="stylesheet" href="{uri}/3d/scene3d.css">',
        f"<script>{FAKE_BRIDGE}</script>",
        *(f'<script src="{uri}/{s}"></script>' for s in SCRIPTS),
    ]
    tail = f'<script src="{uri}/3d/main.js"></script>'
    return f"<!doctype html><html><head>{''.join(head)}</head><body>{body}{tail}</body></html>"


def main() -> int:
    plan = json.loads(sys.argv[1])
    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="specto-3d-render-"))
    html = tmp / "page.html"
    html.write_text(page_html(), encoding="utf-8")

    app = QApplication([sys.argv[0]])
    view = QWebEngineView()
    if os.environ.get("SPECTO_RENDER_DEBUG"):
        from PySide6.QtWebEngineCore import QWebEnginePage

        class Page(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, msg, line, src):
                print(f"JS {msg} ({src}:{line})", flush=True)

        view.setPage(Page(view))
    view.settings().setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
    )
    view.resize(*plan.get("size", [720, 420]))
    view.show()
    view.load(QUrl.fromLocalFile(str(html)))
    result: dict = {"steps": []}
    state = {"phase": "boot", "step": 0, "t0": time.monotonic(), "report": None}
    if "entries" in plan:
        # the plan's own entries (SPEC-42 meshes): {id, kind, file, ext, ...}; the file
        # becomes a file:// URL, so an OBJ's MTL and textures load next to it
        entries = [
            {
                "index": i,
                "label": Path(e["file"]).name,
                **{k: v for k, v in e.items() if k != "file"},
                "url": Path(e["file"]).resolve().as_uri(),
            }
            for i, e in enumerate(plan["entries"])
        ]
    else:
        glb = Path(plan["glb"]).resolve()
        entries = [
            {
                "id": "spheres",
                "index": 0,
                "kind": "mesh",
                "label": glb.name,
                "url": glb.as_uri(),
                "ext": ".glb",
                "bounds": [[-5.4, 0, -1], [5.4, 2, 1]],
            }
        ]
    scene = {
        "entries": entries,
        "params": {"point_size": 2, "up": "y", **plan.get("params", {})},
        "generation": 1,
        "camera": plan.get("camera") or {
            "position": [0, 3.2, 11.5], "target": [0, 1, 0], "up": [0, 1, 0],
            "fov_y": 0.8, "near": 0.05, "far": 400,
        },
        "environments": [
            {**e, "url": Path(e["file"]).resolve().as_uri()} for e in plan.get("envs", [])
        ],
    }  # fmt: skip

    def js(code: str, then=None) -> None:
        view.page().runJavaScript(code, 0, then or (lambda _r: None))

    def finish(rc: int = 0) -> None:
        print("RESULT " + json.dumps(result), flush=True)
        timer.stop()
        app.exit(rc)

    def got_report(text: object) -> None:
        state["report"] = json.loads(text) if isinstance(text, str) and text else None

    def got_ready(text: object) -> None:
        state["ready"] = text == "complete:object"

    def tick() -> None:
        js("JSON.stringify(window.__report)", got_report)
        js("document.readyState + ':' + typeof BABYLON", got_ready)
        r = state["report"]
        now = time.monotonic()
        if now - state["t0"] > 45:
            result["error"] = (
                f"timed out in {state['phase']} step {state['step']}: {r and r.get('environment')}"
            )
            finish(1)
            return
        if state["phase"] == "boot":
            if state.get("ready"):
                js("window.__fire('config', {params: {}});")
                js(f"window.__fire('scene', {json.dumps(scene)});")
                state.update(phase="load", report=None)
            return
        if state["phase"] == "load":
            items = [((r or {}).get("items") or {}).get(e["id"]) or {} for e in entries]
            broken = [i["error"] for i in items if i.get("error")]
            if broken:
                result["error"] = "load: " + "; ".join(broken)
                finish(1)
            elif all(i.get("loaded") for i in items):
                if (r.get("babylon") or "") and "webgl" not in result:
                    result["webgl"] = True
                state.update(phase="step", settle=None)
                send_step()
            return
        if state["phase"] == "step":
            env = (r or {}).get("environment") or {}
            want = plan["steps"][state["step"]].get("environment", "none")
            if env.get("error"):
                result["error"] = f"environment {want}: {env['error']}"
                finish(1)
                return
            if env.get("name") != want or (want != "none" and not env.get("ready")):
                return
            if state["settle"] is None:
                state["settle"] = now + 0.8  # a few frames after the switch: IBL and the sky in
                return
            if now < state["settle"]:
                return
            png = out / f"{state['step']}.png"
            view.grab().save(str(png))
            result["steps"].append(
                {"params": plan["steps"][state["step"]], "png": str(png), "report": r}
            )
            state["step"] += 1
            if state["step"] == len(plan["steps"]):
                finish(0)
                return
            state["settle"] = None
            send_step()

    def send_step() -> None:
        state["report"] = None
        js(f"window.__fire('params', {json.dumps(plan['steps'][state['step']])});")

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    rc = app.exec()
    view.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
