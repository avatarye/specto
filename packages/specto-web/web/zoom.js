/* No page zoom (#189): Ctrl+wheel (and a touchpad pinch, which Chromium sends as one)
 * was Chromium's page zoom, a factor on every CSS px of the page, so the side panels
 * (OUTLINE, LAYERS, the 3D panels) and the footer grew and shrank with it instead of
 * keeping the theme's size. This page refuses it; WebView.qml also pins the view's
 * zoomFactor at 1 for what no page script sees (the html view's iframe).
 *
 * A view that reads may zoom its content alone: `content(el)` scales that element (CSS
 * zoom, so the text reflows to the width) by Ctrl+wheel over it and Ctrl+= / Ctrl+- /
 * Ctrl+0 on the page, 50 % to 300 %, a notch 10 %.
 *
 *   const z = SpectoZoom.content(document.getElementById("doc"), {onChange(level)});
 *   z.level; z.set(1.5); z.reset()
 */
(function () {
  "use strict";
  const MIN = 0.5;
  const MAX = 3;
  const STEP = 1.1;          // per notch
  /** @type {{el: HTMLElement, set: (level: number) => void, by: (notches: number) => void} | null} */
  let zoomed = null;
  // whole notches on any display scale; a touchpad's deltas add up to steps (wheel.js)
  const notchesOf = SpectoWheel.counter();

  function clamp(level) { return Math.max(MIN, Math.min(MAX, Math.round(level * 100) / 100)); }

  function inside(el, target) {
    for (let n = target; n; n = n.parentNode) if (n === el) return true;
    return false;
  }

  document.addEventListener("wheel", function (ev) {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    if (!zoomed || !inside(zoomed.el, ev.target)) return;
    const notches = notchesOf(ev);
    if (notches) zoomed.by(notches);
  }, { capture: true, passive: false });

  window.addEventListener("keydown", function (ev) {
    if (!zoomed || !ev.ctrlKey || ev.altKey || ev.metaKey) return;
    if (ev.key === "=" || ev.key === "+") zoomed.by(1);
    else if (ev.key === "-") zoomed.by(-1);
    else if (ev.key === "0") zoomed.set(1);
    else return;
    ev.preventDefault();
  });

  /** @param {HTMLElement} el @param {{onChange?: (level: number) => void}} [o] */
  function content(el, o) {
    let level = 1;
    function set(l) {
      l = clamp(l);
      if (l === level) return;
      level = l;
      el.style.zoom = String(level);
      if (o && o.onChange) o.onChange(level);
    }
    zoomed = { el: el, set: set, by: function (n) { set(level * Math.pow(STEP, n)); } };
    return {
      get level() { return level; },
      set: set,
      reset: function () { set(1); },
    };
  }

  window.SpectoZoom = { content: content, MIN: MIN, MAX: MAX };
})();
