// @ts-check
/// <reference path="../../types/specto.d.ts" />
/* HTML view: shows the selected html file of its collection in a sandboxed frame. The
 * URL the host sends ends in the file's own name on the view's items origin, so the
 * page's relative stylesheets, scripts and images resolve next to it.
 *
 * Python sends: `item` {key, url, file, name} (or {key, url: null, reason}), `reload`
 * {network, capabilities} (the view's network grant changed). The page writes no
 * params. The footer (#130): step < >, the position and the file's name, and a reload
 * button for this page; the arrows and the page keys step through the collection's html
 * files (step.js, #107) while the focus is on this page, not inside the frame (the
 * user's page keeps its own keys). A problem shows as the view's health. */
(function () {
  "use strict";
  const $ = function (id) { return /** @type {HTMLElement} */ (document.getElementById(id)); };
  /** @type {{key: string, url: string} | null} */
  let current = null;

  const footer = SpectoFooter.mount($("footer"), { step: ["ArrowLeft", "ArrowRight", "PageUp", "PageDown"] });
  const reload = /** @type {HTMLButtonElement} */ (footer.tool("refresh", "Reload this page", reloadFrame, "reload"));
  reload.disabled = true;

  function show(key, url, name) {
    current = { key: key, url: url };
    footer.setName(name || key, key);
    const frame = /** @type {HTMLIFrameElement} */ ($("frame"));
    if (frame.getAttribute("src") !== url) frame.setAttribute("src", url);
    frame.classList.remove("hidden");
    $("empty").classList.add("hidden");
    reload.disabled = false;
    document.title = "HTML " + key;
    reportSoon();
  }

  function showEmpty(text) {
    current = null;
    footer.setName("", "");
    const frame = /** @type {HTMLIFrameElement} */ ($("frame"));
    frame.classList.add("hidden");
    frame.removeAttribute("src");
    $("empty").classList.remove("hidden");
    if (text) $("empty").textContent = text;
    reload.disabled = true;
    reportSoon();
  }

  /* what the page shows, for `snap` (agents, scripted tests) */
  let reportTimer = 0;
  function reportPage() {
    const frame = $("frame");
    Specto.report({
      file: current ? current.key : null,
      url: current ? current.url : null,
      frame: Specto.rectOf(frame.classList.contains("hidden") ? null : frame),
      buttons: { reload: Specto.rectOf(reload) },
      step: SpectoStep.rects(),
      dpr: window.devicePixelRatio,
    });
  }
  function reportSoon() { clearTimeout(reportTimer); reportTimer = window.setTimeout(reportPage, 150); }
  window.addEventListener("resize", reportSoon);
  Specto.on("step", reportSoon);

  /* load the shown page again: a fragment-only change of src does not (#99), so go
   * through about:blank */
  function reloadFrame() {
    const frame = /** @type {HTMLIFrameElement} */ ($("frame"));
    if (!current) return;
    const url = current.url;
    frame.setAttribute("src", "about:blank");
    window.setTimeout(function () { if (current && current.url === url) frame.setAttribute("src", url); }, 50);
  }

  $("frame").addEventListener("load", function () {
    if (current) Specto.setHealth("ok", "");
    reportSoon();
  });

  /* the view's network grant changed (#93): load the page again under it */
  Specto.on("reload", function (p) {
    if (Specto.config && p && p.capabilities) Specto.config.capabilities = p.capabilities;
    reloadFrame();
  });
  Specto.on("item", function (item) {
    if (!item || !item.key) { showEmpty(""); Specto.setHealth("ok", "(no file selected)"); return; }
    if (!item.url) {
      Specto.setHealth("error", item.reason || "nothing to show for " + item.key);
      return;
    }
    show(item.key, item.url, item.file || item.name);
  });

  Specto.connect().catch(function (e) { Specto.setHealth("error", String(e)); });
})();
