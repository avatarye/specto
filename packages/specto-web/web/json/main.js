// @ts-check
/// <reference path="../../types/specto.d.ts" />
/* Monaco JSON view: read-only, folding, find (Ctrl+F); shows the selected item of its
 * collection. Python sends `item` notifications {key, url, role, roles, name, file} (or
 * {key, url: null, reason}). The page never resolves paths itself; bytes come from
 * specto://items/... which the scheme handler serves only when this view holds
 * read:link:<its data link>. A content value linked in (a usd view's prim, #140) comes
 * inline instead: {key, url: null, text, role, file}. The one param the page writes is `wrap` (word wrap), as a
 * user submit through setParams.
 *
 * The footer (#130): step < >, the position and the file's name; a fold-all toggle and
 * the wrap toggle. PageUp / PageDown and the footer's < > step through the collection
 * (step.js, #107); the editor keeps the arrows for its caret, so it binds the page keys
 * to the step itself. A problem shows as the view's health. A drag from inside the
 * selection drags its text out (#185, wireDrag). */
(function () {
  "use strict";
  const $ = function (id) { return /** @type {HTMLElement} */ (document.getElementById(id)); };
  /** @type {any} */
  let editor = null;
  /** @type {any} */
  let model = null;
  /** @type {{wrap: boolean}} */
  let params = { wrap: false };
  /** @type {SpectoItemNotify | null} */
  let current = null;
  let folded = false;
  let fetchSeq = 0;
  let reportTimer = 0;
  const LANGUAGES = { ".json": "json", ".geojson": "json", ".md": "markdown", ".markdown": "markdown",
                      ".yaml": "yaml", ".yml": "yaml", ".toml": "ini", ".txt": "plaintext", ".csv": "plaintext", ".log": "plaintext" };

  const footer = SpectoFooter.mount($("footer"), { step: ["PageUp", "PageDown"] });
  const fold = footer.tool("fold", "Fold all", toggleFold, "fold");
  const wrap = footer.tool("wrap", "Word wrap", function () {
    const on = !params.wrap;
    applyParams({ wrap: on });  // now; the document echoes it back through `params`
    Specto.setParams({ wrap: on }).then(function (r) { if (r.ok === false) setError(r.error.message); });
  }, "wrap");

  function topLine() {
    const r = editor && editor.getVisibleRanges()[0];
    return r ? r.startLineNumber : 1;
  }

  /* what the page shows, for `snap` (agents, scripted tests) */
  function report() {
    Specto.report({
      item: current ? current.key : null,
      language: model ? model.getLanguageId() : null,
      topLine: topLine(),
      lines: model ? model.getLineCount() : 0,
      wrap: params.wrap,
      folded: folded,
      buttons: { fold: Specto.rectOf(fold), wrap: Specto.rectOf(wrap) },
      step: SpectoStep.rects(),
      dpr: window.devicePixelRatio,
    });
  }
  function reportSoon() { clearTimeout(reportTimer); reportTimer = window.setTimeout(report, 120); }

  function setError(msg) { Specto.setHealth(msg ? "error" : "ok", msg || ""); }

  function setFolded(on) {
    folded = on;
    footer.setOn(fold, on);
    SpectoIcons.setIcon(fold, on ? "unfold" : "fold");
    fold.title = on ? "Unfold all" : "Fold all";
  }

  function toggleFold() {
    if (!editor) return;
    editor.getAction(folded ? "editor.unfoldAll" : "editor.foldAll").run();
    setFolded(!folded);
    reportSoon();
  }

  function applyParams(p) {
    params = Object.assign({}, params, p || {});
    footer.setOn(wrap, !!params.wrap);
    if (editor) editor.updateOptions({ wordWrap: params.wrap ? "on" : "off" });
    reportSoon();
  }

  function showEmpty(html) {
    $("editor").classList.add("hidden");
    $("empty").classList.remove("hidden");
    $("empty").innerHTML = html;
  }

  /** @param {SpectoItemNotify} item */
  function showItem(item) {
    if (!item.key) {
      current = null;
      footer.setName("", "");
      showEmpty("Select an item in a linked gallery<br><small>the sidecar <code>.json</code> shows here</small>");
      reportSoon();
      return;
    }
    const inline = typeof item.text === "string";
    if (!item.url && !inline) {
      // not in the collection, or no readable part: say so, keep what is shown
      setError(item.reason || "no JSON part for this item");
      reportSoon();
      return;
    }
    current = item;
    footer.setName(item.file || item.key, item.key);
    const seq = ++fetchSeq;
    const text = inline
      ? Promise.resolve(/** @type {string} */ (item.text))
      : fetch(/** @type {string} */ (item.url)).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + " for " + item.url);
        return r.text();
      });
    text.then(function (text) {
      if (seq !== fetchSeq) return;   // a newer item arrived while fetching
      if (typeof monaco === "undefined") throw new Error("Monaco not loaded");
      const lang = LANGUAGES[(item.role || ".json").toLowerCase()] || "plaintext";
      let pretty = text;
      if (lang === "json") { try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* show raw */ } }
      $("empty").classList.add("hidden");
      $("editor").classList.remove("hidden");
      monaco.editor.setModelLanguage(model, lang);
      model.setValue(pretty);  // a new text: nothing folded
      setFolded(false);
      editor.setScrollTop(0);
      editor.layout();
      setError("");
      document.title = (lang === "json" ? "JSON " : "Source ") + item.key;
      report();
    }).catch(function (e) {
      setError(e.message);
    });
  }

  function makeEditor(monaco) {
    defineSpectoTheme(monaco);
    model = monaco.editor.createModel("", "json");
    editor = monaco.editor.create($("editor"), {
      model: model,
      theme: "specto-calm",
      readOnly: true,
      domReadOnly: true,
      folding: true,
      foldingStrategy: "indentation",
      showFoldingControls: "always",
      minimap: { enabled: false },
      lineNumbers: "on",
      renderLineHighlight: "line",
      scrollBeyondLastLine: false,
      automaticLayout: true,
      wordWrap: params.wrap ? "on" : "off",
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono") + ", Consolas, monospace",
      fontSize: 13,
      padding: { top: 8 },
      occurrencesHighlight: "off",
    });
    editor.onDidScrollChange(function (e) { if (e.scrollTopChanged) reportSoon(); });
    editor.addCommand(monaco.KeyCode.PageUp, function () { SpectoStep.go(-1); });
    editor.addCommand(monaco.KeyCode.PageDown, function () { SpectoStep.go(1); });
    wireDrag(monaco);
  }

  /* Drag out (#185): the read-only editor drags nothing itself, so a press inside the
   * selection that moves the platform's drag distance hands the selected text to the shell
   * (Specto.startDrag); a press there that does not move puts the caret under it, as a click
   * would. Presses elsewhere, and with a modifier, stay the editor's. */
  function wireDrag(monaco) {
    const box = $("editor");
    /** @type {{x: number, y: number, at: any} | null} */
    let press = null;
    const TEXT = monaco.editor.MouseTargetType ? monaco.editor.MouseTargetType.CONTENT_TEXT : null;
    /** @param {PointerEvent} ev */
    function down(ev) {
      press = null;
      if (ev.button !== 0 || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey) return;
      const sel = editor.getSelection();
      if (!sel || sel.isEmpty()) return;
      const t = editor.getTargetAtClientPoint(ev.clientX, ev.clientY);
      if (!t || !t.position || (TEXT !== null && t.type !== TEXT) || !sel.containsPosition(t.position)) return;
      press = { x: ev.clientX, y: ev.clientY, at: t.position };
      ev.preventDefault();
      ev.stopPropagation();
    }
    box.addEventListener("pointerdown", down, true);
    box.addEventListener("mousedown", function (/** @type {MouseEvent} */ ev) {
      if (press) { ev.preventDefault(); ev.stopPropagation(); }
    }, true);
    window.addEventListener("pointermove", function (/** @type {PointerEvent} */ ev) {
      if (!press) return;
      if (!(ev.buttons & 1)) { press = null; return; }
      const dist = (Specto.config && Specto.config.dragDistance) || 10;
      if (Math.abs(ev.clientX - press.x) + Math.abs(ev.clientY - press.y) < dist) return;
      press = null;
      Specto.startDrag({ text: model.getValueInRange(editor.getSelection()) });
    }, true);
    window.addEventListener("pointerup", function () {
      if (!press) return;
      const at = press.at;
      press = null;
      editor.setPosition(at);
      editor.focus();
    }, true);
  }

  Specto.on("config", function (cfg) {
    applyParams(cfg.params);
    if (window.monaco) defineSpectoTheme(window.monaco);
    if (!current) Specto.setHealth("ok", "(no item selected)");  // the page is up; waiting is not loading
  });
  Specto.on("params", function (p) { applyParams(p); });
  Specto.on("item", showItem);
  Specto.on("step", reportSoon);

  loadMonaco().then(function (monaco) {
    makeEditor(monaco);
    return Specto.connect();
  }).catch(function (e) {
    showEmpty(e.message);
    Specto.setHealth("error", e.message);
  });
})();
