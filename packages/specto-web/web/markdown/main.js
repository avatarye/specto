// @ts-check
/// <reference path="../../types/specto.d.ts" />
/* Markdown view: renders the selected note of its collection (SpectoMarkdown), follows
 * [[wikilinks]] by telling the host (`navigate`: it shows the note and emits it as the
 * selection, so linked views move too), keeps a back history, and shows the note's
 * headings in the OUTLINE side panel (sidepanel.js). Read-only. Ctrl+wheel over the note
 * (Ctrl+= / Ctrl+- / Ctrl+0) zooms the note alone; the outline and the footer keep the
 * theme's size (zoom.js, #189).
 *
 * Python sends: `notes` {notes: [{key, name, url, role}]} (the keys the wikilinks
 * resolve to), `item` {key, url, role, name, file} (or {key, url: null, reason}),
 * `params` {panels, zoom}. The page writes the outline panel's state (`panels.outline`
 * {collapsed, width}) as a user submit through setParams. The reading zoom is the view's
 * `params.zoom` (#202: 1 when absent; the agent may set it): the page applies it, and
 * tells the host its own zoom as a `zoom {level}` page event, which the host remembers
 * as navigation (no undo step), so a reload and a relaunch show the note at that size.
 *
 * The footer (#130): step < >, the position and the note's file name; back, only after
 * a wikilink was followed. No note list (ADR 0020: browsing is the file tree's job; the
 * step covers moving through the collection). A problem shows as the view's health. */
(function () {
  "use strict";
  const $ = function (id) { return /** @type {HTMLElement} */ (document.getElementById(id)); };
  const OUTLINE_W = 200;
  /** @type {{panels?: Record<string, {collapsed?: boolean, width?: number}>, zoom?: number}} */
  let params = {};
  /** @type {{key: string, name?: string, url: string, role: string}[]} */
  let notes = [];
  /** @type {{key: string, url: string} | null} */
  let current = null;
  /** @type {string[]} */
  const history = [];
  let fetchSeq = 0;
  /** @type {{level: number, text: string, id: string}[]} */
  let headings = [];
  /** @type {string} the heading id the outline marks */
  let currentHeading = "";

  const footer = SpectoFooter.mount($("footer"), { step: ["ArrowLeft", "ArrowRight", "PageUp", "PageDown"] });
  const backBtn = footer.tool("back", "Back to the previous note", back, "back");
  backBtn.classList.add("hidden");

  const panel = SpectoSidePanel.mount($("row"), {
    id: "outline", title: "Outline", side: "right", keep: "outline", allNone: false,
    width: OUTLINE_W, minWidth: 120, maxWidth: 420,
    onCollapse: reportSoon,
    onResize: reportSoon,
  });
  const zoom = SpectoZoom.content($("doc"), { onChange: function () { spy(); if (!applyingZoom) tellZoomSoon(); reportSoon(); } });

  function setError(msg) { Specto.setHealth(msg ? "error" : "ok", msg || ""); }

  function showEmpty(html) {
    $("doc").classList.add("hidden");
    $("empty").classList.remove("hidden");
    $("empty").innerHTML = html;
  }

  /* The reading zoom is the view's `params.zoom` (#202). A zoom the reader made goes to
   * the host once the notches settle (`zoom {level}`; the host remembers it as
   * navigation) and comes back as `params`; the levels sent and not yet seen back are
   * kept, so an echo of an earlier level never moves a note the reader zoomed on since.
   * Any other `params.zoom` is the agent's (or a reopened project's): applied, not told
   * back (the host has it). */
  /** @type {number[]} */
  const toldZoom = [];
  let tellZoomTimer = 0;
  let applyingZoom = false;
  function tellZoom() {
    tellZoomTimer = 0;
    toldZoom.push(zoom.level);
    Specto.send("zoom", { level: zoom.level });
  }
  function tellZoomSoon() { clearTimeout(tellZoomTimer); tellZoomTimer = window.setTimeout(tellZoom, 200); }
  function applyZoom(p) {
    const level = p && typeof p.zoom === "number" ? p.zoom : 1;
    const told = toldZoom.indexOf(level);
    if (told >= 0) { toldZoom.splice(0, told + 1); return; }
    toldZoom.length = 0;
    clearTimeout(tellZoomTimer);
    tellZoomTimer = 0;
    applyingZoom = true;
    try { zoom.set(level); } finally { applyingZoom = false; }
  }

  function applyParams(p) {
    params = Object.assign({}, params, p || {});
    SpectoSidePanel.restore(params.panels);
    applyZoom(p);
    reportSoon();
  }

  function noteByKey(key) {
    for (let i = 0; i < notes.length; i++) if (notes[i].key === key) return notes[i];
    return null;
  }

  /* [[target]] -> key: the exact key, the key without its "<source index>:" prefix (a
   * multi-source intaker keys items as "1:name"), then the last path segment; all
   * case-insensitive, ".md" ignored */
  function resolve(target) {
    const t = String(target).replace(/\\/g, "/").replace(/\.md$/i, "").toLowerCase();
    const last = t.split("/").pop();
    let hit = null;
    for (let i = 0; i < notes.length; i++) {
      const k = notes[i].key.toLowerCase();
      const bare = k.replace(/^\d+:/, "");
      if (k === t || bare === t) return notes[i].key;
      if (!hit && bare.split("/").pop() === last) hit = notes[i].key;
    }
    return hit;
  }

  function renderOutline() {
    const list = panel.body;
    list.innerHTML = "";
    if (!headings.length) {
      const none = document.createElement("div");
      none.className = "outline-none";
      none.textContent = current ? "no headings" : "";
      list.appendChild(none);
      return;
    }
    headings.forEach(function (h) {
      const row = document.createElement("div");
      row.className = "outline-row h" + h.level;
      if (h.id === currentHeading) row.classList.add("current");
      row.textContent = h.text;
      row.title = h.text;
      row.setAttribute("data-id", h.id);
      row.addEventListener("click", function () { scrollTo(h.id); });
      list.appendChild(row);
    });
  }

  function markCurrent(id) {
    if (id === currentHeading) return;
    currentHeading = id;
    panel.body.querySelectorAll(".outline-row").forEach(function (r) {
      r.classList.toggle("current", r.getAttribute("data-id") === id);
    });
    reportSoon();
  }

  /* the heading the reader is in: the last one at or above the top of the note */
  function spy() {
    const doc = $("doc");
    const top = doc.getBoundingClientRect().top + 8;
    let id = headings.length ? headings[0].id : "";
    headings.forEach(function (h) {
      const el = document.getElementById(h.id);
      if (el && el.getBoundingClientRect().top <= top) id = h.id;
    });
    markCurrent(id);
  }

  /* what the page shows, with CSS px rects, for `snap` (agents, scripted tests) */
  let reportTimer = 0;
  function reportPage() {
    const doc = $("doc");
    Specto.report({
      note: current ? current.key : null,
      history: history.slice(),
      outline: !panel.collapsed,
      headings: headings.map(function (h) { return { level: h.level, text: h.text, id: h.id }; }),
      outlineRows: Array.prototype.map.call(panel.body.querySelectorAll(".outline-row"), function (r) {
        return { text: r.textContent, rect: Specto.rectOf(r), current: r.classList.contains("current") };
      }),
      wikilinks: Array.prototype.map.call(doc.querySelectorAll("a.wikilink"), function (a) {
        return { key: a.getAttribute("data-key"), anchor: a.getAttribute("data-anchor") || "", text: a.textContent, rect: Specto.rectOf(a) };
      }),
      missing: Array.prototype.map.call(doc.querySelectorAll("span.wikilink.missing"), function (s) { return s.textContent; }),
      callouts: Array.prototype.map.call(doc.querySelectorAll(".callout"), function (c) {
        return { type: (c.className.match(/callout-([a-z0-9_-]+)/) || [])[1] || "", folded: c.classList.contains("folded"), rect: Specto.rectOf(c) };
      }),
      diagrams: doc.querySelectorAll("pre.mermaid svg").length,
      math: doc.querySelectorAll(".katex").length,
      images: Array.prototype.map.call(doc.querySelectorAll("img"), function (i) { return { src: i.getAttribute("src"), loaded: i.complete && i.naturalWidth > 0, rect: Specto.rectOf(i) }; }),
      panel: panel.rects(),
      buttons: { back: backBtn.classList.contains("hidden") ? null : Specto.rectOf(backBtn) },
      step: window.SpectoStep.rects(),
      scrollTop: doc.scrollTop,
      zoom: zoom.level,
      dpr: window.devicePixelRatio,
    });
  }
  function reportSoon() { clearTimeout(reportTimer); reportTimer = window.setTimeout(reportPage, 150); }
  $("doc").addEventListener("scroll", function () { spy(); reportSoon(); });
  window.addEventListener("resize", reportSoon);
  /* previous / next note of the collection (step.js, #107): the arrows and the page keys
   * step; the wheel, Up / Down and Space still scroll the note */
  Specto.on("step", reportSoon);

  function scrollTo(id) {
    const el = id ? document.getElementById(id) : null;
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
    markCurrent(id);
  }

  function mount(text, note, anchor) {
    const out = SpectoMarkdown.render(text, { noteUrl: note.url, resolve: resolve });
    headings = out.headings;
    currentHeading = headings.length ? headings[0].id : "";
    const doc = $("doc");
    doc.innerHTML = out.html;
    $("empty").classList.add("hidden");
    doc.classList.remove("hidden");
    doc.scrollTop = 0;
    // wikilinks and note links navigate; callout titles fold
    doc.querySelectorAll("a.wikilink").forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        navigate(a.getAttribute("data-key") || "", a.getAttribute("data-anchor") || "");
      });
    });
    doc.querySelectorAll(".callout.foldable > .callout-title").forEach(function (t) {
      t.addEventListener("click", function () { t.parentElement.classList.toggle("folded"); });
    });
    doc.querySelectorAll("a.external").forEach(function (a) {
      a.addEventListener("click", function (ev) { ev.preventDefault(); Specto.log("info", "external link not opened: " + a.getAttribute("href")); });
    });
    renderOutline();
    SpectoMarkdown.runMermaid(doc).then(function (n) {
      if (anchor) scrollTo(anchor);
      Specto.setHealth("ok", n ? n + " diagram(s)" : "");
      reportSoon();
    });
    doc.querySelectorAll("img").forEach(function (i) { i.addEventListener("load", reportSoon); i.addEventListener("error", reportSoon); });
    reportSoon();
    document.title = "Markdown " + note.key;
  }

  function showNote(key, url, anchor, name) {
    current = { key: key, url: url };
    footer.setName(name || key, key);
    backBtn.classList.toggle("hidden", history.length === 0);
    const seq = ++fetchSeq;
    fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
      return r.text();
    }).then(function (text) {
      if (seq !== fetchSeq) return;
      setError("");
      mount(text, { key: key, url: url }, anchor);
    }).catch(function (e) {
      setError(e.message);
    });
  }

  function noteName(n) { return n.name ? n.name + (n.role || "") : n.key; }

  /* a wikilink click: show it, tell the host (it emits the selection) */
  function navigate(key, anchor) {
    if (!key) return;
    if (current && current.key === key) { if (anchor) scrollTo(anchor); return; }
    const n = noteByKey(key);
    if (!n) { setError("no note " + key); return; }
    if (current) history.push(current.key);
    showNote(n.key, n.url, anchor || "", noteName(n));
    Specto.send("navigate", { key: key, anchor: anchor || "" });
  }

  function back() {
    const key = history.pop();
    if (!key) return;
    const n = noteByKey(key);
    if (!n) return;
    showNote(n.key, n.url, "", noteName(n));
    Specto.send("navigate", { key: key, anchor: "" });
  }

  /** @param {SpectoItemNotify} item */
  function onItem(item) {
    if (!item.key) {
      current = null;
      headings = [];
      footer.setName("", "");
      renderOutline();
      showEmpty("no note selected");
      reportSoon();
      return;
    }
    if (!item.url) {
      setError(item.reason || "no markdown part for this item");
      reportSoon();
      return;
    }
    if (current && current.key === item.key) {  // our own navigation echoed back
      footer.setName(item.file || item.name || item.key, item.key);
      return;
    }
    showNote(item.key, item.url, "", item.file || item.name);
  }

  Specto.on("config", function (cfg) {
    applyParams(cfg.params);
    if (!current) Specto.setHealth("ok", "(no note selected)");
    reportSoon();
  });
  Specto.on("params", function (p) { applyParams(p); });
  Specto.on("notes", function (p) {
    notes = p.notes || [];
    reportSoon();
  });
  Specto.on("item", onItem);

  Specto.connect().catch(function (e) {
    setError(e.message);
    showEmpty(e.message);
  });
})();
