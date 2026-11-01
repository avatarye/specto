// Boot a web view's page under node: its index.html as a page DOM (fakes/page_dom.mjs),
// a recording Specto bridge, and the page's scripts run in order in one vm context.
//
//   const page = bootPage("map", ["icons.js", "sidepanel.js", "map/main.js"], {maplibregl});
//   page.fire("layers", {...});  page.sent; page.params; page.health; page.reports
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

import { pageDocument } from "./page_dom.mjs";

export const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "web");

/** A page with no index.html of its own (the shared modules alone). */
export function blankPage(scripts, extras) { return boot("<body></body>", scripts, extras); }

export function bootPage(view, scripts, extras) {
  return boot(readFileSync(path.join(WEB, view, "index.html"), "utf-8"), scripts, extras);
}

/** A plain copy in this realm: the page's objects come from the vm's own Object. */
const plain = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

function boot(html, scripts, extras = {}) {
  const document = pageDocument(html);
  const handlers = {};
  const ids = new Map();
  const cleared = new Set();
  let timerSeq = 0;
  const page = {
    document,
    sent: [],        // Specto.send(kind, payload)
    params: [],      // Specto.setParams(patch)
    emitted: [],     // Specto.emit(port, value)
    health: [],      // Specto.setHealth(status, msg)
    reports: [],     // Specto.report(state)
    drags: [],       // Specto.startDrag(d)
    logged: [],
    timers: [],
    $: (id) => document.getElementById(id),
    /** a notification from Python */
    fire(kind, payload) { (handlers[kind] || []).forEach((fn) => fn(payload)); },
    /** run the pending timeouts (debounced reports, delayed sends) */
    flush() { const t = page.timers.splice(0); t.forEach((fn) => { if (!cleared.delete(fn)) fn(); }); },
    get lastReport() { page.flush(); return page.reports[page.reports.length - 1]; },
  };
  const winListeners = {};
  const Specto = {
    config: null,
    on(kind, fn) { (handlers[kind] = handlers[kind] || []).push(fn); return () => {}; },
    connect() { return Promise.resolve(); },
    emit(port, value) { page.emitted.push([port, plain(value)]); return Promise.resolve({ ok: true }); },
    setParams(patch) { page.params.push(plain(patch)); return Promise.resolve({ ok: true }); },
    send(kind, payload) { page.sent.push([kind, plain(payload)]); },
    call() { return Promise.resolve({ ok: true }); },
    log(level, msg) { page.logged.push([level, msg]); },
    setHealth(status, msg) { page.health.push([status, msg || ""]); },
    report(state) { page.reports.push(plain(state)); },
    rectOf(el) { return el ? [0, 0, 0, 0] : null; },
    startDrag(d) { page.drags.push(plain(d)); },
  };
  const sandbox = {
    Specto,
    document,
    console,
    // a cleared timeout does not run (debounced reports run once per flush, as in a page)
    setTimeout(fn) { const t = () => fn(); page.timers.push(t); ids.set(++timerSeq, t); return timerSeq; },
    clearTimeout(id) { const t = ids.get(id); if (t) { ids.delete(id); cleared.add(t); } },
    getComputedStyle: (el) => ({ getPropertyValue: (n) => (el && el.style ? el.style.getPropertyValue(n) : "") }),
    devicePixelRatio: 1,
    fetch: () => new Promise(() => {}),
    ...extras,
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = (kind, fn) => { (winListeners[kind] = winListeners[kind] || []).push(fn); };
  sandbox.removeEventListener = () => {};
  page.key = (key, target, mods) => {
    const ev = { type: "keydown", key, target: target || document.body, defaultPrevented: false, ...mods };
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    (winListeners.keydown || []).forEach((fn) => fn(ev));
    return ev;
  };
  /** an event at the window (pointer moves and ups, dragstart): its listeners, in order */
  page.fireWindow = (type, props) => {
    const ev = { type, defaultPrevented: false, ...props };
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    ev.stopPropagation = () => {};
    (winListeners[type] || []).forEach((fn) => fn(ev));
    return ev;
  };
  page.window = sandbox;
  vm.createContext(sandbox);
  scripts.forEach((s) => {
    const file = path.join(WEB, s);
    vm.runInContext(readFileSync(file, "utf-8"), sandbox, { filename: file });
  });
  return page;
}

/** pointerdown at x on el, moves through xs, pointerup at the last (CSS px). */
export function drag(el, x0, xs) {
  const ev = (type, x) => ({ type, clientX: x, clientY: 0, pointerId: 1, button: 0 });
  el.dispatchEvent(ev("pointerdown", x0));
  xs.forEach((x) => el.dispatchEvent(ev("pointermove", x)));
  el.dispatchEvent(ev("pointerup", xs.length ? xs[xs.length - 1] : x0));
}
