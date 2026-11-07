// The 3D page's scene bookkeeping, run under node against a recording Babylon fake
// (fakes/babylon.mjs) and the page DOM built from 3d/index.html (fakes/page_dom.mjs):
// what `scene` notifications do to the meshes, buffers and materials the page created,
// and what the side panels and the footer do (#129). Run by pytest through
// packages/specto-app/tests/test_scene3d_page.py, or directly:
//     node --test packages/specto-web/tests/scene3d_page.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

import { makeBabylon } from "./fakes/babylon.mjs";
import { pageDocument } from "./fakes/page_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MAIN = path.join(HERE, "..", "web", "3d", "main.js");
const WEB = path.join(HERE, "..", "web");
// the shared modules the page loads before its own script (#129: sidepanel, footer,
// icons; #152: the viewport controls)
const CHROME = ["icons.js", "sidepanel.js", "footer.js", "wheel.js", "viewnav.js"].map((f) => path.join(WEB, f));

/** Boot main.js in a sandbox: returns the handlers it registered on Specto, the
 * recording Babylon, the fetches it started (deferreds) and what it reported. */
function bootPage(tweak) {
  const { babylon, scene, created, imports, frame, textures, loop } = makeBabylon();
  if (tweak) tweak(babylon);   // a test changes the fake Babylon before the page boots
  const handlers = {};
  const observers = [];   // the page's ResizeObservers
  const fetches = [];
  const sent = [];
  const reports = [];
  const health = [];
  const params = [];   // what the page wrote through setParams
  const emitted = [];
  const logged = [];
  const clock = { now: 0 };   // performance.now() in ms; a test advances it
  const Specto = {
    on(kind, fn) { (handlers[kind] = handlers[kind] || []).push(fn); return () => {}; },
    connect() { return Promise.resolve(); },
    emit(port, value) { emitted.push([port, value]); return Promise.resolve({ ok: true, result: { links: [] } }); },
    setParams(patch) { params.push(JSON.parse(JSON.stringify(patch))); return Promise.resolve({ ok: true }); },
    send(kind, payload) { sent.push([kind, payload]); },
    log(level, msg) { logged.push([level, msg]); },
    setHealth(status, msg) { health.push([status, msg || ""]); },
    report(state) { reports.push(state); },
    rectOf() { return null; },
    config: null,
  };
  const document = pageDocument(readFileSync(path.join(WEB, "3d", "index.html"), "utf-8"));
  const sandbox = {
    BABYLON: babylon,
    Specto,
    document,
    console,
    setTimeout,
    clearTimeout,
    performance: { now: () => clock.now },
    location: { origin: "specto://kobi.page" },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    // records what it observes; a test calls `fn` as the browser would on a size change
    ResizeObserver: class { constructor(fn) { this.fn = fn; this.targets = []; observers.push(this); } observe(el) { this.targets.push(el); } },
    Float32Array,
    Uint8Array,
    Math,
    Promise,
    Object,
    Array,
    Error,
    String,
    parseInt,
    fetch(url) {
      let resolve, reject;
      const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
      fetches.push({
        url,
        resolve(points = 2) {
          const buf = new ArrayBuffer(points * 16);
          resolve({ ok: true, status: 200, arrayBuffer: () => Promise.resolve(buf) });
        },
        reject(msg) { reject(new Error(msg || "fetch failed")); },
      });
      return promise;
    },
  };
  sandbox.window = sandbox;
  sandbox.window.addEventListener = () => {};
  sandbox.window.fflate = {};
  vm.createContext(sandbox);
  CHROME.forEach((f) => vm.runInContext(readFileSync(f, "utf-8"), sandbox, { filename: f }));
  vm.runInContext(readFileSync(MAIN, "utf-8"), sandbox, { filename: MAIN });
  const notify = (kind, payload) => (handlers[kind] || []).forEach((fn) => fn(payload));
  // tick the page's render loop n times, as the browser's requestAnimationFrame would
  const frames = (n) => { for (let i = 0; i < n; i++) frame(); };
  return { babylon, scene, created, imports, fetches, sent, reports, health, logged, clock, notify, handlers, frames, document, params, emitted, textures, observers, loop };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(n = 6) { for (let i = 0; i < n; i++) await tick(); }

function points(id, link = "l2") {
  return { id, index: 0, kind: "pointcloud", label: id + ".ply", url: `specto://items/${link}/${id}/points`, count: 2, bounds: [[-1, -1, -1], [1, 1, 1]] };
}
function glb(id, link = "l2") {
  return { id, index: 1, kind: "mesh", label: id + ".glb", url: `specto://items/${link}/${id}/glb`, ext: ".glb", count: 4, bounds: [[0, 0, 0], [2, 2, 2]] };
}
function spz(id, link = "l2") {
  return { id, index: 2, kind: "splat", label: id + ".spz", url: `specto://items/${link}/${id}/source`, ext: ".spz", count: 0, bounds: [[0, 0, 0], [1, 1, 1]] };
}
const scenePayload = (entries, generation = 1) => ({ entries, params: { point_size: 2, up: "y" }, generation, camera: { position: [5, 5, 5], target: [0, 0, 0], up: [0, 1, 0], fov_y: 0.8, near: 0.01, far: 500 } });

/** Ids of the items whose meshes are alive in the scene (the page's own marker aside). */
function liveItems(scene) {
  return [...new Set(scene.meshes.filter((m) => m.metadata && m.metadata.entry).map((m) => m.metadata.entry))].sort();
}
function lastReport(page) { return page.reports[page.reports.length - 1]; }

test("switching source A -> B leaves only B's items, A's buffers and materials disposed", async () => {
  const page = bootPage();
  page.notify("scene", scenePayload([points("x"), glb("y")]));
  await settle();
  page.fetches[0].resolve();
  page.imports[0].resolve();
  await settle();
  assert.deepEqual(liveItems(page.scene), ["x", "y"]);
  // everything created so far except the page's own scene root and pick marker
  const own = new Set(["pick", "pickMat", "root", "root:y", "root:z"]);
  const aMeshes = page.created.filter((o) => o.name && !own.has(o.name));
  const aBuffers = page.created.filter((o) => o.kind === "position" || o.kind === "color");
  assert.equal(aBuffers.length, 2);

  page.notify("scene", scenePayload([points("p", "l3"), glb("q", "l3")], 2));
  await settle();
  page.fetches[1].resolve();
  page.imports[1].resolve();
  await settle();

  assert.deepEqual(liveItems(page.scene), ["p", "q"]);
  assert.deepEqual(lastReport(page).order, ["p", "q"]);
  assert.deepEqual(aMeshes.filter((m) => !m.disposed).map((m) => m.name), [], "A's meshes are disposed");
  assert.ok(aBuffers.every((b) => b.disposed), "A's vertex buffers are disposed");
  const aMaterials = page.created.filter((o) => o.name && o.name.startsWith("ptsMat:x"));
  assert.ok(aMaterials.length === 1 && aMaterials[0].disposed, "A's point material is disposed");
  assert.equal(page.scene.materials.filter((m) => m.pointsCloud).length, 1);
});

test("a partial overlap keeps the shared item, drops the old one, adds the new one", async () => {
  const page = bootPage();
  page.notify("scene", scenePayload([points("x"), glb("y")]));
  await settle();
  page.fetches[0].resolve();
  page.imports[0].resolve();
  await settle();
  const yMesh = page.scene.meshes.find((m) => m.metadata && m.metadata.entry === "y");

  page.notify("scene", scenePayload([glb("y"), points("z")], 2));
  await settle();
  assert.equal(page.imports.length, 1, "y is not loaded again");
  page.fetches[1].resolve();
  await settle();

  assert.deepEqual(liveItems(page.scene), ["y", "z"]);
  assert.ok(!yMesh.disposed && page.scene.meshes.includes(yMesh), "y's mesh is the same one");
  assert.deepEqual(lastReport(page).order, ["y", "z"]);
  assert.ok(page.created.filter((o) => o.name === "pts:x").every((m) => m.disposed));
});

test("a load still in flight for A does not land after B arrived", async () => {
  const page = bootPage();
  page.notify("scene", scenePayload([points("x"), glb("y")]));
  await settle();
  // nothing of A has arrived yet when the source switches
  page.notify("scene", scenePayload([points("p", "l3")], 2));
  await settle();
  page.fetches[1].resolve();
  await settle();
  assert.deepEqual(liveItems(page.scene), ["p"]);

  // A's fetch and import finish late: they must not enter the scene (or stay disposed)
  page.fetches[0].resolve();
  page.imports[0].resolve();
  await settle();
  assert.deepEqual(liveItems(page.scene), ["p"], "A's late loads stay out of the scene");
  assert.ok(page.created.filter((o) => o.name === "pts:x").every((m) => m.disposed));
  assert.ok(page.created.filter((o) => o.name === "imported:y").every((m) => m.disposed));
  assert.deepEqual(lastReport(page).order, ["p"]);
  const items = lastReport(page).items;
  assert.ok(items.p && items.p.loaded && !items.x && !items.y);
  assert.deepEqual(page.health[page.health.length - 1], ["ok", "1 items"]);
});

test("a splat import names the vendored SPZ decoder on the page's own host, never a CDN (#66)", async () => {
  const page = bootPage();
  page.notify("scene", scenePayload([spz("s")]));
  await settle();
  assert.equal(page.imports.length, 1);
  const splat = page.imports[0].options.pluginOptions.splat;
  assert.equal(splat.spzLibraryUrl, "specto://kobi.page/vendor/babylon/spz.js");
  assert.equal(splat.deflateURL, "specto://kobi.page/vendor/babylon/fflate.js");
  page.imports[0].resolve();
  await settle();
  assert.deepEqual(liveItems(page.scene), ["s"]);
  assert.deepEqual(page.health[page.health.length - 1], ["ok", "1 items"]);
});

test("frames keep coming while a splat still sorts, and stop once it has settled (#85)", async () => {
  const page = bootPage();
  page.notify("scene", scenePayload([spz("s")]));
  await settle();
  const splat = page.imports[0].resolveSplat();
  await settle();
  // the sort outlasts the idle budget by far (a 7.6 M-splat file takes seconds)
  const before = page.scene.renders;
  page.frames(60);
  assert.equal(page.scene.renders - before, 60, "every frame renders while the splat sorts");

  splat.settled = true;
  page.frames(60);
  const drawn = page.scene.renders - before - 60;
  assert.ok(drawn >= 1, "the settled splat is drawn");
  assert.ok(drawn <= 20, `rendering stops soon after the splat settled (drew ${drawn})`);
  const settledAt = page.scene.renders;
  page.frames(60);
  assert.equal(page.scene.renders, settledAt, "a static scene renders nothing more");
});

test("a splat that never settles stops costing frames after the settle bound, with a warning (#85)", async () => {
  const page = bootPage();
  page.notify("scene", scenePayload([spz("s")]));
  await settle();
  page.imports[0].resolveSplat();   // and it never reports settled
  await settle();
  page.clock.now = 1000;
  page.frames(30);
  const before = page.scene.renders;
  page.clock.now = 1000 + 60000 + 1;
  page.frames(60);
  assert.ok(page.scene.renders - before < 60, "frames stop once the bound has passed");
  const stopped = page.scene.renders;
  page.frames(60);
  assert.equal(page.scene.renders, stopped);
  assert.deepEqual(page.logged.filter((l) => l[0] === "warn").length, 1, "warned once");
  assert.match(page.logged.find((l) => l[0] === "warn")[1], /did not settle/);
});

test("the camera re-centres on the first load only, not on a source switch", async () => {
  const page = bootPage();
  const cam = () => lastReport(page).camera;
  page.notify("scene", scenePayload([points("x")]));
  await settle();
  page.fetches[0].resolve();
  await settle();
  assert.deepEqual(cam().target, [0, 0, 0]);
  assert.deepEqual(cam().position, [5, 5, 5]);

  // the user moved: a new source keeps their camera
  page.notify("port:camera", { position: [9, 9, 9], target: [1, 2, 3] });
  page.notify("scene", scenePayload([points("p", "l3")], 2));
  await settle();
  page.fetches[1].resolve();
  await settle();
  assert.deepEqual(cam().target, [1, 2, 3]);
  assert.deepEqual(cam().position, [9, 9, 9]);
});

/* ---- the panels and the footer (#129) ------------------------------------------------- */
const $ = (page, sel) => page.document.querySelector(sel);
const $$ = (page, sel) => page.document.querySelectorAll(sel);
const sized = (e, size, label) => Object.assign(e, { size, size_label: label });
const key = (el, type, k) => el.dispatchEvent({ type, key: k });

async function loaded(page, entries) {
  page.notify("scene", scenePayload(entries));
  await settle();
  page.fetches.forEach((f) => f.resolve());
  page.imports.forEach((i) => i.resolve());
  await settle();
}

test("the items panel lists every item with its file size under the name", async () => {
  const page = bootPage();
  await loaded(page, [sized(points("terrain"), 6396313, "6.1 MB"), sized(glb("route"), 1258291, "1.2 MB")]);
  const rows = $$(page, "#items .panel-row");
  assert.deepEqual(rows.map((r) => r.querySelector(".name").textContent), ["terrain.ply", "route.glb"]);
  assert.deepEqual(rows.map((r) => r.querySelector(".sub").textContent), ["6.1 MB", "1.2 MB"]);
});

test("a row's check hides its item; a click elsewhere on the row picks it", async () => {
  const page = bootPage();
  await loaded(page, [points("x"), glb("y")]);
  const row = $(page, "#items .panel-row[data-item=y]");
  row.querySelector(".check").click();
  assert.equal(lastReport(page).items.y.enabled, false);
  assert.ok(row.classList.contains("off") && !row.querySelector(".check").classList.contains("on"));
  assert.deepEqual(page.sent.filter((m) => m[0] === "select"), [], "the check does not pick");
  row.querySelector(".name").click();
  assert.deepEqual(page.sent.filter((m) => m[0] === "select").map((m) => m[1].item), ["y"]);
});

test("robot meshes: an STL keeps its coordinates and draws grey; a converted DAE hangs where up does not turn it (SPEC-42)", async () => {
  const page = bootPage();
  assert.equal(page.babylon.STLFileLoader.DO_NOT_ALTER_FILE_COORDINATES, true, "no y/z swap: a mirror in this right-handed scene");
  const stl = { id: "link", index: 0, kind: "mesh", label: "link.stl", url: "specto://items/l2/link/stl", ext: ".stl", count: 36, bounds: [[0, 0, 0], [1, 1, 1]] };
  const arm = { ...glb("arm"), label: "arm.dae", up: "y" };
  page.notify("scene", { ...scenePayload([stl, arm]), params: { point_size: 2, up: "z" } });
  await settle();
  assert.equal(page.imports[0].options.pluginExtension, ".stl");
  const [linkMesh] = page.imports[0].resolve([{ name: "stlmesh", vertices: 36 }]);
  const [armMesh] = page.imports[1].resolve([{ name: "box", material: "pbr", vertices: 24 }]);
  await settle();
  // an STL comes with no material: the item's own grey one, not Babylon's white default
  assert.ok(linkMesh.material, "a material");
  assert.equal(linkMesh.material.name, "plainMat:link");
  assert.equal(armMesh.material.name, "box:mat", "a file's own material is kept");
  // the STL follows the view's up param (the root turns), the DAE declared y up
  const holder = (id) => page.scene.meshes.find((m) => m.name === "item:" + id);
  assert.equal(holder("link").parent.name, "root");
  assert.equal(holder("arm").parent.name, "root:y");
  assert.equal(holder("link").parent.rotation.x, -Math.PI / 2);
  assert.equal(holder("arm").parent.rotation.x, 0);
  // mesh_unit scales the STL (a unitless file), never the converted DAE
  assert.equal(holder("link").scaling.x, 1);
  page.notify("mesh_scale", { scale: 0.001 });   // the host's factor for mesh_unit mm
  await settle();
  assert.equal(holder("link").scaling.x, 0.001);
  assert.equal(holder("link").scaling.z, 0.001);
  assert.notEqual(holder("arm").scaling.x, 0.001, "a DAE keeps its own scale");
});

test("an MTL texture map's options are stripped before Babylon builds its URL (SPEC-42, SPEC-58)", () => {
  const page = bootPage();
  const get = page.babylon.MTLFileLoader._GetTexture;
  assert.equal(get("specto://items/l2/p/obj/@root/m/", "-s 1 1 1 -o 0 0 -clamp on ../tex/skin.png", {}), "specto://items/l2/p/obj/@root/m/../tex/skin.png");
  assert.equal(get("r/", "plain.png", {}), "r/plain.png");
  assert.equal(get("r/", "-bm 0.5 bump.png", {}), "r/bump.png");
});

test("a Babylon whose MTL loader has no _GetTexture is said in health, not skipped silently (SPEC-58)", async () => {
  const page = bootPage((b) => { delete b.MTLFileLoader; });
  assert.ok(page.logged.some(([level, msg]) => level === "warn" && /_GetTexture/.test(msg)));
  page.notify("scene", scenePayload([glb("y")]));
  await settle();
  page.imports[0].resolve();
  await settle();
  const [status, msg] = page.health[page.health.length - 1];
  assert.equal(status, "ok");
  assert.match(msg, /^1 items · OBJ texture map options are not stripped/);
});

test("the vendored MTLFileLoader still has the _GetTexture the page wraps (SPEC-58)", (t) => {
  const file = path.join(WEB, "vendor", "babylon", "babylonjs.loaders.min.js");
  let text;
  try { text = readFileSync(file, "utf-8"); } catch { t.skip("Babylon is not vendored (packages/specto-web/build.py)"); return; }
  assert.match(text, /static _GetTexture\(/, "Babylon's MTL loader no longer has _GetTexture: the page's option stripping needs a new hook");
});

test("a converted DAE whose version changed loads again: same item, new URL (SPEC-42)", async () => {
  const page = bootPage();
  const arm = (v) => ({ ...glb("arm"), label: "arm.dae", up: "y", url: "specto://items/l2/arm/glb?v=" + v });
  page.notify("scene", scenePayload([arm("1")]));
  await settle();
  const [first] = page.imports[0].resolve([{ name: "box", material: "pbr", vertices: 24 }]);
  await settle();
  assert.deepEqual(liveItems(page.scene), ["arm"]);
  assert.equal(page.imports[0].url, "specto://items/l2/arm/glb?v=1");
  // the DAE changed: the host sends the same item at a new version
  page.notify("scene", scenePayload([arm("2")], 2));
  await settle();
  assert.equal(page.imports.length, 2, "imported again");
  assert.equal(page.imports[1].url, "specto://items/l2/arm/glb?v=2");
  assert.ok(first.disposed, "the old GLB's meshes are gone");
  page.imports[1].resolve([{ name: "box", material: "pbr", vertices: 24 }]);
  await settle();
  assert.deepEqual(liveItems(page.scene), ["arm"]);
});

test("None hides every item and All shows them again", async () => {
  const page = bootPage();
  await loaded(page, [points("x"), glb("y")]);
  const shown = () => Object.fromEntries(Object.entries(lastReport(page).items).map(([k, v]) => [k, v.enabled]));
  $(page, "#items .panel-none").click();
  assert.deepEqual(shown(), { x: false, y: false });
  const itemMeshes = page.scene.meshes.filter((m) => m.parent && m.parent.name === "root");
  assert.equal(itemMeshes.length, 2);
  assert.ok(itemMeshes.every((m) => !m.enabled), "the meshes are off");
  assert.ok($$(page, "#items .panel-row").every((r) => r.classList.contains("off")));
  $(page, "#items .panel-all").click();
  assert.deepEqual(shown(), { x: true, y: true });
  assert.ok($$(page, "#items .panel-row").every((r) => !r.classList.contains("off")));
});

test("a panel's collapse and width are the view's: kept in params.panels and taken back", async () => {
  const page = bootPage();
  await loaded(page, [points("x")]);
  $(page, "#items .panel-head .panel-toggle").click();
  assert.deepEqual(page.params, [{ panels: { items: { collapsed: true, width: 180 } } }]);
  $(page, "#view .panel-head .panel-toggle").click();
  assert.deepEqual(page.params[1], { panels: { items: { collapsed: true, width: 180 }, view: { collapsed: true, width: 150 } } });

  // the document says otherwise (an undo, another window): the panels follow, quietly
  page.notify("params", { panels: { items: { collapsed: false, width: 260 } } });
  assert.equal($(page, "#items").style.width, "260px");
  assert.ok(!$(page, "#items").classList.contains("collapsed"));
  assert.equal(page.params.length, 2);
  assert.equal(lastReport(page).panels.items.width, 260);
});

test("a finished grip drag keeps the new width", async () => {
  const page = bootPage();
  await loaded(page, [points("x")]);
  const grip = $(page, "#items .panel-grip");
  const ev = (type, x) => ({ type, clientX: x, clientY: 0, pointerId: 1, button: 0 });
  grip.dispatchEvent(ev("pointerdown", 500));
  grip.dispatchEvent(ev("pointermove", 440));
  assert.deepEqual(page.params, [], "nothing kept mid-drag");
  grip.dispatchEvent(ev("pointerup", 440));
  assert.deepEqual(page.params, [{ panels: { items: { collapsed: false, width: 240 } } }]);
});

test("the camera speed slider scales how far RMB+W flies, shows the factor and is kept when let go", async () => {
  const page = bootPage();
  await loaded(page, [points("x")]);
  const canvas = $(page, "#c");
  const fly = () => {
    const before = lastReport(page).camera.target[2];
    page.clock.now += 1000; page.frames(1);   // dt starts the clock
    canvas.dispatchEvent({ type: "pointerdown", button: 2, buttons: 2, clientX: 0, clientY: 0, pointerId: 1 });
    key(canvas, "keydown", "w");               // W flies while the right button is held (#152)
    page.clock.now += 50; page.frames(1);
    key(canvas, "keyup", "w");
    canvas.dispatchEvent({ type: "pointerup", button: 2, buttons: 0, clientX: 0, clientY: 0, pointerId: 1 });
    page.handlers.render[0]({ running: true });   // report the camera now
    return Math.abs(lastReport(page).camera.target[2] - before);
  };
  const base = fly();
  assert.ok(base > 0);
  const slider = $(page, "#view input");
  slider.value = "1";   // log2: 2x
  slider.dispatchEvent({ type: "input" });
  assert.equal($(page, "#view .speed-value").textContent, "2.0x");
  assert.ok(Math.abs(fly() - 2 * base) < 1e-9, "twice as far");
  assert.deepEqual(page.params, [], "nothing kept while dragging");
  slider.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params, [{ camera_speed: 2 }]);

  page.notify("params", { camera_speed: 0.5 });
  assert.equal(slider.value, "-1");
  assert.equal($(page, "#view .speed-value").textContent, "0.50x");
});

test("World origin takes the camera back to the host's first look and says so on the camera port", async () => {
  const page = bootPage();
  await loaded(page, [points("x")]);
  page.notify("port:camera", { position: [9, 9, 9], target: [4, 4, 4] });
  page.emitted.length = 0;
  $(page, "#origin").click();
  assert.deepEqual(lastReport(page).camera.target, [0, 0, 0]);
  assert.deepEqual(lastReport(page).camera.position, [5, 5, 5]);
  await new Promise((r) => setTimeout(r, 200));   // the camera emit is throttled
  assert.deepEqual(page.emitted.map((e) => [e[0], e[1].target]), [["camera", [0, 0, 0]]]);
});

test("the footer counts the items and its target button frames them all", async () => {
  const page = bootPage();
  await loaded(page, [points("x"), glb("y"), spz("z")]);
  assert.equal($(page, "#count").textContent, "3 items");
  assert.equal($(page, "#frameAll").getAttribute("data-icon"), "target");
  page.notify("port:camera", { position: [90, 90, 90], target: [40, 40, 40] });
  $(page, "#frameAll").click();
  await new Promise((r) => setTimeout(r, 200));   // reported with the throttled camera emit
  // every fake item spans -1..1: frame all centres on the origin
  assert.deepEqual(lastReport(page).camera.target, [0, 0, 0]);
  assert.ok(Math.hypot(...lastReport(page).camera.position) < 20);
});

test("the page has no in-body bar: panels either side of the canvas, the footer under them", () => {
  const html = readFileSync(path.join(HERE, "..", "web", "3d", "index.html"), "utf-8");
  assert.doesNotMatch(html, /class="bar"|id="fps"|id="caps"|id="title"|Fit all|WASD/);
  const page = bootPage();
  const row = $(page, "#row");
  assert.deepEqual(row.children.map((c) => c.id || c.className), ["view", "stage", "items"]);
  assert.equal(row.nextElementSibling.id, "footer");
});

test("the canvas's own size drives the engine: a panel restored from params resizes it and draws again (#180)", async () => {
  const page = bootPage();
  await loaded(page, [points("x")]);
  const canvas = $(page, "#c");
  const watch = page.observers.find((o) => o.targets.includes(canvas));
  assert.ok(watch, "a ResizeObserver on the canvas");
  page.frames(40);   // idle: nothing left to draw
  const resizes = page.loop.resizes, renders = page.scene.renders;
  // a saved document opens with the VIEW panel wider: the canvas narrows without a
  // window resize or a user's toggle, and the browser reports it to the observer
  page.notify("params", { panels: { view: { collapsed: false, width: 300 } } });
  watch.fn([{ target: canvas }]);
  assert.equal(page.loop.resizes, resizes + 1);
  page.frames(1);
  assert.equal(page.scene.renders, renders + 1);
});

/* ---- the viewport controls (#152, web/viewnav.js) ---------------------------------------- */
const press = (el, type, o) => el.dispatchEvent({ type, pointerId: 1, clientX: o.x, clientY: o.y, offsetX: o.x, offsetY: o.y, button: o.button || 0, buttons: o.buttons, altKey: !!o.alt });
function itemNode(page, id) { return page.scene.meshes.find((m) => m.metadata && m.metadata.entry === id && m.parent && m.parent.name === "root"); }

test("an LMB click on the canvas picks; a drag moves the camera and does not", async () => {
  const page = bootPage();
  await loaded(page, [points("x")]);
  const canvas = $(page, "#c");
  press(canvas, "pointerdown", { x: 50, y: 40, buttons: 1 });
  press(canvas, "pointerup", { x: 51, y: 40, buttons: 0 });
  assert.deepEqual(page.sent.map((m) => m[0]), ["pick"]);
  const before = lastReport(page).camera.position;
  press(canvas, "pointerdown", { x: 50, y: 40, buttons: 1 });
  press(canvas, "pointermove", { x: 50, y: 10, buttons: 1 });
  press(canvas, "pointerup", { x: 50, y: 10, buttons: 0 });
  assert.deepEqual(page.sent.map((m) => m[0]), ["pick"], "a drag is not a pick");
  page.handlers.render[0]({ running: true });
  assert.notDeepEqual(lastReport(page).camera.position, before, "the drag walked the camera");
});

test("F frames the selection, else everything (#152)", async () => {
  const page = bootPage();
  await loaded(page, [points("x"), glb("y")]);
  itemNode(page, "y").bounds = { min: page.babylon.Vector3.FromArray([10, 10, 10]), max: page.babylon.Vector3.FromArray([12, 12, 12]) };
  const canvas = $(page, "#c");
  const target = () => { page.handlers.render[0]({ running: true }); return lastReport(page).camera.target; };   // report now

  page.notify("port:selection", { keys: ["y"] });
  key(canvas, "keydown", "f");
  assert.deepEqual(target(), [11, 11, 11], "the selected item");

  page.notify("port:selection", { keys: [] });
  key(canvas, "keydown", "f");
  assert.deepEqual(target(), [5.5, 5.5, 5.5], "nothing selected: everything");

  // the page's own pick is the selection too: a mesh hit frames its item
  page.notify("picked", { item: "y", index: null, position: [11, 12, 11] });
  key(canvas, "keydown", "f");
  assert.deepEqual(target(), [11, 11, 11]);
  // a picked point frames a small box around it
  page.notify("picked", { item: "x", index: 1, position: [0.5, 0.5, 0.5] });
  key(canvas, "keydown", "f");
  assert.deepEqual(target(), [0.5, 0.5, 0.5]);
  // a click on nothing clears it
  page.notify("picked", null);
  key(canvas, "keydown", "f");
  assert.deepEqual(target(), [5.5, 5.5, 5.5]);
});

test("Alt+LMB orbits around the selection, keeping the eye's distance to it", async () => {
  const page = bootPage();
  await loaded(page, [points("x"), glb("y")]);
  itemNode(page, "y").bounds = { min: page.babylon.Vector3.FromArray([10, 10, 10]), max: page.babylon.Vector3.FromArray([12, 12, 12]) };
  page.notify("port:selection", { keys: ["y"] });
  const canvas = $(page, "#c");
  const cam = () => lastReport(page).camera;
  const d = (p) => Math.hypot(p[0] - 11, p[1] - 11, p[2] - 11);
  page.handlers.render[0]({ running: true });
  const before = cam().position;
  press(canvas, "pointerdown", { x: 50, y: 40, buttons: 1, alt: true });
  press(canvas, "pointermove", { x: 120, y: 60, buttons: 1, alt: true });
  press(canvas, "pointerup", { x: 120, y: 60, buttons: 0, alt: true });
  page.handlers.render[0]({ running: true });
  assert.notDeepEqual(cam().position, before);
  assert.ok(Math.abs(d(cam().position) - d(before)) < 1e-9, "the pivot is the selected item's centre");
  assert.deepEqual(page.sent, [], "an Alt press never picks");
});

/* ---- view modes (#153): lit, unlit, wireframe; collision when the content has it ------- */
async function withParts(page, parts) {
  page.notify("scene", scenePayload([points("x"), glb("y")]));
  await settle();
  page.fetches[0].resolve();
  const meshes = page.imports[0].resolve(parts);
  await settle();
  return Object.fromEntries(meshes.map((m) => [m.name, m]));
}
const modeItems = (page) => $$(page, "#viewModeMenu .mode-item").map((b) => b.getAttribute("data-mode"));
function chooseMode(page, mode) {
  $(page, "#viewMode").click();
  $(page, `#viewModeMenu .mode-item[data-mode=${mode}]`).click();
}

test("view modes: the pill always in sight (#158); unlit and wireframe from the footer, kept", async () => {
  const page = bootPage();
  const m = await withParts(page, [{ name: "Body", material: "pbr" }, { name: "Trim", material: "standard" }]);
  const pts = page.scene.meshes.find((x) => x.name === "pts:x");
  const button = $(page, "#viewMode");
  assert.equal(lastReport(page).view_mode, "lit");
  assert.ok(!button.classList.contains("rest-hidden") && button.textContent === "Lit", "lit shows too (#158)");
  assert.ok(!button.classList.contains("on"), "lit is the default: not in accent");
  $(page, "#viewMode").click();
  assert.deepEqual(modeItems(page), ["lit", "unlit", "wireframe"], "no collision data, no collision mode");
  assert.deepEqual([...lastReport(page).view_modes], ["lit", "unlit", "wireframe"]);
  $(page, "#viewMode").click();

  const lightBg = page.scene.clearColor;
  chooseMode(page, "wireframe");
  assert.ok(m.Body.material.wireframe && m.Trim.material.wireframe);
  assert.equal(pts.material.wireframe, false, "points stay points");
  assert.ok(page.scene.clearColor.r < 0.2 && page.scene.clearColor.b < 0.2, "wire over a dark background");
  assert.deepEqual(page.params[page.params.length - 1], { view_mode: "wireframe" });
  assert.ok(button.classList.contains("on") && button.textContent === "Wireframe", "a mode other than lit in accent");

  chooseMode(page, "unlit");
  assert.equal(m.Body.material.wireframe, false);
  assert.equal(m.Body.material.unlit, true, "PBR: albedo only");
  assert.equal(m.Trim.material.disableLighting, true, "standard: no lighting...");
  assert.equal(m.Trim.material.emissiveColor, m.Trim.material.diffuseColor, "...its base colour as is");
  assert.deepEqual({ ...page.scene.clearColor }, { ...lightBg }, "the theme background back");

  chooseMode(page, "lit");
  assert.equal(m.Body.material.unlit, false);
  assert.equal(m.Trim.material.disableLighting, false);
  assert.deepEqual([m.Trim.material.emissiveColor.r, m.Trim.material.emissiveColor.g], [0, 0]);
  assert.ok(!button.classList.contains("on") && !button.classList.contains("rest-hidden"));
  assert.deepEqual(page.params.map((p) => p.view_mode), ["wireframe", "unlit", "lit"], "one kept change per pick");
});

test("collision shows only with Unreal collision meshes: tinted, the rest ghosted, points and splats hidden", async () => {
  const page = bootPage();
  const m = await withParts(page, [{ name: "Body", material: "pbr" }, { name: "UCX_Body_00", material: "standard" }]);
  const pts = page.scene.meshes.find((x) => x.name === "pts:x");
  assert.equal(m.UCX_Body_00.isVisible, false, "collision hulls hide in the normal modes");
  $(page, "#viewMode").click();
  assert.deepEqual(modeItems(page), ["lit", "unlit", "wireframe", "collision"]);
  $(page, "#viewMode").click();

  chooseMode(page, "collision");
  assert.equal(m.UCX_Body_00.isVisible, true);
  assert.equal(m.UCX_Body_00.material.name, "collisionMat", "a flat tint");
  assert.ok(m.Body.visibility < 0.5 && m.Body.isVisible, "the rest ghosted");
  assert.equal(pts.isVisible, false);
  assert.equal(lastReport(page).view_mode, "collision");

  chooseMode(page, "lit");
  assert.equal(m.UCX_Body_00.isVisible, false);
  assert.equal(m.UCX_Body_00.material.name, "UCX_Body_00:mat", "its own material back");
  assert.equal(m.Body.visibility, 1);
  assert.equal(pts.isVisible, true);
});

test("the agent's view_mode param switches quietly; collision without collision data shows lit", async () => {
  const page = bootPage();
  const m = await withParts(page, [{ name: "Body", material: "pbr" }]);
  page.notify("params", { view_mode: "unlit" });
  assert.equal(m.Body.material.unlit, true);
  assert.equal($(page, "#viewMode").textContent, "Unlit");
  page.notify("params", { view_mode: "collision" });
  assert.equal(lastReport(page).view_mode, "lit");
  assert.equal(m.Body.material.unlit, false);
  assert.deepEqual(page.params, [], "nothing written back");
});

/* ---- HDRI environments (#175): the footer's pill, the VIEW panel's group, the params ---- */
const ENVS = [
  { name: "studio", label: "Studio", look: "softboxes", author: "Sergej Majboroda", url: "/vendor/hdri/studio.env" },
  { name: "sunny", label: "Sunny", look: "midday sun", author: "Greg Zaal", url: "/vendor/hdri/sunny.hdr" },
  { name: "urban", label: "Urban", look: "a city square", author: "Greg Zaal", url: null },
];
async function withEnvironments(page, params = {}) {
  page.notify("scene", { ...scenePayload([points("x"), glb("y")]), environments: ENVS, params: { point_size: 2, up: "y", ...params } });
  await settle();
  page.fetches[0].resolve();
  const meshes = page.imports[0].resolve([{ name: "Body", material: "pbr" }]);
  await settle();
  return meshes[0];
}
const envItems = (page) => $$(page, "#environmentMenu .mode-item").map((b) => b.getAttribute("data-env"));
function chooseEnv(page, name) {
  $(page, "#environment").click();
  $(page, `#environmentMenu .mode-item[data-env=${name}]`).click();
}
const lightsOn = (page) => page.scene.lights.map((l) => l.enabled);

test("the environment pill is always in sight; its menu lists None and every bundled HDRI", async () => {
  const page = bootPage();
  await withEnvironments(page);
  const pill = $(page, "#environment");
  assert.equal(pill.textContent, "No HDRI", "none is the default");
  assert.ok(!pill.classList.contains("on") && !pill.classList.contains("rest-hidden"));
  assert.equal(lastReport(page).environment.name, "none");
  assert.deepEqual([...lastReport(page).environments], ["studio", "sunny", "urban"]);
  pill.click();
  assert.deepEqual(envItems(page), ["none", "studio", "sunny", "urban"]);
  assert.ok(lastReport(page).envs && "studio" in lastReport(page).envs, "the menu's rows are reported");
  const urban = $(page, "#environmentMenu .mode-item[data-env=urban]");
  assert.ok(urban.disabled && /not vendored/.test(urban.title), "a missing file is offered greyed, with the fix");
  assert.ok(/Poly Haven \(CC0\)/.test($(page, "#environmentMenu .mode-item[data-env=studio]").title), "the credit");
  pill.click();
  assert.equal(lastReport(page).envs, null);
  assert.deepEqual(lightsOn(page), [true, true], "none keeps today's two lights");
  assert.equal(page.scene.environmentTexture, null);
});

test("picking an environment loads its prefiltered .env, lights PBR by it, turns the lights off, keeps it", async () => {
  const page = bootPage();
  await withEnvironments(page);
  chooseEnv(page, "studio");
  assert.deepEqual(page.params[page.params.length - 1], { environment: "studio" }, "one user submit");
  assert.equal(page.textures.length, 1);
  const t = page.textures[0];
  assert.equal(t.kind, "env");
  assert.equal(t.url, "/vendor/hdri/studio.env", "the vendored file on the page's own host, never the network");
  assert.deepEqual(t.tex.args, { prefiltered: true, forcedExtension: ".env" });
  // while it loads: the look before, the pill says so
  assert.deepEqual(lightsOn(page), [true, true]);
  assert.equal(page.scene.environmentTexture, null);
  assert.equal($(page, "#environment").textContent, "Studio…");
  assert.equal(lastReport(page).environment.ready, false);

  page.clock.now = 85;
  t.load();
  assert.equal(page.scene.environmentTexture, t.tex, "image-based lighting from the HDRI");
  assert.deepEqual(lightsOn(page), [false, false], "the two lights step aside");
  const pill = $(page, "#environment");
  assert.ok(pill.textContent === "Studio" && pill.classList.contains("on"), "named, in accent");
  const env = lastReport(page).environment;
  assert.equal(env.name, "studio");
  assert.equal(env.ready, true);
  assert.equal(env.load_ms, 85);
  assert.equal(env.background, false, "no background unless asked");

  chooseEnv(page, "none");
  assert.equal(page.scene.environmentTexture, null);
  assert.deepEqual(lightsOn(page), [true, true], "None: the lights as they were");
  chooseEnv(page, "studio");
  assert.equal(page.textures.length, 1, "loaded once, kept");
  assert.equal(page.scene.environmentTexture, t.tex, "and on again at once");
  assert.deepEqual(page.params.map((p) => p.environment), ["studio", "none", "studio"]);
});

test("a raw .hdr (no bake on the build machine) filters on load; a load error goes to health", async () => {
  const page = bootPage();
  await withEnvironments(page);
  chooseEnv(page, "sunny");
  const t = page.textures[0];
  assert.equal(t.kind, "hdr");
  assert.deepEqual(t.tex.args, { size: 256, noMipmap: false, harmonics: true, gamma: false, prefilter: true });
  t.fail("bad header");
  assert.deepEqual(page.health[page.health.length - 1], ["error", "Sunny environment: bad header"]);
  assert.equal(lastReport(page).environment.error, "bad header");
  assert.deepEqual(lightsOn(page), [true, true], "the scene stays lit");
});

test("the agent's environment params: intensity, rotation, background; the skybox behind all, not in wireframe", async () => {
  const page = bootPage();
  await withEnvironments(page, { environment: "studio", environment_intensity: 2.5, environment_rotation: 450, environment_background: true });
  assert.equal(page.textures.length, 1, "the param alone loads it");
  page.textures[0].load();
  const tex = page.textures[0].tex;
  assert.equal(page.scene.environmentIntensity, 2.5);
  assert.ok(Math.abs(tex.rotationY - Math.PI / 2) < 1e-9, "450 deg is a quarter turn about up");
  const sky = page.scene.meshes.find((m) => m.name === "hdrSkyBox");
  assert.ok(sky && sky.enabled, "the HDRI drawn behind the scene");
  assert.deepEqual(sky.skybox, { tex, pbr: true, size: 1, blur: 0, setGlobal: false });
  assert.equal(sky.isPickable, false, "a click never picks the sky");
  assert.equal(sky.material.disableDepthWrite, true);
  assert.ok(Math.abs(sky.material.reflectionTexture.rotationY - Math.PI / 2) < 1e-9, "turned with the lighting");
  const first = page.scene.renderingOrder[0];
  const sub = (mesh) => ({ getMesh: () => mesh });
  const body = page.scene.meshes.find((m) => m.name === "Body");
  assert.ok(first(sub(sky), sub(body)) < 0 && first(sub(body), sub(sky)) > 0, "the sky draws first");
  assert.equal(first(sub(body), sub(body)), 0, "the rest keeps its order");
  page.frames(1);
  assert.ok(sky.scaling.x > 0, "sized to the far plane");
  assert.equal(lastReport(page).environment.background, true);
  assert.deepEqual(page.params, [], "the agent's params are not written back");

  page.notify("params", { environment: "studio", environment_background: true, view_mode: "wireframe" });
  assert.equal(sky.enabled, false, "wireframe keeps its dark background");
  page.notify("params", { environment: "studio", environment_background: false, view_mode: "lit" });
  assert.equal(sky.enabled, false);
  page.notify("params", { environment: "nowhere" });
  assert.equal(lastReport(page).environment.name, "none", "an unknown name is none");
  assert.deepEqual(lightsOn(page), [true, true]);
});

test("the VIEW panel's environment group: sliders live while dragged and kept when let go, the background toggle", async () => {
  const page = bootPage();
  await withEnvironments(page);
  const [intensity, rotation] = $$(page, ".env-controls input");
  const bg = $(page, "#environmentBackground");
  assert.ok(intensity.disabled && rotation.disabled && bg.disabled, "off while no HDRI is on");
  chooseEnv(page, "studio");
  page.textures[0].load();
  assert.ok(!intensity.disabled && !rotation.disabled && !bg.disabled);
  assert.equal(intensity.value, "1");

  intensity.value = "0.5";
  intensity.dispatchEvent({ type: "input" });
  assert.equal(page.scene.environmentIntensity, 0.5, "live");
  assert.equal(page.params.length, 1, "nothing kept while dragging");
  intensity.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params[page.params.length - 1], { environment_intensity: 0.5 });
  rotation.value = "90";
  rotation.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params[page.params.length - 1], { environment_rotation: 90 });
  assert.ok(Math.abs(page.textures[0].tex.rotationY - Math.PI / 2) < 1e-9);
  assert.equal($$(page, ".env-controls .speed-value").map((v) => v.textContent).join(" "), "0.50x 90°");

  bg.checked = true;
  bg.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params[page.params.length - 1], { environment_background: true });
  assert.equal(lastReport(page).environment.background, true);
  const b = lastReport(page).buttons;
  assert.ok("environment" in b && "envIntensity" in b && "envRotation" in b && "envBackground" in b, "located for GUI flows");
});

test("points keep their own look under an environment", async () => {
  const page = bootPage();
  await withEnvironments(page, { environment: "studio" });
  page.textures[0].load();
  const pts = page.scene.meshes.find((m) => m.name === "pts:x");
  assert.equal(pts.material.disableLighting, true, "points stay unlit, their own colours");
  assert.equal(pts.material.emissiveColor.r, 1);
  assert.equal(pts.isVisible, true);
});
