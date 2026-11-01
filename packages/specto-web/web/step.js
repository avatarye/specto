/* Previous / next through a web viewer's input collection (#107; the Python side is
 * specto_app.hosts.stepping), in the view's footer (#130): two chevron buttons and the
 * position ("2 / 7"), and keys while the page has the focus. The host decides
 * everything (collection order, stopping at the ends, emitting the pick); the page
 * sends `step {delta}` and hears `step {index, count}` back, which dims a button at an
 * end and hides the three while there is nothing to step to.
 *
 *   SpectoStep.mount(footerEl, ["ArrowLeft", "ArrowRight", "PageUp", "PageDown"])
 *
 * A key steps only when nothing on the page consumes it: a text field, a select, a
 * modifier chord, or a handler that already called preventDefault. Keys typed inside
 * the html view's frame stay with the user's page (the frame's events never reach this
 * one). An editor that binds the keys itself (Monaco, in the json view) calls
 * SpectoStep.go(delta) from its own key binding. */
(function () {
  "use strict";
  const DELTA = { ArrowLeft: -1, PageUp: -1, ArrowRight: 1, PageDown: 1 };
  let state = { index: -1, count: 0 };
  /** @type {HTMLButtonElement | null} */
  let prev = null;
  /** @type {HTMLButtonElement | null} */
  let next = null;
  /** @type {HTMLElement | null} */
  let pos = null;

  function go(delta) { Specto.send("step", { delta: delta }); }

  function button(id, icon, delta, tip) {
    const b = SpectoIcons.button(icon, tip, id);
    b.classList.add("step");
    b.addEventListener("click", function () { go(delta); });
    return b;
  }

  function update() {
    if (!prev || !next || !pos) return;
    const none = state.count < 2;
    [prev, next, pos].forEach(function (e) { e.classList.toggle("hidden", none); });
    prev.disabled = state.index <= 0;
    next.disabled = state.index < 0 || state.index >= state.count - 1;
    pos.textContent = state.index >= 0 ? (state.index + 1) + " / " + state.count : "";
  }

  function consumed(ev) {
    if (ev.defaultPrevented || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return true;
    const t = ev.target;
    if (!t || !t.tagName) return false;
    return /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || !!t.isContentEditable;
  }

  /** @param {HTMLElement} container @param {string[]} keys */
  function mount(container, keys) {
    const names = keys.map(function (k) { return k.replace("Arrow", ""); }).join(" / ");
    prev = button("stepPrev", "chevron-left", -1, "previous item (" + names + ")");
    next = button("stepNext", "chevron-right", 1, "next item (" + names + ")");
    pos = document.createElement("span");
    pos.className = "footer-pos";
    pos.id = "stepPos";
    container.appendChild(prev);
    container.appendChild(next);
    container.appendChild(pos);
    update();
    window.addEventListener("keydown", function (ev) {
      if (keys.indexOf(ev.key) < 0 || consumed(ev)) return;
      ev.preventDefault();
      go(DELTA[ev.key]);
    });
  }

  /* CSS px rects of the two controls for the page's `report` (scripted flows click them) */
  function rects() {
    const shown = prev && !prev.classList.contains("hidden");
    return { prev: shown ? Specto.rectOf(prev) : null, next: shown ? Specto.rectOf(next) : null };
  }

  Specto.on("step", function (s) { state = s || { index: -1, count: 0 }; update(); });

  window.SpectoStep = {
    mount: mount, go: go, rects: rects,
    get state() { return state; },
  };
})();
