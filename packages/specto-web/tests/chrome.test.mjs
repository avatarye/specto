// The shared web view chrome (#130, Figma 05 UX pass 44:670 and section 4): the side
// panel standard (sidepanel.js) and the viewer footer with its step controls
// (footer.js, step.js), run under node against a small page DOM. Run by pytest through
// packages/specto-app/tests/test_web_pages.py, or directly:
//     node --test packages/specto-web/tests/chrome.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { blankPage, drag } from "./fakes/page.mjs";

const SHARED = ["icons.js", "step.js", "footer.js", "sidepanel.js"];

function panelPage(opts) {
  const page = blankPage(SHARED);
  const calls = { all: 0, none: 0, collapse: [], resize: [] };
  const host = page.document.createElement("div");
  page.document.body.appendChild(host);
  const panel = page.window.SpectoSidePanel.mount(host, Object.assign({
    id: "layers", title: "Layers", side: "right", width: 160, minWidth: 120, maxWidth: 300,
    onAll: () => calls.all++, onNone: () => calls.none++,
    onCollapse: (c) => calls.collapse.push(c), onResize: (w) => calls.resize.push(w),
  }, opts || {}));
  return { page, panel, calls, host };
}

test("a side panel has a header with its title, All / None and a collapse button", () => {
  const { panel, calls } = panelPage({ allNone: true });
  const head = panel.el.querySelector(".panel-head");
  assert.equal(head.querySelector(".panel-title").textContent, "LAYERS");
  head.querySelector(".panel-all").click();
  head.querySelector(".panel-none").click();
  assert.deepEqual([calls.all, calls.none], [1, 1]);
  assert.ok(panel.body, "rows go in the panel's body");
  assert.equal(panel.el.style.width, "160px");
});

test("a panel that lists no items has no All / None", () => {
  const { panel } = panelPage({ allNone: false, title: "Outline" });
  assert.equal(panel.el.querySelector(".panel-all"), null);
  assert.equal(panel.el.querySelector(".panel-title").textContent, "OUTLINE");
});

test("collapse turns the panel into a 24 px strip with the expand button and its title", () => {
  const { panel, calls } = panelPage({ allNone: true });
  panel.el.querySelector(".panel-head .panel-toggle").click();
  assert.equal(panel.collapsed, true);
  assert.ok(panel.el.classList.contains("collapsed"));
  assert.equal(panel.el.style.width, "24px");
  const strip = panel.el.querySelector(".panel-strip");
  assert.equal(strip.querySelector(".panel-strip-title").textContent, "LAYERS");
  const expand = strip.querySelector(".panel-toggle");
  assert.ok(expand.classList.contains("on"), "the collapsed strip's toggle shows as on");
  expand.click();
  assert.equal(panel.collapsed, false);
  assert.equal(panel.el.style.width, "160px", "the width comes back");
  assert.deepEqual(calls.collapse, [true, false]);
});

test("setCollapsed from the view's params does not call back", () => {
  const { panel, calls } = panelPage();
  panel.setCollapsed(true);
  panel.setCollapsed(true);
  assert.equal(panel.collapsed, true);
  assert.deepEqual(calls.collapse, []);
});

test("the grip on the inner edge sets the width, clamped, and reports it once at the end", () => {
  const { panel, calls } = panelPage({ side: "right" });
  const grip = panel.el.querySelector(".panel-grip");
  drag(grip, 500, [480, 460]);          // a right panel widens as the grip goes left
  assert.equal(panel.width, 200);
  assert.equal(panel.el.style.width, "200px");
  assert.deepEqual(calls.resize, [200]);
  drag(grip, 500, [900]);               // narrower than the minimum: clamped
  assert.equal(panel.width, 120);
  drag(grip, 500, [0]);                 // wider than the maximum: clamped
  assert.equal(panel.width, 300);
  assert.deepEqual(calls.resize, [200, 120, 300]);
});

test("a left panel sits first in its row and widens as the grip goes right", () => {
  const { panel, calls, host, page } = panelPage({ side: "left" });
  const other = page.document.createElement("div");
  host.appendChild(other);
  panel.el.remove();
  page.window.SpectoSidePanel.mount(host, { id: "view", title: "View", side: "left", width: 150 });
  assert.equal(host.children[0].classList.contains("left"), true);
  const left = host.children[0];
  drag(left.querySelector(".panel-grip"), 100, [130]);
  assert.equal(left.style.width, "180px");
  assert.deepEqual(calls.resize, []);
});

test("a click on the grip without a move changes nothing and reports nothing", () => {
  const { panel, calls } = panelPage();
  drag(panel.el.querySelector(".panel-grip"), 500, []);
  assert.equal(panel.width, 160);
  assert.deepEqual(calls.resize, []);
});

function footerPage(keys) {
  const page = blankPage(SHARED);
  const host = page.document.createElement("div");
  page.document.body.appendChild(host);
  const footer = page.window.SpectoFooter.mount(host, { step: keys === undefined ? ["ArrowLeft", "ArrowRight", "PageUp", "PageDown"] : keys });
  return { page, footer, host };
}

test("a viewer's footer: step, position and name on the left, its tools on the right", () => {
  const { page, footer, host } = footerPage();
  footer.setName("pieces.md", "notes/pieces.md");
  let reloads = 0;
  const reload = footer.tool("refresh", "Reload the page", function () { reloads++; }, "reload");
  page.fire("step", { index: 1, count: 7 });
  assert.ok(host.classList.contains("footer"));
  const order = host.children.map((c) => c.className.split(" ")[0]);
  assert.deepEqual(order, ["icon-btn", "icon-btn", "footer-pos", "footer-name", "footer-spacer", "icon-btn"]);
  assert.equal(page.$("stepPrev").getAttribute("data-icon"), "chevron-left");
  assert.equal(host.querySelector(".footer-pos").textContent, "2 / 7");
  assert.equal(host.querySelector(".footer-name").textContent, "pieces.md");
  assert.equal(host.querySelector(".footer-name").title, "notes/pieces.md");
  reload.click();
  assert.equal(reloads, 1);
  assert.equal(page.$("reload"), reload);
});

test("the step buttons send a step and dim at the ends; nothing to step hides them", () => {
  const { page, host } = footerPage();
  page.fire("step", { index: 0, count: 3 });
  assert.equal(page.$("stepPrev").disabled, true);
  assert.equal(page.$("stepNext").disabled, false);
  page.$("stepNext").click();
  assert.deepEqual(page.sent, [["step", { delta: 1 }]]);
  page.fire("step", { index: 2, count: 3 });
  assert.equal(page.$("stepNext").disabled, true);
  page.fire("step", { index: 0, count: 1 });
  for (const el of [page.$("stepPrev"), page.$("stepNext"), host.querySelector(".footer-pos")]) {
    assert.ok(el.classList.contains("hidden"));
  }
});

test("the step keys step unless a field or a chord has them", () => {
  const { page } = footerPage(["ArrowLeft", "ArrowRight"]);
  page.fire("step", { index: 1, count: 3 });
  assert.equal(page.key("ArrowRight").defaultPrevented, true);
  page.key("ArrowLeft", page.document.createElement("input"));
  page.key("PageDown");
  assert.deepEqual(page.sent, [["step", { delta: 1 }]]);
});

test("a footer without step (the map) has only its tools", () => {
  const { page, footer, host } = footerPage(null);
  footer.tool("target", "Frame all", function () {}, "frameAll");
  assert.equal(page.$("stepPrev"), null);
  assert.deepEqual(host.children.map((c) => c.className.split(" ")[0]), ["footer-spacer", "icon-btn"]);
});

test("the footer's meter: loaded / total, a slim bar and Load more, on the left (#181)", () => {
  const { page, footer, host } = footerPage(null);
  footer.tool("target", "Frame all", function () {}, "frameAll");
  let more = 0;
  const m = footer.meter(function () { more++; }, { id: "loadMore", noun: "layers" });
  assert.deepEqual(host.children.map((c) => c.className.split(" ")[0]), ["footer-meter", "footer-spacer", "icon-btn"]);
  assert.ok(m.el.classList.contains("hidden"), "hidden until set shows it");

  m.set(200, 1350, { shown: true, next: 200 });
  assert.ok(!m.el.classList.contains("hidden"));
  assert.equal(m.el.querySelector(".meter-text").textContent, "200 / 1,350");
  assert.equal(m.el.querySelector(".meter-fill").style.width, "14.8%");
  assert.equal(m.el.title, "200 of 1,350 layers loaded");
  assert.equal(page.$("loadMore"), m.button);
  assert.equal(m.button.textContent, "Load more");
  assert.match(m.button.title, /next 200 of the 1,150 layers not loaded/);
  m.button.click();
  assert.equal(more, 1);

  // the last batch is short; everything loaded: the button is disabled, the meter stays
  m.set(1300, 1350, { shown: true, next: 200 });
  assert.match(m.button.title, /next 50 of the 50/);
  m.set(1350, 1350, { shown: true, next: 200 });
  assert.equal(m.el.querySelector(".meter-fill").style.width, "100%");
  assert.equal(m.button.disabled, true);
  m.button.click();
  assert.equal(more, 1);
  assert.ok(!m.el.classList.contains("hidden"));
  // by default the meter shows while some are not loaded
  m.set(3, 3);
  assert.ok(m.el.classList.contains("hidden"));
  m.set(3, 4);
  assert.ok(!m.el.classList.contains("hidden"));
});

/* ---- the side panel's extras (#129 shares them) --------------------------------------- */

test("a left panel's toggles show the sidebar-left glyph, a right one's the sidebar", () => {
  const { panel, host, page } = panelPage({ side: "right" });
  assert.deepEqual(panel.el.querySelectorAll(".panel-toggle").map((b) => b.getAttribute("data-icon")), ["sidebar", "sidebar"]);
  const left = page.window.SpectoSidePanel.mount(host, { id: "view", title: "View", side: "left" });
  assert.deepEqual(left.el.querySelectorAll(".panel-toggle").map((b) => b.getAttribute("data-icon")), ["sidebar-left", "sidebar-left"]);
});

test("apply sets the collapse and the width without calling back", () => {
  const { panel, calls } = panelPage();
  panel.apply({ collapsed: true, width: 250 });
  assert.equal(panel.collapsed, true);
  assert.equal(panel.width, 250);
  panel.apply({ collapsed: false });
  assert.equal(panel.el.style.width, "250px");
  panel.apply({});
  assert.deepEqual([calls.collapse, calls.resize], [[], []]);
});

test("a kept panel writes its state into the view's one `panels` param and restores from it", () => {
  const page = blankPage(SHARED);
  const host = page.document.createElement("div");
  page.document.body.appendChild(host);
  const SP = page.window.SpectoSidePanel;
  let collapses = 0;
  const a = SP.mount(host, { title: "View", side: "left", width: 150, keep: "view", onCollapse: () => collapses++ });
  const b = SP.mount(host, { title: "Items", side: "right", width: 180, keep: "items" });
  SP.restore({ view: { collapsed: true, width: 140 }, items: { collapsed: false, width: 260 } });
  assert.deepEqual([a.collapsed, a.width, b.collapsed, b.width], [true, 140, false, 260]);
  assert.deepEqual(page.params, [], "restoring writes nothing");
  b.el.querySelector(".panel-head .panel-toggle").click();
  assert.deepEqual(page.params, [{ panels: { view: { collapsed: true, width: 140 }, items: { collapsed: true, width: 260 } } }]);
  drag(a.el.querySelector(".panel-grip"), 0, []);  // collapsed: the grip is inert
  a.el.querySelector(".panel-strip .panel-toggle").click();
  assert.equal(collapses, 1, "the view still hears its panel's change");
  assert.deepEqual(page.params.pop().panels.view, { collapsed: false, width: 140 });
});

test("row: a check, a mono name and a small second line; the check toggles, the name clicks", () => {
  const { page, panel } = panelPage({ allNone: true });
  const SP = page.window.SpectoSidePanel;
  const toggles = [];
  let clicks = 0;
  const row = SP.row({ checked: true, name: "terrain.ply", sub: SP.bytes(6.1 * 1024 * 1024), title: "terrain",
    onToggle: (c) => toggles.push(c), onClick: () => clicks++ });
  panel.body.appendChild(row);
  assert.ok(row.classList.contains("panel-row"));
  assert.equal(row.querySelector(".name").textContent, "terrain.ply");
  assert.equal(row.querySelector(".sub").textContent, "6.1 MB");
  assert.equal(row.title, "terrain");
  row.querySelector(".check").click();
  assert.deepEqual(toggles, [false]);
  assert.ok(!row.querySelector(".check").classList.contains("on"));
  row.querySelector(".name").click();
  assert.equal(clicks, 1);
  assert.deepEqual(toggles, [false], "a click on the name is not a toggle");
  const plain = SP.row({ checked: false, name: "coverage", onToggle: (c) => toggles.push(c) });
  plain.querySelector(".name").click();
  assert.deepEqual(toggles, [false, true], "without onClick the whole row toggles");
  assert.equal(plain.querySelector(".sub"), null);
});

test("bytes: 1024-based like the file tree's details", () => {
  const { page } = panelPage();
  const b = page.window.SpectoSidePanel.bytes;
  assert.deepEqual([b(12), b(1536), b(1.2 * 1024 * 1024), b(48 * 1024 * 1024), b(3 * 1024 ** 3)],
    ["12 B", "1.5 KB", "1.2 MB", "48 MB", "3.0 GB"]);
});

/* ---- zoom.js (#189): Ctrl+wheel never zooms the page; a view may zoom its content ---- */

function wheel(el, opts) {
  const ev = Object.assign({ type: "wheel", deltaY: -100, deltaMode: 0, ctrlKey: true }, opts || {});
  el.dispatchEvent(ev);
  return ev;
}

test("Ctrl+wheel anywhere on a page is no page zoom: the chrome keeps the theme size", () => {
  const page = blankPage(["wheel.js", "zoom.js"]);
  assert.equal(wheel(page.document.body).defaultPrevented, true, "Chromium's page zoom is refused");
  assert.equal(wheel(page.document.body, { ctrlKey: false }).defaultPrevented, false, "a plain wheel scrolls");
});

test("a content zoom scales its element alone: Ctrl+wheel steps it, Ctrl+0 resets, bounded", () => {
  const page = blankPage(["wheel.js", "zoom.js"]);
  const doc = page.document.createElement("article");
  const side = page.document.createElement("aside");
  const inner = page.document.createElement("p");
  doc.appendChild(inner);
  page.document.body.appendChild(doc);
  page.document.body.appendChild(side);
  const seen = [];
  const z = page.window.SpectoZoom.content(doc, { onChange: (level) => seen.push(level) });
  assert.equal(z.level, 1);
  assert.equal(wheel(inner).defaultPrevented, true);
  assert.equal(z.level, 1.1);
  assert.equal(doc.style.zoom, "1.1");
  wheel(side);
  assert.equal(z.level, 1.1, "outside the content the wheel zooms nothing");
  assert.equal(side.style.zoom, undefined);
  wheel(inner, { deltaY: 100 });
  assert.equal(z.level, 1);
  for (let i = 0; i < 40; i++) wheel(inner);
  assert.equal(z.level, 3, "at most 300 %");
  assert.equal(page.key("0", inner).defaultPrevented, false, "a bare 0 is no reset");
  assert.equal(page.key("0", inner, { ctrlKey: true }).defaultPrevented, true);
  assert.equal(z.level, 1, "Ctrl+0 is 100 %");
  page.key("=", inner, { ctrlKey: true });
  assert.equal(z.level, 1.1, "Ctrl+= zooms in");
  page.key("-", inner, { ctrlKey: true });
  assert.equal(z.level, 1, "Ctrl+- zooms out");
  z.set(0.1);
  assert.equal(z.level, 0.5, "at least 50 %");
  assert.deepEqual(seen.slice(0, 2), [1.1, 1]);
});

test("one wheel notch is one 10 % step on any display scale; a touchpad's small deltas add up", () => {
  const page = blankPage(["wheel.js", "zoom.js"]);
  const doc = page.document.createElement("article");
  page.document.body.appendChild(doc);
  const z = page.window.SpectoZoom.content(doc);
  // a notch is deltaY 100 at 100 %, about 60 on a scaled display, 120 elsewhere
  for (const dy of [60, 100, 120]) {
    z.reset();
    wheel(doc, { deltaY: -dy });
    wheel(doc, { deltaY: -dy });
    assert.equal(z.level, 1.21, `two notches of deltaY ${dy}`);
    wheel(doc, { deltaY: dy });
    assert.equal(z.level, 1.1, `one notch back of deltaY ${dy}`);
  }
  z.reset();
  wheel(doc, { deltaY: -1, deltaMode: 1 });
  assert.equal(z.level, 1.1, "a line-mode notch is one step");
  z.reset();
  for (let i = 0; i < 9; i++) wheel(doc, { deltaY: -10 });
  assert.equal(z.level, 1, "touchpad deltas under a step wait");
  wheel(doc, { deltaY: -10 });
  assert.equal(z.level, 1.1, "ten of 10 make one step");
});

test("wheel.js: a notch is one by its sign at deltaY 60, 100 or 120; touchpad deltas are fractions (#194)", () => {
  const W = blankPage(["wheel.js"]).window.SpectoWheel;
  for (const dy of [60, 100, 120]) {
    assert.equal(W.notches({ deltaY: -dy, deltaMode: 0 }), 1, `away, deltaY ${dy}`);
    assert.equal(W.notches({ deltaY: dy, deltaMode: 0 }), -1, `toward, deltaY ${dy}`);
  }
  assert.equal(W.notches({ deltaY: -1, deltaMode: 1 }), 1, "a line-mode notch");
  assert.equal(W.notches({ deltaY: 0 }), 0);
  assert.equal(W.notches({ deltaY: -10, deltaMode: 0 }), 0.1, "a touchpad delta");
  const count = W.counter();
  const got = [];
  for (let i = 0; i < 10; i++) got.push(count({ deltaY: -10, deltaMode: 0 }));
  assert.deepEqual(got, [0, 0, 0, 0, 0, 0, 0, 0, 0, 1], "ten deltas of 10 make one whole notch");
  assert.equal(count({ deltaY: 60, deltaMode: 0 }), -1);
});
