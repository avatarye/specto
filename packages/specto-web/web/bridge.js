/* Specto web-view bridge (JS side of the QWebChannel contract, docs/contracts.md 6, 8;
 * ports per ADR 0014).
 *
 * The Python side registers one `PageBridge` object as `specto` on the page's channel.
 *   Python -> JS signals: configured(json), port(json), params(json), notify(kind, json),
 *                         callResult(reqId, json)
 *   JS -> Python slots:   ready(), emit(port, valueJson) -> json, setParams(json) -> json,
 *                         call(name, argsJson, reqId), send(kind, json), log(level, msg),
 *                         jsError(msg), setHealth(status, msg), pageState(json)
 * Every payload is a JSON string; nothing crosses as a QVariant tree.
 * Capabilities are enforced on the Python side; a refused call rejects with {code, message, fix}.
 *
 * Context menu (#154): a right-click, the Menu key or Shift+F10 that the page itself did not
 * handle (preventDefault) sends `contextmenu {x, y, target}` and the shell opens its one menu
 * there. `target` is what `Specto.contextTarget(event)` returns ({kind: "items" | "view" |
 * "background", ...}); without one, the page's background. Selected text and editable fields
 * keep the browser's own menu (copy, paste).
 *
 * Drag out (#185): a drag of the page's text selection (the browser's dragstart, unless the
 * page handled it) or one the page starts itself (`Specto.startDrag({text, html})`: the Monaco
 * json view, which has no drag of its own) is sent as `drag {text, html}` and the shell
 * starts the drag with the contract's mime data: the text, its html, Specto's payload (the
 * view and the item the text came from). A drag of an element alone (an image, a link)
 * stays the browser's. `config.dragDistance` is the platform's drag distance (CSS px).
 */
(function () {
  "use strict";
  const handlers = {};
  const pending = {};
  let api = null;
  let config = null;
  let nextReq = 1;

  function on(kind, fn) {
    (handlers[kind] = handlers[kind] || []).push(fn);
    return function off() {
      handlers[kind] = (handlers[kind] || []).filter(function (f) { return f !== fn; });
    };
  }

  function dispatch(kind, data) {
    (handlers[kind] || []).forEach(function (fn) {
      try { fn(data); } catch (e) { reportError(e); }
    });
  }

  function reportError(e) {
    const msg = (e && e.stack) || String(e);
    if (api) { api.jsError(msg); } else { console.error(msg); }
  }

  /* Theme tokens arrive flattened by specto_app.theme.theme_for_web (color-surface,
   * metric-pad with its unit, font-mono, glyph-added); each becomes --<name> on :root. */
  function applyTheme(theme) {
    if (!theme) return;
    const root = document.documentElement.style;
    Object.keys(theme).forEach(function (k) {
      root.setProperty("--" + k, String(theme[k]));
    });
  }

  function parseReply(j) {
    const r = JSON.parse(j);
    if (!r.ok) log("warn", "refused: " + (r.error && r.error.message));
    return r;
  }

  function attach(channel) {
    api = channel.objects.specto;
    if (!api) throw new Error("bridge object 'specto' missing on the channel");
    api.configured.connect(function (j) {
      config = JSON.parse(j);
      applyTheme(config.theme);
      dispatch("config", config);
    });
    api.port.connect(function (j) {
      const p = JSON.parse(j);
      dispatch("port", p);
      dispatch("port:" + p.port, p.value);
    });
    api.params.connect(function (j) { dispatch("params", JSON.parse(j)); });
    api.notify.connect(function (kind, j) { dispatch(kind, JSON.parse(j)); });
    api.callResult.connect(function (id, j) {
      const p = pending[id];
      if (!p) return;
      delete pending[id];
      const r = JSON.parse(j);
      if (r.ok) { p.resolve(r.result); } else { p.reject(Object.assign(new Error(r.error.message), r.error)); }
    });
    api.ready();
  }

  function connect() {
    return new Promise(function (resolve, reject) {
      if (typeof qt === "undefined" || !qt.webChannelTransport) {
        reject(new Error("no qt.webChannelTransport: page is not inside a Specto web view"));
        return;
      }
      if (typeof QWebChannel === "undefined") {
        reject(new Error("qwebchannel.js did not load (qrc:///qtwebchannel/qwebchannel.js)"));
        return;
      }
      new QWebChannel(qt.webChannelTransport, function (channel) {
        try { attach(channel); resolve(api); } catch (e) { reject(e); }
      });
    });
  }

  function call(name, args) {
    return new Promise(function (resolve, reject) {
      if (!api) { reject(new Error("bridge not connected")); return; }
      const id = String(nextReq++);
      pending[id] = { resolve: resolve, reject: reject };
      api.call(name, JSON.stringify(args || {}), id);
    });
  }

  /* Emit a value on one of this view's output ports (capability emit:<port>). */
  function emit(port, value) {
    if (!api) return Promise.reject(new Error("bridge not connected"));
    return new Promise(function (resolve) {
      api.emit(port, JSON.stringify(value === undefined ? null : value), function (j) { resolve(parseReply(j)); });
    });
  }

  /* Write keys of this view's own params (capability params:<key>): one user submit. */
  function setParams(patch) {
    if (!api) return Promise.reject(new Error("bridge not connected"));
    return new Promise(function (resolve) {
      api.setParams(JSON.stringify(patch || {}), function (j) { resolve(parseReply(j)); });
    });
  }

  /* A page event for the host (a wikilink followed, a feature hovered); no reply. */
  function send(kind, payload) { if (api) api.send(kind, JSON.stringify(payload === undefined ? null : payload)); }

  function log(level, msg) { if (api) api.log(level, String(msg)); else console.log(level, msg); }
  /* What the page shows and where (CSS px rects): `snap` returns it as `page`, so an
   * agent or a scripted human test can read and click what is inside the web view. */
  function report(state) { if (api) api.pageState(JSON.stringify(state || {})); }
  function rectOf(el) {
    if (!el) return null;
    // an inline element wrapped over two lines: its first line box, not the union
    const boxes = el.getClientRects ? el.getClientRects() : null;
    const r = boxes && boxes.length > 1 ? boxes[0] : el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  }
  function setHealth(status, msg) { if (api) api.setHealth(status, msg || ""); }

  function wantsNativeMenu(ev) {
    const el = ev.target;
    if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return true;
    const sel = window.getSelection ? window.getSelection() : null;
    return !!(sel && String(sel).trim());
  }
  window.addEventListener("contextmenu", function (ev) {
    if (ev.defaultPrevented || wantsNativeMenu(ev)) return;
    ev.preventDefault();
    let target = null;
    const fn = window.Specto && window.Specto.contextTarget;
    if (typeof fn === "function") {
      try { target = fn(ev); } catch (e) { reportError(e); }
    }
    send("contextmenu", { x: ev.clientX, y: ev.clientY, target: target || { kind: "background" } });
  });

  /* the selection's markup, for rich-text drop targets */
  function selectionHtml(sel) {
    if (!sel.rangeCount) return null;
    const box = document.createElement("div");
    for (let i = 0; i < sel.rangeCount; i++) box.appendChild(sel.getRangeAt(i).cloneContents());
    return box.innerHTML || null;
  }
  function startDrag(d) {
    const text = d && d.text != null ? String(d.text) : "";
    if (!text) return;
    send("drag", { text: text, html: (d && d.html) || null });
  }
  window.addEventListener("dragstart", function (ev) {
    if (ev.defaultPrevented) return;
    const sel = window.getSelection ? window.getSelection() : null;
    const text = sel ? String(sel) : "";
    // a drag from inside the selection drags the selection; anything else is the element's
    if (!text.trim() || !ev.target || !sel.containsNode(ev.target, true)) return;
    ev.preventDefault();
    startDrag({ text: text, html: selectionHtml(sel) });
  });

  window.addEventListener("error", function (ev) {
    const where = ev.filename ? " (" + ev.filename + ":" + ev.lineno + ")" : "";
    if (api) api.jsError(String(ev.message) + where);
  });
  window.addEventListener("unhandledrejection", function (ev) {
    const r = ev.reason;
    if (api) api.jsError("unhandled rejection: " + ((r && (r.stack || r.message)) || String(r)));
  });

  window.Specto = {
    connect: connect, on: on, call: call, emit: emit, setParams: setParams, send: send,
    log: log, setHealth: setHealth, applyTheme: applyTheme, report: report, rectOf: rectOf,
    /* (event) -> {kind, ...}: what a context menu is about; a page sets it (#154) */
    contextTarget: null,
    /* ({text, html}) -> hand a drag the page detected itself to the shell (#185) */
    startDrag: startDrag,
    get config() { return config; },
  };
})();
