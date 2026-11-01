/* The side panel standard (#130, Figma 05 UX pass 44:670): one panel for every web view
 * that has one (3D view controls and items, map layers, markdown outline).
 *
 * Expanded: a 24 px header (TITLE, All / None when the panel lists items, the collapse
 * button), the view's rows in `body`, and a grip on the inner edge (a 2 px bar, 30 px
 * tall, over a 6 px band) that sets the width. Collapsed: a 24 px strip with the expand
 * button and the title on its side. The view keeps the width and the collapse in ONE
 * view param, `panels` = {<keep name>: {collapsed, width}}: a panel mounted with `keep`
 * writes the whole map through Specto.setParams on a user's change (a resize once, at
 * the end of the drag), and SpectoSidePanel.restore(params.panels) puts the kept state
 * back (from `config` and every `params`) without writing. onCollapse / onResize still
 * tell the view; apply({collapsed, width}) sets state without calling back; toggle() is
 * the user's collapse or expand from a control outside the panel (a footer toggle, #157),
 * written and called back like the panel's own buttons.
 *
 *   const p = SpectoSidePanel.mount(row, {id: "layers", title: "Layers", side: "right",
 *     keep: "layers", allNone: true, width: 180, minWidth: 120, maxWidth: 420,
 *     onAll, onNone, onCollapse(collapsed), onResize(width)});
 *   p.body.appendChild(SpectoSidePanel.row({checked, name, sub, title, onToggle, onClick}));
 *   SpectoSidePanel.restore(params.panels); p.rects(); p.toggle()
 *
 * `row` is the flex row the panel sits in: a left panel goes first, a right one last;
 * its collapse buttons show the divider on its own side (sidebar-left / sidebar). */
(function () {
  "use strict";
  const STRIP = 24;
  /** the view's `panels` param as last known, and the panels kept in it by name */
  let known = {};
  /** @type {Record<string, any>} */
  const kept = {};

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function link(cls, text, tip, fn) {
    const b = el("button", "panel-link " + cls, text);
    b.title = tip;
    b.addEventListener("click", function () { if (fn) fn(); });
    return b;
  }

  /** @param {HTMLElement} row @param {any} o */
  function mount(row, o) {
    const side = o.side === "left" ? "left" : "right";
    const min = o.minWidth || 120;
    const max = o.maxWidth || 480;
    const clamp = function (w) { return Math.max(min, Math.min(max, Math.round(w))); };
    const title = String(o.title || "").toUpperCase();
    let width = clamp(o.width || 180);
    let collapsed = !!o.collapsed;

    const panel = el("aside", "side-panel " + side);
    if (o.id) panel.id = o.id;

    const head = el("div", "panel-head");
    head.appendChild(el("span", "panel-title", title));
    head.appendChild(el("span", "panel-spacer"));
    if (o.allNone) {
      head.appendChild(link("panel-all", "All", "check every row", o.onAll));
      head.appendChild(link("panel-none", "None", "uncheck every row", o.onNone));
    }
    const glyph = side === "left" ? "sidebar-left" : "sidebar";
    const hide = SpectoIcons.button(glyph, "collapse the " + o.title.toLowerCase() + " panel");
    hide.classList.add("panel-toggle");
    head.appendChild(hide);

    const body = el("div", "panel-body");
    const grip = el("div", "panel-grip");
    grip.title = "drag to set the width";

    const strip = el("div", "panel-strip");
    const show = SpectoIcons.button(glyph, "show the " + o.title.toLowerCase() + " panel");
    show.classList.add("panel-toggle", "on");
    strip.appendChild(show);
    strip.appendChild(el("span", "panel-strip-title", title));

    panel.appendChild(head);
    panel.appendChild(body);
    panel.appendChild(grip);
    panel.appendChild(strip);
    if (side === "left") row.insertBefore(panel, row.firstChild);
    else row.appendChild(panel);

    function layout() {
      panel.classList.toggle("collapsed", collapsed);
      panel.style.width = (collapsed ? STRIP : width) + "px";
    }

    function setCollapsed(c, user) {
      c = !!c;
      if (c === collapsed) return;
      collapsed = c;
      layout();
      if (!user) return;
      write();
      if (o.onCollapse) o.onCollapse(collapsed);
    }

    /* a kept panel's user change: the whole `panels` map, one user submit */
    function write() {
      if (!o.keep) return;
      known = Object.assign({}, known);
      known[o.keep] = { collapsed: collapsed, width: width };
      Specto.setParams({ panels: known });
    }

    function apply(state) {
      if (!state) return;
      if (typeof state.width === "number" && isFinite(state.width)) width = clamp(state.width);
      if (typeof state.collapsed === "boolean") collapsed = state.collapsed;
      layout();
    }

    hide.addEventListener("click", function () { setCollapsed(true, true); });
    show.addEventListener("click", function () { setCollapsed(false, true); });

    /* the grip: pointer capture keeps the moves coming while the pointer is off it */
    let drag = null;
    grip.addEventListener("pointerdown", function (ev) {
      if (collapsed || ev.button !== 0) return;
      drag = { x: ev.clientX, w: width, moved: false };
      grip.setPointerCapture(ev.pointerId);
      panel.classList.add("resizing");
      ev.preventDefault();
    });
    grip.addEventListener("pointermove", function (ev) {
      if (!drag) return;
      const dx = ev.clientX - drag.x;
      const w = clamp(drag.w + (side === "right" ? -dx : dx));
      if (w === width && !drag.moved) return;
      drag.moved = true;
      width = w;
      layout();
    });
    function end(ev) {
      if (!drag) return;
      const moved = drag.moved && width !== drag.w;
      drag = null;
      panel.classList.remove("resizing");
      if (ev && ev.pointerId !== undefined) grip.releasePointerCapture(ev.pointerId);
      if (!moved) return;
      write();
      if (o.onResize) o.onResize(width);
    }
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);

    layout();

    const api = {
      el: panel,
      body: body,
      get collapsed() { return collapsed; },
      get width() { return width; },
      apply: apply,
      setCollapsed: function (c) { setCollapsed(c, false); },
      toggle: function () { setCollapsed(!collapsed, true); },
      setWidth: function (w) { apply({ width: w }); },
      /* CSS px rects for the page's `report` (scripted flows click them) */
      rects: function () {
        const r = Specto.rectOf;
        return {
          panel: r(panel),
          collapsed: collapsed,
          width: width,
          toggle: r(collapsed ? show : hide),
          grip: collapsed ? null : r(grip),
          all: collapsed || !o.allNone ? null : r(head.querySelector(".panel-all")),
          none: collapsed || !o.allNone ? null : r(head.querySelector(".panel-none")),
        };
      },
    };
    if (o.keep) {
      kept[o.keep] = api;
      apply(known[o.keep]);
    }
    return api;
  }

  /** The view's `panels` param (from `config` or `params`): every kept panel takes its
   * entry; nothing is written back. */
  function restore(panels) {
    known = panels && typeof panels === "object" ? Object.assign({}, panels) : {};
    Object.keys(kept).forEach(function (name) { kept[name].apply(known[name]); });
  }

  /** An ITEMS row: a check, the name in mono and an optional small second line. A click
   * on the check is onToggle(checked); on the rest of the row onClick, or the toggle
   * when there is no onClick. */
  function row(o) {
    const r = el("div", "panel-row");
    if (o.title) r.title = o.title;
    let checked = !!o.checked;
    const check = el("span", "check" + (checked ? " on" : ""));
    check.setAttribute("role", "checkbox");
    const text = el("span", "row-text");
    text.appendChild(el("span", "name", o.name || ""));
    if (o.sub) text.appendChild(el("span", "sub", o.sub));
    r.appendChild(check);
    r.appendChild(text);
    function toggle() {
      checked = !checked;
      check.classList.toggle("on", checked);
      if (o.onToggle) o.onToggle(checked);
    }
    check.addEventListener("click", function (ev) { ev.stopPropagation(); toggle(); });
    r.addEventListener("click", function () { if (o.onClick) o.onClick(); else toggle(); });
    return r;
  }

  /** A size, 1024-based like the file tree's details: 12 B, 1.5 KB, 6.1 MB, 48 MB. */
  function bytes(n) {
    if (!(n >= 1024)) return Math.max(0, Math.round(n || 0)) + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let v = n;
    let i = -1;
    do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
    return (v < 10 ? v.toFixed(1) : String(Math.round(v))) + " " + units[i];
  }

  window.SpectoSidePanel = { mount: mount, restore: restore, row: row, bytes: bytes, STRIP: STRIP };
})();
