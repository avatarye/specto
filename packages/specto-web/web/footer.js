/* The web views' footer (#130, Figma 05 UX pass section 4): one 24 px row under the
 * view's body, a top divider, 11 px text. Viewers share its left side (step < > from
 * step.js, the position, the shown item's name in mono); the kind's one or two tools
 * sit on the right as icon buttons (icons.js), a toggle that is on in accent.
 *
 *   const f = SpectoFooter.mount(document.getElementById("footer"), {step: keys | null});
 *   f.setName("pieces.md", "notes/pieces.md");
 *   const wrap = f.tool("wrap", "Word wrap", onClick, "wrap"); f.setOn(wrap, true);
 *
 * Without `step` (the map) the footer is its tools alone.
 *
 * A view with a large input loads it in batches (#181): `f.meter(onMore, {id, noun})`
 * puts a loaded / total meter on the left (a slim bar, "200 / 1,350" and a Load more
 * button), hidden until `set(loaded, total, {shown, next})` shows it. Shown, it stays
 * visible at rest (#158); Load more is disabled once everything is loaded.
 *
 *   const m = f.meter(function () { loadNext(); }, {id: "loadMore", noun: "layers"});
 *   m.set(200, 1350, {shown: true, next: 200});
 */
(function () {
  "use strict";

  /** @param {number} n */
  function count(n) { return Number(n).toLocaleString("en-US"); }

  /** @param {HTMLElement} el @param {HTMLElement} before
   *  @param {() => void} onMore @param {{id?: string, noun?: string}} [opts] */
  function meter(el, before, onMore, opts) {
    const noun = (opts && opts.noun) || "items";
    const wrap = document.createElement("span");
    wrap.className = "footer-meter hidden";
    const track = document.createElement("span");
    track.className = "meter-track";
    const fill = document.createElement("span");
    fill.className = "meter-fill";
    track.appendChild(fill);
    const text = document.createElement("span");
    text.className = "meter-text";
    const more = document.createElement("button");
    more.className = "quiet meter-more";
    more.textContent = "Load more";
    if (opts && opts.id) more.id = opts.id;
    more.addEventListener("click", function () { onMore(); });
    wrap.appendChild(track);
    wrap.appendChild(text);
    wrap.appendChild(more);
    el.insertBefore(wrap, before);
    const state = { loaded: 0, total: 0, shown: false };
    return {
      el: wrap,
      button: more,
      state: state,
      /** loaded of total; `shown` (default: some are not loaded) shows the meter, `next`
       *  is how many Load more adds */
      set: function (loaded, total, o) {
        const shown = o && o.shown !== undefined ? !!o.shown : loaded < total;
        state.loaded = loaded; state.total = total; state.shown = shown;
        wrap.classList.toggle("hidden", !shown);
        const pct = total > 0 ? Math.min(100, Math.round((loaded / total) * 1000) / 10) : 0;
        fill.style.width = pct + "%";
        text.textContent = count(loaded) + " / " + count(total);
        wrap.title = count(loaded) + " of " + count(total) + " " + noun + " loaded";
        const left = Math.max(0, total - loaded);
        more.disabled = left === 0;
        const next = o && o.next ? Math.min(o.next, left) : left;
        more.title = left === 0 ? "every one of the " + count(total) + " " + noun + " is loaded"
          : "Load the next " + count(next) + " of the " + count(left) + " " + noun + " not loaded";
      },
    };
  }

  /** @param {HTMLElement} el @param {{step?: string[] | null}} opts */
  function mount(el, opts) {
    el.classList.add("footer");
    /** @type {HTMLElement | null} */
    let name = null;
    if (opts && opts.step) {
      SpectoStep.mount(el, opts.step);
      name = document.createElement("span");
      name.className = "footer-name";
      name.id = "footerName";
      el.appendChild(name);
    }
    const spacer = document.createElement("span");
    spacer.className = "footer-spacer";
    el.appendChild(spacer);

    return {
      el: el,
      /** the shown item's name; empty while nothing is shown */
      setName: function (text, tip) {
        if (!name) return;
        name.textContent = text || "";
        name.title = tip || text || "";
      },
      /** an icon button on the right, in the order added */
      tool: function (icon, tip, onClick, id) {
        const b = SpectoIcons.button(icon, tip, id);
        b.addEventListener("click", function () { onClick(); });
        el.appendChild(b);
        return b;
      },
      setOn: function (b, on) { b.classList.toggle("on", !!on); },
      /** a loaded / total meter with Load more, on the left (before the spacer) */
      meter: function (onMore, o) { return meter(el, spacer, onMore, o); },
    };
  }

  window.SpectoFooter = { mount: mount };
})();
