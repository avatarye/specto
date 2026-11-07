/* Unreal Editor's perspective-viewport controls for the web 3D views (#152), one scheme
 * for every view that shows a 3D world; the usd view mirrors it in Python
 * (specto_app/usd/nav.py, #151), so keep this table and its rates in step with it. The
 * usd view's orbit mode (#39) is mirrored too, opt-in through `mode()` (the urdf view's
 * `camera_mode`, SPEC-43.2); a view without `mode` is always free.
 *
 * Renderer-free: the view hands over its camera as {position, target} (the target is the
 * focus point straight ahead) in a right-handed, Y-up world (Babylon's frame; z-up data
 * is turned at the view's root) and takes new ones back through `setCamera`. Every
 * distance is `reach() * speed()` (the scene's radius times the view's camera speed)
 * times the rate below; turns are in radians. dx > 0 is the mouse going right, dy > 0
 * going down.
 *
 *   gesture                    does                                         rate
 *   LMB drag                   x: yaw (right turns right); y: forward / back  ROTATE rad/px,
 *                              on the ground plane (up goes forward)          WALK /px
 *   LMB click                  click(x, y): the view picks (moved <= CLICK_SLOP px, no
 *                              other button, no Alt); the drag starts past the slop
 *   RMB drag                   mouse look: yaw and pitch, the eye stays       ROTATE rad/px
 *                              (down looks down; pitch stops PITCH_LIMIT short of vertical)
 *   RMB held + W/S A/D Q/E     fly: forward / back along the view, left /     FLY /s, x SHIFT
 *                              right, down / up the world's Y                 with Shift
 *   RMB held + wheel           camera speed: away faster, a notch is          SPEED_STEP
 *                              SPEED_STEP in log2, clamped to 2^SPEED_MIN..2^SPEED_MAX;
 *                              live, kept once (setSpeed(v, true)) when RMB lets go
 *   MMB drag, LMB+RMB drag     pan: the camera moves with the mouse (truck,   PAN /px
 *                              pedestal); eye and target together
 *   wheel                      dolly forward (away) / back                    WHEEL /notch
 *   Alt+LMB drag               orbit around the pivot taken at the press:     ROTATE rad/px
 *                              pivot(x, y) (the selection, else the point
 *                              under the cursor), else the target; the turn
 *                              is the mouse look's, eye and target swing with it
 *   Alt+RMB drag               dolly: right or up goes forward (dx - dy)      DOLLY /px
 *   Alt+MMB drag               pan, as MMB                                    PAN /px
 *   F                          frame(): the selection, else everything
 *
 * Orbit mode (`mode()` returns "orbit"; nav.py's table): the same rates, Alt changes
 * nothing, a click still picks, RMB flies nothing and the wheel never sets the speed.
 *
 *   gesture                    does                                         rate
 *   LMB drag                   orbit the pivot taken at the press: pivot(x,   ROTATE rad/px
 *                              y), else anchor(), else the target; the pivot
 *                              keeps its place on screen, the target moves to
 *                              its depth
 *   MMB drag, LMB+RMB drag     pan, as above                                  PAN /px
 *   RMB drag                   dolly toward the pivot (right or up: closer),  DOLLY /px
 *                              never nearer than MIN_RADIUS
 *   wheel                      dolly toward anchor(), else the target (away:  WHEEL /notch
 *                              closer), the same stop
 *
 * A notch is one wheel notch on any display scale (wheel.js, #194); a touchpad's deltas
 * are fractions of one.
 * The mode follows the buttons held (a second button joins with a pointermove, as
 * Pointer Events chord), Alt as it was at the first press.
 *
 *   const nav = SpectoViewNav.attach(canvas, {
 *     camera() -> {position, target}, setCamera(position, target), reach() -> number,
 *     speed() -> number, setSpeed(v, keep), pivot(x, y) -> [x,y,z] | null,
 *     frame(), click(x, y), changed(),
 *     mode?() -> "free" | "orbit", anchor?() -> [x,y,z] | null });
 *   nav.tick(dt)   // each frame: flies while RMB is held; true when the camera moved
 *   nav.state()    // {mode, flying, keys, camera_mode (with `mode`)} for the view's report */
(function () {
  "use strict";
  const ROTATE = 0.004;      // rad per px: yaw, pitch, orbit
  const WALK = 0.01;         // reach per px: LMB forward / back
  const PAN = 0.004;         // reach per px
  const DOLLY = 0.01;        // reach per px: Alt+RMB
  const WHEEL = 0.1;         // reach per notch
  const FLY = 0.5;           // reach per second
  const SHIFT = 4;           // Shift flies this much faster
  const SPEED_STEP = 0.25;   // log2 of the camera speed per notch with RMB held
  const SPEED_MIN = -3, SPEED_MAX = 3;
  const PITCH_LIMIT = Math.PI / 2 - 0.01;
  const MIN_RADIUS = 0.01;   // orbit mode: the eye stops this far from the pivot (nav.py's LOWER_RADIUS_LIMIT)
  const CLICK_SLOP = 4;      // px: a press that moves no further is a click
  const FLY_KEYS = "wasdqe";
  const UP = [0, 1, 0];
  const LEFT = 1, RIGHT = 2, MIDDLE = 4;   // PointerEvent.buttons bits

  /* ---- vectors (plain arrays) and the camera moves ------------------------------------ */
  const add = function (a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; };
  const sub = function (a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; };
  const scale = function (a, k) { return [a[0] * k, a[1] * k, a[2] * k]; };
  const dot = function (a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; };
  const cross = function (a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; };
  const len = function (a) { return Math.hypot(a[0], a[1], a[2]); };
  const unit = function (a) { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  /** v turned by angle (radians, right-handed) about the unit axis k (Rodrigues). */
  function rotate(v, k, angle) {
    const c = Math.cos(angle), s = Math.sin(angle);
    return add(add(scale(v, c), scale(cross(k, v), s)), scale(k, dot(k, v) * (1 - c)));
  }

  function forward(cam) { return unit(sub(cam.target, cam.position)); }
  /** The camera's right: forward x up (the pitch limit keeps forward off the vertical). */
  function rightOf(f) {
    const r = cross(f, UP);
    return len(r) < 1e-9 ? [1, 0, 0] : unit(r);
  }
  function focus(cam) { return len(sub(cam.target, cam.position)) || 1; }

  /** The turn a mouse look of (dx, dy) px makes, as a function of a vector: yaw about the
   * world's up, then pitch about the yawed right, held PITCH_LIMIT short of vertical. */
  function turn(cam, dx, dy) {
    const yaw = -dx * ROTATE;
    const f = rotate(forward(cam), UP, yaw);
    const pitch = Math.asin(Math.max(-1, Math.min(1, f[1])));
    const next = Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, pitch - dy * ROTATE));
    const axis = rightOf(f);
    return function (v) { return rotate(rotate(v, UP, yaw), axis, next - pitch); };
  }

  const moves = {
    /** RMB: the eye stays, the view turns. */
    look: function (cam, dx, dy) {
      const r = turn(cam, dx, dy);
      return { position: cam.position, target: add(cam.position, scale(r(forward(cam)), focus(cam))) };
    },
    /** LMB: yaw, and forward / back along the view's heading on the ground plane. */
    walk: function (cam, dx, dy, unitLen) {
      const turned = moves.look(cam, dx, 0);
      const f = forward(turned);
      let ground = [f[0], 0, f[2]];
      if (len(ground) < 1e-6) ground = cross(UP, rightOf(f));   // straight down: the heading
      const step = scale(unit(ground), -dy * WALK * unitLen);
      return { position: add(turned.position, step), target: add(turned.target, step) };
    },
    /** MMB, LMB+RMB, Alt+MMB: the camera moves with the mouse. */
    pan: function (cam, dx, dy, unitLen) {
      const f = forward(cam), r = rightOf(f), u = cross(r, f);
      const step = add(scale(r, dx * PAN * unitLen), scale(u, -dy * PAN * unitLen));
      return { position: add(cam.position, step), target: add(cam.target, step) };
    },
    /** Wheel and Alt+RMB: along the view, eye and target together. */
    dolly: function (cam, distance) {
      const step = scale(forward(cam), distance);
      return { position: add(cam.position, step), target: add(cam.target, step) };
    },
    /** Alt+LMB: the mouse look's turn, swung around the pivot. */
    orbit: function (cam, dx, dy, pivot) {
      const r = turn(cam, dx, dy);
      return { position: add(pivot, r(sub(cam.position, pivot))), target: add(pivot, r(sub(cam.target, pivot))) };
    },
    /** Orbit mode's LMB: the orbit, with the target moved to the pivot's depth. */
    orbitAround: function (cam, dx, dy, pivot) {
      const next = moves.orbit(cam, dx, dy, pivot);
      return { position: next.position, target: toDepth(next, pivot) };
    },
    /** Orbit mode's RMB and wheel: the eye `step` closer to the pivot (back when negative),
     * never nearer than MIN_RADIUS; the view keeps its direction. */
    zoom: function (cam, step, pivot) {
      const rel = sub(pivot, cam.position);
      const n = len(rel);
      const toward = n >= 1e-12 ? scale(rel, 1 / n) : forward(cam);
      const position = sub(pivot, scale(toward, Math.max(MIN_RADIUS, n - step)));
      return { position: position, target: toDepth({ position: position, target: add(position, forward(cam)) }, pivot) };
    },
    /** RMB held: fly by the keys held for dt seconds. */
    fly: function (cam, keys, dt, unitLen) {
      const f = forward(cam), r = rightOf(f);
      let m = [0, 0, 0];
      if (keys.w) m = add(m, f);
      if (keys.s) m = sub(m, f);
      if (keys.d) m = add(m, r);
      if (keys.a) m = sub(m, r);
      if (keys.e) m = add(m, UP);
      if (keys.q) m = sub(m, UP);
      const step = scale(m, FLY * unitLen * dt * (keys.shift ? SHIFT : 1));
      return { position: add(cam.position, step), target: add(cam.target, step) };
    },
  };

  /** The target straight ahead at the pivot's depth (orbit mode keeps it there). */
  function toDepth(cam, pivot) {
    const f = forward(cam);
    return add(cam.position, scale(f, Math.max(MIN_RADIUS, dot(sub(pivot, cam.position), f))));
  }

  /** What the held buttons (and Alt at the first press) do; `camMode` "orbit" is orbit
   * mode's table (LMB orbits, RMB zooms toward the pivot, Alt changes nothing). */
  function modeOf(buttons, alt, camMode) {
    if (camMode === "orbit") {
      if ((buttons & MIDDLE) || ((buttons & LEFT) && (buttons & RIGHT))) return "pan";
      if (buttons & LEFT) return "orbit";
      if (buttons & RIGHT) return "zoom";
      return null;
    }
    if (alt) {
      if (buttons & MIDDLE) return "pan";
      if (buttons & LEFT) return "orbit";
      if (buttons & RIGHT) return "dolly";
      return null;
    }
    if ((buttons & MIDDLE) || ((buttons & LEFT) && (buttons & RIGHT))) return "pan";
    if (buttons & RIGHT) return "look";
    if (buttons & LEFT) return "walk";
    return null;
  }

  function clampSpeed(v) { return Math.max(Math.pow(2, SPEED_MIN), Math.min(Math.pow(2, SPEED_MAX), v)); }

  /* ---- the input binding --------------------------------------------------------------- */
  function attach(el, o) {
    const keys = {};
    let drag = null;   // {buttons, alt, x, y, sx, sy, chord, armed, pivot, speedChanged}

    function apply(next) { o.setCamera(next.position, next.target); o.changed(); }
    function unitLen() { return (o.reach() || 1) * (o.speed() || 1); }
    function pos(ev) {
      return [ev.offsetX !== undefined ? ev.offsetX : ev.clientX, ev.offsetY !== undefined ? ev.offsetY : ev.clientY];
    }
    function camMode() { return o.mode && o.mode() === "orbit" ? "orbit" : "free"; }
    function gesture() { return drag ? modeOf(drag.buttons, drag.alt, camMode()) : null; }
    function looking() { return gesture() === "look"; }
    function pivoting() { const g = gesture(); return g === "orbit" || g === "zoom"; }
    /** what orbit mode turns around without a press-time pivot */
    function standing(cam) { const a = o.anchor ? o.anchor() : null; return a || cam.target; }

    function end(ev) {
      const d = drag;
      drag = null;
      if (!d) return;
      if (ev && el.releasePointerCapture && ev.pointerId !== undefined) el.releasePointerCapture(ev.pointerId);
      if (d.speedChanged) o.setSpeed(o.speed(), true);
      if (ev && d.first === LEFT && !d.chord && !d.alt && !d.armed) {
        const p = pos(ev);
        o.click(p[0], p[1]);
      }
      o.changed();
    }

    el.addEventListener("pointerdown", function (ev) {
      const buttons = ev.buttons || ({ 0: LEFT, 1: MIDDLE, 2: RIGHT }[ev.button] || 0);
      if (drag) { drag.buttons = buttons; drag.chord = true; return; }
      if (el.focus) el.focus();
      if (el.setPointerCapture && ev.pointerId !== undefined) el.setPointerCapture(ev.pointerId);
      const p = pos(ev);
      drag = {
        buttons: buttons, first: buttons, alt: !!ev.altKey, x: ev.clientX, y: ev.clientY,
        sx: ev.clientX, sy: ev.clientY, chord: false, armed: false, pivot: null, speedChanged: false,
      };
      if (pivoting()) drag.pivot = o.pivot(p[0], p[1]);
      if (ev.preventDefault) ev.preventDefault();
      o.changed();
    });

    el.addEventListener("pointermove", function (ev) {
      if (!drag) return;
      if (ev.buttons !== undefined && ev.buttons !== drag.buttons) {
        // a chord: a button joined or left mid-drag
        if (!ev.buttons) { end(ev); return; }
        if (ev.buttons & ~drag.first) drag.chord = true;
        drag.buttons = ev.buttons;
        if (pivoting() && !drag.pivot) {
          const p = pos(ev);
          drag.pivot = o.pivot(p[0], p[1]);
        }
      }
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      drag.x = ev.clientX; drag.y = ev.clientY;
      // a left press is a click until it has moved past the slop; then the drag starts
      if (!drag.armed) {
        if (Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy) <= CLICK_SLOP && drag.first === LEFT && !drag.chord && !drag.alt) return;
        drag.armed = true;
      }
      const cam = o.camera();
      const mode = gesture();
      const orbiting = camMode() === "orbit";
      if (mode === "walk") apply(moves.walk(cam, dx, dy, unitLen()));
      else if (mode === "look") apply(moves.look(cam, dx, dy));
      else if (mode === "pan") apply(moves.pan(cam, dx, dy, unitLen()));
      else if (mode === "orbit" && orbiting) apply(moves.orbitAround(cam, dx, dy, drag.pivot || standing(cam)));
      else if (mode === "orbit") apply(moves.orbit(cam, dx, dy, drag.pivot || cam.target));
      else if (mode === "zoom") apply(moves.zoom(cam, (dx - dy) * DOLLY * unitLen(), drag.pivot || standing(cam)));
      else if (mode === "dolly") apply(moves.dolly(cam, (dx - dy) * DOLLY * unitLen()));
    });

    el.addEventListener("pointerup", function (ev) {
      if (!drag) return;
      if (ev.buttons) { drag.buttons = ev.buttons; drag.chord = true; return; }
      end(ev);
    });
    el.addEventListener("pointercancel", function () { end(null); });
    el.addEventListener("lostpointercapture", function () { if (drag) end(null); });
    el.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });

    el.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      const notches = SpectoWheel.notches(ev);
      if (!notches) return;
      if (looking()) {
        // with RMB held the wheel sets the fly speed, kept when RMB lets go
        o.setSpeed(clampSpeed(Math.pow(2, Math.log2(o.speed() || 1) + notches * SPEED_STEP)), false);
        drag.speedChanged = true;
        return;
      }
      const cam = o.camera();
      if (camMode() === "orbit") { apply(moves.zoom(cam, notches * WHEEL * unitLen(), standing(cam))); return; }
      apply(moves.dolly(cam, notches * WHEEL * unitLen()));
    }, { passive: false });

    function onKey(ev, down) {
      const k = (ev.key || "").toLowerCase();
      if (k === "f" && down && !ev.altKey && !ev.ctrlKey && !ev.metaKey) { o.frame(); ev.preventDefault(); return; }
      if (k === "shift" || (k.length === 1 && FLY_KEYS.indexOf(k) >= 0)) {
        keys[k] = down;
        if (looking() || k !== "shift") ev.preventDefault();
        if (down && looking()) o.changed();
      }
    }
    el.addEventListener("keydown", function (ev) { onKey(ev, true); });
    el.addEventListener("keyup", function (ev) { onKey(ev, false); });
    el.addEventListener("blur", function () { for (const k in keys) keys[k] = false; end(null); });

    return {
      /** Fly while RMB is held: true when the camera moved this frame. */
      tick: function (dt) {
        if (!dt || !looking()) return false;
        if (!FLY_KEYS.split("").some(function (k) { return keys[k]; })) return false;
        apply(moves.fly(o.camera(), keys, dt, unitLen()));
        return true;
      },
      /** Drop the held keys and any drag (the window lost the focus). */
      release: function () { for (const k in keys) keys[k] = false; end(null); },
      state: function () {
        const st = {
          mode: gesture(),
          flying: looking() && FLY_KEYS.split("").some(function (k) { return keys[k]; }),
          keys: Object.keys(keys).filter(function (k) { return keys[k]; }),
        };
        if (o.mode) st.camera_mode = camMode();   // only a view with modes reports one
        return st;
      },
    };
  }

  window.SpectoViewNav = {
    attach: attach,
    moves: moves,
    modeOf: modeOf,
    rates: {
      ROTATE: ROTATE, WALK: WALK, PAN: PAN, DOLLY: DOLLY, WHEEL: WHEEL, FLY: FLY,
      SHIFT: SHIFT, SPEED_STEP: SPEED_STEP, SPEED_MIN: SPEED_MIN, SPEED_MAX: SPEED_MAX,
      PITCH_LIMIT: PITCH_LIMIT, CLICK_SLOP: CLICK_SLOP, MIN_RADIUS: MIN_RADIUS,
    },
  };
})();
