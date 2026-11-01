/* Wheel notches (#189, #194): a mouse wheel notch's DOM deltaY is not a constant. It is
 * 100 at 100 % display scale, about 60 on a scaled display, 120 elsewhere (Chromium scales
 * it by the display and the system's scroll lines). So a pixel-mode event of at least
 * NOTCH_MIN is one notch by its sign, a line- or page-mode event is one notch too, and a
 * touchpad's (or a pinch's) smaller deltas are fractions of TRACKPAD_NOTCH.
 *
 *   SpectoWheel.notches(ev)          // +1 / -1 a notch (+ away: zoom in, dolly forward);
 *                                    // a fraction for a touchpad delta; 0 for none
 *   const count = SpectoWheel.counter();
 *   count(ev)                        // whole notches: the fractions add up across events
 */
(function () {
  "use strict";
  const NOTCH_MIN = 40;
  const TRACKPAD_NOTCH = 100;

  /** @param {{deltaY?: number, deltaMode?: number}} ev @returns {number} */
  function notches(ev) {
    const dy = ev.deltaY || 0;
    if (!dy) return 0;
    if (ev.deltaMode || Math.abs(dy) >= NOTCH_MIN) return dy < 0 ? 1 : -1;
    return -dy / TRACKPAD_NOTCH;
  }

  /** @returns {(ev: {deltaY?: number, deltaMode?: number}) => number} */
  function counter() {
    let partial = 0;  // a touchpad's deltaY not yet a whole notch (in deltaY: no float drift)
    return function (ev) {
      const n = notches(ev);
      if (Number.isInteger(n)) { partial = 0; return n; }
      partial += ev.deltaY || 0;
      const whole = Math.trunc(partial / TRACKPAD_NOTCH);
      partial -= whole * TRACKPAD_NOTCH;
      return -whole;
    };
  }

  window.SpectoWheel = { notches: notches, counter: counter, NOTCH_MIN: NOTCH_MIN, TRACKPAD_NOTCH: TRACKPAD_NOTCH };
})();
