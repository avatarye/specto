/* Monaco bootstrap for pages served from a specto:// host. Workers come from the same
 * origin via a blob shim; nothing is fetched from the network. Call `loadMonaco()` after
 * /vendor/vs/loader.js is on the page; it resolves with the `monaco` namespace. */
(function () {
  "use strict";
  const base = location.origin + "/vendor/vs";
  self.MonacoEnvironment = {
    // baseUrl is the directory that CONTAINS vs/ (the worker resolves "vs/..." under it)
    getWorkerUrl: function () {
      const src = "self.MonacoEnvironment={baseUrl:" + JSON.stringify(location.origin + "/vendor/") + "};" +
        "importScripts(" + JSON.stringify(base + "/base/worker/workerMain.js") + ");";
      return URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    },
  };
  window.loadMonaco = function () {
    return new Promise(function (resolve, reject) {
      if (typeof require === "undefined") {
        reject(new Error("Monaco loader missing: run packages/specto-web/build.py"));
        return;
      }
      require.config({ paths: { vs: base } });
      require(["vs/editor/editor.main"], function () { resolve(window.monaco); }, reject);
    });
  };
  /* The editor theme is derived from the CSS variables, so it follows the app's tokens
   * (call again after Specto.applyTheme to pick up new values). */
  window.defineSpectoTheme = function (monaco) {
    const css = getComputedStyle(document.documentElement);
    const v = function (name, fallback) { return (css.getPropertyValue(name) || fallback).trim(); };
    const hex = function (c) { return c.replace("#", "").slice(0, 6); };
    monaco.editor.defineTheme("specto-calm", {
      base: "vs",
      inherit: true,
      rules: [
        { token: "string.key.json", foreground: hex(v("--color-accent", "#2f6fab")) },
        { token: "number", foreground: "8a5a00" },
        { token: "string.value.json", foreground: hex(v("--color-added", "#3f8f5a")) },
        { token: "keyword.json", foreground: hex(v("--color-agent", "#b86a2b")) },
      ],
      colors: {
        "editor.background": v("--color-surface", "#ffffff"),
        "editor.foreground": v("--color-text", "#3a3630"),
        "editorLineNumber.foreground": v("--color-text-faint", "#a89f91"),
        "editorLineNumber.activeForeground": v("--color-text-muted", "#7a7266"),
        "editor.lineHighlightBackground": v("--color-surface-alt", "#faf7f1"),
        "editor.selectionBackground": v("--color-accent-soft", "#dce8f5"),
        "editorGutter.background": v("--color-surface", "#ffffff"),
        "editorIndentGuide.background": v("--color-border", "#e3dccd"),
        "diffEditor.insertedTextBackground": v("--color-added-soft", "#e1f1e6") + "cc",
        "diffEditor.removedTextBackground": v("--color-removed-soft", "#f6dedd") + "cc",
        "diffEditor.insertedLineBackground": v("--color-added-soft", "#e1f1e6") + "66",
        "diffEditor.removedLineBackground": v("--color-removed-soft", "#f6dedd") + "66",
        "diffEditor.border": v("--color-border", "#e3dccd"),
        "editorWidget.background": v("--color-surface", "#ffffff"),
        "editorWidget.border": v("--color-border", "#e3dccd"),
        "focusBorder": v("--color-accent", "#2f6fab"),
        "scrollbarSlider.background": v("--color-bg-tint", "#ece6da") + "cc",
      },
    });
    monaco.editor.setTheme("specto-calm");
  };
})();
