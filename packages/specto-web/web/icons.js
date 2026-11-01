/* The line icons of the web views' footers and side panels (#130), the same 14 px
 * glyphs as the Figma 05 UX pass (HeaderButton 21:204): 1.17 px round strokes in
 * currentColor, so a button's colour (muted at rest, accent when on, faint when
 * disabled) tints them.
 *
 *   SpectoIcons.svg("globe")                    -> <svg> element
 *   SpectoIcons.button("wrap", "Word wrap", "wrap") -> <button class="icon-btn" id="wrap">
 *
 * A toggle that is on gets the `on` class (accent-soft fill, accent glyph). */
(function () {
  "use strict";
  const PATHS = {
    "chevron-left": "M8.75 3.5L5.25 7L8.75 10.5",
    "chevron-right": "M5.25 3.5L8.75 7L5.25 10.5",
    back: "M5.83333 10.5L2.33333 7L5.83333 3.5M2.33333 7H11.6667",
    sidebar: "M8.75 2.33333V11.6667M2.91667 2.33333H11.0833C11.7277 2.33333 12.25 2.85567 12.25 3.5V10.5C12.25 11.1443 11.7277 11.6667 11.0833 11.6667H2.91667C2.27233 11.6667 1.75 11.1443 1.75 10.5V3.5C1.75 2.85567 2.27233 2.33333 2.91667 2.33333Z",
    // a house: back to the world origin (the 3D view's VIEW panel, #129)
    home: "M1.75 6.41667L7 2.04167L12.25 6.41667M3.20833 5.25V11.6667H10.7917V5.25",
    globe: "M12.25 7C12.25 9.8995 9.8995 12.25 7 12.25M12.25 7C12.25 4.10051 9.8995 1.75 7 1.75M12.25 7H1.75M7 12.25C4.10051 12.25 1.75 9.8995 1.75 7M7 12.25C8.45833 10.7333 9.15833 8.98333 9.15833 7C9.15833 5.01667 8.45833 3.26667 7 1.75M7 12.25C5.54167 10.7333 4.84167 8.98333 4.84167 7C4.84167 5.01667 5.54167 3.26667 7 1.75M1.75 7C1.75 4.10051 4.10051 1.75 7 1.75",
    // a stack of sheets: the map's LAYERS panel shown or not (#157)
    layers: "M7 1.75L12.25 4.375L7 7L1.75 4.375Z M1.75 7L7 9.625L12.25 7M1.75 9.625L7 12.25L12.25 9.625",
    target: "M10.5 7C10.5 8.933 8.933 10.5 7 10.5M10.5 7C10.5 5.067 8.933 3.5 7 3.5M10.5 7H12.8333M7 10.5C5.067 10.5 3.5 8.933 3.5 7M7 10.5V12.8333M3.5 7C3.5 5.067 5.067 3.5 7 3.5M3.5 7H1.16667M7 3.5V1.16667",
    fold: "M4.08333 2.33333L7 5.25L9.91667 2.33333M4.08333 11.6667L7 8.75L9.91667 11.6667",
    unfold: "M4.08333 5.25L7 2.33333L9.91667 5.25M4.08333 8.75L7 11.6667L9.91667 8.75",
    wrap: "M2.33333 3.5H11.6667M2.33333 7H9.33333C9.79746 7 10.2426 7.18437 10.5708 7.51256C10.899 7.84075 11.0833 8.28587 11.0833 8.75C11.0833 9.21413 10.899 9.65925 10.5708 9.98744C10.2426 10.3156 9.79746 10.5 9.33333 10.5H7.58333M8.16667 9.04167L6.70833 10.5L8.16667 11.9583M2.33333 10.5H4.66667",
    refresh: "M11.6667 7C11.6658 8.07961 11.2907 9.12553 10.6051 9.95956C9.91961 10.7936 8.96611 11.3641 7.90709 11.574C6.84807 11.7838 5.74905 11.62 4.79727 11.1104C3.84549 10.6008 3.09985 9.777 2.68737 8.77929C2.27489 7.78158 2.22109 6.67172 2.53515 5.6388C2.84921 4.60588 3.51169 3.7138 4.40972 3.11455C5.30775 2.5153 6.38578 2.24595 7.46013 2.3524C8.53448 2.45885 9.53869 2.93451 10.3017 3.69833M11.6667 2.33333V5.25H8.75",
    // a pencil: edit the scratch page as markdown source (#184)
    edit: "M9.33333 2.33333L11.6667 4.66667L4.66667 11.6667H2.33333V9.33333L9.33333 2.33333Z",
    // an eye: show it rendered again (#184)
    eye: "M1.16667 7C1.16667 7 3.20833 2.91667 7 2.91667C10.7917 2.91667 12.8333 7 12.8333 7C12.8333 7 10.7917 11.0833 7 11.0833C3.20833 11.0833 1.16667 7 1.16667 7Z M8.75 7C8.75 7.9665 7.9665 8.75 7 8.75C6.0335 8.75 5.25 7.9665 5.25 7C5.25 6.0335 6.0335 5.25 7 5.25C7.9665 5.25 8.75 6.0335 8.75 7Z",
    // the collapse button of a left panel: its divider on the panel's side
    "sidebar-left": "M5.25 2.33333V11.6667M2.91667 2.33333H11.0833C11.7277 2.33333 12.25 2.85567 12.25 3.5V10.5C12.25 11.1443 11.7277 11.6667 11.0833 11.6667H2.91667C2.27233 11.6667 1.75 11.1443 1.75 10.5V3.5C1.75 2.85567 2.27233 2.33333 2.91667 2.33333Z",
  };
  const NS = "http://www.w3.org/2000/svg";

  /** @param {string} name */
  function svg(name) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 14 14");
    s.setAttribute("width", "14");
    s.setAttribute("height", "14");
    s.setAttribute("fill", "none");
    s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", PATHS[name] || "");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", "1.16667");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    s.appendChild(p);
    return s;
  }

  /** A 22 px round icon button. @param {string} name @param {string} tip @param {string=} id */
  function button(name, tip, id) {
    const b = document.createElement("button");
    b.className = "icon-btn";
    b.setAttribute("data-icon", name);
    if (id) b.id = id;
    b.title = tip;
    b.setAttribute("aria-label", tip);
    b.appendChild(svg(name));
    return b;
  }

  /** Swap the glyph of an icon button (a toggle whose icon says what a click does). */
  function setIcon(b, name) {
    if (b.getAttribute("data-icon") === name) return;
    b.setAttribute("data-icon", name);
    b.textContent = "";
    b.appendChild(svg(name));
  }

  window.SpectoIcons = { svg: svg, button: button, setIcon: setIcon, names: Object.keys(PATHS) };
})();
