"""Child process for test_html_frame: the html view in the real shell with QtWebEngine
(the pytest process itself runs with web views off), on the offscreen platform.

Each file named on the command line (under ``deck/``) is opened the way the user does
it, a Ctrl+double-click on its row in the file tree (``gestures.open_file`` in its
contextual mode, SPEC-32), so the preview, its link and the html view's page inside it come
up as in the app; the second file re-points the preview. One
JSON line per file on stdout: whether the view's page came up, what the scheme handler
served for it (the document and the assets it references relative to itself) and what
reached the view host from the frame. The console lines a framed page prints go to
stderr through the bridge as any frame's do; ``probe.html`` reports there whether it
can see the WebChannel transport (#68).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "opengl")

from specto_app.qt_setup import prepare

prepare(web=True, offscreen=True)

from PySide6.QtGui import QGuiApplication  # noqa: E402

qapp = QGuiApplication(["html-frame"])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_helpers import make_deck  # noqa: E402

from specto_app.app import SpectoApp  # noqa: E402
from specto_app.web.runtime import runtime  # noqa: E402
from specto_core import Project  # noqa: E402
from specto_core.gestures import open_file  # noqa: E402

#: what the framed probe page prints: whether the WebChannel transport exists in the
#: frame, now and a little later (an injection could lag the script), whether the
#: parent's is reachable, and, if any is, whether the bridge object answers
PROBE_JS = """
(function () {
  function report(when) {
    var qtSeen = typeof qt !== "undefined";
    var transport = qtSeen && !!qt.webChannelTransport;
    var parentQt;
    try { parentQt = typeof window.parent.qt; } catch (e) { parentQt = "blocked:" + e.name; }
    console.log("[PROBE " + when + "] qt=" + qtSeen + " transport=" + transport +
                " parentQt=" + parentQt + " origin=" + location.origin);
    if (!transport) return;
    var s = document.createElement("script");
    s.src = "qrc:///qtwebchannel/qwebchannel.js";
    s.onload = function () {
      new QWebChannel(qt.webChannelTransport, function (ch) {
        var b = ch.objects.specto;
        console.log("[PROBE " + when + "] bridge=" + (b ? "reached" : "absent"));
        if (b) b.send("escaped", JSON.stringify({from: location.href}));
      });
    };
    document.head.appendChild(s);
  }
  report("load");
  window.setTimeout(function () { report("late"); }, 400);
})();
"""


def spin(ms: float = 60, until=None, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + (timeout if until else ms / 1000)
    while time.monotonic() < deadline:
        qapp.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.004)
    qapp.processEvents()
    return until() if until is not None else True


def served_since(start: int) -> list[dict]:
    return [
        {k: e.get(k) for k in ("host", "path", "status", "rel") if k in e}
        for e in runtime().log[start:]
        if e.get("host", "").endswith(".items") or e.get("status", "ok") != "ok"
    ]


def open_one(app: SpectoApp, name: str, events: list[dict]) -> dict:
    """Open ``deck/<name>`` from the file tree as the user does and report what the
    scheme handler served for it once the page is up."""
    start = len(runtime().log)
    opened = open_file(
        app.live, "fege", f"deck/{name}", mode="contextual", plugins=app.engine.plugins
    )
    vid = opened.view
    spin(until=lambda: getattr(app.hosts.hosts.get(vid), "renderer", None) is not None)
    h = app.hosts.hosts[vid].renderer  # the html view's own host, in the preview (#106)
    if h.page.on_event is not None and getattr(h.page.on_event, "__name__", "") != "on_event":
        real_event = h.page.on_event

        def on_event(kind, payload):
            events.append({"kind": kind, "payload": payload})
            real_event(kind, payload)

        h.page.on_event = on_event
    ready = spin(until=lambda: h.page.ready, timeout=20)
    # the document, then what it references: give the handler time to see them
    spin(until=lambda: any(e.get("rel") == name for e in runtime().log[start:]), timeout=6)
    spin(1200)
    snap = h.snapshot()
    return {
        "file": name,
        "view": vid,
        "action": opened.action,
        "ready": ready,
        "health": h.ctx.health_of(vid),
        "item": snap.get("item"),
        "url": snap.get("url"),
        "served": served_since(start),
        "events": list(events),
        "bridge_calls": list(h.bridge.calls),
        "frame": (h.page.page_state or {}).get("frame"),
    }


def press_reload(app: SpectoApp, vid: str, name: str) -> dict:
    """Press the view's own Reload button inside its page (#99) and report what the
    scheme handler served for the framed page ``name`` afterwards."""
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject

    h = app.hosts.hosts[vid].renderer
    web = h.page.item.findChild(QObject, "webView")  # the WebEngineView in WebView.qml
    start = len(runtime().log)
    click = "document.getElementById('reload').click()"
    QMetaObject.invokeMethod(web, "runJavaScript", Q_ARG(str, click))
    spin(until=lambda: any(e.get("rel") == name for e in runtime().log[start:]), timeout=6)
    spin(800)
    return {"file": name, "view": vid, "action": "reload", "served": served_since(start)}


def run(files: list[str]) -> list[dict]:
    root = Path(tempfile.mkdtemp(prefix="html-frame-"))
    project = Project.init(root / "proj", "demo")
    deck = project.root / "deck"
    make_deck(deck)
    (deck / "probe.js").write_text(PROBE_JS, encoding="utf-8")
    (deck / "probe.html").write_text(
        '<!doctype html><html><head><script src="probe.js"></script></head>'
        "<body><p>probe</p></body></html>\n",
        encoding="utf-8",
    )
    app = SpectoApp(project, jobs="inline", web=True, offscreen=True, serve=False)
    app.start()
    spin(50)
    events: list[dict] = []
    try:
        spin(until=lambda: "fege" in app.hosts.hosts)
        app.engine.links.resolve("l1")  # the project scan ran before the deck was written
        spin(100)
        rows: list[dict] = []
        for name in files:
            # "reload": press the Reload button of the view the last file opened in
            if name == "reload":
                rows.append(press_reload(app, rows[-1]["view"], rows[-1]["file"]))
            else:
                rows.append(open_one(app, name, events))
        return rows
    finally:
        app.close()
        spin(20)


if __name__ == "__main__":
    files = sys.argv[1:] or ["deck.html"]
    try:
        for line in run(files):
            print(json.dumps(line), flush=True)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
