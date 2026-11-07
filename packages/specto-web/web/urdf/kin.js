/* URDF view: the robot's kinematics and the ROBOT panel's bookkeeping, renderer-free
 * (SPEC-43.2; backlog/docs/urdf-view-design.md section 11 is the model's shape). The page
 * (urdf/main.js) turns these numbers into Babylon TransformNodes; the node tests check
 * them directly.
 *
 * Frames are URDF's: right-handed, metres and radians; quaternions are [x, y, z, w].
 * A joint's child link sits at parent * origin(xyz, rpy) * motion(q), where the motion
 * turns about the joint's unit axis (revolute, continuous) or slides along it
 * (prismatic); fixed, floating and planar joints have no motion here (no slider: the
 * properties say why). rpy is URDF's fixed-axis roll, pitch, yaw: Rz(y) Ry(p) Rx(r).
 * A mimic joint's value is multiplier * master + offset, chains followed, cycles cut.
 *
 * Joint values in the UI: degrees for revolute and continuous joints, millimetres for
 * prismatic ones; params and messages keep radians and metres. Revolute and prismatic
 * joints clamp into their limits; continuous joints wrap into [-180, 180) degrees. */
(function (root) {
  "use strict";
  const DEG = 180 / Math.PI;
  const MOVING = { revolute: true, continuous: true, prismatic: true };
  const LETTER = { revolute: "R", continuous: "C", prismatic: "P", fixed: "F", floating: "FL", planar: "PL" };
  const NO_SLIDER = {
    fixed: "a fixed joint does not move",
    floating: "a floating joint has six degrees of freedom: no slider in this view",
    planar: "a planar joint moves in a plane: no slider in this view",
  };
  const FALLBACK = { revolute: [-Math.PI, Math.PI], prismatic: [-1, 1] };   // a joint without limits

  /* ---- quaternions and vectors --------------------------------------------------------- */
  function qmul(a, b) {
    return [
      a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
      a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
      a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
      a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
    ];
  }
  function axisAngle(axis, angle) {
    const l = Math.hypot(axis[0], axis[1], axis[2]) || 1;
    const s = Math.sin(angle / 2) / l;
    return [axis[0] * s, axis[1] * s, axis[2] * s, Math.cos(angle / 2)];
  }
  /** URDF rpy: roll about X, then pitch about Y, then yaw about Z, all fixed axes. */
  function fromRpy(rpy) {
    const r = rpy || [0, 0, 0];
    return qmul(qmul(axisAngle([0, 0, 1], r[2] || 0), axisAngle([0, 1, 0], r[1] || 0)), axisAngle([1, 0, 0], r[0] || 0));
  }
  function rotate(q, v) {
    const u = [q[0], q[1], q[2]], w = q[3];
    const c1 = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const c2 = [u[1] * c1[2] - u[2] * c1[1], u[2] * c1[0] - u[0] * c1[2], u[0] * c1[1] - u[1] * c1[0]];
    return [v[0] + 2 * (w * c1[0] + c2[0]), v[1] + 2 * (w * c1[1] + c2[1]), v[2] + 2 * (w * c1[2] + c2[2])];
  }
  /** The shortest turn taking unit-ish vector a onto b (an arrow's +Y onto a joint axis). */
  function fromTo(a, b) {
    const la = Math.hypot(a[0], a[1], a[2]) || 1, lb = Math.hypot(b[0], b[1], b[2]) || 1;
    const u = [a[0] / la, a[1] / la, a[2] / la], v = [b[0] / lb, b[1] / lb, b[2] / lb];
    const d = u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
    if (d < -1 + 1e-9) {
      // opposite: half a turn about any axis square to a
      const side = Math.abs(u[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
      const c = [u[1] * side[2] - u[2] * side[1], u[2] * side[0] - u[0] * side[2], u[0] * side[1] - u[1] * side[0]];
      return axisAngle(c, Math.PI);
    }
    const c = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const q = [c[0], c[1], c[2], 1 + d];
    const l = Math.hypot(q[0], q[1], q[2], q[3]);
    return [q[0] / l, q[1] / l, q[2] / l, q[3] / l];
  }
  /** a * b for rigid transforms {p, q}. */
  function compose(a, b) {
    const r = rotate(a.q, b.p);
    return { p: [a.p[0] + r[0], a.p[1] + r[1], a.p[2] + r[2]], q: qmul(a.q, b.q) };
  }
  const IDENTITY = { p: [0, 0, 0], q: [0, 0, 0, 1] };
  function originOf(o) {
    return { p: (o && o.xyz ? o.xyz : [0, 0, 0]).slice(), q: fromRpy(o && o.rpy) };
  }

  /** The rotation that puts the robot's `up` ("x", "-y", "z", ...) on Babylon's +Y. */
  function upRotation(up) {
    const u = String(up || "z").toLowerCase();
    const h = Math.PI / 2;
    switch (u) {
      case "y": return [0, 0, 0, 1];
      case "-y": return axisAngle([1, 0, 0], Math.PI);
      case "x": return axisAngle([0, 0, 1], h);
      case "-x": return axisAngle([0, 0, 1], -h);
      case "-z": return axisAngle([1, 0, 0], h);
      default: return axisAngle([1, 0, 0], -h);   // z: ROS's REP 103
    }
  }
  const UPS = ["x", "y", "z", "-x", "-y", "-z"];
  function upOf(up) { const u = String(up || "").toLowerCase(); return UPS.indexOf(u) >= 0 ? u : "z"; }

  /* ---- joints --------------------------------------------------------------------------- */
  function moving(j) { return !!(j && MOVING[j.type]); }
  /** The joints with a slider: moving and not a mimic. */
  function actuated(model) { return (model.joints || []).filter(function (j) { return moving(j) && !j.mimic; }); }
  function limits(j) {
    if (j.type === "continuous") return [-Math.PI, Math.PI];
    const l = j.limit;
    const f = FALLBACK[j.type] || [0, 0];
    if (!l || !isFinite(l.lower) || !isFinite(l.upper) || l.upper < l.lower) return f.slice();
    return [l.lower, l.upper];
  }
  /** v kept inside what the joint can do: clamped, or wrapped for a continuous joint. */
  function clampJoint(j, v) {
    v = Number(v);
    if (!isFinite(v)) v = 0;
    if (j.type === "continuous") return wrapAngle(v);
    const lim = limits(j);
    return Math.max(lim[0], Math.min(lim[1], v));
  }
  function wrapAngle(v) { return ((v + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI; }

  /** Every actuated joint at 0, clamped into its limits (Reset pose). */
  function zeroPose(model) {
    const out = {};
    actuated(model).forEach(function (j) { out[j.name] = clampJoint(j, 0); });
    return out;
  }

  /** The value of every moving joint: the given actuated values (missing ones 0, kept in
   * their limits) and each mimic from its master. */
  function resolve(model, values) {
    const byName = {};
    (model.joints || []).forEach(function (j) { byName[j.name] = j; });
    const out = {};
    const v = values || {};
    actuated(model).forEach(function (j) {
      out[j.name] = j.name in v ? clampJoint(j, v[j.name]) : clampJoint(j, 0);
    });
    function mimicValue(j, seen) {
      if (j.name in out) return out[j.name];
      if (seen[j.name]) return 0;   // a cycle: cut
      seen[j.name] = true;
      const m = j.mimic;
      const master = byName[m.joint];
      const mv = master ? (master.mimic ? mimicValue(master, seen) : (master.name in out ? out[master.name] : 0)) : 0;
      const mult = isFinite(m.multiplier) ? m.multiplier : 1;
      const off = isFinite(m.offset) ? m.offset : 0;
      return mv * mult + off;
    }
    (model.joints || []).forEach(function (j) {
      if (moving(j) && j.mimic) out[j.name] = mimicValue(j, {});
    });
    return out;
  }

  /** A joint's motion at value q, as a transform in its own (origin) frame. */
  function motion(j, q) {
    const axis = j.axis && j.axis.length === 3 ? j.axis : [1, 0, 0];
    if (j.type === "revolute" || j.type === "continuous") return { p: [0, 0, 0], q: axisAngle(axis, q || 0) };
    if (j.type === "prismatic") {
      const l = Math.hypot(axis[0], axis[1], axis[2]) || 1;
      const d = (q || 0) / l;
      return { p: [axis[0] * d, axis[1] * d, axis[2] * d], q: [0, 0, 0, 1] };
    }
    return { p: [0, 0, 0], q: [0, 0, 0, 1] };
  }

  /** Each link's transform in the robot's frame at the resolved joint values. */
  function forward(model, values) {
    const v = resolve(model, values);
    const out = {};
    const children = childJoints(model);
    const walk = function (link, t) {
      if (out[link]) return;   // a link reached twice (a malformed model): first wins
      out[link] = t;
      (children[link] || []).forEach(function (j) {
        walk(j.child, compose(compose(t, originOf(j.origin)), motion(j, v[j.name])));
      });
    };
    walk(rootOf(model), IDENTITY);
    (model.links || []).forEach(function (l) { if (!out[l.name]) walk(l.name, IDENTITY); });
    return out;
  }

  function childJoints(model) {
    const c = {};
    (model.joints || []).forEach(function (j) { (c[j.parent] = c[j.parent] || []).push(j); });
    return c;
  }
  function rootOf(model) {
    if (model.root) return model.root;
    const kids = {};
    (model.joints || []).forEach(function (j) { kids[j.child] = true; });
    const r = (model.links || []).find(function (l) { return !kids[l.name]; });
    return r ? r.name : (model.links && model.links[0] ? model.links[0].name : "");
  }

  /* ---- UI units ------------------------------------------------------------------------- */
  /** The slider's units and range for a joint: degrees or millimetres. */
  function sliderSpec(j) {
    const lim = limits(j);
    if (j.type === "prismatic") return { unit: "mm", min: lim[0] * 1000, max: lim[1] * 1000, step: 0.1, scale: 1000 };
    return { unit: "°", min: lim[0] * DEG, max: lim[1] * DEG, step: 0.1, scale: DEG };
  }
  function toDisplay(j, v) { return Number(v || 0) * sliderSpec(j).scale; }
  function fromDisplay(j, d) { return clampJoint(j, Number(d) / sliderSpec(j).scale); }
  function displayText(j, v) {
    const s = sliderSpec(j);
    const d = toDisplay(j, v);
    return (Math.abs(d) < 0.05 ? 0 : d).toFixed(1) + (s.unit === "mm" ? " mm" : "°");
  }
  function typeLetter(type) { return LETTER[type] || "?"; }

  /* ---- the ROBOT tree --------------------------------------------------------------------- */
  /** Links nested under the joints that hold them (RViz's link tree): a node is
   * {kind: "link"|"joint", name, type?, children}. Links no joint reaches come last, at the
   * top level. */
  function tree(model) {
    const children = childJoints(model);
    const seen = {};
    const linkNode = function (name) {
      seen[name] = true;
      return {
        kind: "link", name: name,
        children: (children[name] || []).filter(function (j) { return !seen[j.child]; }).map(function (j) {
          return { kind: "joint", name: j.name, type: j.type, children: seen[j.child] ? [] : [linkNode(j.child)] };
        }),
      };
    };
    const out = [];
    const r = rootOf(model);
    if (r) out.push(linkNode(r));
    (model.links || []).forEach(function (l) { if (!seen[l.name]) out.push(linkNode(l.name)); });
    return out;
  }
  function key(node) { return node.kind + ":" + node.name; }
  /** The keys a search keeps: each node whose name contains the query (any case) and
   * every ancestor of one; null (everything) for an empty query. */
  function filter(nodes, query) {
    const q = String(query || "").trim().toLowerCase();
    if (!q) return null;
    const keep = {};
    const walk = function (n, path) {
      const here = path.concat([key(n)]);
      if (n.name.toLowerCase().indexOf(q) >= 0) here.forEach(function (k) { keep[k] = true; });
      n.children.forEach(function (c) { walk(c, here); });
    };
    nodes.forEach(function (n) { walk(n, []); });
    return keep;
  }
  /** The tree keys from the top down to a node (to open and scroll to it). */
  function pathTo(nodes, k) {
    let found = null;
    const walk = function (n, path) {
      if (found) return;
      const here = path.concat([key(n)]);
      if (key(n) === k) { found = here; return; }
      n.children.forEach(function (c) { walk(c, here); });
    };
    nodes.forEach(function (n) { walk(n, []); });
    return found || [];
  }

  const api = {
    qmul: qmul, axisAngle: axisAngle, fromRpy: fromRpy, fromTo: fromTo, rotate: rotate, compose: compose, originOf: originOf,
    upRotation: upRotation, upOf: upOf, UPS: UPS,
    moving: moving, actuated: actuated, limits: limits, clampJoint: clampJoint, wrapAngle: wrapAngle,
    zeroPose: zeroPose, resolve: resolve, motion: motion, forward: forward, rootOf: rootOf,
    sliderSpec: sliderSpec, toDisplay: toDisplay, fromDisplay: fromDisplay, displayText: displayText,
    typeLetter: typeLetter, NO_SLIDER: NO_SLIDER,
    tree: tree, key: key, filter: filter, pathTo: pathTo,
  };
  root.SpectoUrdfKin = api;
})(typeof window !== "undefined" ? window : globalThis);
