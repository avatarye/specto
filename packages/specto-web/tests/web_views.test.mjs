// The web views' pages after the UX pass (#130, Figma 05 section 4): no page bar (no
// title, key pill, caps badge, counters, file dropdown or word buttons); a footer with
// step, position and name on the left and the kind's tools on the right; the map's
// LAYERS and the markdown's OUTLINE side panels. Each page boots from its own
// index.html under node (fakes/page.mjs) and is driven by the notifications Python
// sends. Run by pytest through packages/specto-app/tests/test_web_pages.py, or directly:
//     node --test packages/specto-web/tests/web_views.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { bootPage } from "./fakes/page.mjs";

const SHARED = ["icons.js", "step.js", "footer.js", "sidepanel.js", "wheel.js", "zoom.js"];

function noPageBar(page) {
  assert.equal(page.document.querySelector(".bar"), null, "no page bar");
  assert.equal(page.document.querySelector(".caps"), null, "no caps badge");
  assert.equal(page.document.querySelector(".pill"), null, "no pills");
}

/* ---- html --------------------------------------------------------------------------- */

const HTML_ITEM = { key: "1:deck", url: "specto://duna.items/l2/1:deck/.html/report.html", file: "report.html", name: "deck" };

test("html: no page bar and no file list; the footer names the file and reloads it", () => {
  const page = bootPage("html", [...SHARED, "html/main.js"]);
  noPageBar(page);
  assert.equal(page.$("files"), null, "the step replaces the file list");
  const reload = page.$("reload");
  assert.equal(reload.getAttribute("data-icon"), "refresh");
  assert.equal(reload.disabled, true, "nothing to reload yet");
  page.fire("files", { files: [HTML_ITEM] });   // an older host: ignored, no list
  page.fire("item", HTML_ITEM);
  page.fire("step", { index: 0, count: 2 });
  assert.equal(page.$("footerName").textContent, "report.html");
  assert.equal(page.$("stepPos").textContent, "1 / 2");
  assert.equal(page.$("frame").getAttribute("src"), HTML_ITEM.url);
  assert.equal(reload.disabled, false);
  reload.click();
  assert.equal(page.$("frame").getAttribute("src"), "about:blank");
  page.flush();
  assert.equal(page.$("frame").getAttribute("src"), HTML_ITEM.url);
});

test("html: a missing part goes to the health dot, not a pill", () => {
  const page = bootPage("html", [...SHARED, "html/main.js"]);
  page.fire("item", { key: "x", url: null, reason: "item x has no readable part" });
  assert.deepEqual(page.health[page.health.length - 1], ["error", "item x has no readable part"]);
  page.fire("item", HTML_ITEM);
  page.$("frame").dispatchEvent({ type: "load" });
  assert.deepEqual(page.health[page.health.length - 1], ["ok", ""]);
});

/* ---- json --------------------------------------------------------------------------- */

function fakeMonaco() {
  // the selection spans lines 2..4 of the text (the fake maps a client y to that line)
  const rec = { actions: [], options: [], created: null, selection: [2, 4], caret: null };
  const model = { lang: "json", value: "", getLanguageId() { return this.lang; }, getLineCount() { return 1; }, setValue(v) { this.value = v; },
                  getValueInRange: () => '"frame": 3' };
  const editor = {
    getAction: (id) => ({ run: () => { rec.actions.push(id); return Promise.resolve(); } }),
    updateOptions: (o) => rec.options.push(o),
    onDidScrollChange() {}, addCommand() {}, getVisibleRanges: () => [], setScrollTop() {}, layout() {}, focus() {},
    getSelection: () => ({
      isEmpty: () => !rec.selection,
      containsPosition: (p) => !!rec.selection && p.lineNumber >= rec.selection[0] && p.lineNumber <= rec.selection[1],
    }),
    getTargetAtClientPoint: (x, y) => ({ type: x < 40 ? 3 : 6, position: { lineNumber: y, column: 1 } }),
    setPosition(p) { rec.caret = p; },
  };
  const monaco = {
    KeyCode: { PageUp: 11, PageDown: 12 },
    editor: {
      MouseTargetType: { GUTTER_LINE_NUMBERS: 3, CONTENT_TEXT: 6 },
      create(_el, opts) { rec.created = opts; return editor; },
      createModel() { return model; },
      setModelLanguage(m, lang) { m.lang = lang; },
    },
  };
  return { monaco, rec, model };
}

async function jsonPage() {
  const { monaco, rec, model } = fakeMonaco();
  const page = bootPage("json", [...SHARED, "json/main.js"], {
    monaco,
    loadMonaco: () => Promise.resolve(monaco),
    defineSpectoTheme() {},
    fetch: () => Promise.resolve({ ok: true, text: () => Promise.resolve('{"frame": 3}') }),
  });
  await new Promise((r) => setImmediate(r));
  return { page, rec, model };
}

const JSON_ITEM = { key: "00003", url: "specto://items/l3/00003/.json", role: ".json", file: "00003.json", name: "00003" };

test("json: no page bar, no minimap; the footer names the item, then fold-all and wrap", async () => {
  const { page, rec, model } = await jsonPage();
  noPageBar(page);
  assert.equal(rec.created.minimap.enabled, false);
  page.fire("config", { params: { wrap: false } });
  page.fire("item", JSON_ITEM);
  page.fire("step", { index: 2, count: 12 });
  await new Promise((r) => setImmediate(r));
  assert.equal(model.value, '{\n  "frame": 3\n}');
  assert.equal(page.$("footerName").textContent, "00003.json");
  assert.equal(page.$("stepPos").textContent, "3 / 12");
  const tools = page.$("footer").children.slice(-2);
  assert.deepEqual(tools.map((b) => b.id), ["fold", "wrap"]);
  assert.equal(page.$("find"), null, "Ctrl+F finds; no Find button");
  assert.equal(page.$("unfold"), null, "one fold-all toggle");
});

test("json: fold-all is one toggle; wrap is the view's param", async () => {
  const { page, rec } = await jsonPage();
  const fold = page.$("fold");
  fold.click();
  assert.deepEqual(rec.actions, ["editor.foldAll"]);
  assert.ok(fold.classList.contains("on"));
  assert.equal(fold.getAttribute("data-icon"), "unfold", "folded: a click unfolds");
  fold.click();
  assert.deepEqual(rec.actions, ["editor.foldAll", "editor.unfoldAll"]);
  assert.ok(!fold.classList.contains("on"));
  const wrap = page.$("wrap");
  wrap.click();
  assert.deepEqual(page.params, [{ wrap: true }]);
  assert.ok(wrap.classList.contains("on"));
  page.fire("params", { wrap: false });
  assert.ok(!wrap.classList.contains("on"));
  assert.equal(rec.options[rec.options.length - 1].wordWrap, "off");
});

test("json: a new item unfolds the toggle; a missing part goes to the health dot", async () => {
  const { page } = await jsonPage();
  page.$("fold").click();
  page.fire("item", JSON_ITEM);
  await new Promise((r) => setImmediate(r));
  assert.ok(!page.$("fold").classList.contains("on"), "a fresh item shows unfolded");
  page.fire("item", { key: "x", url: null, reason: "no JSON part" });
  assert.deepEqual(page.health[page.health.length - 1], ["error", "no JSON part"]);
});

/* ---- markdown ----------------------------------------------------------------------- */

const NOTES = [
  { key: "pieces", name: "pieces", url: "specto://items/l2/pieces/md", role: ".md" },
  { key: "pose", name: "pose", url: "specto://items/l2/pose/md", role: ".md" },
];
const TEXTS = {
  "specto://items/l2/pieces/md": "# Scene preparation\n## Frame counts\nSee [[pose]].",
  "specto://items/l2/pose/md": "# Pose math",
};

/* the renderer's contract (render.js): html with heading ids and wikilink anchors */
const fakeMarkdown = {
  render(text, ctx) {
    const headings = [];
    const html = text.split("\n").map((line) => {
      const h = line.match(/^(#+) (.*)$/);
      if (h) {
        const id = h[2].toLowerCase().replace(/ /g, "-");
        headings.push({ level: h[1].length, text: h[2], id });
        return `<h${h[1].length} id="${id}">${h[2]}</h${h[1].length}>`;
      }
      return "<p>" + line.replace(/\[\[(\w+)\]\]/g, (_, t) => `<a class="wikilink" data-key="${ctx.resolve(t)}" href="#">${t}</a>`) + "</p>";
    }).join("");
    return { html, headings, frontmatter: null };
  },
  runMermaid: () => Promise.resolve(0),
};

async function markdownPage(params) {
  const page = bootPage("markdown", [...SHARED, "markdown/main.js"], {
    SpectoMarkdown: fakeMarkdown,
    fetch: (url) => Promise.resolve({ ok: true, text: () => Promise.resolve(TEXTS[url]) }),
  });
  page.fire("config", { params: params || {} });
  page.fire("notes", { notes: NOTES });
  page.fire("item", { key: "pieces", url: NOTES[0].url, role: ".md", file: "pieces.md", name: "pieces" });
  page.fire("step", { index: 1, count: 7 });
  await new Promise((r) => setImmediate(r));
  return page;
}

test("markdown: no page bar and no Notes list; the outline is a panel on the right", async () => {
  const page = await markdownPage();
  noPageBar(page);
  assert.equal(page.$("notes"), null, "no Notes button");
  const panel = page.$("outline");
  assert.ok(panel.classList.contains("side-panel") && panel.classList.contains("right"));
  assert.equal(panel.parentNode.lastChild, panel, "the panel sits right of the note");
  assert.equal(panel.querySelector(".panel-title").textContent, "OUTLINE");
  assert.equal(panel.querySelector(".panel-all"), null, "an outline has no All / None");
  const rows = panel.querySelectorAll(".outline-row");
  assert.deepEqual(rows.map((r) => r.textContent), ["Scene preparation", "Frame counts"]);
  // the section the reader is in shows in accent: at the top, the first
  assert.deepEqual(rows.map((r) => r.className), ["outline-row h1 current", "outline-row h2"]);
  rows[1].click();
  assert.equal(page.document.scrolledTo, page.$("frame-counts"));
  assert.deepEqual(rows.map((r) => r.classList.contains("current")), [false, true]);
});

test("markdown: the footer steps, names the note, and shows back only after a wikilink", async () => {
  const page = await markdownPage();
  assert.equal(page.$("footerName").textContent, "pieces.md");
  assert.equal(page.$("stepPos").textContent, "2 / 7");
  const back = page.$("back");
  assert.equal(back.getAttribute("data-icon"), "back");
  assert.ok(back.classList.contains("hidden"), "nothing to go back to");
  page.document.querySelector("a.wikilink").click();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(page.sent.filter((s) => s[0] === "navigate"), [["navigate", { key: "pose", anchor: "" }]]);
  assert.ok(!back.classList.contains("hidden"));
  assert.deepEqual(page.$("outline").querySelectorAll(".outline-row").map((r) => r.textContent), ["Pose math"]);
  back.click();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(page.sent.filter((s) => s[0] === "navigate").pop(), ["navigate", { key: "pieces", anchor: "" }]);
  assert.ok(back.classList.contains("hidden"));
});

test("markdown: the outline's collapse and width live in the view's `panels` param", async () => {
  const page = await markdownPage({ panels: { outline: { collapsed: true, width: 240 } } });
  const panel = page.$("outline");
  assert.ok(panel.classList.contains("collapsed"), "a collapsed outline is a strip, not hidden");
  panel.querySelector(".panel-strip .panel-toggle").click();
  assert.deepEqual(page.params, [{ panels: { outline: { collapsed: false, width: 240 } } }]);
  assert.equal(panel.style.width, "240px");
  page.fire("params", { panels: { outline: { collapsed: false, width: 200 } } });
  assert.equal(panel.style.width, "200px");
  panel.querySelector(".panel-head .panel-toggle").click();
  assert.deepEqual(page.params.pop(), { panels: { outline: { collapsed: true, width: 200 } } });
});

test("markdown: Ctrl+wheel over the note zooms the note alone; the outline keeps its size (#189)", async () => {
  const page = await markdownPage();
  const ctrlWheel = (el, deltaY) => {
    const ev = { type: "wheel", deltaY, deltaMode: 0, ctrlKey: true };
    el.dispatchEvent(ev);
    return ev;
  };
  const row = page.$("outline").querySelector(".outline-row");
  assert.equal(ctrlWheel(row, -100).defaultPrevented, true, "no page zoom over the outline");
  assert.equal(page.$("doc").style.zoom, undefined);
  assert.equal(ctrlWheel(page.$("frame-counts"), -100).defaultPrevented, true, "no page zoom over the note");
  assert.equal(page.$("doc").style.zoom, "1.1", "the note zooms");
  assert.equal(page.$("outline").style.zoom, undefined, "the outline does not");
  assert.equal(page.lastReport.zoom, 1.1, "snap says the reading zoom");
});

/* ---- map ---------------------------------------------------------------------------- */

function fakeMaplibre() {
  const rec = { fits: [], styles: [] };
  class Map {
    constructor() { this.handlers = {}; rec.map = this; }
    addControl() {}
    on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
    setStyle(s) { rec.styles.push(s); }
    getSource() { return null; }
    addSource() {}
    addLayer() {}
    getLayer() { return null; }
    removeLayer() {}
    removeSource() {}
    setLayoutProperty() {}
    setPaintProperty() {}
    setFeatureState() {}
    fitBounds(b) { rec.fits.push(b); }
    flyTo() {}
    getZoom() { return 1; }
    getCenter() { return { lng: 0, lat: 0 }; }
    project() { return { x: 0, y: 0 }; }
    resize() {}
    getCanvas() { return { style: {} }; }
    queryRenderedFeatures() { return []; }
  }
  class Popup { setLngLat() { return this; } setHTML() { return this; } addTo() { return this; } on() {} remove() {} isOpen() { return false; } }
  return { maplibregl: { Map, Popup, NavigationControl: class {}, ScaleControl: class {} }, rec };
}

const LAYERS = {
  layers: [
    { id: "geo/route", key: "geo/route", label: "trajectory", url: "specto://items/l2/geo%2Froute/geojson", visible: true, color: "#8a63b8", features: 1, bbox: [0, 0, 2, 1], error: null },
    { id: "geo/points", key: "geo/points", label: "candidates", url: "specto://items/l2/geo%2Fpoints/geojson", visible: true, color: "#4a7fb5", features: 4, bbox: [1, -1, 3, 0.5], error: null },
    { id: "geo/cover", key: "geo/cover", label: "coverage", url: "specto://items/l2/geo%2Fcover/geojson", visible: false, color: "#5f9e6e", features: 2, bbox: [-5, -5, -4, -4], error: null },
  ],
  basemap: "osm", online: true, net_tiles: true, link: "l2",
  // the provider table as web/basemaps.py sends it
  basemaps: [
    { name: "osm", label: "OSM", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], overlays: [], hosts: ["tile.openstreetmap.org"], attribution: "&copy; OpenStreetMap contributors", max_zoom: 19 },
    { name: "satellite", label: "Satellite", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], overlays: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"], hosts: ["server.arcgisonline.com"], attribution: "Powered by Esri", max_zoom: 19 },
  ],
};

function mapPage(params) {
  const { maplibregl, rec } = fakeMaplibre();
  const page = bootPage("map", ["icons.js", "footer.js", "sidepanel.js", "map/main.js"], {
    maplibregl,
    fetch: () => new Promise(() => {}),
  });
  page.fire("config", { params: params || { layers: {}, basemap: "osm" } });
  page.fire("layers", LAYERS);
  return { page, rec };
}

test("map: no page bar; a LAYERS panel on the right lists one row per layer", () => {
  const { page } = mapPage();
  noPageBar(page);
  const panel = page.$("layers");
  assert.ok(panel.classList.contains("side-panel") && panel.classList.contains("right"));
  assert.equal(panel.querySelector(".panel-title").textContent, "LAYERS");
  const rows = panel.querySelectorAll(".panel-row");
  assert.deepEqual(rows.map((r) => r.querySelector(".name").textContent), ["trajectory", "candidates", "coverage"]);
  assert.deepEqual(rows.map((r) => r.querySelector(".check").classList.contains("on")), [true, true, false]);
  assert.equal(rows[0].querySelector(".check").style.getPropertyValue("--check"), "#8a63b8", "the check is the layer's colour");
  assert.equal(page.$("stepPrev"), null, "the map does not step");
});

test("map: a row toggles its layer; All and None set every layer in one write", () => {
  const { page } = mapPage();
  const panel = page.$("layers");
  panel.querySelectorAll(".panel-row")[0].click();
  assert.deepEqual(page.params.pop(), { layers: { "geo/route": { visible: false, color: "#8a63b8" } } });
  assert.ok(!panel.querySelectorAll(".panel-row")[0].querySelector(".check").classList.contains("on"));
  panel.querySelector(".panel-all").click();
  const all = page.params.pop().layers;
  assert.deepEqual(Object.keys(all).map((k) => all[k].visible), [true, true, true]);
  panel.querySelector(".panel-none").click();
  const none = page.params.pop().layers;
  assert.deepEqual(Object.keys(none).map((k) => none[k].visible), [false, false, false]);
  assert.equal(page.params.length, 0);
});

test("map: the footer's switcher offers None, OSM and Satellite, the shown one lit (#157)", () => {
  const { page, rec } = mapPage();
  const seg = page.$("basemap");
  assert.equal(seg.parentNode, page.$("footer"));
  const buttons = seg.querySelectorAll("button");
  assert.deepEqual(buttons.map((b) => b.textContent), ["None", "OSM", "Satellite"]);
  const lit = () => seg.querySelectorAll("button").filter((b) => b.classList.contains("on")).map((b) => b.textContent);
  assert.deepEqual(lit(), ["OSM"]);
  const style = rec.styles[rec.styles.length - 1];
  assert.deepEqual(Array.from(style.sources.base.tiles), LAYERS.basemaps[0].tiles);
  assert.match(style.sources.base.attribution, /OpenStreetMap/);

  // a click writes the choice; the map changes when Python says it shows
  buttons[2].click();
  assert.deepEqual(page.params.pop(), { basemap: "satellite" });
  page.fire("layers", Object.assign({}, LAYERS, { basemap: "satellite" }));
  assert.deepEqual(lit(), ["Satellite"]);
  const sat = rec.styles[rec.styles.length - 1];
  assert.deepEqual(Array.from(sat.layers, (l) => l.id), ["bg", "base", "overlay0"], "imagery, then its labels");
  assert.match(sat.sources.base.tiles[0], /World_Imagery/);
  assert.equal(sat.sources.base.attribution, "Powered by Esri");
  buttons[0].click();
  assert.deepEqual(page.params.pop(), { basemap: "none" });
  page.fire("layers", Object.assign({}, LAYERS, { basemap: "none" }));
  assert.deepEqual(lit(), ["None"]);
  assert.deepEqual(Array.from(rec.styles[rec.styles.length - 1].layers, (l) => l.id), ["bg"]);

  // Esri's host does not answer while OSM's does: Satellite alone is disabled, and says so
  const down = LAYERS.basemaps.map((b) => Object.assign({}, b, { reachable: b.name !== "satellite" }));
  page.fire("layers", Object.assign({}, LAYERS, { basemap: "osm", basemaps: down }));
  assert.deepEqual(buttons.map((b) => b.disabled), [false, false, true]);
  assert.match(buttons[2].title, /server\.arcgisonline\.com is unreachable/);
  // no net:tiles: the providers are disabled and say why; None stays
  page.fire("layers", Object.assign({}, LAYERS, { net_tiles: false, basemap: "none" }));
  assert.deepEqual(buttons.map((b) => b.disabled), [false, true, true]);
  assert.match(buttons[1].title, /net:tiles/);
});

test("map: frame-all fits the visible layers", () => {
  const { page, rec } = mapPage();
  const frame = page.$("frameAll");
  assert.equal(frame.getAttribute("data-icon"), "target");
  rec.fits.length = 0;
  frame.click();
  assert.deepEqual(rec.fits.map((b) => JSON.parse(JSON.stringify(b))), [[[0, -1], [3, 1]]], "coverage is hidden: not framed");
});

test("map: a layer set that changes entirely is framed again; a partial change keeps the camera (#176)", async () => {
  const { maplibregl, rec } = fakeMaplibre();
  const fc = { type: "FeatureCollection", features: [] };
  const page = bootPage("map", ["icons.js", "footer.js", "sidepanel.js", "map/main.js"], {
    maplibregl,
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(fc) }),
  });
  const layer = (id, bbox) => ({ id, key: id, label: id, url: "specto://items/l9/" + id + "/geojson", visible: true, color: "#4a7fb5", features: 0, bbox, error: null });
  const fire = (ls) => page.fire("layers", Object.assign({}, LAYERS, { layers: ls }));
  const settle = () => new Promise((r) => setTimeout(r, 0));
  const fits = () => rec.fits.map((b) => JSON.parse(JSON.stringify(b)));
  page.fire("config", { params: { layers: {}, basemap: "osm" } });

  fire([layer("a1/outline", [0, 0, 1, 1]), layer("a1/path", [1, 1, 2, 2])]);
  await settle();
  assert.deepEqual(fits(), [[[0, 0], [2, 2]]], "the first layers are framed");

  // one layer kept, one added: the user's camera stays
  fire([layer("a1/outline", [0, 0, 1, 1]), layer("a1/trees", [5, 5, 6, 6])]);
  await settle();
  assert.equal(rec.fits.length, 1);

  // nothing in common (a per-pick link moved to another address): framed again
  fire([layer("a2/outline", [10, 10, 11, 12])]);
  await settle();
  assert.deepEqual(fits()[1], [[10, 10], [11, 12]]);
});

function loadingMap() {
  const { maplibregl, rec } = fakeMaplibre();
  const fetched = [];
  const fc = { type: "FeatureCollection", features: [] };
  const page = bootPage("map", ["icons.js", "footer.js", "sidepanel.js", "map/main.js"], {
    maplibregl,
    fetch: (url) => { fetched.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve(fc) }); },
  });
  page.fire("config", { params: { layers: {}, basemap: "osm" } });
  const layer = (id, bbox) => ({ id, key: id, label: id, url: "specto://items/l9/" + id + "/geojson", visible: true, color: "#4a7fb5", features: 0, bbox, error: null });
  const many = (n) => Array.from({ length: n }, (_, i) => layer("g/" + i, [i, 0, i + 1, 1]));
  const fire = (ls, extra) => page.fire("layers", Object.assign({}, LAYERS, { layers: ls, total: ls.length, limit: 200, batch: 200, revision: 1 }, extra));
  const settle = () => new Promise((r) => setTimeout(r, 0));
  const fits = () => rec.fits.map((b) => JSON.parse(JSON.stringify(b)));
  return { page, rec, fetched, layer, many, fire, settle, fits };
}

test("map: every data reload frames the data again; a params change keeps the camera (#181)", async () => {
  const { rec, fetched, layer, fire, settle, fits } = loadingMap();
  fire([layer("a/outline", [0, 0, 1, 1]), layer("a/path", [1, 1, 2, 2])], { revision: 1 });
  await settle();
  assert.deepEqual(fits(), [[[0, 0], [2, 2]]]);
  assert.equal(fetched.length, 2);

  // the same revision again (a colour, a basemap, Load more): nothing refetched, no frame
  fire([layer("a/outline", [0, 0, 1, 1]), layer("a/path", [1, 1, 2, 2])], { revision: 1 });
  await settle();
  assert.equal(rec.fits.length, 1);
  assert.equal(fetched.length, 2);

  // a reload (a refresh, a watched arrival, a link re-resolve): the same files with new
  // contents at the same URLs are fetched again, and the map frames what they hold now
  fire([layer("a/outline", [5, 5, 6, 6]), layer("a/path", [6, 6, 7, 8])], { revision: 2 });
  await settle();
  assert.equal(fetched.length, 4, "every layer is fetched again");
  assert.deepEqual(fits()[1], [[5, 5], [7, 8]]);

  // a reload that keeps one layer and adds one is framed too (the #176 rule kept the camera)
  fire([layer("a/outline", [5, 5, 6, 6]), layer("a/trees", [9, 9, 10, 10])], { revision: 3 });
  await settle();
  assert.deepEqual(fits()[2], [[5, 5], [10, 10]]);
});

test("map: over 200 layers the footer shows loaded / total; Load more asks for the next batch (#181)", async () => {
  const { page, fetched, many, fire, settle } = loadingMap();
  const meter = page.document.querySelector(".footer-meter");
  assert.equal(meter.parentNode, page.$("footer"));

  // under the cap: no meter, unless the agent's limit leaves some out
  fire(many(3), { total: 3 });
  assert.ok(meter.classList.contains("hidden"));
  fire(many(3), { total: 5, limit: 3 });
  assert.ok(!meter.classList.contains("hidden"));
  assert.equal(meter.querySelector(".meter-text").textContent, "3 / 5");

  // 1,350 files, the host sends the first 200: 200 / 1,350, Load more enabled
  fire(many(200), { total: 1350, revision: 2 });
  await settle();
  assert.ok(!meter.classList.contains("hidden"), "always visible, not on hover only");
  assert.equal(meter.querySelector(".meter-text").textContent, "200 / 1,350");
  assert.equal(meter.querySelector(".meter-fill").style.width, "14.8%");
  assert.equal(page.$("layers").querySelectorAll(".panel-row").length, 200);
  assert.equal(fetched.length, 203);
  const more = page.$("loadMore");
  assert.equal(more.disabled, false);
  more.click();
  assert.deepEqual(page.params.pop(), { limit: 400 }, "Load more raises the view's limit by a batch");

  // the host answers with 400 layers: only the new 200 are fetched, the camera stays
  const before = fetched.length;
  fire(many(400), { total: 1350, revision: 2, limit: 400 });
  await settle();
  assert.equal(fetched.length - before, 200);
  assert.equal(meter.querySelector(".meter-text").textContent, "400 / 1,350");
  more.click();
  assert.deepEqual(page.params.pop(), { limit: 600 });

  // the agent's batch: Load more adds 500
  fire(many(400), { total: 1350, revision: 2, limit: 400, batch: 500 });
  more.click();
  assert.deepEqual(page.params.pop(), { limit: 900 });

  // everything loaded: the meter stays, Load more is disabled
  fire(many(1350), { total: 1350, revision: 2, limit: 1400 });
  assert.equal(meter.querySelector(".meter-text").textContent, "1,350 / 1,350");
  assert.equal(more.disabled, true);
  assert.ok(!meter.classList.contains("hidden"));
  const rep = page.lastReport;
  assert.deepEqual(rep.meter, { loaded: 1350, total: 1350, shown: true });
  assert.ok(rep.buttons.loadMore);
});

test("map: the LAYERS panel's collapse and width live in the view's `panels` param", () => {
  const { page } = mapPage({ layers: {}, basemap: "osm", panels: { layers: { collapsed: true, width: 220 } } });
  const panel = page.$("layers");
  assert.ok(panel.classList.contains("collapsed"));
  panel.querySelector(".panel-strip .panel-toggle").click();
  assert.deepEqual(page.params.pop(), { panels: { layers: { collapsed: false, width: 220 } } });
  assert.equal(panel.style.width, "220px");
});

test("map: the footer's layers toggle shows and hides the LAYERS panel, lit while it shows (#157)", () => {
  const { page } = mapPage({ layers: {}, basemap: "osm", panels: { layers: { collapsed: false, width: 200 } } });
  const panel = page.$("layers");
  const toggle = page.$("layersToggle");
  assert.equal(toggle.getAttribute("data-icon"), "layers");
  assert.equal(toggle.parentNode, page.$("footer"), "a footer tool, under the panel");
  assert.ok(toggle.classList.contains("on"), "the panel shows");
  toggle.click();
  assert.ok(panel.classList.contains("collapsed"));
  assert.ok(!toggle.classList.contains("on"));
  assert.deepEqual(page.params.pop(), { panels: { layers: { collapsed: true, width: 200 } } });
  toggle.click();
  assert.ok(!panel.classList.contains("collapsed"));
  assert.ok(toggle.classList.contains("on"));
  assert.deepEqual(page.params.pop(), { panels: { layers: { collapsed: false, width: 200 } } });
  // the panel's own collapse and a kept state arriving in params light it the same way
  panel.querySelector(".panel-head .panel-toggle").click();
  assert.ok(!toggle.classList.contains("on"));
  page.fire("params", { panels: { layers: { collapsed: false, width: 200 } } });
  assert.ok(toggle.classList.contains("on"));
});

test("json: a press inside the selection that moves the drag distance drags its text out (#185)", async () => {
  const { page, rec } = await jsonPage();
  page.window.Specto.config = { dragDistance: 10 };
  const box = page.$("editor");
  const down = box.dispatchEvent.bind(box);
  const ev = (type, x, y, extra) => ({ type, clientX: x, clientY: y, button: 0, buttons: 1, ...extra });

  // a wobble inside the distance, then the button up: a click, the caret goes there
  const press = ev("pointerdown", 100, 3);
  down(press);
  assert.equal(press.defaultPrevented, true, "the editor does not see it");
  page.fireWindow("pointermove", ev("pointermove", 104, 3));
  page.fireWindow("pointerup", ev("pointerup", 104, 3, { buttons: 0 }));
  assert.deepEqual(rec.caret, { lineNumber: 3, column: 1 });
  assert.deepEqual(page.drags, []);

  // past the distance: the selected text goes to the shell
  down(ev("pointerdown", 100, 3));
  page.fireWindow("pointermove", ev("pointermove", 112, 3));
  assert.deepEqual(page.drags, [{ text: '"frame": 3' }]);
  page.fireWindow("pointerup", ev("pointerup", 112, 3, { buttons: 0 }));
  assert.equal(page.drags.length, 1);

  // outside the selection, in the gutter, with Shift, or with nothing selected: the editor's
  for (const p of [ev("pointerdown", 100, 7), ev("pointerdown", 20, 3), ev("pointerdown", 100, 3, { shiftKey: true })]) {
    down(p);
    assert.equal(p.defaultPrevented, false);
    page.fireWindow("pointermove", ev("pointermove", 160, 3));
  }
  rec.selection = null;
  const none = ev("pointerdown", 100, 3);
  down(none);
  page.fireWindow("pointermove", ev("pointermove", 160, 3));
  assert.equal(none.defaultPrevented, false);
  assert.equal(page.drags.length, 1);
});
