// @ts-check
/// <reference path="../../types/specto.d.ts" />
/* Scratch pad page (#184): one page of the project's scratch pad, a markdown note,
 * rendered like the markdown view (SpectoMarkdown: GitHub style, Mermaid, KaTeX,
 * callouts; the OUTLINE side panel) or edited as source in Monaco. The tab strip and
 * the drop target are the view's QML around this page; the host keeps the markdown.
 *
 * Python sends `page` {id, name, url, text, rev} (url: the note's specto://items URL, so
 * relative pictures under <page>.assets/ resolve; text: the markdown itself). The page
 * sends `save` {page, text, rev} (the source, autosaved SAVE_MS after the last keystroke
 * and when leaving the source; one undo step each), `caret` {page, offset} (where a drop
 * lands while the source shows; offset null in the rendered view: drops append),
 * `open` {href} (a link to a file clicked: the host shows it in the pad's preview) and
 * `paste` {} (Ctrl+V in the rendered view, or a picture pasted in the source: the host
 * reads the clipboard and adds it like a drop).
 *
 * The footer: the page's name, the rendered / source toggle (E) and, in the source, wrap.
 * Clicks on external links do nothing; a link to another note's .md does nothing either
 * (scratch pages link files, not notes). A problem shows as the view's health. */
(function () {
  "use strict";
  const $ = function (id) { return /** @type {HTMLElement} */ (document.getElementById(id)); };
  const SAVE_MS = 600;
  /** @type {{id: string, name: string, url: string, text: string, rev: number} | null} */
  let page = null;
  let source = false;   // the source editor shows
  let dirty = false;    // the editor holds text not saved yet
  let saveTimer = 0;
  let reportTimer = 0;
  /** @type {any} */
  let editor = null;
  /** @type {any} */
  let model = null;
  let settingModel = false;
  /** @type {{level: number, text: string, id: string}[]} */
  let headings = [];
  let currentHeading = "";

  const footer = SpectoFooter.mount($("footer"), { step: null });
  const nameEl = document.createElement("span");
  nameEl.className = "footer-name";
  nameEl.id = "footerName";
  footer.el.insertBefore(nameEl, footer.el.firstChild);
  const toggle = footer.tool("edit", "Edit the markdown (E)", function () { setSource(!source); }, "toggleSource");
  const wrapBtn = footer.tool("wrap", "Word wrap", function () {
    const on = !(editor && editor.getOption(monaco.editor.EditorOption.wordWrap) !== "off");
    if (editor) editor.updateOptions({ wordWrap: on ? "on" : "off" });
    footer.setOn(wrapBtn, on);
  }, "wrap");
  footer.setOn(wrapBtn, true);

  const panel = SpectoSidePanel.mount($("row"), {
    // the pad is narrow: the outline starts folded to its strip
    id: "outline", title: "Outline", side: "right", keep: "outline", allNone: false,
    width: 160, minWidth: 120, maxWidth: 420, collapsed: true,
    onCollapse: reportSoon,
    onResize: reportSoon,
  });

  function setError(msg) { Specto.setHealth(msg ? "error" : "ok", msg || ""); }

  /* ---- rendered ------------------------------------------------------------------------ */
  function renderOutline() {
    const list = panel.body;
    list.innerHTML = "";
    if (!headings.length) {
      const none = document.createElement("div");
      none.className = "outline-none";
      none.textContent = page ? "no headings" : "";
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
      row.addEventListener("click", function () {
        const el = document.getElementById(h.id);
        if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
      });
      list.appendChild(row);
    });
  }

  function isFileLink(href) {
    return !!href && !/^(https?|mailto):/i.test(href) && href.charAt(0) !== "#";
  }

  function renderDoc() {
    const doc = $("doc");
    const text = page ? page.text : "";
    $("empty").classList.toggle("hidden", !!(text && text.trim()) || source);
    doc.classList.toggle("hidden", source || !(text && text.trim()));
    if (!page) return;
    const out = SpectoMarkdown.render(text, { noteUrl: page.url, resolve: function () { return null; } });
    headings = out.headings;
    currentHeading = headings.length ? headings[0].id : "";
    const keep = doc.scrollTop;
    doc.innerHTML = out.html;
    doc.scrollTop = keep;
    doc.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        const href = a.getAttribute("href") || "";
        if (isFileLink(href) && !a.classList.contains("wikilink")) Specto.send("open", { href: href });
        else Specto.log("info", "link not opened here: " + href);
      });
    });
    doc.querySelectorAll(".callout.foldable > .callout-title").forEach(function (t) {
      t.addEventListener("click", function () { t.parentElement.classList.toggle("folded"); });
    });
    doc.querySelectorAll("img").forEach(function (i) { i.addEventListener("load", reportSoon); i.addEventListener("error", reportSoon); });
    renderOutline();
    SpectoMarkdown.runMermaid(doc).then(reportSoon);
    reportSoon();
  }

  /* ---- source -------------------------------------------------------------------------- */
  function caret() {
    if (!source || !editor || !model) return null;
    const pos = editor.getPosition();
    return pos ? model.getOffsetAt(pos) : null;
  }

  function sendCaret() {
    if (page) Specto.send("caret", { page: page.id, offset: caret() });
  }

  function saveNow() {
    clearTimeout(saveTimer);
    if (!dirty || !page || !model) return;
    dirty = false;
    page.text = model.getValue();
    Specto.send("save", { page: page.id, text: page.text, rev: page.rev });
  }

  function setSource(on) {
    if (on === source) return;
    if (!on) saveNow();
    source = on;
    footer.setOn(toggle, on);
    SpectoIcons.setIcon(toggle, on ? "eye" : "edit");
    toggle.title = on ? "Show it rendered (E)" : "Edit the markdown (E)";
    wrapBtn.classList.toggle("hidden", !on);
    $("editor").classList.toggle("hidden", !on);
    if (on && editor) {
      if (model.getValue() !== (page ? page.text : "")) setModel(page ? page.text : "");
      editor.layout();
      editor.focus();
    }
    renderDoc();
    sendCaret();
    reportSoon();
  }

  function setModel(text) {
    settingModel = true;
    const pos = editor.getPosition();
    model.setValue(text);
    if (pos) editor.setPosition(pos);
    settingModel = false;
  }

  function makeEditor(monaco) {
    defineSpectoTheme(monaco);
    model = monaco.editor.createModel("", "markdown");
    editor = monaco.editor.create($("editor"), {
      model: model,
      theme: "specto-calm",
      minimap: { enabled: false },
      lineNumbers: "off",
      wordWrap: "on",
      scrollBeyondLastLine: false,
      automaticLayout: true,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono") + ", Consolas, monospace",
      fontSize: 13,
      padding: { top: 8 },
      occurrencesHighlight: "off",
    });
    model.onDidChangeContent(function () {
      if (settingModel) return;
      dirty = true;
      clearTimeout(saveTimer);
      saveTimer = window.setTimeout(saveNow, SAVE_MS);
    });
    editor.onDidChangeCursorPosition(function () { sendCaret(); });
    editor.onDidBlurEditorText(function () { saveNow(); });
    editor.addCommand(monaco.KeyCode.Escape, function () { setSource(false); });
  }

  /* ---- the host ------------------------------------------------------------------------ */
  function onPage(p) {
    const other = !page || page.id !== p.id;
    if (other && page) saveNow();
    page = p;
    nameEl.textContent = p.name || "";
    nameEl.title = (p.name || "") + " (.specto/scratch/" + p.id + ".md)";
    if (source && editor) {
      // an outside change (an undo, the agent, a drop): the editor takes it unless the
      // user is typing on this very page
      if (other || !dirty) setModel(p.text);
    }
    if (other) $("doc").scrollTop = 0;
    renderDoc();
    setError("");
    document.title = "Scratch " + p.name;
    sendCaret();
  }

  function report() {
    const doc = $("doc");
    Specto.report({
      page: page ? page.id : null,
      name: page ? page.name : null,
      source: source,
      dirty: dirty,
      chars: page ? page.text.length : 0,
      caret: caret(),
      headings: headings.map(function (h) { return { level: h.level, text: h.text, id: h.id }; }),
      images: Array.prototype.map.call(doc.querySelectorAll("img"), function (i) { return { src: i.getAttribute("src"), loaded: i.complete && i.naturalWidth > 0, rect: Specto.rectOf(i) }; }),
      links: Array.prototype.map.call(doc.querySelectorAll("a"), function (a) { return { href: a.getAttribute("href"), text: a.textContent, rect: Specto.rectOf(a) }; }),
      quotes: doc.querySelectorAll("blockquote").length,
      panel: panel.rects(),
      buttons: { toggle: Specto.rectOf(toggle), wrap: wrapBtn.classList.contains("hidden") ? null : Specto.rectOf(wrapBtn) },
      editor: source ? Specto.rectOf($("editor")) : null,
      dpr: window.devicePixelRatio,
    });
  }
  function reportSoon() { clearTimeout(reportTimer); reportTimer = window.setTimeout(report, 150); }
  $("doc").addEventListener("scroll", reportSoon);
  window.addEventListener("resize", reportSoon);

  /* E toggles (outside the editor); Ctrl+V in the rendered view pastes through the host,
   * which reads the clipboard like a drop (a picture, files, text): on the key itself, as
   * a page with nothing editable may get no paste event */
  document.addEventListener("keydown", function (ev) {
    if (source) return;
    const plain = !ev.ctrlKey && !ev.altKey && !ev.metaKey;
    if ((ev.key === "e" || ev.key === "E") && plain) {
      ev.preventDefault();
      setSource(true);
    } else if ((ev.key === "v" || ev.key === "V") && ev.ctrlKey && !ev.altKey) {
      ev.preventDefault();
      Specto.send("paste", {});
    }
  });
  /* in the source, a pasted picture goes through the host too (Monaco takes text) */
  document.addEventListener("paste", function (ev) {
    const data = ev.clipboardData;
    const picture = !!data && Array.prototype.some.call(data.items || [], function (it) { return it.kind === "file"; });
    if (!source || !picture) return;
    ev.preventDefault();
    Specto.send("paste", {});
  }, true);

  Specto.on("config", function () {
    if (window.monaco) defineSpectoTheme(window.monaco);
    Specto.setHealth("ok", "");
    reportSoon();
  });
  Specto.on("page", onPage);
  Specto.on("source", function (p) { setSource(!!(p && p.on)); });

  wrapBtn.classList.add("hidden");
  loadMonaco().then(function (monaco) {
    makeEditor(monaco);
    return Specto.connect();
  }).catch(function (e) {
    setError(e.message);
    return Specto.connect();
  });
})();
