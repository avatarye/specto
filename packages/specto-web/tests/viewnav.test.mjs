// The shared Unreal Editor viewport controls (web/viewnav.js, #152) under node: which
// gesture moves the camera how, against a plain {position, target} camera and the fake
// DOM's elements. Run by pytest through packages/specto-app/tests/test_scene3d_page.py,
// or directly:
//     node --test packages/specto-web/tests/viewnav.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

import { pageDocument } from "./fakes/page_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NAV = path.join(HERE, "..", "web", "viewnav.js");
const WHEEL_JS = path.join(HERE, "..", "web", "wheel.js");

function loadNav() {
  const sandbox = { Math, Object, console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(WHEEL_JS, "utf-8"), sandbox, { filename: WHEEL_JS });
  vm.runInContext(readFileSync(NAV, "utf-8"), sandbox, { filename: NAV });
  return sandbox.SpectoViewNav;
}

const NavLib = loadNav();
const R = NavLib.rates;

/** A canvas with the controls attached to a camera at (0, 1, 10) looking down -Z at
 * (0, 1, 0); reach 10 and speed 1, so a unit length is 10 world units. */
function rig(opts = {}) {
  const doc = pageDocument('<canvas id="c"></canvas>');
  const el = doc.getElementById("c");
  const cam = { position: [0, 1, 10], target: [0, 1, 0] };
  const log = { clicks: [], frames: 0, speeds: [], pivots: [] };
  let speed = opts.speed || 1;
  const nav = NavLib.attach(el, {
    camera: () => ({ position: cam.position.slice(), target: cam.target.slice() }),
    setCamera(p, t) { cam.position = p; cam.target = t; },
    reach: () => 10,
    speed: () => speed,
    setSpeed(v, keep) { speed = v; log.speeds.push([v, keep]); },
    pivot(x, y) { log.pivots.push([x, y]); return opts.pivot === undefined ? null : opts.pivot; },
    frame() { log.frames += 1; },
    click(x, y) { log.clicks.push([x, y]); },
    changed() {},
    // orbit mode (SPEC-43.2) only when a test asks for it: the 3d view passes no mode
    mode: opts.mode ? () => opts.mode : undefined,
    anchor: opts.anchor ? () => opts.anchor : undefined,
  });
  const fire = (type, o = {}) => el.dispatchEvent({ type, pointerId: 1, ...o });
  const at = (x, y, buttons, extra = {}) => ({ clientX: x, clientY: y, offsetX: x, offsetY: y, buttons, ...extra });
  /** press `buttons` at (x0, y0), move to (x1, y1) in one step, let go */
  const drag = (buttons, x0, y0, x1, y1, extra = {}) => {
    fire("pointerdown", at(x0, y0, buttons, { button: buttons === 2 ? 2 : buttons === 4 ? 1 : 0, ...extra }));
    fire("pointermove", at(x1, y1, buttons, extra));
    fire("pointerup", at(x1, y1, 0, extra));
  };
  const key = (type, k, extra = {}) => el.dispatchEvent({ type, key: k, ...extra });
  return { el, cam, log, nav, fire, at, drag, key, speed: () => speed };
}

const close = (a, b, eps = 1e-9) => a.every((v, i) => Math.abs(v - b[i]) < eps);
const fwd = (c) => { const d = c.target.map((v, i) => v - c.position[i]); const l = Math.hypot(...d); return d.map((v) => v / l); };
const dist = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));

test("the mapping: buttons and Alt pick the mode", () => {
  const m = NavLib.modeOf;
  assert.equal(m(1, false), "walk");
  assert.equal(m(2, false), "look");
  assert.equal(m(4, false), "pan");
  assert.equal(m(3, false), "pan", "LMB+RMB pans");
  assert.equal(m(1, true), "orbit");
  assert.equal(m(2, true), "dolly");
  assert.equal(m(4, true), "pan");
  assert.equal(m(0, false), null);
});

test("an LMB click without a drag picks; past the slop it does not", () => {
  const r = rig();
  r.drag(1, 100, 80, 102, 81);
  assert.deepEqual(r.log.clicks, [[102, 81]]);
  assert.deepEqual(r.cam.position, [0, 1, 10], "a click does not move the camera");
  r.drag(1, 100, 80, 140, 80);
  assert.equal(r.log.clicks.length, 1);
  r.drag(2, 100, 80, 100, 80);
  r.drag(1, 100, 80, 100, 80, { altKey: true });
  assert.equal(r.log.clicks.length, 1, "only a plain left click picks");
});

test("LMB drag: x yaws (right turns right), y walks on the ground plane (up goes forward)", () => {
  const r = rig();
  r.fire("pointerdown", r.at(100, 100, 1, { button: 0 }));
  r.fire("pointermove", r.at(105, 100, 1));            // past the slop: the drag starts, this step too
  r.fire("pointermove", r.at(155, 100, 1));            // 55 px right in all
  const f = fwd(r.cam);
  assert.ok(f[0] > 0 && Math.abs(f[1]) < 1e-9, "turned right, level");
  assert.ok(Math.abs(Math.atan2(f[0], -f[2]) - 55 * R.ROTATE) < 1e-9);
  assert.ok(close(r.cam.position, [0, 1, 10]), "a yaw keeps the eye");
  r.fire("pointerup", r.at(155, 100, 0));

  const s = rig();
  s.cam.target = [0, -9, 0];                            // looking down at 45 degrees
  s.fire("pointerdown", s.at(100, 100, 1, { button: 0 }));
  s.fire("pointermove", s.at(100, 95, 1));
  s.fire("pointermove", s.at(100, 75, 1));             // 25 px up in all
  assert.ok(close(s.cam.position, [0, 1, 10 - 25 * R.WALK * 10]), "forward along the ground, height kept");
  assert.ok(close(fwd(s.cam), fwd({ position: [0, 1, 10], target: [0, -9, 0] })), "the view does not tilt");
});

test("RMB drag looks around from where the eye is; down looks down, held short of vertical", () => {
  const r = rig();
  r.drag(2, 100, 100, 100, 150);
  const f = fwd(r.cam);
  assert.ok(close(r.cam.position, [0, 1, 10]));
  assert.ok(Math.abs(Math.asin(f[1]) + 50 * R.ROTATE) < 1e-9, "pitched down");
  assert.ok(Math.abs(dist(r.cam.position, r.cam.target) - 10) < 1e-9, "the focus distance stays");
  r.drag(2, 100, 100, 100, 100000);
  assert.ok(Math.abs(Math.asin(fwd(r.cam)[1]) + R.PITCH_LIMIT) < 1e-9);
});

test("MMB and LMB+RMB pan: the camera moves with the mouse, the view does not turn", () => {
  const r = rig();
  r.drag(4, 100, 100, 150, 100);
  assert.ok(close(r.cam.position, [50 * R.PAN * 10, 1, 10]) && close(r.cam.target, [50 * R.PAN * 10, 1, 0]));
  r.drag(4, 100, 100, 100, 150);
  assert.ok(Math.abs(r.cam.position[1] - (1 - 50 * R.PAN * 10)) < 1e-9, "down moves it down");

  const c = rig();
  c.fire("pointerdown", c.at(100, 100, 1, { button: 0 }));
  c.fire("pointermove", c.at(100, 100, 3));             // RMB joins: a chord
  c.fire("pointermove", c.at(150, 100, 3));
  c.fire("pointerup", c.at(150, 100, 0));
  assert.ok(close(c.cam.position, [50 * R.PAN * 10, 1, 10]));
  assert.deepEqual(c.log.clicks, [], "a chord is never a click");
});

test("the wheel dollies along the view; with RMB held it sets the speed, kept on release", () => {
  const r = rig();
  r.el.dispatchEvent({ type: "wheel", deltaY: -100 });
  assert.ok(close(r.cam.position, [0, 1, 10 - R.WHEEL * 10]) && close(r.cam.target, [0, 1, -R.WHEEL * 10]));

  r.fire("pointerdown", r.at(100, 100, 2, { button: 2 }));
  r.el.dispatchEvent({ type: "wheel", deltaY: -100 });
  r.el.dispatchEvent({ type: "wheel", deltaY: -100 });
  assert.ok(Math.abs(r.speed() - Math.pow(2, 2 * R.SPEED_STEP)) < 1e-12);
  assert.ok(r.log.speeds.every((s) => !s[1]), "live while held");
  r.fire("pointerup", r.at(100, 100, 0));
  const kept = r.log.speeds[r.log.speeds.length - 1];
  assert.ok(kept[1] && Math.abs(kept[0] - Math.pow(2, 2 * R.SPEED_STEP)) < 1e-12);
  assert.ok(close(r.cam.position, [0, 1, 10 - R.WHEEL * 10]), "the held wheel did not dolly");
});

test("one wheel notch dollies and sets the speed alike at deltaY 60, 100 or 120 (#194)", () => {
  for (const dy of [60, 100, 120]) {
    const r = rig();
    r.el.dispatchEvent({ type: "wheel", deltaY: -dy, deltaMode: 0 });
    assert.ok(close(r.cam.position, [0, 1, 10 - R.WHEEL * 10]), `a notch of deltaY ${dy} dollies one WHEEL`);
    r.fire("pointerdown", r.at(100, 100, 2, { button: 2 }));
    r.el.dispatchEvent({ type: "wheel", deltaY: -dy, deltaMode: 0 });
    assert.ok(Math.abs(r.speed() - Math.pow(2, R.SPEED_STEP)) < 1e-12, `and one SPEED_STEP at ${dy}`);
    r.fire("pointerup", r.at(100, 100, 0));
  }
  // a touchpad's small deltas dolly smoothly, a fraction of a notch each
  const r = rig();
  r.el.dispatchEvent({ type: "wheel", deltaY: -10, deltaMode: 0 });
  assert.ok(close(r.cam.position, [0, 1, 10 - R.WHEEL * 10 * 0.1]));
});

test("WASD/QE fly only while RMB is held, scaled by the speed and Shift", () => {
  const r = rig();
  r.key("keydown", "w");
  assert.equal(r.nav.tick(0.1), false, "no RMB, no flight");
  r.fire("pointerdown", r.at(100, 100, 2, { button: 2 }));
  assert.equal(r.nav.tick(0.1), true);
  assert.ok(close(r.cam.position, [0, 1, 10 - R.FLY * 10 * 0.1]));
  r.key("keyup", "w");
  r.key("keydown", "e");
  r.key("keydown", "shift");
  r.nav.tick(0.1);
  assert.ok(Math.abs(r.cam.position[1] - (1 + R.FLY * 10 * 0.1 * R.SHIFT)) < 1e-9, "E goes up the world's Y, faster with Shift");
  r.fire("pointerup", r.at(100, 100, 0));
  assert.equal(r.nav.tick(0.1), false, "RMB let go: the flight stops");
});

test("Alt+LMB orbits around the pivot from the press; the eye keeps its distance to it", () => {
  const r = rig({ pivot: [0, 1, 0] });
  r.drag(1, 100, 100, 160, 130, { altKey: true });
  assert.deepEqual(r.log.pivots, [[100, 100]], "the pivot is asked once, at the press");
  assert.ok(Math.abs(dist(r.cam.position, [0, 1, 0]) - 10) < 1e-9);
  assert.ok(close(r.cam.target, [0, 1, 0]), "still looking at the pivot");
  assert.ok(r.cam.position[0] < 0 && r.cam.position[1] > 1, "right swings the eye left, down lifts it");

  // the pivot off the view axis: the view turns as a mouse look would, around the pivot
  const s = rig({ pivot: [3, 0, 0] });
  s.drag(1, 100, 100, 150, 100, { altKey: true });
  assert.ok(Math.abs(dist(s.cam.position, [3, 0, 0]) - dist([0, 1, 10], [3, 0, 0])) < 1e-9);
  const look = rig();
  look.drag(2, 100, 100, 150, 100);
  assert.ok(close(fwd(s.cam), fwd(look.cam)));

  // no pivot from the view: the focus point
  const t = rig();
  t.drag(1, 100, 100, 150, 100, { altKey: true });
  assert.ok(close(t.cam.target, [0, 1, 0]) && Math.abs(dist(t.cam.position, [0, 1, 0]) - 10) < 1e-9);
});

test("Alt+RMB dollies (right or up goes forward); Alt+MMB pans", () => {
  const r = rig();
  r.drag(2, 100, 100, 130, 100, { altKey: true });
  assert.ok(close(r.cam.position, [0, 1, 10 - 30 * R.DOLLY * 10]));
  r.drag(2, 100, 100, 100, 130, { altKey: true });
  assert.ok(close(r.cam.position, [0, 1, 10]));
  r.drag(4, 100, 100, 150, 100, { altKey: true });
  assert.ok(close(r.cam.position, [50 * R.PAN * 10, 1, 10]));
});

test("F frames; the right button's menu never opens; a lost focus drops the held keys", () => {
  const r = rig();
  r.key("keydown", "f");
  r.key("keydown", "F");
  assert.equal(r.log.frames, 2);
  const menu = { type: "contextmenu" };
  r.el.dispatchEvent(menu);
  assert.equal(menu.defaultPrevented, true);
  r.fire("pointerdown", r.at(100, 100, 2, { button: 2 }));
  r.key("keydown", "w");
  r.el.dispatchEvent({ type: "blur" });
  assert.equal(r.nav.tick(0.1), false);
  assert.deepEqual(JSON.parse(JSON.stringify(r.nav.state())), { mode: null, flying: false, keys: [] });
});

test("orbit mode: LMB orbits the pivot, RMB and the wheel close on it, Alt changes nothing", () => {
  const m = NavLib.modeOf;
  assert.equal(m(1, false, "orbit"), "orbit");
  assert.equal(m(1, true, "orbit"), "orbit", "Alt changes nothing");
  assert.equal(m(2, false, "orbit"), "zoom");
  assert.equal(m(4, false, "orbit"), "pan");
  assert.equal(m(3, false, "orbit"), "pan");

  // LMB: the eye swings around the pivot at its distance; the target moves to its depth
  const r = rig({ mode: "orbit", pivot: [0, 1, 0] });
  r.fire("pointerdown", r.at(100, 100, 1, { button: 0 }));
  r.fire("pointermove", r.at(105, 100, 1));
  r.fire("pointermove", r.at(100 + Math.round(Math.PI / 2 / R.ROTATE), 100, 1));
  r.fire("pointerup", r.at(0, 0, 0));
  assert.ok(Math.abs(dist(r.cam.position, [0, 1, 0]) - 10) < 1e-9, "the distance to the pivot stays");
  assert.ok(close(r.cam.target, [0, 1, 0], 1e-9), "the target sits at the pivot");
  assert.ok(r.log.pivots.length === 1, "the pivot is taken once, at the press");
  assert.equal(r.log.clicks.length, 0);
  // a plain click still picks
  r.drag(1, 50, 50, 50, 50);
  assert.equal(r.log.clicks.length, 1);

  // RMB: right or up closes on the pivot, never past MIN_RADIUS; nothing flies
  const z = rig({ mode: "orbit", pivot: [0, 1, 0] });
  z.drag(2, 100, 100, 150, 100);
  assert.ok(close(z.cam.position, [0, 1, 10 - 50 * R.DOLLY * 10]));
  z.fire("pointerdown", z.at(100, 100, 2, { button: 2 }));
  z.key("keydown", "w");
  assert.equal(z.nav.tick(0.1), false, "RMB flies nothing in orbit mode");
  z.fire("pointerup", z.at(100, 100, 0));
  z.key("keyup", "w");
  z.drag(2, 100, 100, 100000, 100);
  assert.ok(Math.abs(dist(z.cam.position, [0, 1, 0]) - R.MIN_RADIUS) < 1e-9);

  // the wheel closes on the anchor (the selection) without a press, and never sets the speed
  const w = rig({ mode: "orbit", anchor: [0, 1, 4] });
  w.fire("pointerdown", w.at(100, 100, 2, { button: 2 }));
  w.el.dispatchEvent({ type: "wheel", deltaY: -120, deltaMode: 0 });
  w.fire("pointerup", w.at(100, 100, 0));
  assert.equal(w.log.speeds.length, 0);
  const before = dist(w.cam.position, [0, 1, 4]);
  w.el.dispatchEvent({ type: "wheel", deltaY: -120, deltaMode: 0 });
  assert.ok(dist(w.cam.position, [0, 1, 4]) < before, "away closes on the anchor");
  assert.deepEqual(JSON.parse(JSON.stringify(w.nav.state())).camera_mode, "orbit");
});
