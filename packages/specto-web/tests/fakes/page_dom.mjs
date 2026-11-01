// A small real DOM for running the web views' pages under node: a tree of elements with
// ids, classes, attributes, text, styles and listeners, built from the page's own
// index.html (scripts and comments skipped). Nothing lays out: rects are zeros.
//
//   const doc = pageDocument("<html>...</html>");
//   doc.getElementById("footer"); doc.querySelector(".panel .rows"); el.click();

const VOID = new Set(["br", "hr", "img", "input", "meta", "link", "source"]);

function camel(name) { return name.replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }

function makeStyle() {
  const style = {
    setProperty(name, value) { style[name.startsWith("--") ? name : camel(name)] = String(value); },
    getPropertyValue(name) { return style[name.startsWith("--") ? name : camel(name)] || ""; },
    removeProperty(name) { delete style[name.startsWith("--") ? name : camel(name)]; },
  };
  return style;
}

export function makeElement(doc, tag) {
  const classes = new Set();
  const listeners = {};
  const attrs = {};
  let text = "";
  const el = {
    ownerDocument: doc,
    tagName: tag.toUpperCase(),
    nodeType: 1,
    children: [],
    parentNode: null,
    style: makeStyle(),
    dataset: {},
    title: "",
    disabled: false,
    checked: false,
    value: "",
    type: "",
    id: "",
    scrollTop: 0,
    clientWidth: 0,
    clientHeight: 0,
    classList: {
      add: (...cs) => cs.forEach((c) => classes.add(c)),
      remove: (...cs) => cs.forEach((c) => classes.delete(c)),
      contains: (c) => classes.has(c),
      toggle(c, force) {
        const on = force === undefined ? !classes.has(c) : !!force;
        if (on) classes.add(c); else classes.delete(c);
        return on;
      },
    },
    get className() { return [...classes].join(" "); },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get textContent() { return text + el.children.map((c) => c.textContent).join(""); },
    set textContent(v) { el.children.forEach((c) => { c.parentNode = null; }); el.children = []; text = String(v); },
    get innerHTML() { return text; },
    set innerHTML(v) { el.textContent = ""; parseInto(doc, el, String(v)); },
    get firstChild() { return el.children[0] || null; },
    get lastChild() { return el.children[el.children.length - 1] || null; },
    get nextElementSibling() {
      const p = el.parentNode;
      return p ? p.children[p.children.indexOf(el) + 1] || null : null;
    },
    setAttribute(k, v) {
      attrs[k] = String(v);
      if (k === "id") el.id = String(v);
      else if (k === "class") el.className = v;
      else if (k === "title") el.title = String(v);
      else if (k === "disabled") el.disabled = true;
      else if (k.startsWith("data-")) el.dataset[camel(k.slice(5))] = String(v);
    },
    getAttribute(k) {
      if (k === "id") return el.id || null;
      if (k === "class") return el.className;
      if (k.startsWith("data-") && camel(k.slice(5)) in el.dataset) return el.dataset[camel(k.slice(5))];
      return k in attrs ? attrs[k] : null;
    },
    hasAttribute(k) { return el.getAttribute(k) !== null; },
    removeAttribute(k) { delete attrs[k]; if (k === "disabled") el.disabled = false; },
    appendChild(c) {
      if (c.parentNode) c.remove();
      el.children.push(c);
      c.parentNode = el;
      return c;
    },
    append(...cs) { cs.forEach((c) => el.appendChild(typeof c === "string" ? doc.createTextNode(c) : c)); },
    insertBefore(c, ref) {
      if (c.parentNode) c.remove();
      const i = ref ? el.children.indexOf(ref) : -1;
      if (i < 0) el.children.push(c); else el.children.splice(i, 0, c);
      c.parentNode = el;
      return c;
    },
    prepend(c) { el.insertBefore(c, el.firstChild); },
    insertAdjacentElement(where, c) {
      if (where === "afterend") el.parentNode.insertBefore(c, el.nextElementSibling);
      else if (where === "beforebegin") el.parentNode.insertBefore(c, el);
      else if (where === "afterbegin") el.prepend(c);
      else el.appendChild(c);
      return c;
    },
    remove() {
      const p = el.parentNode;
      if (!p) return;
      p.children.splice(p.children.indexOf(el), 1);
      el.parentNode = null;
    },
    contains(other) {
      for (let n = other; n; n = n.parentNode) if (n === el) return true;
      return false;
    },
    addEventListener(kind, fn) { (listeners[kind] = listeners[kind] || []).push(fn); },
    removeEventListener(kind, fn) { listeners[kind] = (listeners[kind] || []).filter((f) => f !== fn); },
    /** Fire an event at this element; it bubbles to the document. */
    dispatchEvent(ev) {
      ev.target = ev.target || el;
      if (!ev.preventDefault) {
        ev.defaultPrevented = false;
        ev.preventDefault = () => { ev.defaultPrevented = true; };
      }
      ev.stopPropagation = ev.stopPropagation || (() => { ev.stopped = true; });
      for (let n = el; n && !ev.stopped; n = n.parentNode) {
        ev.currentTarget = n;
        (n._listeners()[ev.type] || []).slice().forEach((fn) => fn.call(n, ev));
        if (typeof n["on" + ev.type] === "function") n["on" + ev.type](ev);
      }
      return !ev.defaultPrevented;
    },
    _listeners: () => listeners,
    // a failed assertion prints the element, not the whole document graph
    [Symbol.for("nodejs.util.inspect.custom")]() {
      return "<" + tag + (el.id ? "#" + el.id : "") + (el.className ? "." + el.className.replace(/ /g, ".") : "") + ">";
    },
    click() { if (!el.disabled) el.dispatchEvent({ type: "click" }); },
    focus() { doc.activeElement = el; },
    blur() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    scrollIntoView() { doc.scrolledTo = el; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; },
    getClientRects() { return []; },
    matches(sel) { return matches(el, sel); },
    querySelector(sel) { return el.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const out = [];
      const walk = (n) => n.children.forEach((c) => { if (c.nodeType === 1 && matchesChain(c, sel, el)) out.push(c); walk(c); });
      walk(el);
      return out;
    },
  };
  // assert prints a failing value with customInspect off and a deep depth: keep the
  // links into the rest of the tree out of what it walks
  for (const k of ["ownerDocument", "parentNode", "children", "style", "dataset", "classList"]) {
    Object.defineProperty(el, k, { enumerable: false, writable: true, value: el[k] });
  }
  return el;
}

/** One compound selector: tag, #id, .class, [attr] or [attr=value], e.g. "li.h2". */
function matches(el, sel) {
  const re = /([#.]?[\w-]+)|\[([\w-]+)(?:="?([^"\]]*)"?)?\]/g;
  let m;
  while ((m = re.exec(sel))) {
    if (m[2]) {
      const v = el.getAttribute(m[2]);
      if (v === null || (m[3] !== undefined && v !== m[3])) return false;
    } else if (m[1][0] === "#") { if (el.id !== m[1].slice(1)) return false; }
    else if (m[1][0] === ".") { if (!el.classList.contains(m[1].slice(1))) return false; }
    else if (el.tagName !== m[1].toUpperCase()) return false;
  }
  return true;
}

/** "a b > c" without the ">" (descendant combinators only), within root. */
function matchesChain(el, sel, root) {
  const parts = sel.trim().split(/\s+/);
  if (!matches(el, parts[parts.length - 1])) return false;
  let n = el.parentNode;
  for (let i = parts.length - 2; i >= 0; i--) {
    while (n && n !== root && !matches(n, parts[i])) n = n.parentNode;
    if (!n || n === root) return false;
    n = n.parentNode;
  }
  return true;
}

/** The body of an index.html as elements: tags, attributes and text; no scripts. */
function parseInto(doc, parent, html) {
  const body = (html.match(/<body[^>]*>([\s\S]*)<\/body>/i) || [null, html])[1]
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "");
  const stack = [parent];
  const re = /<\/?([a-zA-Z0-9]+)([^>]*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(body))) {
    const top = stack[stack.length - 1];
    if (m[3] !== undefined) {
      const t = m[3].replace(/&larr;/g, "←").replace(/&amp;/g, "&");
      if (t.trim()) top.appendChild(doc.createTextNode(t));
      continue;
    }
    if (m[0][1] === "/") { stack.pop(); continue; }
    const el = doc.createElement(m[1].toLowerCase());
    const attrRe = /([\w-]+)(?:="([^"]*)")?/g;
    let a;
    while ((a = attrRe.exec(m[2]))) if (a[1] !== "/") el.setAttribute(a[1], a[2] === undefined ? "" : a[2]);
    top.appendChild(el);
    if (!VOID.has(el.tagName.toLowerCase()) && !/\/\s*$/.test(m[2])) stack.push(el);
  }
}

export function pageDocument(html) {
  const listeners = {};
  const doc = {
    activeElement: null,
    scrolledTo: null,
    title: "",
    createElement(tag) { return makeElement(doc, tag); },
    createElementNS(_ns, tag) { return makeElement(doc, tag); },
    createTextNode(t) {
      const n = makeElement(doc, "#text");
      n.nodeType = 3;
      n.textContent = t;
      return n;
    },
    getElementById(id) {
      const find = (n) => {
        if (n.id === id) return n;
        for (const c of n.children) { const hit = find(c); if (hit) return hit; }
        return null;
      };
      return find(doc.documentElement);
    },
    querySelector(sel) { return doc.documentElement.querySelector(sel); },
    querySelectorAll(sel) { return doc.documentElement.querySelectorAll(sel); },
    addEventListener(kind, fn) { (listeners[kind] = listeners[kind] || []).push(fn); },
    removeEventListener() {},
    _listeners: () => listeners,
  };
  doc.documentElement = makeElement(doc, "html");
  doc.body = makeElement(doc, "body");
  doc.documentElement.appendChild(doc.body);
  // events bubble from the html element to the document
  doc.documentElement.parentNode = doc;
  doc.children = [doc.documentElement];
  if (html) parseInto(doc, doc.body, html);
  return doc;
}
