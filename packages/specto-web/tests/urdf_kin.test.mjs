// The URDF view's kinematics and ROBOT-panel bookkeeping (web/urdf/kin.js, SPEC-43.2),
// renderer-free, against the hand-written model in fixtures/urdf/arm.json (the shape of
// backlog/docs/urdf-view-design.md section 11). Run by pytest through
// packages/specto-app/tests/test_urdf_page.py, or directly:
//     node --test packages/specto-web/tests/urdf_kin.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const KIN_JS = path.join(HERE, "..", "web", "urdf", "kin.js");
const ARM = JSON.parse(readFileSync(path.join(HERE, "fixtures", "urdf", "arm.json"), "utf-8"));

function load() {
  const sandbox = { Math, Object, Array, Number, String, isFinite };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(KIN_JS, "utf-8"), sandbox, { filename: KIN_JS });
  return sandbox.SpectoUrdfKin;
}
const K = load();
// the sandbox's objects come from another realm: compare them as plain JSON
const plain = (x) => JSON.parse(JSON.stringify(x));
const near = (a, b, eps = 1e-6) => {
  assert.equal(a.length, b.length);
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < eps, `[${a}] != [${b}] at ${i}`));
};
const joint = (name) => ARM.joints.find((j) => j.name === name);

test("rpy is URDF's fixed-axis roll, pitch, yaw", () => {
  // yaw 90 degrees turns +X onto +Y
  near(K.rotate(K.fromRpy([0, 0, Math.PI / 2]), [1, 0, 0]), [0, 1, 0]);
  // roll then pitch about the FIXED axes: roll 90 (Y onto Z), then pitch 90 about world Y
  // takes that Z onto X
  near(K.rotate(K.fromRpy([Math.PI / 2, Math.PI / 2, 0]), [0, 1, 0]), [1, 0, 0]);
});

test("forward kinematics: the zero pose stacks the joint origins", () => {
  const t = K.forward(ARM, {});
  near(t.base_link.p, [0, 0, 0]);
  near(t.upper_arm.p, [0, 0, 0.1]);
  near(t.forearm.p, [0, 0, 0.52]);
  near(t.wrist.p, [0, 0, 0.82]);
  near(t.gripper.p, [0, 0, 0.9]);
  near(t.tool0.p, [0, 0, 1.0]);
});

test("forward kinematics: a revolute joint turns its whole subtree about its axis", () => {
  // shoulder +90 degrees about +Y: the arm above it lies along +X
  const t = K.forward(ARM, { shoulder: Math.PI / 2 });
  near(t.forearm.p, [0.42, 0, 0.1]);
  near(t.tool0.p, [0.9, 0, 0.1]);
});

test("forward kinematics: a prismatic joint slides along its axis; its mimic follows", () => {
  const t = K.forward(ARM, { gripper_joint: 0.02 });
  // finger_a slides +X from its origin; finger_b's origin is turned 180 degrees about Z,
  // so the same +X motion takes it the other way
  near(t.finger_a.p, [0.03, 0, 0.94]);
  near(t.finger_b.p, [-0.03, 0, 0.94], 1e-5);
});

test("a mimic is multiplier * master + offset; chains are followed and cycles cut", () => {
  const model = {
    root: "a", links: [{ name: "a" }, { name: "b" }, { name: "c" }, { name: "d" }],
    joints: [
      { name: "m", type: "revolute", parent: "a", child: "b", limit: { lower: -3, upper: 3 }, mimic: null },
      { name: "s1", type: "revolute", parent: "b", child: "c", limit: { lower: -3, upper: 3 }, mimic: { joint: "m", multiplier: 2, offset: 0.1 } },
      { name: "s2", type: "revolute", parent: "c", child: "d", limit: { lower: -3, upper: 3 }, mimic: { joint: "s1", multiplier: -1, offset: 0 } },
    ],
  };
  const v = K.resolve(model, { m: 0.5 });
  near([v.m, v.s1, v.s2], [0.5, 1.1, -1.1]);
  const loop = {
    links: [{ name: "a" }, { name: "b" }, { name: "c" }],
    joints: [
      { name: "x", type: "revolute", parent: "a", child: "b", mimic: { joint: "y", multiplier: 1, offset: 0 } },
      { name: "y", type: "revolute", parent: "b", child: "c", mimic: { joint: "x", multiplier: 1, offset: 0 } },
    ],
  };
  const w = K.resolve(loop, {});
  assert.ok(isFinite(w.x) && isFinite(w.y));
});

test("sliders: degrees and millimetres in the UI, radians and metres out; limits and wrap", () => {
  const shoulder = joint("shoulder"), wrist = joint("wrist_1"), grip = joint("gripper_joint");
  const s = K.sliderSpec(shoulder);
  assert.equal(s.unit, "°");
  assert.ok(Math.abs(s.min + 140) < 0.01 && Math.abs(s.max - 140) < 0.01);
  assert.ok(Math.abs(K.fromDisplay(shoulder, 64) - 64 * Math.PI / 180) < 1e-9);
  assert.equal(K.displayText(shoulder, 64 * Math.PI / 180), "64.0°");
  // clamped into the URDF limits
  assert.ok(Math.abs(K.fromDisplay(shoulder, 500) - 2.4435) < 1e-9);
  // continuous: plus or minus 180, wrapped
  const c = K.sliderSpec(wrist);
  assert.ok(Math.abs(c.min + 180) < 1e-9 && Math.abs(c.max - 180) < 1e-9);
  assert.ok(Math.abs(K.fromDisplay(wrist, 190) - (-170 * Math.PI / 180)) < 1e-9);
  // prismatic in mm
  const p = K.sliderSpec(grip);
  assert.equal(p.unit, "mm");
  assert.equal(p.max, 40);
  assert.equal(K.fromDisplay(grip, 2), 0.002);
  assert.equal(K.displayText(grip, 0.002), "2.0 mm");
});

test("the zero pose clamps 0 into each joint's limits; only actuated joints get sliders", () => {
  const model = { links: [{ name: "a" }, { name: "b" }], joints: [{ name: "j", type: "revolute", parent: "a", child: "b", limit: { lower: 0.5, upper: 1 } }] };
  assert.deepEqual(plain(K.zeroPose(model)), { j: 0.5 });
  assert.deepEqual(plain(K.actuated(ARM).map((j) => j.name)), ["shoulder", "elbow", "wrist_1", "gripper_joint"]);
  assert.deepEqual(Object.keys(K.zeroPose(ARM)), ["shoulder", "elbow", "wrist_1", "gripper_joint"]);
});

test("the tree nests links under the joints that hold them, with type letters", () => {
  const t = K.tree(ARM);
  assert.equal(t.length, 1);
  assert.equal(t[0].name, "base_link");
  const shoulder = t[0].children[0];
  assert.equal(shoulder.kind, "joint");
  assert.equal(shoulder.name, "shoulder");
  assert.equal(K.typeLetter(shoulder.type), "R");
  assert.equal(shoulder.children[0].name, "upper_arm");
  const find = (nodes, k) => { for (const n of nodes) { if (K.key(n) === k) return n; const f = find(n.children, k); if (f) return f; } return null; };
  const gripper = find(t, "link:gripper");
  assert.equal(find(t, "joint:wrist_fix").children[0], gripper);
  assert.deepEqual(plain(gripper.children.map((j) => j.name)), ["gripper_joint", "finger_mimic", "tool_fix"]);
  assert.deepEqual(["revolute", "prismatic", "continuous", "fixed"].map(K.typeLetter), ["R", "P", "C", "F"]);
});

test("search keeps each match and its ancestors; an empty query keeps everything", () => {
  const t = K.tree(ARM);
  assert.equal(K.filter(t, ""), null);
  const keep = K.filter(t, "FINGER_B");
  assert.ok(keep["link:finger_b"]);
  assert.ok(keep["joint:finger_mimic"]);
  assert.ok(keep["link:gripper"]);
  assert.ok(keep["link:base_link"]);
  assert.ok(!keep["link:finger_a"]);
  assert.ok(!keep["joint:tool_fix"]);
  assert.deepEqual(plain(K.pathTo(t, "link:wrist")), [
    "link:base_link", "joint:shoulder", "link:upper_arm", "joint:elbow", "link:forearm", "joint:wrist_1", "link:wrist",
  ]);
});

test("the up axis puts the robot's up on Babylon's +Y, negatable", () => {
  for (const [up, v] of [["z", [0, 0, 1]], ["-z", [0, 0, -1]], ["x", [1, 0, 0]], ["-x", [-1, 0, 0]], ["y", [0, 1, 0]], ["-y", [0, -1, 0]]]) {
    near(K.rotate(K.upRotation(up), v), [0, 1, 0]);
  }
  assert.equal(K.upOf("bogus"), "z");
  assert.equal(K.upOf("-X"), "-x");
});

test("fromTo turns one direction onto another, opposite ones included", () => {
  for (const b of [[1, 0, 0], [0, 0, -1], [0, -1, 0], [0.3, 0.4, 0.5]]) {
    const l = Math.hypot(...b);
    near(K.rotate(K.fromTo([0, 1, 0], b), [0, 1, 0]), b.map((x) => x / l));
  }
});
