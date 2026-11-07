// The URDF view's page (web/urdf/main.js, SPEC-43.2) under node, against the recording
// Babylon fake (fakes/babylon.mjs) and the page DOM built from urdf/index.html
// (fakes/page_dom.mjs), fed the hand-written model in fixtures/urdf/arm.json (section 11
// of backlog/docs/urdf-view-design.md): what the robot's scene graph, geometry, view
// modes, overlays, panels, sliders, selection and footer do. Run by pytest through
// packages/specto-app/tests/test_urdf_page.py, or directly:
//     node --test packages/specto-web/tests/urdf_page.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

import { makeBabylon } from "./fakes/babylon.mjs";
import { pageDocument } from "./fakes/page_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.join(HERE, "..", "web");
const MAIN = path.join(WEB, "urdf", "main.js");
const SCRIPTS = ["icons.js", "sidepanel.js", "step.js", "footer.js", "wheel.js", "viewnav.js", "urdf/kin.js"].map((f) => path.join(WEB, f));
const ARM = JSON.parse(readFileSync(path.join(HERE, "fixtures", "urdf", "arm.json"), "utf-8"));
const ENVIRONMENTS = [
  { name: "studio", label: "Studio", look: "", author: "", page: "", url: "/vendor/hdri/studio.env" },
  { name: "sunny", label: "Sunny", look: "", author: "", page: "", url: null },
];
const plain = (x) => JSON.parse(JSON.stringify(x));

function bootPage(opts = {}) {
  const { babylon, scene, created, imports, frame, textures } = makeBabylon();
  if (opts.noWebGL) babylon.Engine = class { constructor() { throw new Error("WebGL not supported"); } };
  const handlers = {};
  const sent = [];
  const reports = [];
  const health = [];
  const params = [];
  const emitted = [];
  const logged = [];
  const Specto = {
    on(kind, fn) { (handlers[kind] = handlers[kind] || []).push(fn); return () => {}; },
    connect() { return Promise.resolve(); },
    emit(port, value) { emitted.push([port, value]); return Promise.resolve({ ok: true }); },
    setParams(patch) { params.push(plain(patch)); return Promise.resolve({ ok: true }); },
    send(kind, payload) { sent.push([kind, plain(payload)]); },
    log(level, msg) { logged.push([level, msg]); },
    setHealth(status, msg) { health.push([status, msg || ""]); },
    report(state) { reports.push(state); },
    rectOf() { return null; },
    config: null,
  };
  const document = pageDocument(readFileSync(path.join(WEB, "urdf", "index.html"), "utf-8"));
  const sandbox = {
    BABYLON: babylon,
    Specto,
    document,
    console,
    setTimeout,
    clearTimeout,
    performance: { now: () => 0 },
    location: { origin: "specto://kobi.page" },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    ResizeObserver: class { observe() {} },
    Math, Promise, Object, Array, Error, String, Number, JSON, isFinite, parseInt,
  };
  sandbox.window = sandbox;
  sandbox.window.addEventListener = () => {};
  vm.createContext(sandbox);
  SCRIPTS.forEach((f) => vm.runInContext(readFileSync(f, "utf-8"), sandbox, { filename: f }));
  vm.runInContext(readFileSync(MAIN, "utf-8"), sandbox, { filename: MAIN });
  const notify = (kind, payload) => (handlers[kind] || []).forEach((fn) => fn(payload));
  const frames = (n) => { for (let i = 0; i < n; i++) frame(); };
  const last = () => reports[reports.length - 1];
  const named = (name) => scene.meshes.filter((m) => !m.disposed && m.name === name);
  const one = (name) => { const m = named(name); assert.equal(m.length, 1, `one ${name}, found ${m.length}`); return m[0]; };
  return { babylon, scene, created, imports, sent, reports, health, logged, notify, frames, document, params, emitted, textures, last, named, one, handlers };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(n = 8) { for (let i = 0; i < n; i++) await tick(); }
/** Boot, send the arm (a fresh copy) with the given params, and resolve its STL imports. */
async function armPage(p = {}, opts = {}) {
  const page = bootPage();
  page.notify("config", { params: { show_grid: true, ...p } });
  page.notify("model", { generation: 3, model: JSON.parse(JSON.stringify(ARM)), environments: ENVIRONMENTS });
  if (opts.resolve !== false) {
    await settle();
    page.imports.forEach((imp) => imp.resolve());
    await settle();
  }
  return page;
}
const q4 = (n) => (n.rotationQuaternion ? n.rotationQuaternion.asArray() : null);
const near = (a, b, eps = 1e-6) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < eps);
const rowEl = (page, key) => page.document.querySelector(`[data-key=${key}]`);
const sliderRow = (page, joint) => page.document.querySelector(`[data-joint=${joint}]`);
const sentOf = (page, kind) => page.sent.filter((s) => s[0] === kind).map((s) => s[1]);

test("the model builds a node per link under the joint origin that holds it, the robot under the up turn", async () => {
  const page = await armPage();
  const base = page.one("link:base_link");
  const upper = page.one("link:upper_arm");
  const shoulder = page.one("joint:shoulder");
  assert.equal(base.parent.name, "robot");
  assert.equal(page.one("robot").parent.name, "world");
  assert.equal(shoulder.parent, base);
  assert.equal(upper.parent, shoulder);
  assert.deepEqual(shoulder.position.asArray(), [0, 0, 0.1]);
  assert.equal(page.one("joint:elbow").parent, upper);
  // up z (REP 103): a quarter turn about X takes the robot's Z onto Babylon's Y
  const w = q4(page.one("world"));
  assert.ok(near(w, [-Math.SQRT1_2, 0, 0, Math.SQRT1_2]), String(w));
  const r = page.last();
  assert.deepEqual(plain(r.robot), { name: "arm", root: "base_link", links: 8, joints: 7 });
  assert.equal(r.generation, 3);
});

test("primitives through MeshBuilder: URDF's cylinder stands on Z, sizes from the model, one material per colour", async () => {
  const page = await armPage();
  const box = page.one("visual-shape:base_link:0");
  assert.equal(box.builder.shape, "box");
  assert.deepEqual([box.builder.width, box.builder.height, box.builder.depth], [0.3, 0.3, 0.1]);
  assert.equal(box.parent.name, "visual:base_link:0");
  assert.deepEqual(box.parent.position.asArray(), [0, 0, 0.05]);
  const cyl = page.one("visual-shape:upper_arm:0");
  assert.equal(cyl.builder.shape, "cylinder");
  assert.equal(cyl.builder.diameter, 0.12);
  assert.equal(cyl.builder.height, 0.42);
  assert.ok(Math.abs(cyl.rotation.x - Math.PI / 2) < 1e-12, "turned from Babylon's Y onto Z");
  // the same grey on both links is one PBR material
  assert.equal(box.material, cyl.material);
  assert.equal(box.material.getClassName(), "PBRMaterial");
  assert.equal(box.material.albedoColor.r, 0.54);
  assert.equal(box.material.backFaceCulling, false);
});

test("a mesh file loads once, by its ext, and each link that uses it gets a clone at its own scale", async () => {
  const page = await armPage();
  const stl = page.imports.filter((i) => i.url === "/items/arm/meshes/segment.stl");
  assert.equal(stl.length, 1, "segment.stl is shared by forearm and wrist");
  assert.equal(stl[0].options.pluginExtension, ".stl");
  assert.equal(stl[0].options.rootUrl, "/items/arm/meshes/");
  assert.equal(page.imports.some((i) => /collision/.test(i.url)), false, "collision meshes wait for a collision mode");
  const fore = page.one("mesh:forearm:0");
  const wrist = page.one("mesh:wrist:0");
  assert.equal(fore.cloneOf, wrist.cloneOf, "both clone the one template");
  assert.equal(fore.cloneOf.enabled, false, "the template itself never draws");
  assert.deepEqual(wrist.parent.scaling.asArray(), [0.5, 0.5, 0.5]);
  // STL brings no material: the URDF colour (forearm orange), the default grey for wrist
  const meshOf = (copy) => copy.getChildMeshes(false).concat([copy]).find((m) => m.metadata && m.metadata.link);
  assert.equal(meshOf(fore).material.albedoColor.r, 0.9);
  assert.equal(meshOf(wrist).material.albedoColor.r, 0.6);
  const loaded = sentOf(page, "loaded");
  assert.equal(loaded.length, 1);
  assert.deepEqual(loaded[0].failed, []);
  assert.deepEqual(page.health.map((h) => h[0]), ["loading"], "the page only says loading; the host owns the dot");
});

test("a missing mesh draws the link's collision primitive as a magenta wire stand-in; an unreadable one a 5 cm box", async () => {
  const page = await armPage({}, { resolve: false });
  await settle();
  const stand = page.one("placeholder:gripper");
  assert.equal(stand.builder.shape, "box");
  assert.deepEqual([stand.builder.width, stand.builder.height, stand.builder.depth], [0.08, 0.04, 0.04]);
  assert.equal(stand.material.wireframe, true);
  assert.equal(stand.material.emissiveColor.r, 1);
  assert.equal(stand.material.emissiveColor.g, 0);
  page.imports[0].reject("bad stl");
  await settle();
  // forearm has only a mesh collider: the 5 cm box; wrist's collider is a sphere: its stand-in
  const fore = page.one("placeholder:forearm");
  assert.equal(fore.builder.size, 0.05);
  assert.equal(page.one("placeholder:wrist").builder.shape, "sphere");
  const failed = sentOf(page, "loaded")[0].failed;
  assert.deepEqual(failed.map((f) => f.link).sort(), ["forearm", "wrist"]);
  assert.match(failed[0].error, /bad stl/);
  assert.equal(page.last().meshes.placeholders, 3);
  // the tree marks the link whose mesh is missing
  assert.ok(rowEl(page, "link:gripper").classList.contains("broken"));
});

test("collision: built the first time it shows, visuals hidden, each link's colliders flat in its own tint", async () => {
  const page = await armPage();
  assert.equal(page.named("collision-shape:base_link:0").length, 0);
  page.notify("params", { view_mode: "collision" });
  await settle();
  page.imports.filter((i) => /collision/.test(i.url)).forEach((i) => i.resolve());
  await settle();
  const base = page.one("collision-shape:base_link:0");
  const upper = page.one("collision-shape:upper_arm:0");
  assert.equal(base.isVisible, true);
  assert.equal(page.one("visual-shape:base_link:0").isVisible, false);
  assert.notEqual(base.material, upper.material, "a tint per link");
  assert.equal(base.material.disableLighting, true);
  assert.equal(base.material.alpha, 1);
  assert.ok(near([base.material.emissiveColor.r, base.material.emissiveColor.g, base.material.emissiveColor.b], [0.2, 0.8, 0.45]));
  assert.equal(page.imports.filter((i) => /segment_collision/.test(i.url)).length, 1);
  assert.equal(page.last().meshes.collisions_built, true);
  // visual + collision: visuals lit again, colliders 35 % shells with wire edges
  page.notify("params", { view_mode: "visual_collision" });
  assert.equal(page.one("visual-shape:base_link:0").isVisible, true);
  assert.equal(base.material.alpha, 0.35);
  assert.ok(base.edges, "wire edges");
  page.notify("params", { view_mode: "lit" });
  assert.equal(base.isVisible, false);
  assert.equal(base.edges, null);
});

test("collision hover names the link and the shape under the cursor", async () => {
  const page = await armPage({ view_mode: "collision" });
  const target = page.one("collision-shape:upper_arm:0");
  page.scene.pick = (x, y, predicate) => (predicate(target) ? { hit: true, pickedMesh: target } : null);
  page.document.getElementById("c").dispatchEvent({ type: "pointermove", offsetX: 40, offsetY: 30, buttons: 0 });
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(page.last().hover, "upper_arm · cylinder");
  assert.equal(page.document.getElementById("hover").textContent, "upper_arm · cylinder");
});

test("a pose turns the joints; a mimic follows its master", async () => {
  const page = await armPage();
  page.notify("pose", { joints: { shoulder: Math.PI / 2, gripper_joint: 0.02 }, source: "params" });
  const upper = page.one("link:upper_arm");
  assert.ok(near(q4(upper), [0, Math.SQRT1_2, 0, Math.SQRT1_2]), "a quarter turn about +Y");
  assert.ok(near(page.one("link:finger_a").position.asArray(), [0.02, 0, 0]));
  assert.ok(near(page.one("link:finger_b").position.asArray(), [0.02, 0, 0]), "the mimic slides with it");
  const r = page.last();
  assert.equal(r.source, "params");
  assert.equal(r.footer.source, "joints: params");
  assert.equal(r.joints.finger_mimic.text, "20.0 mm");
  assert.equal(sliderRow(page, "finger_mimic").querySelector(".follows").textContent, "= gripper_joint");
});

test("sliders: degrees and mm with the URDF limits, live while dragged, one `joints` message on release", async () => {
  const page = await armPage();
  const r0 = page.last();
  assert.deepEqual(Object.keys(r0.joints), ["shoulder", "elbow", "wrist_1", "gripper_joint", "finger_mimic"]);
  assert.ok(Math.abs(r0.joints.shoulder.min + 140) < 0.01 && Math.abs(r0.joints.shoulder.max - 140) < 0.01);
  assert.equal(r0.joints.wrist_1.min, -180);
  assert.equal(r0.joints.gripper_joint.max, 40);
  assert.equal(r0.joints.finger_mimic.slider, null, "a mimic row has no handle");
  const input = sliderRow(page, "elbow").querySelector("input");
  input.value = "64";
  input.dispatchEvent({ type: "input" });
  assert.equal(sentOf(page, "joints").length, 0, "nothing sent while dragging");
  assert.equal(page.last().joints.elbow.text, "64.0°");
  assert.equal(page.last().footer.source, "joints: slider");
  input.dispatchEvent({ type: "change" });
  const j = sentOf(page, "joints");
  assert.equal(j.length, 1);
  assert.deepEqual(Object.keys(j[0]).sort(), ["elbow", "gripper_joint", "shoulder", "wrist_1"]);
  assert.ok(Math.abs(j[0].elbow - 64 * Math.PI / 180) < 1e-9, "radians out");
  assert.equal(page.params.length, 0, "joints go as a message, not a params write");
});

test("a click on a value types one: Enter keeps it (clamped or wrapped), Escape drops it", async () => {
  const page = await armPage();
  const value = sliderRow(page, "wrist_1").querySelector(".value");
  value.click();
  const typed = value.querySelector("input");
  assert.ok(typed, "an input replaces the value");
  typed.value = "190";
  typed.dispatchEvent({ type: "keydown", key: "Enter" });
  const j = sentOf(page, "joints");
  assert.equal(j.length, 1);
  assert.ok(Math.abs(j[0].wrist_1 + 170 * Math.PI / 180) < 1e-9, "continuous wraps at 180");
  const g = sliderRow(page, "gripper_joint").querySelector(".value");
  g.click();
  g.querySelector("input").value = "99";
  g.querySelector("input").dispatchEvent({ type: "keydown", key: "Enter" });
  assert.equal(sentOf(page, "joints")[1].gripper_joint, 0.04, "prismatic clamps into its limits, in metres");
  g.click();
  g.querySelector("input").value = "5";
  g.querySelector("input").dispatchEvent({ type: "keydown", key: "Escape" });
  assert.equal(sentOf(page, "joints").length, 2);
  assert.equal(page.last().joints.gripper_joint.text, "40.0 mm");
});

test("while a track drives the robot the sliders are locked and say what they follow", async () => {
  const page = await armPage();
  page.notify("pose", { joints: { shoulder: 0.3 }, source: "track", track: "/joint_states" });
  const r = page.last();
  assert.equal(r.locked, true);
  assert.equal(r.following, "following /joint_states");
  assert.equal(r.joints.shoulder.disabled, true);
  sliderRow(page, "shoulder").querySelector(".value").click();
  assert.equal(sliderRow(page, "shoulder").querySelector("input.typed"), null, "no typing while locked");
  page.document.getElementById("resetPose").click();
  assert.equal(sentOf(page, "joints").length, 0, "no reset while locked");
  page.notify("pose", { joints: {}, source: "track" });
  assert.equal(page.last().following, "following the joint track");
  page.notify("pose", { joints: {}, source: "zero" });
  assert.equal(page.last().locked, false);
});

test("Reset pose sends every actuated joint at 0, clamped into its limits, as one message", async () => {
  const model = JSON.parse(JSON.stringify(ARM));
  model.joints.find((j) => j.name === "gripper_joint").limit.lower = 0.01;
  const page = bootPage();
  page.notify("model", { generation: 1, model, environments: [] });
  page.notify("pose", { joints: { shoulder: 1 }, source: "params" });
  page.document.getElementById("resetPose").click();
  const j = sentOf(page, "joints");
  assert.equal(j.length, 1);
  assert.deepEqual(j[0], { shoulder: 0, elbow: 0, wrist_1: 0, gripper_joint: 0.01 });
  page.document.getElementById("jointsReset").click();
  assert.equal(sentOf(page, "joints").length, 2, "the JOINTS section's Reset does the same");
});

test("the tree nests links under their joints with R/P/C/F markers; search keeps each match's ancestors", async () => {
  const page = await armPage();
  const order = page.last().tree_order;
  assert.deepEqual(order.slice(0, 5), ["link:base_link", "joint:shoulder", "link:upper_arm", "joint:elbow", "link:forearm"]);
  assert.equal(order.length, 15);
  assert.equal(rowEl(page, "joint:wrist_1").querySelector(".type").textContent, "C");
  assert.equal(rowEl(page, "joint:gripper_joint").querySelector(".type").textContent, "P");
  assert.equal(rowEl(page, "joint:tool_fix").querySelector(".type").textContent, "F");
  const search = page.document.getElementById("search");
  search.value = "finger_b";
  search.dispatchEvent({ type: "input" });
  const kept = page.last().tree_order;
  assert.ok(kept.includes("link:finger_b") && kept.includes("joint:finger_mimic") && kept.includes("link:base_link"));
  assert.ok(!kept.includes("link:finger_a") && !kept.includes("link:tool0"));
  assert.ok(rowEl(page, "link:finger_b").classList.contains("match"));
  search.value = "";
  search.dispatchEvent({ type: "input" });
  // a chevron folds a subtree
  rowEl(page, "link:gripper").querySelector(".chev").click();
  assert.ok(!page.last().tree_order.includes("link:tool0"));
});

test("selection both ways: tree rows and picks send `select`, outline the link; keys from outside select quietly", async () => {
  const page = await armPage();
  rowEl(page, "link:upper_arm").click();
  assert.deepEqual(sentOf(page, "select").at(-1), { link: "upper_arm", joint: null, add: false });
  assert.deepEqual(page.params.at(-1), { selected_links: ["upper_arm"] }, "kept in selected_links");
  const cyl = page.one("visual-shape:upper_arm:0");
  assert.equal(cyl.renderOverlay, true);
  assert.equal(cyl.renderOutline, true);
  assert.equal(page.one("visual-shape:base_link:0").renderOutline, false);
  assert.equal(page.last().footer.name, "upper_arm");
  assert.match(page.last().props.join("|"), /mass3.42 kg/);
  // Ctrl adds
  rowEl(page, "link:base_link").dispatchEvent({ type: "click", ctrlKey: true });
  assert.deepEqual(plain(page.last().selected_links), ["upper_arm", "base_link"]);
  assert.deepEqual(page.params.at(-1), { selected_links: ["upper_arm", "base_link"] });
  // a joint row outlines its child link and shows that joint's axis
  rowEl(page, "joint:elbow").click();
  assert.deepEqual(sentOf(page, "select").at(-1), { link: null, joint: "elbow", add: false });
  assert.deepEqual(plain(page.last().selected_links), ["forearm"]);
  assert.deepEqual(plain(page.last().overlays.axes), ["elbow"]);
  assert.match(page.last().props.join("|"), /typerevolute/);
  assert.deepEqual(page.params.at(-1), { selected_links: ["forearm"] }, "a joint keeps its child link");
  // the write coming back as params keeps the joint row's selection
  page.notify("params", { selected_links: ["forearm"] });
  assert.deepEqual(plain(page.last().selection), ["joint:elbow"]);
  // from outside: `<item key>#<link>`, no echo
  const before = page.sent.length;
  page.notify("port:selection", { keys: ["robots/arm.urdf#wrist"] });
  assert.deepEqual(plain(page.last().selected_links), ["wrist"]);
  assert.equal(page.sent.length, before);
  assert.ok(rowEl(page, "link:wrist").classList.contains("selected"));
  assert.equal(page.document.scrolledTo, rowEl(page, "link:wrist"));
  // a pick in the picture; a click on nothing clears
  const canvas = page.document.getElementById("c");
  const mesh = page.one("visual-shape:base_link:0");
  page.scene.pick = (x, y, predicate) => (predicate(mesh) ? { hit: true, pickedMesh: mesh, pickedPoint: new page.babylon.Vector3(0, 0, 0) } : null);
  canvas.dispatchEvent({ type: "pointerdown", pointerId: 1, button: 0, buttons: 1, clientX: 10, clientY: 10, offsetX: 10, offsetY: 10 });
  canvas.dispatchEvent({ type: "pointerup", pointerId: 1, button: 0, buttons: 0, clientX: 10, clientY: 10, offsetX: 10, offsetY: 10 });
  assert.deepEqual(sentOf(page, "select").at(-1), { link: "base_link", joint: null, add: false });
  page.scene.pick = () => null;
  canvas.dispatchEvent({ type: "pointerdown", pointerId: 1, button: 0, buttons: 1, clientX: 10, clientY: 10, offsetX: 10, offsetY: 10 });
  canvas.dispatchEvent({ type: "pointerup", pointerId: 1, button: 0, buttons: 0, clientX: 10, clientY: 10, offsetX: 10, offsetY: 10 });
  assert.deepEqual(sentOf(page, "select").at(-1), { link: null, joint: null, add: false });
  assert.deepEqual(plain(page.last().selection), []);
  assert.deepEqual(page.params.at(-1), { selected_links: [] });
});

test("a double-click frames the link and sends `frame`; the agent's `selected_links` selects", async () => {
  const page = await armPage({ selected_links: ["wrist"] });
  assert.deepEqual(plain(page.last().selected_links), ["wrist"], "kept across a reload");
  rowEl(page, "link:forearm").dispatchEvent({ type: "dblclick" });
  assert.deepEqual(sentOf(page, "frame"), [{ link: "forearm" }]);
  rowEl(page, "link:forearm").click();
  assert.ok(page.last().props.includes("visualpackage://arm_description/meshes/segment.stl ok"), "the mesh's URDF reference");
  const writes = page.params.length;
  page.notify("params", { selected_links: ["tool0"] });
  assert.deepEqual(plain(page.last().selected_links), ["tool0"]);
  assert.equal(page.params.length, writes, "the agent's selection is not written back");
  // `selected` is the shown robot's item key: not the page's
  page.notify("params", { selected: "robots/arm.urdf" });
  assert.deepEqual(plain(page.last().selected_links), ["tool0"]);
  assert.equal(page.document.getElementById("frameAll").title.startsWith("Frame"), true);
});

test("the footer: the robot, where the joints come from, the view mode pill and its menu", async () => {
  const page = await armPage();
  const r = page.last();
  assert.equal(r.footer.robot, "arm (8 links, 7 joints)");
  assert.equal(r.footer.source, "joints: zero");
  const pill = page.document.getElementById("viewMode");
  assert.equal(pill.textContent, "Lit");
  pill.click();
  assert.deepEqual(Object.keys(page.last().modes), ["lit", "unlit", "wireframe", "collision", "visual_collision"]);
  page.document.getElementById("viewModeMenu").querySelector("[data-mode=visual_collision]").click();
  assert.deepEqual(page.params.at(-1), { view_mode: "visual_collision" });
  assert.equal(pill.textContent, "Visual + collision");
  assert.ok(pill.classList.contains("on"));
  assert.equal(page.last().modes, null, "the menu closes");
});

test("unlit and wireframe change the visuals' materials and come back", async () => {
  const page = await armPage();
  const mat = page.one("visual-shape:base_link:0").material;
  page.notify("params", { view_mode: "unlit" });
  assert.equal(mat.unlit, true);
  page.notify("params", { view_mode: "wireframe" });
  assert.equal(mat.unlit, false);
  assert.equal(mat.wireframe, true);
  assert.equal(page.scene.clearColor.r, 0.11);
  page.notify("params", { view_mode: "lit" });
  assert.equal(mat.wireframe, false);
});

test("the VIEW panel: environment Default / HDRI, its sliders and background, the ground grid on by default", async () => {
  const page = await armPage();
  assert.equal(page.last().grid, true);
  assert.equal(page.named("grid-minor").length, 1);
  assert.equal(page.document.getElementById("showGrid").checked, true);
  const hdri = page.document.querySelector("[data-value=hdri]");
  hdri.click();
  assert.deepEqual(page.params.at(-1), { environment: "studio" });
  assert.equal(page.textures.length, 1);
  assert.equal(page.textures[0].url, "/vendor/hdri/studio.env");
  page.textures[0].load();
  assert.equal(page.scene.environmentTexture, page.textures[0].tex);
  assert.equal(page.last().environment.lights, false, "the lights go off once the HDRI lights the robot");
  const bg = page.document.getElementById("environmentBackground");
  bg.checked = true;
  bg.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params.at(-1), { environment_background: true });
  assert.equal(page.last().environment.background, true);
  page.document.querySelector("[data-value=default]").click();
  assert.deepEqual(page.params.at(-1), { environment: "none" });
  assert.equal(page.last().environment.lights, true);
  const grid = page.document.getElementById("showGrid");
  grid.checked = false;
  grid.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params.at(-1), { show_grid: false });
  assert.equal(page.named("grid-minor").length, 0);
});

test("overlays: link frames, joint axes on the moving joints, a centre of mass per link with an inertial", async () => {
  const page = await armPage({ show_frames: true, show_joint_axes: true, show_com: true });
  const r = page.last();
  assert.equal(r.overlays.frames, 8);
  assert.deepEqual(plain(r.overlays.axes).sort(), ["elbow", "finger_mimic", "gripper_joint", "shoulder", "wrist_1"]);
  assert.equal(r.overlays.com, 4);
  const frame = page.one("frame:upper_arm");
  assert.equal(frame.parent, page.one("link:upper_arm"));
  assert.equal(frame.builder.lines.length, 3);
  const com = page.one("com:upper_arm");
  assert.deepEqual(com.position.asArray(), [0, 0, 0.21]);
  // the axis arrow's tip points along the joint's axis (+Y for the shoulder)
  const tip = page.one("axis-tip:shoulder");
  assert.ok(near(q4(tip), [0, 0, 0, 1]));
  page.notify("params", { show_frames: false, show_joint_axes: false, show_com: false });
  assert.deepEqual(plain(page.last().overlays), { frames: 0, axes: [], com: 0, inertia: 0 });
  assert.equal(page.named("frame:upper_arm").length, 0);
});

test("the up axis switches among X, Y and Z, negatable, kept in params", async () => {
  const page = await armPage();
  page.document.querySelector("[data-value=y]").click();
  assert.deepEqual(page.params.at(-1), { up: "y" });
  assert.ok(near(q4(page.one("world")), [0, 0, 0, 1]));
  page.document.getElementById("upNegate").click();
  assert.deepEqual(page.params.at(-1), { up: "-y" });
  assert.equal(page.last().up, "-y");
  page.document.querySelector("[data-value=x]").click();
  assert.deepEqual(page.params.at(-1), { up: "-x" }, "the sign stays with a new axis");
});

test("camera: Free / Orbit kept in camera_mode and handed to the navigation; the camera goes out on its port", async () => {
  const page = await armPage();
  page.document.querySelector("[data-value=orbit]").click();
  assert.deepEqual(page.params.at(-1), { camera_mode: "orbit" });
  assert.equal(page.last().nav.camera_mode, "orbit");
  await new Promise((r) => setTimeout(r, 200));
  assert.ok(page.emitted.some((e) => e[0] === "camera"), "the first look goes out on the camera port");
  page.notify("port:camera", { position: [3, 3, 3], target: [0, 0.5, 0], fov_y: 0.7 });
  assert.deepEqual(plain(page.last().camera.position), [3, 3, 3]);
});

test("the side panels are kept in `panels`; params from the host restore them", async () => {
  const page = await armPage({ panels: { robot: { collapsed: true, width: 260 } } });
  assert.ok(page.document.getElementById("robot").classList.contains("collapsed"));
  assert.ok(!page.document.getElementById("view").classList.contains("collapsed"));
});

test("without WebGL the panels and the footer still show, the tree and sliders work, and health says why", () => {
  const page = bootPage({ noWebGL: true });
  assert.deepEqual(page.health.at(-1), ["error", "no 3D here: WebGL not supported"]);
  assert.ok(page.document.querySelector(".footer"), "the footer is mounted");
  page.notify("config", { params: {} });
  page.notify("model", { generation: 1, model: JSON.parse(JSON.stringify(ARM)), environments: [] });
  assert.equal(page.last().tree_order[0], "link:base_link");
  rowEl(page, "link:upper_arm").click();
  assert.deepEqual(sentOf(page, "select").at(-1), { link: "upper_arm", joint: null, add: false });
  page.document.getElementById("resetPose").click();
  assert.equal(sentOf(page, "joints").length, 1);
});

test("a new robot takes the place of the old: its nodes gone, unused mesh templates disposed", async () => {
  const page = await armPage();
  const oldBase = page.one("link:base_link");
  const template = page.one("mesh:forearm:0").cloneOf;
  const other = { name: "bot", root: "a", links: [{ name: "a", visuals: [], collisions: [], inertial: null }], joints: [], missing: [] };
  page.notify("model", { generation: 4, model: other, environments: [] });
  await settle();
  assert.equal(oldBase.disposed, true);
  assert.equal(template.disposed, true);
  assert.deepEqual(plain(page.last().robot), { name: "bot", root: "a", links: 1, joints: 0 });
  assert.deepEqual(page.last().tree_order, ["link:a"]);
});

test("inertia boxes: a translucent box of equivalent inertia per link with a mass, toggled in the VIEW panel", async () => {
  const page = await armPage();
  assert.equal(page.last().overlays.inertia, 0, "off by default");
  const box = page.document.getElementById("showInertia");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
  assert.deepEqual(page.params.at(-1), { show_inertia: true });
  assert.equal(page.last().overlays.inertia, 4);
  const b = page.one("inertia:upper_arm");
  assert.equal(b.parent, page.one("link:upper_arm"));
  assert.deepEqual(b.position.asArray(), [0, 0, 0.21]);
  // 3.42 kg, ixx = iyy = 0.05, izz = 0.006: the long side along the link's Z
  const sides = [b.builder.width, b.builder.height, b.builder.depth];
  const want = [Math.sqrt(6 / 3.42 * 0.006), Math.sqrt(6 / 3.42 * 0.006), Math.sqrt(6 / 3.42 * (0.1 - 0.006))];
  assert.ok(sides.slice().sort().every((x, i) => Math.abs(x - want.slice().sort()[i]) < 1e-6), String(sides));
  assert.ok(b.material.alpha < 0.5, "translucent");
  assert.equal(b.isPickable, false);
  page.notify("params", { show_inertia: false });
  assert.equal(page.last().overlays.inertia, 0);
  assert.equal(page.named("inertia:upper_arm").length, 0);
});
