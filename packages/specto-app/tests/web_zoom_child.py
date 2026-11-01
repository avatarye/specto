"""Child process of test_web_zoom.py (#189): each web page (web/<page>/index.html, the
shared chrome, a stand-in ``Specto`` bridge) in the app's own ``WebView.qml`` in
headless Qt WebEngine, served over a loopback HTTP server (WebView.qml keeps local
content off file URLs). Ctrl+wheel goes to the window over the page's chrome (and, on
the markdown page, over the note), then the page says what it renders: the outline
row's and the footer's font size in CSS px, the note's own zoom, the WebEngineView's
zoomFactor (Chromium's page zoom: a factor on every CSS px, chrome included). Prints
one ``RESULT {json}`` line.

    python web_zoom_child.py '{"pages": ["markdown", "map"], "note": "<path of a .md>"}'
"""

from __future__ import annotations

import functools
import json
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for var in ("QT_QUICK_BACKEND", "QSG_RHI_BACKEND"):
    os.environ.pop(var, None)
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " --disable-gpu-compositing --in-process-gpu"
).strip()

from PySide6.QtWebEngineQuick import QtWebEngineQuick  # noqa: E402

QtWebEngineQuick.initialize()

from PySide6.QtCore import (  # noqa: E402
    Q_ARG,
    QMetaObject,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtGui import QGuiApplication, QWheelEvent  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402

import specto_app  # noqa: E402
import specto_web  # noqa: E402

WEB = specto_web.web_root()
WEBVIEW_QML = Path(specto_app.__file__).parent / "qml" / "Specto" / "Web" / "WebView.qml"
W, H = 900, 420
FAKE_BRIDGE = """
window.__handlers = {}; window.__report = null;
window.Specto = {
  on: function (k, fn) {
    (window.__handlers[k] = window.__handlers[k] || []).push(fn);
    return function () {};
  },
  connect: function () { return Promise.resolve(); },
  emit: function () { return Promise.resolve({ ok: true, result: {} }); },
  call: function () { return Promise.resolve({ ok: true, result: {} }); },
  setParams: function () { return Promise.resolve({ ok: true }); },
  send: function (k, p) { (window.__sent = window.__sent || []).push([k, p]); },
  log: function () {}, setHealth: function () {},
  report: function (s) { window.__report = s; },
  rectOf: function (el) {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  },
  config: null,
};
window.__fire = function (k, p) { (window.__handlers[k] || []).forEach(function (fn) { fn(p); }); };
"""
# The page itself is loaded and laid out. Before the navigation commits, page JS runs in
# the view's initial about:blank document, whose readyState is already "complete" (#198):
# the URL must be the page's, and its footer (every page has one) must have a layout box.
LOADED = """(function () {
  const f = document.querySelector(".footer");
  return document.readyState === "complete" && location.pathname.endsWith("/zoom-test.html")
      && !!f && f.getBoundingClientRect().height > 0;
})()"""
DOC_ZOOM = "parseFloat(getComputedStyle(document.getElementById('doc')).zoom)"
NOTE_SHOWN = """(function () {
  const doc = document.getElementById("doc");
  return !!doc && doc.childElementCount > 0 && !!document.querySelector(".outline-row");
})()"""
MEASURE = """JSON.stringify((function () {
  function px(sel) {
    const e = document.querySelector(sel);
    return e ? parseFloat(getComputedStyle(e).fontSize) : null;
  }
  const doc = document.getElementById("doc");
  return {ready: document.readyState, row: px(".outline-row"), footer: px(".footer"),
          title: px(".panel-title"), docZoom: doc ? parseFloat(getComputedStyle(doc).zoom) : null,
          width: innerWidth};
})())"""
WRAPPER = """
import QtQuick
Item {
    id: root
    property Item host: null
    property string result: ""
    readonly property real zoom: host ? host.view.zoomFactor : -1
    function js(code) {
        root.result = ""
        host.view.runJavaScript(code, function (r) { root.result = String(r) })
    }
    function setZoom(z) { host.view.zoomFactor = z }
}
"""


class StubBridge(QObject):
    """What WebView.qml calls on its PageBridge."""

    @Slot(int, str, int, str)
    def consoleMessage(self, level, message, line, source):
        if os.environ.get("SPECTO_ZOOM_DEBUG"):
            print(f"JS {message} ({source}:{line})", flush=True)

    @Slot(str, str)
    def loadState(self, status, message):
        pass

    @Slot(str, bool, result=bool)
    def allowNavigation(self, url, main_frame):
        return True


def page_html(page: str) -> str:
    """web/<page>/index.html with the stand-in bridge for bridge.js."""
    html = (WEB / page / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
    return html.replace('<script src="/bridge.js"></script>', f"<script>{FAKE_BRIDGE}</script>")


class Handler(SimpleHTTPRequestHandler):
    """The web root as the page origin serves it; /<page>/zoom-test.html is that page
    with the stand-in bridge, /note.md the note."""

    note: Path

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("/zoom-test.html"):
            body, kind = page_html(path.strip("/").split("/")[0]).encode("utf-8"), "text/html"
        elif path == "/note.md":
            body, kind = self.note.read_bytes(), "text/markdown"
        else:
            super().do_GET()
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> int:
    plan = json.loads(sys.argv[1])
    Handler.note = Path(plan["note"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    tmp = Path(plan["tmp"])
    app = QGuiApplication([sys.argv[0]])
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(W, H)
    wrapper = tmp / "Wrapper.qml"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    view.setSource(QUrl.fromLocalFile(str(wrapper)))
    view.show()
    root = view.rootObject()
    bridge = StubBridge()
    comp = QQmlComponent(view.engine(), QUrl.fromLocalFile(str(WEBVIEW_QML)))
    result: dict = {"pages": {}}

    def wheel(x: float, y: float, delta: int = 120) -> None:
        ev = QWheelEvent(
            QPointF(x, y), QPointF(view.mapToGlobal(QPoint(int(x), int(y)))), QPoint(),
            QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )  # fmt: skip
        QGuiApplication.sendEvent(view, ev)

    def wait_for(page: str, expr: str, what: str, timeout: float = 30.0):
        """Poll page JS ``expr`` until it is true, within ``timeout`` seconds; on the
        deadline, record a clear error and return False (the caller stops)."""
        t0 = time.monotonic()
        while True:
            r = yield expr
            if r == "true":
                return True
            if time.monotonic() - t0 > timeout:
                result["error"] = (
                    f"{page}: {what} did not happen within {timeout:.0f}s (last: {r!r})"
                )
                return False

    def run():
        """One step per tick: a float waits that long; a str is page JS whose result
        comes back into the generator."""
        for page in plan["pages"]:
            host = comp.createWithInitialProperties(
                {"bridge": bridge, "source": QUrl(f"{base}/{page}/zoom-test.html")}
            )
            if host is None:
                result["error"] = comp.errorString()
                return
            host.setParentItem(root)
            host.setProperty("width", W)
            host.setProperty("height", H)
            root.setProperty("host", host)
            if not (yield from wait_for(page, LOADED, "the page load and first layout")):
                return
            got: dict = {}
            if page == "markdown":
                yield "window.__fire('config', {params: {}}); 'ok'"
                yield "window.__fire('item', {key: 'n', url: '/note.md', name: 'n.md'}); 'ok'"
                if not (yield from wait_for(page, NOTE_SHOWN, "the note and its outline")):
                    return
                for w in (500, W):  # two view widths
                    host.setProperty("width", w)
                    if not (yield from wait_for(page, f"innerWidth === {w}", f"the {w} px width")):
                        return
                    got[f"w{w}"] = json.loads((yield MEASURE))
                    got[f"w{w}"]["zoomFactor"] = root.property("zoom")
                wheel(W - 100, 40)  # over the OUTLINE's rows
                wheel(W - 100, 40)
                yield 0.8  # nothing should change: give it the time to go wrong
                got["outline"] = json.loads((yield MEASURE))
                got["outline"]["zoomFactor"] = root.property("zoom")
                wheel(200, 120)  # over the note
                wheel(200, 120)
                if not (yield from wait_for(page, DOC_ZOOM + " > 1", "the note zoom", 10)):
                    return
                got["note"] = json.loads((yield MEASURE))
                got["note"]["zoomFactor"] = root.property("zoom")
                # #202: the page tells the host its zoom once the notches settle; one more
                # notch is a second, later level
                first = got["note"]["docZoom"]
                told = "(window.__sent || []).length === "
                if not (yield from wait_for(page, told + "1", "the zoom told", 10)):
                    return
                wheel(200, 120)
                if not (yield from wait_for(page, told + "2", "the second zoom told", 10)):
                    return
                got["sent"] = json.loads((yield "JSON.stringify(window.__sent)"))
                # a stale echo of the first level (the host's params for it) moves nothing
                yield f"window.__fire('params', {{zoom: {first}}}); 'ok'"
                yield 0.3
                got["echo"] = json.loads((yield MEASURE))
                # the agent's level applies; a params without one is 1
                yield "window.__fire('params', {zoom: 2}); 'ok'"
                if not (yield from wait_for(page, DOC_ZOOM + " === 2", "the agent's zoom", 10)):
                    return
                got["agent"] = json.loads((yield MEASURE))
                got["agent"]["zoomFactor"] = root.property("zoom")
                yield "window.__fire('params', {}); 'ok'"
                if not (yield from wait_for(page, DOC_ZOOM + " === 1", "the zoom back at 1", 10)):
                    return
                got["cleared"] = json.loads((yield MEASURE))
                got["told"] = json.loads((yield "JSON.stringify(window.__sent)"))
            else:
                got["before"] = json.loads((yield MEASURE))
                got["before"]["zoomFactor"] = root.property("zoom")
                wheel(W / 2, H - 10)  # over the footer
                wheel(W / 2, H - 10)
                yield 0.8
                got["footer"] = json.loads((yield MEASURE))
                got["footer"]["zoomFactor"] = root.property("zoom")
            # Chromium's zoom set some other way (an html view's iframe: no page script
            # sees its wheel) does not hold either
            QMetaObject.invokeMethod(root, "setZoom", Q_ARG("QVariant", 1.5))
            yield 0.5
            got["forced"] = root.property("zoom")
            result["pages"][page] = got
            root.setProperty("host", None)
            host.setParentItem(None)
            host.deleteLater()

    steps = run()
    state = {"next": None, "until": 0.0, "send": None, "waiting_js": False, "asked": 0.0}
    JS_RETRY_S = 5.0  # a runJavaScript whose callback a navigation swallowed: ask again

    def finish() -> None:
        print("RESULT " + json.dumps(result), flush=True)
        timer.stop()
        app.exit(0)

    def tick() -> None:
        now = time.monotonic()
        if state["waiting_js"]:
            r = root.property("result")
            if not r:
                if now - state["asked"] > JS_RETRY_S:
                    state["asked"] = now
                    QMetaObject.invokeMethod(root, "js", Q_ARG("QVariant", state["next"]))
                return
            state.update(waiting_js=False, send=r)
        elif now < state["until"]:
            return
        try:
            step = steps.send(state["send"]) if state["next"] is not None else next(steps)
        except StopIteration:
            finish()
            return
        state.update(next=step, send=None)
        if isinstance(step, str):
            QMetaObject.invokeMethod(root, "js", Q_ARG("QVariant", step))
            state.update(waiting_js=True, asked=now)
        else:
            state["until"] = now + float(step)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(50)
    QTimer.singleShot(150_000, lambda: (result.setdefault("error", "timed out"), finish()))
    rc = app.exec()
    view.close()
    server.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
