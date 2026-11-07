"""Child process of test_urdf_render.py: the real urdf page (web/urdf/main.js, kin.js, the
shared chrome, vendored Babylon) in headless Qt WebEngine (offscreen platform, GPU
compositing off), with a stand-in ``Specto`` bridge that the test drives. It sends the
model, waits for its meshes, then for each step of the plan sends the step's
notifications (params, pose, port:selection), waits for it to settle and saves a grab of
the page as ``<out>/<step>.png``. Prints one ``RESULT {json}`` line: per step the page's
report and the grab's path; or an ``error`` (one naming WebGL when there is none here).

    python urdf_render_child.py '{"model": ..., "out": ..., "envs": [...], "steps": [...]}'
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
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import specto_web  # noqa: E402

WEB = specto_web.web_root()
SCRIPTS = [
    "icons.js", "sidepanel.js", "step.js", "footer.js", "wheel.js", "viewnav.js",
    "vendor/babylon/babylon.js", "vendor/babylon/babylonjs.loaders.min.js", "urdf/kin.js",
]  # fmt: skip
# the page's bridge, as far as the urdf page and its chrome use it; the test fires the
# notifications (window.__fire) and reads the last report (window.__report)
FAKE_BRIDGE = """
window.__handlers = {}; window.__report = null; window.__health = []; window.__sent = [];
window.Specto = {
  on: function (k, fn) {
    (window.__handlers[k] = window.__handlers[k] || []).push(fn);
    return function () {};
  },
  connect: function () { return Promise.resolve(); },
  emit: function () { return Promise.resolve({ ok: true }); },
  setParams: function () { return Promise.resolve({ ok: true }); },
  send: function (k, p) { window.__sent.push([k, p]); },
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
window.onerror = function (msg, src, line) { window.__jsError = msg + " (" + src + ":" + line + ")"; };
"""


def page_html() -> str:
    index = (WEB / "urdf" / "index.html").read_text(encoding="utf-8")
    body = index[index.index("<body>") + 6 : index.index("</body>")]
    body = body.replace('<script src="main.js"></script>', "")
    uri = WEB.as_uri()
    head = [
        '<meta charset="utf-8">',
        f'<link rel="stylesheet" href="{uri}/style.css">',
        f'<link rel="stylesheet" href="{uri}/urdf/urdf.css">',
        f"<script>{FAKE_BRIDGE}</script>",
        *(f'<script src="{uri}/{s}"></script>' for s in SCRIPTS),
    ]
    tail = f'<script src="{uri}/urdf/main.js"></script>'
    return f"<!doctype html><html><head>{''.join(head)}</head><body>{body}{tail}</body></html>"


class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, msg, line, src):  # noqa: N802 (Qt's name)
        if os.environ.get("SPECTO_RENDER_DEBUG") or level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"JS {msg} ({src}:{line})", flush=True)


def main() -> int:
    plan = json.loads(sys.argv[1])
    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="specto-urdf-render-"))
    html = tmp / "page.html"
    html.write_text(page_html(), encoding="utf-8")

    app = QApplication([sys.argv[0]])
    view = QWebEngineView()
    view.setPage(Page(view))
    view.settings().setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
    )
    view.resize(*plan.get("size", [900, 520]))
    view.show()
    view.load(QUrl.fromLocalFile(str(html)))
    result: dict = {"steps": []}
    state = {"phase": "boot", "step": 0, "t0": time.monotonic(), "report": None}
    envs = [{**e, "url": Path(e["file"]).resolve().as_uri()} for e in plan.get("envs", [])]

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

    def got_error(text: object) -> None:
        if isinstance(text, str) and text:
            result["error"] = "page: " + text

    def settled(r: dict | None, want: dict) -> bool:
        if not r or r.get("meshes", {}).get("loading"):
            return False
        env = r.get("environment") or {}
        if env.get("error"):
            result["error"] = f"environment: {env['error']}"
            return True
        name = want.get("environment")
        if name and name != "none" and not (env.get("name") == name and env.get("ready")):
            return False
        mode = want.get("view_mode")
        return not mode or r.get("view_mode") == mode

    def tick() -> None:
        js("JSON.stringify(window.__report)", got_report)
        js("document.readyState + ':' + typeof BABYLON", got_ready)
        js("window.__jsError || ''", got_error)
        r = state["report"]
        now = time.monotonic()
        if "error" in result:
            finish(1)
            return
        if now - state["t0"] > 60:
            result["error"] = f"timed out in {state['phase']} step {state['step']}"
            finish(1)
            return
        if state["phase"] == "boot":
            if state.get("ready"):
                js(f"window.__fire('config', {{params: {json.dumps(plan.get('params', {}))}}});")
                msg = {"generation": 1, "model": plan["model"], "environments": envs}
                js(f"window.__fire('model', {json.dumps(msg)});")
                state.update(phase="load", report=None, settle=None)
            return
        if state["phase"] == "load":
            if r and r.get("robot") and not r["meshes"]["loading"] and r.get("bounds"):
                if state["settle"] is None:
                    state["settle"] = now + 0.6
                elif now >= state["settle"]:
                    result["loaded"] = r
                    state.update(phase="step", settle=None)
                    send_step()
            return
        if state["phase"] == "step":
            step = plan["steps"][state["step"]]
            if not settled(r, step.get("params", {})):
                return
            if state["settle"] is None:
                state["settle"] = now + 0.8  # a few frames after the switch: IBL, the sky, edges
                return
            if now < state["settle"]:
                return
            png = out / f"{state['step']}.png"
            view.grab().save(str(png))
            result["steps"].append({"step": step, "png": str(png), "report": r})
            state["step"] += 1
            if state["step"] == len(plan["steps"]):
                finish(0)
                return
            state["settle"] = None
            send_step()

    def send_step() -> None:
        state["report"] = None
        step = plan["steps"][state["step"]]
        for kind in ("params", "pose", "port:selection"):
            if kind in step:
                js(f"window.__fire({json.dumps(kind)}, {json.dumps(step[kind])});")
        if not any(k in step for k in ("params", "pose", "port:selection")):
            js("window.__fire('params', {});")

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    rc = app.exec()
    view.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
