/* Shared fake-content generators for the Specto GUI style mockups.
   Everything is procedural (seeded), no network assets.
   Canvas elements declare what to draw:  <canvas data-draw="camera|cloud|map" data-seed="3" data-opts='{"...":...}'>
   Code panes:  <div class="code" data-code="controls|frame" data-theme="dark|light">
   Wires:       window.WIRES = [{a:'#portId', b:'#portId', color:'#hex', ...}] drawn into svg.wire-layer inside data-wire-root.
*/
(function () {
  'use strict';

  function rng(seed) {
    let a = (seed * 2654435761) >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  function turbo(t) {
    t = clamp(t, 0, 1);
    const r = 0.13572138 + t * (4.61539260 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
    const g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
    const b = 0.10667330 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
    return [clamp(r, 0, 1) * 255, clamp(g, 0, 1) * 255, clamp(b, 0, 1) * 255];
  }
  function stops(list) {
    return function (t) {
      t = clamp(t, 0, 1) * (list.length - 1);
      const i = Math.min(Math.floor(t), list.length - 2), f = t - i;
      const a = list[i], b = list[i + 1];
      return [lerp(a[0], b[0], f), lerp(a[1], b[1], f), lerp(a[2], b[2], f)];
    };
  }
  const CMAPS = {
    turbo: turbo,
    viridis: stops([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]]),
    calm: stops([[64, 84, 140], [86, 128, 178], [120, 170, 190], [196, 170, 120], [214, 120, 90]]),
    ice: stops([[40, 70, 110], [70, 140, 200], [140, 210, 240], [235, 250, 255]]),
    neon: stops([[0, 120, 255], [0, 220, 255], [120, 255, 170], [255, 230, 80], [255, 80, 160]]),
  };

  /* ---------------------------------------------------------------- camera frame */
  function drawCamera(ctx, w, h, seed, o) {
    const r = rng(seed + 11);
    const dusk = o.dusk || false;
    const hz = h * (0.42 + r() * 0.08);
    const vx = w * (0.42 + r() * 0.16);
    // sky
    let g = ctx.createLinearGradient(0, 0, 0, hz);
    if (dusk) { g.addColorStop(0, '#2b3557'); g.addColorStop(1, '#d99a6c'); }
    else { g.addColorStop(0, '#7da7d4'); g.addColorStop(1, '#dde7ee'); }
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, hz + 2);
    // far skyline
    ctx.fillStyle = dusk ? '#4a4660' : '#a9b6c2';
    let x = 0;
    while (x < w) { const bw = w * (0.03 + r() * 0.06), bh = h * (0.05 + r() * 0.12); ctx.fillRect(x, hz - bh, bw, bh + 2); x += bw; }
    // building facades left/right converge to vanishing point
    function facade(side) {
      const cols = side < 0 ? ['#8a7a6a', '#9c8d7a', '#6f6a66', '#b5a48c'] : ['#7d858c', '#9a9590', '#6b7178', '#a89a8a'];
      let x0 = side < 0 ? 0 : w;
      const steps = 4 + Math.floor(r() * 3);
      for (let i = 0; i < steps; i++) {
        const t0 = i / steps, t1 = (i + 1) / steps;
        const xa = lerp(x0, vx + side * w * 0.05, t0 * 0.9), xb = lerp(x0, vx + side * w * 0.05, t1 * 0.9);
        const topA = lerp(-h * 0.2 - r() * h * 0.2, hz - h * 0.06, t0 * 0.9), topB = lerp(-h * 0.2, hz - h * 0.06, t1 * 0.9);
        const botA = lerp(h * 0.78, hz + 4, t0 * 0.9), botB = lerp(h * 0.78, hz + 4, t1 * 0.9);
        ctx.fillStyle = cols[Math.floor(r() * cols.length)];
        if (dusk) ctx.fillStyle = shade(ctx.fillStyle, -50);
        ctx.beginPath(); ctx.moveTo(xa, topA); ctx.lineTo(xb, topB); ctx.lineTo(xb, botB); ctx.lineTo(xa, botA); ctx.closePath(); ctx.fill();
        // windows
        ctx.fillStyle = dusk ? 'rgba(255,214,140,0.55)' : 'rgba(40,52,66,0.45)';
        for (let k = 1; k < 6; k++) {
          const yy = k / 6;
          const ya = lerp(topA, botA, yy), yb = lerp(topB, botB, yy);
          for (let m = 0; m < 3; m++) {
            const fx0 = (m + 0.25) / 3, fx1 = (m + 0.65) / 3;
            const wx0 = lerp(xa, xb, fx0), wx1 = lerp(xa, xb, fx1);
            const wy0 = lerp(ya, yb, fx0), wy1 = lerp(ya, yb, fx1);
            const hh = Math.abs(botA - topA) / 14 * lerp(1, Math.abs(botB - topB) / Math.abs(botA - topA), (fx0 + fx1) / 2);
            if (r() < 0.8) { ctx.beginPath(); ctx.moveTo(wx0, wy0); ctx.lineTo(wx1, wy1); ctx.lineTo(wx1, wy1 + hh); ctx.lineTo(wx0, wy0 + hh); ctx.fill(); }
          }
        }
      }
    }
    facade(-1); facade(1);
    // road
    g = ctx.createLinearGradient(0, hz, 0, h);
    g.addColorStop(0, dusk ? '#3d3c44' : '#7b7f84'); g.addColorStop(1, dusk ? '#26252b' : '#4c5055');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.moveTo(vx - 2, hz); ctx.lineTo(vx + 2, hz); ctx.lineTo(w * 1.25, h); ctx.lineTo(-w * 0.25, h); ctx.closePath(); ctx.fill();
    // sidewalks
    ctx.fillStyle = dusk ? '#55525a' : '#a7a49e';
    ctx.beginPath(); ctx.moveTo(vx - 2, hz); ctx.lineTo(-w * 0.25, h); ctx.lineTo(-w * 0.6, h); ctx.lineTo(vx - 6, hz); ctx.fill();
    ctx.beginPath(); ctx.moveTo(vx + 2, hz); ctx.lineTo(w * 1.25, h); ctx.lineTo(w * 1.6, h); ctx.lineTo(vx + 6, hz); ctx.fill();
    // lane dashes
    ctx.fillStyle = dusk ? 'rgba(240,230,200,0.8)' : 'rgba(250,250,245,0.9)';
    for (let i = 0; i < 9; i++) {
      const t0 = Math.pow(i / 9, 2.2), t1 = Math.pow((i + 0.45) / 9, 2.2);
      const y0 = lerp(hz, h, t0), y1 = lerp(hz, h, t1);
      const w0 = lerp(0.5, w * 0.012, t0), w1 = lerp(0.5, w * 0.012, t1);
      ctx.beginPath(); ctx.moveTo(vx - w0, y0); ctx.lineTo(vx + w0, y0); ctx.lineTo(vx + w1, y1); ctx.lineTo(vx - w1, y1); ctx.fill();
    }
    // edge lines
    ctx.strokeStyle = 'rgba(240,220,120,0.8)'; ctx.lineWidth = Math.max(1, w / 300);
    ctx.beginPath(); ctx.moveTo(vx - 3, hz); ctx.lineTo(-w * 0.12, h); ctx.moveTo(vx + 3, hz); ctx.lineTo(w * 1.12, h); ctx.stroke();
    // cars
    const cars = 1 + Math.floor(r() * 3);
    const boxes = [];
    for (let i = 0; i < cars; i++) {
      const t = 0.12 + r() * 0.45;
      const lane = (r() < 0.5 ? -1 : 1) * (0.1 + r() * 0.25);
      const cy = lerp(hz, h, t);
      const cw = lerp(4, w * 0.7, t) * 0.32;
      const ch = cw * 0.62;
      const cx = vx + lane * lerp(0, w * 1.4, t);
      const col = ['#c7ccd1', '#2f3a48', '#8e2b2b', '#e8e8e8', '#3d5a80', '#1f1f22'][Math.floor(r() * 6)];
      ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fillRect(cx - cw * 0.52, cy - ch * 0.05, cw * 1.04, ch * 0.12);
      ctx.fillStyle = dusk ? shade(col, -40) : col;
      roundRect(ctx, cx - cw / 2, cy - ch, cw, ch * 0.95, cw * 0.08); ctx.fill();
      ctx.fillStyle = dusk ? '#20242c' : '#3a4654';
      roundRect(ctx, cx - cw * 0.38, cy - ch * 0.98, cw * 0.76, ch * 0.36, cw * 0.06); ctx.fill();
      ctx.fillStyle = '#ff3b30';
      ctx.fillRect(cx - cw * 0.47, cy - ch * 0.5, cw * 0.12, ch * 0.1); ctx.fillRect(cx + cw * 0.35, cy - ch * 0.5, cw * 0.12, ch * 0.1);
      boxes.push([cx - cw * 0.55, cy - ch * 1.05, cw * 1.1, ch * 1.12, (0.8 + r() * 0.19).toFixed(2)]);
    }
    // vignette
    g = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.max(w, h) * 0.75);
    g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(0,0,0,0.35)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    if (o.boxes) {
      ctx.lineWidth = Math.max(1, w / 260);
      ctx.font = `${Math.max(8, Math.round(w / 38))}px "Cascadia Mono", Consolas, monospace`;
      boxes.forEach(b => {
        ctx.strokeStyle = o.boxColor || '#39ff88'; ctx.strokeRect(b[0], b[1], b[2], b[3]);
        if (w > 160) { const label = 'car ' + b[4]; const tw = ctx.measureText(label).width + 6; ctx.fillStyle = o.boxColor || '#39ff88'; ctx.fillRect(b[0], b[1] - w / 30, tw, w / 30); ctx.fillStyle = '#000'; ctx.fillText(label, b[0] + 3, b[1] - w / 140 - 1); }
      });
    }
    if (o.stamp) {
      ctx.font = `${Math.max(9, Math.round(w / 45))}px "Cascadia Mono", Consolas, monospace`;
      ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fillRect(0, h - w / 22, w, w / 22);
      ctx.fillStyle = '#e6e6e6'; ctx.fillText(o.stamp, 6, h - w / 80 - 2);
    }
  }
  function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
  function shade(hex, amt) {
    if (hex[0] !== '#') return hex;
    const n = parseInt(hex.slice(1), 16);
    const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => clamp(v + amt, 0, 255));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  /* ---------------------------------------------------------------- point cloud */
  function drawCloud(ctx, w, h, seed, o) {
    const r = rng(seed + 101);
    const cmap = CMAPS[o.cmap || 'turbo'];
    ctx.fillStyle = o.bg || '#1b1d21'; ctx.fillRect(0, 0, w, h);
    if (o.bg2) { const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, o.bg2); g.addColorStop(1, o.bg); ctx.fillStyle = g; ctx.fillRect(0, 0, w, h); }
    // camera
    const eye = o.eye || [-22, -14, 13], tgt = o.target || [14, 2, 0];
    const f = norm(sub(tgt, eye)), rt = norm(cross(f, [0, 0, 1])), up = cross(rt, f);
    const fov = (o.fov || 55) * Math.PI / 180, s = (h / 2) / Math.tan(fov / 2);
    function proj(p) {
      const d = sub(p, eye), z = dot(d, f);
      if (z < 0.5) return null;
      return [w / 2 + dot(d, rt) * s / z, h / 2 - dot(d, up) * s / z, z];
    }
    // grid
    ctx.strokeStyle = o.grid || 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1;
    for (let i = -60; i <= 80; i += 5) {
      line3(ctx, proj, [i, -40, 0], [i, 40, 0]);
    }
    for (let j = -40; j <= 40; j += 5) line3(ctx, proj, [-60, j, 0], [80, j, 0]);
    const pts = [];
    // ground rings
    for (let i = 0; i < 46; i++) {
      const rad = 2.6 * Math.pow(1.075, i);
      const n = Math.floor(900 * (o.density || 1));
      for (let k = 0; k < n; k++) {
        const a = (k / n) * Math.PI * 2 + r() * 0.004;
        const x = Math.cos(a) * rad, y = Math.sin(a) * rad;
        if (Math.abs(y) > 15 && x > -8 && x < 70) continue; // walls occlude
        const zz = (Math.abs(y) > 6.5 ? 0.15 : 0) + (r() - 0.5) * 0.04;
        pts.push([x, y, zz, 0.12 + r() * 0.05]);
      }
    }
    // walls
    for (const side of [-1, 1]) {
      for (let x = -8; x < 70; x += 0.18) {
        for (let zi = 0; zi < 22; zi++) {
          const z = zi * 0.45 + r() * 0.05;
          if (z > 9 + 3 * Math.sin(x * 0.21 + side)) continue;
          if (r() < 0.35) continue;
          const y = side * (15 + Math.sin(x * 0.7) * 0.15 + (Math.floor(x / 11) % 2) * 1.2);
          pts.push([x, y, z, 0.5]);
        }
      }
    }
    // cars
    const cars = [[8, -3.2], [17, 2.8], [26, -3], [-6, 3.1], [38, 3.4], [46, -2.6]];
    cars.forEach(c => {
      for (let k = 0; k < 900; k++) {
        const u = r(), v = r(), face = Math.floor(r() * 3);
        let p;
        const L = 4.4, W = 1.8, H = 1.5;
        if (face === 0) p = [c[0] - L / 2 + u * L, c[1] - Math.sign(c[1]) * W / 2, 0.3 + v * H * (u > 0.2 && u < 0.85 ? 1 : 0.6)];
        else if (face === 1) p = [c[0] - L / 2 + u * L, c[1] - W / 2 + v * W, 0.3 + H * (u > 0.2 && u < 0.85 ? 1 : 0.6)];
        else p = [c[0] - L / 2, c[1] - W / 2 + u * W, 0.3 + v * H * 0.6];
        pts.push([p[0], p[1], p[2], 0.9]);
      }
    });
    // trees / poles
    for (let t = 0; t < 9; t++) {
      const tx = -4 + t * 8.3 + r() * 2, ty = (t % 2 ? 1 : -1) * (9 + r());
      for (let k = 0; k < 120; k++) pts.push([tx + (r() - 0.5) * 0.15, ty + (r() - 0.5) * 0.15, r() * 4, 0.4]);
      for (let k = 0; k < 900; k++) {
        const th = r() * Math.PI * 2, ph = Math.acos(2 * r() - 1), rr = 1.9 * Math.cbrt(r());
        pts.push([tx + rr * Math.sin(ph) * Math.cos(th), ty + rr * Math.sin(ph) * Math.sin(th), 5 + rr * Math.cos(ph) * 0.8, 0.3]);
      }
    }
    // pedestrians
    [[12, -7.4], [12.6, -7.9], [30, 7.6]].forEach(p => { for (let k = 0; k < 160; k++) pts.push([p[0] + (r() - 0.5) * 0.5, p[1] + (r() - 0.5) * 0.4, r() * 1.75, 0.8]); });

    const sz = (o.size || 1.4) * (w / 900 + 0.4);
    const alpha = o.alpha || 0.9;
    const zmax = o.zmax || 10;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], q = proj(p);
      if (!q || q[0] < -5 || q[0] > w + 5 || q[1] < -5 || q[1] > h + 5) continue;
      const t = o.by === 'intensity' ? p[3] : p[2] / zmax;
      const c = cmap(o.by === 'mixed' ? clamp(p[2] / zmax * 0.8 + p[3] * 0.25, 0, 1) : t);
      const near = clamp(28 / q[2], 0.5, 2.2);
      ctx.fillStyle = `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${alpha})`;
      const d = sz * near;
      ctx.fillRect(q[0] - d / 2, q[1] - d / 2, d, d);
    }
    // ego vehicle
    ctx.strokeStyle = o.ego || '#ffffff'; ctx.lineWidth = Math.max(1, w / 600);
    box3(ctx, proj, [0, 0, 0], 4.6, 1.9, 1.7);
    // trajectory ahead
    if (o.path) {
      ctx.strokeStyle = o.path; ctx.lineWidth = Math.max(1.5, w / 400); ctx.setLineDash([6, 4]);
      ctx.beginPath(); let st = false;
      for (let x = 2; x < 60; x += 1) { const q = proj([x, Math.sin(x / 18) * 1.2, 0.05]); if (!q) continue; if (!st) { ctx.moveTo(q[0], q[1]); st = true; } else ctx.lineTo(q[0], q[1]); }
      ctx.stroke(); ctx.setLineDash([]);
    }
    // camera frustum (linked camera)
    if (o.frustum) {
      ctx.strokeStyle = o.frustum; ctx.lineWidth = Math.max(1.2, w / 500);
      const apex = [1.8, 0, 1.6], far = 9, hw = 4.2, hh = 2.4;
      const cs = [[far, -hw, 1.6 - hh], [far, hw, 1.6 - hh], [far, hw, 1.6 + hh], [far, -hw, 1.6 + hh]].map(c => [apex[0] + c[0], c[1], c[2]]);
      cs.forEach(c => line3(ctx, proj, apex, c));
      for (let i = 0; i < 4; i++) line3(ctx, proj, cs[i], cs[(i + 1) % 4]);
    }
    // axes gizmo
    if (o.gizmo !== false) {
      const gx = 34, gy = h - 34, L = 20;
      const ax = [[1, 0, 0, '#e5534b'], [0, 1, 0, '#57ab5a'], [0, 0, 1, '#539bf5']];
      ax.forEach(a => {
        const vx = dot([a[0], a[1], a[2]], rt), vy = -dot([a[0], a[1], a[2]], up);
        ctx.strokeStyle = a[3]; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(gx, gy); ctx.lineTo(gx + vx * L, gy + vy * L); ctx.stroke();
        ctx.fillStyle = a[3]; ctx.font = '10px "Segoe UI", sans-serif'; ctx.fillText('xyz'[ax.indexOf(a)], gx + vx * (L + 7) - 3, gy + vy * (L + 7) + 3);
      });
    }
  }
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = a => { const l = Math.hypot(a[0], a[1], a[2]); return [a[0] / l, a[1] / l, a[2] / l]; };
  function line3(ctx, proj, a, b) { const p = proj(a), q = proj(b); if (!p || !q) return; ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke(); }
  function box3(ctx, proj, c, L, W, H) {
    const v = []; for (const dx of [-1, 1]) for (const dy of [-1, 1]) for (const dz of [0, 1]) v.push([c[0] + dx * L / 2, c[1] + dy * W / 2, c[2] + dz * H]);
    const e = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
    e.forEach(p => line3(ctx, proj, v[p[0]], v[p[1]]));
  }

  /* ---------------------------------------------------------------- map */
  const MAPTHEMES = {
    dark: { land: '#1d2126', block: '#252a30', bldg: '#2d333b', park: '#1f3326', water: '#16283a', road: '#3a4049', major: '#525a66', casing: '#14171b', label: '#8b96a3', halo: '#1d2126' },
    light: { land: '#f2efe9', block: '#ebe7df', bldg: '#ddd7cc', park: '#d5e8cf', water: '#bcd7ea', road: '#ffffff', major: '#fde7a8', casing: '#d8d2c6', label: '#6b6459', halo: '#f2efe9' },
    night: { land: '#0e1116', block: '#12161c', bldg: '#171c24', park: '#0f1d17', water: '#0a1a2a', road: '#232a34', major: '#2f3845', casing: '#0a0c10', label: '#5f6b7a', halo: '#0e1116' },
    slate: { land: '#262b33', block: '#2c323b', bldg: '#353c46', park: '#25392f', water: '#1d3247', road: '#424a56', major: '#5a6474', casing: '#1b1f25', label: '#9aa5b3', halo: '#262b33' },
  };
  function drawMap(ctx, w, h, seed, o) {
    const r = rng(seed + 7);
    const T = Object.assign({}, MAPTHEMES[o.theme || 'dark'], o.colors || {});
    ctx.fillStyle = T.land; ctx.fillRect(0, 0, w, h);
    const ang = (o.angle != null ? o.angle : -14) * Math.PI / 180;
    const cx = w / 2, cy = h / 2, B = o.block || 74;
    const R = Math.hypot(w, h);
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(ang);
    // blocks + buildings
    const N = Math.ceil(R / B) + 1;
    const blockColor = T.block;
    for (let i = -N; i < N; i++) for (let j = -N; j < N; j++) {
      const x = i * B, y = j * B * 0.82;
      const isPark = (i === 1 && j === -2) || (i === -3 && j === 1) || (i === -2 && j === 1);
      ctx.fillStyle = isPark ? T.park : blockColor;
      ctx.fillRect(x + 5, y + 5, B - 10, B * 0.82 - 10);
      if (!isPark) {
        ctx.fillStyle = T.bldg;
        const n = 3 + Math.floor(r() * 4);
        for (let k = 0; k < n; k++) {
          const bw = 8 + r() * (B * 0.3), bh = 8 + r() * (B * 0.25);
          ctx.fillRect(x + 8 + r() * (B - 16 - bw), y + 8 + r() * (B * 0.82 - 16 - bh), bw, bh);
        }
      } else {
        ctx.fillStyle = shade(T.park, 12);
        for (let k = 0; k < 7; k++) { ctx.beginPath(); ctx.arc(x + 12 + r() * (B - 24), y + 12 + r() * (B * 0.82 - 24), 3 + r() * 4, 0, 7); ctx.fill(); }
      }
    }
    // river
    ctx.fillStyle = T.water;
    ctx.beginPath();
    const rv = y0 => { const pts = []; for (let x = -R; x <= R; x += 20) pts.push([x, y0 + Math.sin(x / 140) * 40 + Math.sin(x / 57) * 9]); return pts; };
    const top = rv(B * 0.82 * 2.55), bot = rv(B * 0.82 * 2.55 + 46);
    ctx.moveTo(top[0][0], top[0][1]); top.forEach(p => ctx.lineTo(p[0], p[1])); bot.reverse().forEach(p => ctx.lineTo(p[0], p[1])); ctx.closePath(); ctx.fill();
    // streets
    function street(x1, y1, x2, y2, wd, col) { ctx.strokeStyle = T.casing; ctx.lineWidth = wd + 2; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.strokeStyle = col; ctx.lineWidth = wd; ctx.stroke(); }
    ctx.lineCap = 'butt';
    for (let i = -N; i < N; i++) street(i * B, -R, i * B, R, (i % 3 === 0) ? 7 : 4, (i % 3 === 0) ? T.major : T.road);
    for (let j = -N; j < N; j++) street(-R, j * B * 0.82, R, j * B * 0.82, (j % 4 === 0) ? 7 : 4, (j % 4 === 0) ? T.major : T.road);
    street(-R * 0.6, R * 0.5, R * 0.6, -R * 0.5, 8, T.major);
    // GeoJSON polygons
    const polys = o.polys || [];
    polys.forEach(pg => {
      ctx.beginPath(); pg.pts.forEach((p, k) => { const X = p[0] * B, Y = p[1] * B * 0.82; k ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); }); ctx.closePath();
      ctx.fillStyle = pg.fill; ctx.fill(); ctx.strokeStyle = pg.stroke; ctx.lineWidth = pg.lw || 2; if (pg.dash) ctx.setLineDash(pg.dash); ctx.stroke(); ctx.setLineDash([]);
    });
    // GPS track
    const tr = o.track || [[-4, 3], [-4, 1], [-2, 1], [-2, -1], [0, -1], [0, -2], [2, -2], [2, 0], [3, 0], [3, 1.5]];
    const tp = [];
    for (let k = 0; k < tr.length - 1; k++) {
      const a = tr[k], b = tr[k + 1];
      for (let t = 0; t < 1; t += 0.04) tp.push([lerp(a[0], b[0], t) * B + (r() - 0.5) * 2.5, lerp(a[1], b[1], t) * B * 0.82 + (r() - 0.5) * 2.5]);
    }
    tp.push([tr[tr.length - 1][0] * B, tr[tr.length - 1][1] * B * 0.82]);
    const tc = o.trackColor || '#3fa9ff';
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.strokeStyle = o.trackHalo || 'rgba(255,255,255,0.85)'; ctx.lineWidth = 7; path(ctx, tp); ctx.stroke();
    ctx.strokeStyle = tc; ctx.lineWidth = 4; path(ctx, tp); ctx.stroke();
    // future portion
    const cur = Math.floor(tp.length * (o.progress || 0.62));
    ctx.strokeStyle = o.trackFuture || 'rgba(140,150,160,0.9)'; ctx.lineWidth = 4; ctx.setLineDash([2, 6]); path(ctx, tp.slice(cur)); ctx.stroke(); ctx.setLineDash([]);
    // samples
    ctx.fillStyle = '#fff';
    for (let k = 0; k < cur; k += 6) { ctx.beginPath(); ctx.arc(tp[k][0], tp[k][1], 1.6, 0, 7); ctx.fill(); }
    // start/end
    marker(tp[0], '#2fbf71'); marker(tp[tp.length - 1], '#e5534b');
    // current position
    const p = tp[cur], q = tp[cur + 2] || p;
    ctx.fillStyle = (o.posColor || tc) + '40'; ctx.beginPath(); ctx.arc(p[0], p[1], 16, 0, 7); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(p[0], p[1], 7, 0, 7); ctx.fill();
    ctx.fillStyle = o.posColor || tc; ctx.beginPath(); ctx.arc(p[0], p[1], 5, 0, 7); ctx.fill();
    const hd = Math.atan2(q[1] - p[1], q[0] - p[0]);
    ctx.save(); ctx.translate(p[0], p[1]); ctx.rotate(hd); ctx.fillStyle = o.posColor || tc; ctx.beginPath(); ctx.moveTo(14, 0); ctx.lineTo(8, -5); ctx.lineTo(8, 5); ctx.fill(); ctx.restore();
    function marker(pt, col) { ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(pt[0], pt[1], 6, 0, 7); ctx.fill(); ctx.fillStyle = col; ctx.beginPath(); ctx.arc(pt[0], pt[1], 4, 0, 7); ctx.fill(); }
    // street labels (rotated with grid)
    ctx.font = `${o.labelSize || 10}px "Segoe UI", system-ui, sans-serif`; ctx.textAlign = 'center';
    const labels = [['HARBOR AVE', 0, -2 * 0.82 * B - 0, 0], ['5TH ST', 3 * B, 0.5 * B, Math.PI / 2], ['MILL RD', -1.5 * B, 4 * 0.82 * B, 0], ['Riverside Park', 1.5 * B, -1.6 * B, 0], ['CANAL ST', -3 * B, -0.6 * B, Math.PI / 2]];
    labels.forEach(l => { ctx.save(); ctx.translate(l[1], l[2]); ctx.rotate(l[3]); ctx.lineWidth = 3; ctx.strokeStyle = T.halo; ctx.strokeText(l[0], 0, 3); ctx.fillStyle = T.label; ctx.fillText(l[0], 0, 3); ctx.restore(); });
    polys.forEach(pg => { if (!pg.label) return; const c = pg.pts.reduce((a, p) => [a[0] + p[0] / pg.pts.length, a[1] + p[1] / pg.pts.length], [0, 0]); ctx.save(); ctx.translate(c[0] * B, c[1] * B * 0.82); ctx.rotate(-ang); ctx.font = `600 ${o.labelSize || 10}px "Segoe UI", sans-serif`; ctx.lineWidth = 3; ctx.strokeStyle = T.halo; ctx.strokeText(pg.label, 0, 3); ctx.fillStyle = pg.stroke; ctx.fillText(pg.label, 0, 3); ctx.restore(); });
    ctx.restore();
    // scale bar
    if (o.chrome !== false) {
      ctx.textAlign = 'left';
      ctx.fillStyle = T.halo + 'cc'; ctx.fillRect(8, h - 22, 84, 14);
      ctx.strokeStyle = T.label; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(12, h - 12); ctx.lineTo(12, h - 9); ctx.lineTo(72, h - 9); ctx.lineTo(72, h - 12); ctx.stroke();
      ctx.fillStyle = T.label; ctx.font = '9px "Segoe UI", sans-serif'; ctx.fillText('100 m', 76, h - 11);
      ctx.textAlign = 'right'; ctx.fillText('© OpenStreetMap contributors', w - 6, h - 6);
    }
  }
  function path(ctx, pts) { ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); }

  /* ---------------------------------------------------------------- code */
  const SNIPPETS = {
    controls: [
      ['', '{'],
      ['', '  <k>"id"</k>: <s>"controls:v11"</s>,'],
      ['', '  <k>"type"</k>: <s>"panel.controls"</s>,'],
      ['', '  <k>"author"</k>: <s>"agent:claude-code#t12"</s>,'],
      ['', '  <k>"items"</k>: ['],
      ['+', '    { <k>"kind"</k>: <s>"slider"</s>, <k>"label"</k>: <s>"time offset"</s>,'],
      ['+', '      <k>"bind"</k>: <s>"link:time.offset_ms"</s>,'],
      ['+', '      <k>"min"</k>: <n>-500</n>, <k>"max"</k>: <n>500</n>, <k>"step"</k>: <n>10</n> },'],
      ['+', '    { <k>"kind"</k>: <s>"toggle"</s>, <k>"label"</k>: <s>"show boxes"</s>,'],
      ['+', '      <k>"bind"</k>: <s>"view:v2.overlay.boxes"</s> },'],
      ['', '    { <k>"kind"</k>: <s>"toggle"</s>, <k>"label"</k>: <s>"follow camera"</s>,'],
      ['~', '      <k>"bind"</k>: <s>"link:cam.follow"</s>, <k>"value"</k>: <b>true</b> },'],
      ['', '    { <k>"kind"</k>: <s>"button"</s>, <k>"label"</k>: <s>"next run"</s>,'],
      ['', '      <k>"command"</k>: <s>"collection.next"</s>,'],
      ['', '      <k>"args"</k>: { <k>"col"</k>: <s>"col:frames"</s> } }'],
      ['', '  ],'],
      ['', '  <k>"links"</k>: [<s>"time"</s>, <s>"cam"</s>]'],
      ['', '}'],
    ],
    frame: [
      ['', '{'],
      ['', '  <k>"type"</k>: <s>"FeatureCollection"</s>,'],
      ['', '  <k>"features"</k>: [{'],
      ['', '    <k>"type"</k>: <s>"Feature"</s>,'],
      ['', '    <k>"properties"</k>: {'],
      ['', '      <k>"frame"</k>: <n>1236</n>, <k>"t"</k>: <n>41.200</n>,'],
      ['', '      <k>"speed_mps"</k>: <n>6.84</n>, <k>"heading"</k>: <n>78.2</n>,'],
      ['~', '      <k>"fix"</k>: <s>"rtk-fixed"</s>, <k>"hdop"</k>: <n>0.62</n>'],
      ['', '    },'],
      ['', '    <k>"geometry"</k>: {'],
      ['', '      <k>"type"</k>: <s>"Point"</s>,'],
      ['', '      <k>"coordinates"</k>: [<n>-122.33871</n>, <n>47.60412</n>]'],
      ['', '    }'],
      ['', '  }]'],
      ['', '}'],
    ],
  };
  function renderCode(el) {
    const lines = SNIPPETS[el.dataset.code] || [];
    const start = +(el.dataset.start || 1);
    el.innerHTML = lines.map((l, i) => {
      const body = l[1].replace(/<k>/g, '<span class="tk">').replace(/<s>/g, '<span class="ts">').replace(/<n>/g, '<span class="tn">').replace(/<b>/g, '<span class="tb">').replace(/<\/[ksnb]>/g, '</span>');
      const mark = l[0] === '+' ? ' add' : l[0] === '~' ? ' mod' : '';
      return `<div class="cl${mark}"><span class="ln">${start + i}</span><span class="gm"></span><span class="tx">${body.replace(/ {2}/g, '&nbsp;&nbsp;')}</span></div>`;
    }).join('');
  }

  /* ---------------------------------------------------------------- wires */
  function drawWires(root) {
    const svg = root.querySelector('svg.wire-layer');
    if (!svg) return;
    const rb = root.getBoundingClientRect();
    svg.setAttribute('width', rb.width); svg.setAttribute('height', rb.height);
    svg.setAttribute('viewBox', `0 0 ${rb.width} ${rb.height}`);
    const NS = 'http://www.w3.org/2000/svg';
    const defs = document.createElementNS(NS, 'defs'); svg.appendChild(defs);
    const markers = {};
    function marker(color, size) {
      const id = 'm' + color.replace(/[^a-z0-9]/gi, '') + size;
      if (markers[id]) return id;
      const m = document.createElementNS(NS, 'marker');
      m.setAttribute('id', id); m.setAttribute('viewBox', '0 0 10 10'); m.setAttribute('refX', '8'); m.setAttribute('refY', '5');
      m.setAttribute('markerWidth', size); m.setAttribute('markerHeight', size); m.setAttribute('orient', 'auto-start-reverse'); m.setAttribute('markerUnits', 'userSpaceOnUse');
      const p = document.createElementNS(NS, 'path'); p.setAttribute('d', 'M0,0 L10,5 L0,10 z'); p.setAttribute('fill', color);
      m.appendChild(p); defs.appendChild(m); markers[id] = 1; return id;
    }
    function pos(sel) {
      const e = root.querySelector(sel); if (!e) { console.warn('missing port', sel); return null; }
      const b = e.getBoundingClientRect();
      return { x: b.left + b.width / 2 - rb.left, y: b.top + b.height / 2 - rb.top, side: e.dataset.side || 'r' };
    }
    const dirs = { r: [1, 0], l: [-1, 0], t: [0, -1], b: [0, 1] };
    (window.WIRES || []).forEach(wd => {
      const a = pos(wd.a), b = pos(wd.b); if (!a || !b) return;
      const dist = Math.hypot(b.x - a.x, b.y - a.y);
      const k = wd.tension != null ? wd.tension : Math.max(30, dist * 0.45);
      const da = dirs[wd.sideA || a.side], db = dirs[wd.sideB || b.side];
      let d;
      if (wd.via) d = `M${a.x},${a.y} ` + wd.via.map(v => `L${v[0]},${v[1]}`).join(' ') + ` L${b.x},${b.y}`;
      else if (wd.style === 'straight') d = `M${a.x},${a.y} L${b.x},${b.y}`;
      else if (wd.style === 'ortho') {
        const mx = wd.mx != null ? wd.mx : (a.x + b.x) / 2;
        d = `M${a.x},${a.y} H${mx} V${b.y} H${b.x}`;
      } else d = `M${a.x},${a.y} C${a.x + da[0] * k},${a.y + da[1] * k} ${b.x + db[0] * k},${b.y + db[1] * k} ${b.x},${b.y}`;
      if (wd.glow) {
        const g = document.createElementNS(NS, 'path'); g.setAttribute('d', d); g.setAttribute('fill', 'none'); g.setAttribute('stroke', wd.color); g.setAttribute('stroke-width', (wd.w || 2) + 6); g.setAttribute('stroke-opacity', '0.18'); g.setAttribute('stroke-linecap', 'round'); svg.appendChild(g);
      }
      if (wd.casing) {
        const c = document.createElementNS(NS, 'path'); c.setAttribute('d', d); c.setAttribute('fill', 'none'); c.setAttribute('stroke', wd.casing); c.setAttribute('stroke-width', (wd.w || 2) + 3); svg.appendChild(c);
      }
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', d); p.setAttribute('fill', 'none'); p.setAttribute('stroke', wd.color); p.setAttribute('stroke-width', wd.w || 2);
      p.setAttribute('stroke-linecap', 'round'); p.setAttribute('stroke-linejoin', 'round');
      if (wd.dash) p.setAttribute('stroke-dasharray', wd.dash);
      if (wd.opacity) p.setAttribute('stroke-opacity', wd.opacity);
      if (wd.arrow) p.setAttribute('marker-end', `url(#${marker(wd.color, wd.arrowSize || 9)})`);
      if (wd.arrowStart) p.setAttribute('marker-start', `url(#${marker(wd.color, wd.arrowSize || 9)})`);
      svg.appendChild(p);
      if (wd.label) {
        const L = p.getTotalLength(), m = p.getPointAtLength(L * (wd.at || 0.5));
        const g = document.createElementNS(NS, 'g');
        const t = document.createElementNS(NS, 'text');
        t.textContent = wd.label; t.setAttribute('x', m.x); t.setAttribute('y', m.y + 3.5); t.setAttribute('text-anchor', 'middle');
        t.setAttribute('class', 'wire-label'); t.setAttribute('fill', wd.labelColor || wd.color);
        g.appendChild(t); svg.appendChild(g);
        const bb = t.getBBox();
        const rc = document.createElementNS(NS, 'rect');
        rc.setAttribute('x', bb.x - 6); rc.setAttribute('y', bb.y - 2); rc.setAttribute('width', bb.width + 12); rc.setAttribute('height', bb.height + 4);
        rc.setAttribute('rx', wd.labelRadius != null ? wd.labelRadius : (bb.height + 4) / 2); rc.setAttribute('class', 'wire-label-bg');
        if (wd.labelBg) rc.style.fill = wd.labelBg;
        if (wd.labelBorder) { rc.style.stroke = wd.labelBorder; rc.style.strokeWidth = '1'; }
        g.insertBefore(rc, t);
      }
    });
  }

  function hydrate() {
    document.querySelectorAll('canvas[data-draw]').forEach(cv => {
      const b = cv.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      cv.width = Math.max(1, Math.round(b.width * dpr)); cv.height = Math.max(1, Math.round(b.height * dpr));
      const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
      const o = cv.dataset.opts ? JSON.parse(cv.dataset.opts) : {};
      const seed = +(cv.dataset.seed || 1);
      const fn = { camera: drawCamera, cloud: drawCloud, map: drawMap }[cv.dataset.draw];
      if (fn) fn(ctx, b.width, b.height, seed, o);
    });
    document.querySelectorAll('[data-code]').forEach(renderCode);
    document.querySelectorAll('[data-wire-root]').forEach(drawWires);
    document.body.setAttribute('data-ready', '1');
  }
  window.addEventListener('load', hydrate);
})();
