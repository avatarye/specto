/* 3D view: Babylon.js 9.31 on WebGL2, offline (research #31; spec #35 stories 46-47).
 *
 * Python sends `scene` {entries: [{id, index, kind, label, url, ext?, count?, bounds?,
 * error?}], params: {point_size, up}, generation}; the page loads every entry into one
 * scene: point clouds as the 16 B/point buffer resolve decoded (float32 xyz block then
 * uint8 rgba block, straight into VertexBuffers), meshes and splats through Babylon's
 * loaders (`ext` names the plugin; the SPLAT loader is told where fflate and the SPZ
 * WASM decoder are, nothing goes to a CDN). STL and OBJ go to Babylon's own loaders (an
 * OBJ's url ends in its file name, so its MTL and textures load next to it; an STL keeps
 * the file's coordinates); a mesh with no material of its own draws in a plain grey. The
 * view param `up` turns the scene's root for z-up data; an entry with its own `up` (a DAE
 * resolve converted to a Y-up GLB, SPEC-42) hangs under a root that param leaves alone.
 * The view param `mesh_unit` (m, cm, mm, in) scales the STL and OBJ items, whose files
 * carry no unit: the host sends the factor (`mesh_scale` in the scene payload, and the
 * `mesh_scale {scale}` notification when the param changes).
 * Navigation is Unreal Editor's viewport (#152),
 * the shared web module viewnav.js (its header has the table): drag turns and walks,
 * right-drag looks with W A S D Q E flying and the wheel setting the speed, middle-drag
 * or both buttons pan, the wheel dollies, Alt+drag orbits the selection (else the point
 * under the cursor), F frames the selection or everything. A left click picks.
 *
 * Ports: the camera goes out with `Specto.emit("camera", value)` (throttled); a click on
 * a point cloud sends `pick {origin, direction, tolerance, t_max}` (`Specto.send`) and
 * Python answers `picked` from the kd-tree built at resolve (null for a miss, then the
 * page tries the splats' bounding boxes); a click on a mesh sends `select {item,
 * position}`. `port:camera` / `port:selection` from Python move the camera / highlight
 * the item without echoing. `render {running}` stops and restarts the render loop.
 *
 * Chrome (#129): no in-body bar. A VIEW panel on the left (camera speed, back to the
 * world origin), an ITEMS panel on the right (All / None, each item's file size), both
 * the side panel standard (panel.js) and kept in the view param `panels`; the footer
 * says how many items and frames all (the target button, F). Errors go to the view's
 * health dot, the navigation keys to the status bar.
 *
 * View modes (#153), the view param `view_mode`: lit (as loaded), unlit (PBR `unlit`, a
 * standard material's base colour without lighting), wireframe (mesh materials as wire
 * over a dark background; points and splats as they are) and, only when an item has
 * Unreal collision meshes (UCX_/UBX_/USP_/UCP_ names, hidden in the other modes),
 * collision: those in a flat tint, other meshes ghosted, points and splats hidden. The
 * footer's mode pill picks one (kept, one user submit); it is always in sight (#158).
 *
 * HDRI environments (#175): the scene payload lists the bundled ones (`environments`
 * [{name, label, look, author, page, url}], url null until build.py vendors it, a
 * prefiltered .env or a raw .hdr). The view params: `environment` (a name, or "none":
 * the two lights as before), `environment_intensity`, `environment_background` (the HDRI
 * drawn behind the scene) and `environment_rotation` (degrees about the up axis). An
 * environment lights the PBR materials (every glTF and PLY mesh) by image-based lighting
 * and turns the two lights off once it has loaded; points and splats draw as they are.
 * The footer's environment pill picks one (always in sight, #158); the VIEW panel holds
 * intensity, rotation and the background toggle. */
(function () {
  "use strict";
  const $ = function (id) { return document.getElementById(id); };
  const CAMERA_EMIT_MS = 120;
  const PICK_PX = 6;           // pick cone half-width in pixels
  const IDLE_FRAMES = 12;      // frames rendered after the last change (camera inertia)
  const SETTLE_MAX_MS = 60000; // a splat that never reports settled stops costing frames here
  const PLAIN_MESH = "#9ea4ab";  // a mesh whose file has no material (STL; OBJ without MTL)
  const UNITLESS = [".stl", ".obj"];
  let mtlWarning = "";         // the MTL loader hook could not install: said in an ok health
  let meshScale = 1;           // metres per STL/OBJ unit, from the host (mesh_unit)

  let engine = null, scene = null, camera = null, root = null, lights = [];
  const declaredRoots = {};    // up axis -> the root of the items whose file declares it
  let params = {
    point_size: 2, up: "y", camera_speed: 1,
    environment: "none", environment_intensity: 1, environment_background: false, environment_rotation: 0,
  };
  const SPEED_MIN = -3, SPEED_MAX = 3;   // the slider is log2 of the speed: 1/8x .. 8x
  const POINT_FRAME = 0.05;    // a selected point frames a box this many scene radii across
  let originCamera = null;     // the host's first camera: the world origin, everything in sight
  let viewPanel = null, itemsPanel = null;
  let speedInput = null, speedValue = null, originButton = null;
  const nodes = {};            // entry id -> {entry, mesh, enabled, li}
  let order = [];
  let generation = -1;
  let running = true;
  let dirty = true, idle = IDLE_FRAMES;
  let cameraTimer = 0, cameraDirty = false, applyingCamera = false;
  let marker = null, picked = null, selectedKeys = [];
  let current = null;          // the selection F frames and Alt+drag orbits: {keys, position}
  let nav = null;              // the viewport controls (viewnav.js)
  let lastFrame = 0;
  let pendingRay = null;
  let pendingFit = true;
  let loading = 0;
  let loadSeq = 0;             // bumped per scene payload: a stale loadScene stops repainting
  let settleSince = 0, settleGaveUp = false;   // the current splat settling wait (0: none)

  // the view's health dot carries an error (no pill on the page)
  function setError(msg) { if (msg) Specto.setHealth("error", msg); }
  function cssVar(name, fallback) {
    return (getComputedStyle(document.documentElement).getPropertyValue(name) || fallback).trim();
  }
  function color4(hex, a) {
    const c = BABYLON.Color3.FromHexString(hex);
    return new BABYLON.Color4(c.r, c.g, c.b, a === undefined ? 1 : a);
  }
  function requestRender() { dirty = true; idle = IDLE_FRAMES; }

  /* ---- engine ------------------------------------------------------------------------------ */
  function boot() {
    const canvas = $("c");
    // every decoder Babylon would fetch from cdn.babylonjs.com is looked for here instead:
    // missing ones fail loud and offline, never online (research #31 section 7)
    BABYLON.Tools.ScriptBaseUrl = location.origin + "/vendor/babylon/";
    engine = new BABYLON.Engine(canvas, true, { preserveDrawingBuffer: false, stencil: false, antialias: false, premultipliedAlpha: false });
    scene = new BABYLON.Scene(engine);
    scene.useRightHandedSystem = true;   // glTF's frame: resolve-decoded points and GLBs agree
    scene.clearColor = color4(cssVar("--color-surface-alt", "#faf7f1"));
    scene.skipPointerMovePicking = true;
    scene.autoClear = true;
    root = new BABYLON.TransformNode("root", scene);
    // an item whose file declares its up axis (a DAE converted to a Y-up GLB) hangs here,
    // where the view's up param does not turn it (SPEC-42)
    declaredRoots.y = new BABYLON.TransformNode("root:y", scene);
    declaredRoots.z = new BABYLON.TransformNode("root:z", scene);
    declaredRoots.z.rotation = new BABYLON.Vector3(-Math.PI / 2, 0, 0);
    // an STL's numbers as the file has them, as for PLY and OBJ: the up param turns them
    // (the loader would otherwise swap y and z, a mirror in this right-handed scene)
    if (BABYLON.STLFileLoader) BABYLON.STLFileLoader.DO_NOT_ALTER_FILE_COORDINATES = true;
    // an MTL texture map's options (-s 1 1 1, -clamp on, ...) are not part of its file
    // name; Babylon's MTL loader strips only map_bump's -bm (resolve checks the same names)
    const mtl = BABYLON.MTLFileLoader;
    if (!mtl || typeof mtl._GetTexture !== "function") {
      mtlWarning = "OBJ texture map options are not stripped (Babylon's MTL loader has no _GetTexture)";
      Specto.log("warn", mtlWarning);
    } else if (!mtl._spectoOptions) {
      const getTexture = mtl._GetTexture;
      mtl._GetTexture = function (rootUrl, value) {
        const args = Array.prototype.slice.call(arguments);
        args[1] = mapFileName(value);
        return getTexture.apply(this, args);
      };
      mtl._spectoOptions = true;
    }
    camera = new BABYLON.ArcRotateCamera("cam", -Math.PI / 3, 1.1, 20, BABYLON.Vector3.Zero(), scene);
    camera.minZ = 0.05;
    camera.maxZ = 5000;
    camera.lowerRadiusLimit = 0.01;
    // no attachControl: Babylon's own camera inputs stay off, viewnav.js moves the camera
    camera.onViewMatrixChangedObservable.add(function () {
      requestRender();
      if (!applyingCamera) scheduleCameraEmit();
    });
    const light = new BABYLON.HemisphericLight("sky", new BABYLON.Vector3(0.3, 1, 0.2), scene);
    light.intensity = 0.9;
    light.groundColor = new BABYLON.Color3(0.45, 0.42, 0.38);
    const sun = new BABYLON.DirectionalLight("sun", new BABYLON.Vector3(-0.5, -1, -0.4), scene);
    sun.intensity = 0.6;
    lights = [light, sun];   // off while an environment lights the scene (#175)
    scene.setRenderingOrder(0, skyboxFirst);
    marker = BABYLON.MeshBuilder.CreateSphere("pick", { diameter: 1, segments: 10 }, scene);
    const mm = new BABYLON.StandardMaterial("pickMat", scene);
    mm.emissiveColor = BABYLON.Color3.FromHexString(cssVar("--color-accent", "#2f6fab"));
    mm.disableLighting = true;
    marker.material = mm;
    marker.isPickable = false;
    marker.setEnabled(false);
    marker.renderingGroupId = 1;

    nav = SpectoViewNav.attach(canvas, {
      camera: function () { return { position: camera.position.asArray(), target: camera.target.asArray() }; },
      setCamera: function (position, target) {
        camera.setTarget(BABYLON.Vector3.FromArray(target));
        camera.setPosition(BABYLON.Vector3.FromArray(position));
        scheduleCameraEmit();
      },
      reach: sceneRadius,
      speed: cameraSpeed,
      setSpeed: function (v, keep) {
        params = Object.assign({}, params, { camera_speed: v });
        applySpeed(); report();
        if (keep) Specto.setParams({ camera_speed: cameraSpeed() });
      },
      pivot: pivotAt,
      frame: frameSelection,
      click: pick,
      changed: requestRender,
    });
    window.addEventListener("blur", function () { nav.release(); });
    window.addEventListener("resize", function () { engine.resize(); requestRender(); });
    new ResizeObserver(function () { engine.resize(); requestRender(); }).observe(canvas);

    engine.runRenderLoop(frame);
  }

  function frame() {
    if (!running) return;
    const now = performance.now();
    const dt = lastFrame ? Math.min(0.1, (now - lastFrame) / 1000) : 0;
    lastFrame = now;
    if (nav.tick(dt)) requestRender();
    if (splatSettling(now)) requestRender();
    if (!dirty && idle <= 0) return;
    if (!dirty) idle -= 1;
    dirty = false;
    sizeMarker();
    sizeSkybox();
    scene.render();
  }

  /* A GaussianSplattingMesh is not done when its import resolves: it sorts on a worker and
   * uploads the order when the worker answers, which for millions of splats outlasts
   * IDLE_FRAMES by seconds, and until then it draws nothing (#85). Babylon 9.31 has no
   * observable for it; `_isDepthSortSettled` (private: readyToDisplay && !sortIsDirty &&
   * canPostToWorker) is the exact state, `isReady()` the public one (false until the first
   * sort lands). Frames keep coming while any splat is unsettled, bounded by SETTLE_MAX_MS. */
  function splatSettled(m) {
    if (m._isDepthSortSettled !== undefined) return !!m._isDepthSortSettled;
    return typeof m.isReady !== "function" || m.isReady();
  }
  function splatSettling(now) {
    let waiting = false;
    for (const id of order) {
      const n = nodes[id];
      if (!n.mesh || !n.enabled || n.entry.kind !== "splat") continue;
      if (n.mesh.getChildMeshes().some(function (m) { return !splatSettled(m); })) { waiting = true; break; }
    }
    if (!waiting) { settleSince = 0; settleGaveUp = false; return false; }
    if (!settleSince) settleSince = now || 1;
    if (now - settleSince <= SETTLE_MAX_MS) return true;
    if (!settleGaveUp) { settleGaveUp = true; Specto.log("warn", "a splat did not settle in " + SETTLE_MAX_MS + " ms; idle rendering stops"); }
    return false;
  }

  /* ---- navigation (viewnav.js does the gestures) ------------------------------------------ */
  function sceneRadius() {
    let r = 0;
    for (const id of order) {
      const n = nodes[id];
      if (!n.mesh || !n.enabled) continue;
      const b = n.mesh.getHierarchyBoundingVectors(true);
      r = Math.max(r, b.max.subtract(b.min).length() / 2);
    }
    return r || 10;
  }

  /** The selection's box: its items' bounds, a small box around a selected point; null
   * while nothing shown is selected. */
  function selectionBox() {
    if (!current) return null;
    let min = null, max = null;
    const grow = function (lo, hi) {
      min = min ? BABYLON.Vector3.Minimize(min, lo) : lo.clone();
      max = max ? BABYLON.Vector3.Maximize(max, hi) : hi.clone();
    };
    current.keys.forEach(function (key) {
      const parts = key.split("#");
      const n = nodes[parts[0]];
      if (!n || !n.enabled) return;
      if (parts.length === 2) {
        if (!current.position) return;
        const p = BABYLON.Vector3.FromArray(current.position);
        const h = sceneRadius() * POINT_FRAME / 2;
        grow(p.subtract(new BABYLON.Vector3(h, h, h)), p.add(new BABYLON.Vector3(h, h, h)));
      } else if (n.mesh) {
        const b = n.mesh.getHierarchyBoundingVectors(true);
        grow(b.min, b.max);
      }
    });
    return min ? { min: min, max: max } : null;
  }
  /** F and the footer's target: the selection, else everything. */
  function frameSelection() {
    const box = selectionBox();
    if (box) frameBox(box.min, box.max); else fitAll();
  }

  /** Alt+drag's pivot: the selection's centre, else what is under the cursor (a mesh, a
   * splat's box, else the cursor's ray at the focus distance). */
  function pivotAt(x, y) {
    const box = selectionBox();
    if (box) return box.min.add(box.max).scale(0.5).asArray();
    const hit = scene.pick(x, y, function (m) { return m.isPickable && m.metadata && m.metadata.kind === "mesh"; }, false, camera);
    if (hit && hit.hit && hit.pickedPoint) return hit.pickedPoint.asArray();
    const ray = scene.createPickingRay(x, y, BABYLON.Matrix.Identity(), camera, false);
    const splat = pickSplatBox(ray);
    if (splat) return splat.position;
    const d = camera.target.subtract(camera.position).length();
    return ray.origin.add(ray.direction.scale(d)).asArray();
  }

  /* ---- camera speed and the world origin (the VIEW panel) ---------------------------------- */
  function cameraSpeed() {
    const v = Number(params.camera_speed);
    return v > 0 ? Math.max(Math.pow(2, SPEED_MIN), Math.min(Math.pow(2, SPEED_MAX), v)) : 1;
  }
  function applySpeed() {
    const v = cameraSpeed();
    if (speedInput) speedInput.value = String(Math.log2(v));
    if (speedValue) speedValue.textContent = v.toFixed(v < 1 ? 2 : 1) + "x";
  }
  function goToOrigin() {
    if (!originCamera) return;
    applyCamera(originCamera);
    scheduleCameraEmit();
  }

  function fitAll() {
    let min = null, max = null;
    for (const id of order) {
      const n = nodes[id];
      if (!n.mesh || !n.enabled) continue;
      const b = n.mesh.getHierarchyBoundingVectors(true);
      min = min ? BABYLON.Vector3.Minimize(min, b.min) : b.min.clone();
      max = max ? BABYLON.Vector3.Maximize(max, b.max) : b.max.clone();
    }
    if (!min) return;
    frameBox(min, max);
  }

  function frameBox(min, max) {
    const center = min.add(max).scale(0.5);
    const radius = Math.max(0.01, max.subtract(min).length() / 2);
    const distance = radius / Math.sin(camera.fov / 2) * 1.05;
    // keep the viewing direction (setTarget alone would swing the camera under a scene
    // whose centre is above it); the first frame looks down from a three-quarter view
    let dir = camera.target.subtract(camera.position);
    if (pendingFit || dir.length() < 1e-6) dir = new BABYLON.Vector3(-0.55, -0.6, 0.58);
    dir.normalize();
    applyingCamera = true;
    camera.setTarget(center);
    camera.setPosition(center.subtract(dir.scale(distance)));
    camera.maxZ = Math.max(100, radius * 50);
    camera.minZ = Math.max(0.001, radius / 5000);
    applyingCamera = false;
    requestRender();
    scheduleCameraEmit();
  }

  /* ---- camera port -------------------------------------------------------------------------- */
  function cameraValue() {
    return {
      position: camera.position.asArray(),
      target: camera.target.asArray(),
      up: camera.upVector.asArray(),
      fov_y: camera.fov,
      near: camera.minZ,
      far: camera.maxZ,
    };
  }
  function scheduleCameraEmit() {
    cameraDirty = true;
    if (cameraTimer) return;
    cameraTimer = window.setTimeout(function () {
      cameraTimer = 0;
      if (!cameraDirty) return;
      cameraDirty = false;
      Specto.emit("camera", cameraValue()).then(function (r) {
        if (r && r.ok === false) Specto.log("warn", "camera emit refused: " + (r.error && r.error.message));
      });
      report();
    }, CAMERA_EMIT_MS);
  }
  function applyCamera(v) {
    if (!v || !v.position || !v.target) return;
    applyingCamera = true;
    camera.setTarget(BABYLON.Vector3.FromArray(v.target));
    camera.setPosition(BABYLON.Vector3.FromArray(v.position));
    if (v.fov_y) camera.fov = v.fov_y;
    if (v.near) camera.minZ = v.near;
    if (v.far) camera.maxZ = v.far;
    applyingCamera = false;
    requestRender();
    report();
  }

  /* ---- picking ------------------------------------------------------------------------------ */
  function pick(x, y) {
    // meshes: Babylon's triangle pick; then point clouds: the ray goes to Python, which
    // answers from the kd-tree built at resolve; last, splats by their bounding box
    // (GPUPicker is WebGPU-grade work; research #31 section 6)
    const hit = scene.pick(x, y, function (m) { return m.isPickable && m.metadata && m.metadata.kind === "mesh"; }, false, camera);
    if (hit && hit.hit && hit.pickedMesh) {
      const id = ownerOf(hit.pickedMesh);
      if (id) {
        const p = hit.pickedPoint ? hit.pickedPoint.asArray() : null;
        Specto.send("select", { item: id, position: p });
        return;
      }
    }
    const ray = scene.createPickingRay(x, y, BABYLON.Matrix.Identity(), camera, false);
    const h = engine.getRenderHeight() || 1;
    const tolerance = PICK_PX * 2 * Math.tan(camera.fov / 2) / h;
    pendingRay = ray;
    Specto.send("pick", {
      origin: ray.origin.asArray(), direction: ray.direction.asArray(), tolerance: tolerance, t_max: camera.maxZ,
    });
  }

  function onPicked(res) {
    const ray = pendingRay;
    pendingRay = null;
    if (res || !ray) { showPicked(res); return; }
    // the kd-tree pick missed every point cloud: a splat's bounding box, if the ray hits one
    const box = pickSplatBox(ray);
    if (!box) { showPicked(null); return; }
    Specto.send("select", { item: box.id, position: box.position });
  }

  function pickSplatBox(ray) {
    let best = null;
    for (const id of order) {
      const n = nodes[id];
      if (!n.mesh || !n.enabled || n.entry.kind !== "splat") continue;
      const b = n.mesh.getHierarchyBoundingVectors(true);
      const box = new BABYLON.BoundingBox(b.min, b.max);
      const t = ray.intersectsBox(box, 0) ? rayBoxEntry(ray, b.min, b.max) : null;
      if (t !== null && (best === null || t < best.t)) best = { id: id, t: t, position: ray.origin.add(ray.direction.scale(t)).asArray() };
    }
    return best;
  }

  function rayBoxEntry(ray, min, max) {
    let t0 = 0, t1 = Infinity;
    const o = ray.origin.asArray(), d = ray.direction.asArray(), lo = min.asArray(), hi = max.asArray();
    for (let i = 0; i < 3; i++) {
      if (Math.abs(d[i]) < 1e-12) { if (o[i] < lo[i] || o[i] > hi[i]) return null; continue; }
      let a = (lo[i] - o[i]) / d[i], b = (hi[i] - o[i]) / d[i];
      if (a > b) { const c = a; a = b; b = c; }
      t0 = Math.max(t0, a); t1 = Math.min(t1, b);
      if (t1 < t0) return null;
    }
    return t0;
  }

  function ownerOf(mesh) {
    let m = mesh;
    while (m) {
      if (m.metadata && m.metadata.entry) return m.metadata.entry;
      m = m.parent;
    }
    return null;
  }

  function showPicked(res) {
    picked = res || null;
    // the page's own pick is the selection F frames (a miss clears it)
    current = picked ? {
      keys: [picked.index !== null && picked.index !== undefined ? picked.item + "#" + picked.index : picked.item],
      position: picked.position || null,
    } : null;
    if (picked && picked.position) {
      marker.position = BABYLON.Vector3.FromArray(picked.position);
      marker.setEnabled(true);
    } else {
      marker.setEnabled(false);
    }
    updateCount();
    requestRender();
    report();
  }

  function sizeMarker() {
    if (!marker.isEnabled()) return;
    const d = BABYLON.Vector3.Distance(camera.position, marker.position);
    const px = 10 * 2 * Math.tan(camera.fov / 2) / (engine.getRenderHeight() || 1);
    marker.scaling.setAll(Math.max(1e-4, d * px));
  }

  function applySelection(sel) {
    selectedKeys = (sel && sel.keys) || [];
    const items = {};
    selectedKeys.forEach(function (k) { items[k.split("#")[0]] = true; });
    for (const id of order) {
      const n = nodes[id];
      if (n.li) n.li.classList.toggle("selected", !!items[id]);
      if (n.mesh && n.mesh.renderOverlay !== undefined && n.entry.kind !== "pointcloud") {
        n.mesh.renderOverlay = !!items[id];
        n.mesh.overlayColor = BABYLON.Color3.FromHexString(cssVar("--color-accent", "#2f6fab"));
        n.mesh.overlayAlpha = 0.25;
      }
    }
    // a point selection from outside: mark the point when its cloud is loaded
    const pt = selectedKeys.map(function (k) { return k.split("#"); }).find(function (p) { return p.length === 2 && nodes[p[0]] && nodes[p[0]].positions; });
    if (pt) {
      const n = nodes[pt[0]], i = parseInt(pt[1], 10) * 3;
      if (i + 2 < n.positions.length) {
        marker.position = new BABYLON.Vector3(n.positions[i], n.positions[i + 1], n.positions[i + 2]);
        marker.setEnabled(true);
      }
    }
    requestRender();
    report();
  }

  /* ---- loading ------------------------------------------------------------------------------ */
  function setLoading(delta) {
    loading += delta;
    $("loading").classList.toggle("hidden", loading <= 0);
    $("loading").textContent = loading > 0 ? "loading " + loading + " item" + (loading > 1 ? "s" : "") : "";
  }

  async function loadScene(payload) {
    const seq = ++loadSeq;
    if (payload.environments) environments = payload.environments;
    if (typeof payload.mesh_scale === "number") meshScale = payload.mesh_scale;
    params = Object.assign({}, params, payload.params || {});
    applyParams();
    const wanted = {};
    (payload.entries || []).forEach(function (e) { wanted[e.id] = e; });
    // drop what left the collection
    for (const id of Object.keys(nodes)) {
      if (!wanted[id] || wanted[id].url !== nodes[id].entry.url) {
        // the shared collision tint is not the item's to dispose: its own materials go back first
        itemMeshes(nodes[id]).forEach(function (m) { if (m._spectoMat !== undefined) m.material = m._spectoMat; });
        if (nodes[id].mesh) nodes[id].mesh.dispose(false, true);
        delete nodes[id];
      }
    }
    order = (payload.entries || []).map(function (e) { return e.id; });
    generation = payload.generation;
    if (payload.camera) originCamera = payload.camera;
    const jobs = [];
    for (const e of payload.entries || []) {
      if (nodes[e.id]) { nodes[e.id].entry = e; continue; }
      nodes[e.id] = { entry: e, mesh: null, enabled: true, li: null, positions: null };
      if (e.error || !e.url) continue;
      jobs.push(loadEntry(nodes[e.id]));
    }
    renderList();
    updateCount();
    setError("");
    if (!order.length) { Specto.setHealth("paused", "no 3D items"); report(); return; }
    Specto.setHealth("loading", "loading " + jobs.length + " items");
    setLoading(jobs.length);
    const results = await Promise.allSettled(jobs);
    setLoading(-jobs.length);
    if (seq !== loadSeq) return;   // a newer collection landed meanwhile: it owns the page now
    const failed = results.filter(function (r) { return r.status === "rejected"; });
    if (failed.length) {
      const msg = failed.map(function (r) { return String(r.reason && r.reason.message || r.reason); }).join("; ");
      setError(msg);
      Specto.setHealth("error", msg);
    } else {
      Specto.setHealth("ok", order.length + " items" + (mtlWarning ? " · " + mtlWarning : ""));
    }
    renderList();
    updateCount();
    if (pendingFit) {
      // the first look: the host's camera at the world origin (frame all is F's)
      pendingFit = false;
      if (payload.camera) { applyCamera(payload.camera); scheduleCameraEmit(); } else { fitAll(); }
    }
    applySelection({ keys: selectedKeys });
    requestRender();
    report();
  }

  async function loadEntry(node) {
    const e = node.entry;
    try {
      const mesh = e.kind === "pointcloud" ? await loadPoints(node) : await loadWithBabylon(node);
      if (nodes[e.id] !== node) {
        // the collection changed while this loaded: the item left the scene (or was
        // replaced); Babylon registered the mesh on creation, so take it out again
        mesh.dispose(false, true);
        return;
      }
      node.mesh = mesh;
      node.mesh.parent = declaredRoots[e.up] || root;
      applyUnit(node);
      node.mesh.metadata = Object.assign({}, node.mesh.metadata || {}, { entry: e.id, kind: e.kind });
      node.mesh.getChildMeshes().forEach(function (m) {
        m.metadata = Object.assign({}, m.metadata || {}, { entry: e.id, kind: e.kind, collision: e.kind === "mesh" && COLLISION_NAME.test(m.name || "") });
      });
      node.mesh.setEnabled(node.enabled);
      applyViewMode();
      requestRender();
    } catch (err) {
      node.error = String(err && err.message || err);
      Specto.log("error", "load " + e.label + ": " + node.error);
      throw new Error(e.label + ": " + node.error);
    }
  }

  async function loadPoints(node) {
    const e = node.entry;
    const resp = await fetch(e.url);
    if (!resp.ok) throw new Error("fetch " + e.url + " -> " + resp.status);
    const buf = await resp.arrayBuffer();
    const n = Math.floor(buf.byteLength / 16);
    if (e.count && n !== e.count) Specto.log("warn", e.label + ": " + n + " points in buffer, " + e.count + " expected");
    const pos = new Float32Array(buf, 0, n * 3);
    const col = new Uint8Array(buf, n * 12, n * 4);
    const mesh = new BABYLON.Mesh("pts:" + e.id, scene);
    // positions as float32, colours as normalized uint8: 16 B/point on the GPU
    mesh.setVerticesBuffer(new BABYLON.VertexBuffer(engine, pos, BABYLON.VertexBuffer.PositionKind, false, false, 3, false, 0, 3, BABYLON.VertexBuffer.FLOAT, false));
    mesh.setVerticesBuffer(new BABYLON.VertexBuffer(engine, col, BABYLON.VertexBuffer.ColorKind, false, false, 4, false, 0, 4, BABYLON.VertexBuffer.UNSIGNED_BYTE, true));
    mesh.isUnIndexed = true;
    mesh.isPickable = false;
    const b = e.bounds || [[0, 0, 0], [0, 0, 0]];
    mesh.setBoundingInfo(new BABYLON.BoundingInfo(BABYLON.Vector3.FromArray(b[0]), BABYLON.Vector3.FromArray(b[1])));
    mesh.alwaysSelectAsActiveMesh = true;
    const mat = new BABYLON.StandardMaterial("ptsMat:" + e.id, scene);
    mat.disableLighting = true;
    mat.emissiveColor = BABYLON.Color3.White();
    mat.pointsCloud = true;
    mat.pointSize = params.point_size || 2;
    mesh.material = mat;
    mesh.useVertexColors = true;
    node.positions = pos;   // kept for marking a point selected from outside
    return mesh;
  }

  async function loadWithBabylon(node) {
    const e = node.entry;
    const slash = e.url.lastIndexOf("/");
    const rootUrl = e.url.slice(0, slash + 1);
    const file = e.url.slice(slash + 1);
    const options = {
      pluginExtension: e.ext || undefined,
      pluginOptions: {
        // v4+ (NGSP) .spz needs the WASM decoder; Babylon's default is unpkg (#66)
        splat: { spzLibraryUrl: location.origin + "/vendor/babylon/spz.js", fflate: window.fflate, deflateURL: location.origin + "/vendor/babylon/fflate.js" },
      },
    };
    options.rootUrl = rootUrl;
    const res = await BABYLON.ImportMeshAsync(file, scene, options);
    const holder = new BABYLON.TransformNode("item:" + e.id, scene);
    let plain = null;   // the item's own: it goes with the item's meshes when they are disposed
    res.meshes.forEach(function (m) {
      if (!m.parent) m.parent = holder;
      if (m.material && m.material.pointsCloud) m.material.pointSize = params.point_size || 2;
      // an STL, or an OBJ without its MTL, comes with no material: a mid grey, not
      // Babylon's white default that vanishes on the light background
      if (!m.material && m.getTotalVertices() > 0) m.material = plain = plain || plainMaterial(e.id);
    });
    holder.metadata = { babylon: res.meshes.length };
    return holder;
  }

  // a texture map's options and how many values each takes (the MTL format's); resolve
  // checks the same names (_MAP_OPTIONS in specto_readers/scene3d.py): change both together
  const MAP_OPTIONS = { "-blendu": 1, "-blendv": 1, "-bm": 1, "-boost": 1, "-cc": 1, "-clamp": 1, "-imfchan": 1, "-mm": 2, "-o": 3, "-s": 3, "-t": 3, "-texres": 1, "-type": 1 };
  function mapFileName(value) {
    if (typeof value !== "string") return value;
    const words = value.trim().split(/\s+/);
    let i = 0;
    while (i < words.length && MAP_OPTIONS[words[i].toLowerCase()]) {
      const n = MAP_OPTIONS[words[i].toLowerCase()];
      i += 1;
      for (let taken = 0; taken < n && i < words.length; taken++) {
        if (taken > 0 && !isFinite(parseFloat(words[i]))) break;
        i += 1;
      }
    }
    return words.slice(i).join(" ");
  }

  function plainMaterial(id) {
    const mat = new BABYLON.StandardMaterial("plainMat:" + id, scene);
    mat.diffuseColor = BABYLON.Color3.FromHexString(PLAIN_MESH);
    mat.specularColor = new BABYLON.Color3(0.15, 0.15, 0.15);
    return mat;
  }

  /** An STL or OBJ item at the view's mesh_unit (its file says nothing). */
  function applyUnit(node) {
    if (!node.mesh || UNITLESS.indexOf(node.entry.ext) < 0) return;
    node.mesh.scaling = new BABYLON.Vector3(meshScale, meshScale, meshScale);
  }

  function applyParams() {
    if (!root) return;
    Object.keys(nodes).forEach(function (id) { applyUnit(nodes[id]); });
    // z-up data (LiDAR, most photogrammetry) is turned to Babylon's y-up at the root
    root.rotation = params.up === "z" ? new BABYLON.Vector3(-Math.PI / 2, 0, 0) : BABYLON.Vector3.Zero();
    const ps = params.point_size || 2;
    scene.materials.forEach(function (m) { if (m.pointsCloud) m.pointSize = ps; });
    applySpeed();
    applyViewMode();
    SpectoSidePanel.restore(params.panels);   // the kept collapse and widths, quietly
    requestRender();
  }

  /* ---- view modes (#153): lit, unlit, wireframe, and collision when the content has it ------ */
  const VIEW_MODES = ["lit", "unlit", "wireframe", "collision"];
  const MODE_LABELS = { lit: "Lit", unlit: "Unlit", wireframe: "Wireframe", collision: "Collision" };
  // Unreal's collision naming (UCX_ convex, UBX_ box, USP_ sphere, UCP_ capsule): the
  // collision data a glTF or PLY item can carry; such meshes only show in collision mode
  const COLLISION_NAME = /^(UCX|UBX|USP|UCP)_/i;
  const GHOST = 0.15;          // what the rest of the scene shows of itself under collision
  const WIRE_BG = [0.11, 0.115, 0.125];   // wireframe draws over a dark background, as UE's
  let collisionMat = null, modeButton = null, modeMenu = null;

  function itemMeshes(n) {
    if (!n.mesh) return [];
    return [n.mesh].concat(n.mesh.getChildMeshes()).filter(function (m) { return "isVisible" in m; });
  }
  function hasCollision() {
    return order.some(function (id) { return itemMeshes(nodes[id]).some(function (m) { return m.metadata && m.metadata.collision; }); });
  }
  function viewModes() { return hasCollision() ? VIEW_MODES.slice() : VIEW_MODES.slice(0, 3); }
  /** The view param `view_mode` when offered, else lit. */
  function viewMode() { return viewModes().indexOf(params.view_mode) >= 0 ? params.view_mode : "lit"; }

  function collisionMaterial() {
    if (!collisionMat) {
      collisionMat = new BABYLON.StandardMaterial("collisionMat", scene);
      collisionMat.disableLighting = true;
      collisionMat.emissiveColor = BABYLON.Color3.FromHexString(cssVar("--color-collision", "#4fbf6a"));
      collisionMat.alpha = 0.6;
      collisionMat.backFaceCulling = false;
    }
    return collisionMat;
  }

  /** A mesh material in a mode, from what it was when first seen (lit): wireframe on the
   * mesh materials, unlit as PBR's `unlit` (albedo only) or a standard material's base
   * colour without lighting. Points (already unlit) and splats keep theirs. */
  function materialMode(mat, mode) {
    if (!mat || mat.pointsCloud || mat === collisionMat) return;
    const lit = mat._spectoLit || (mat._spectoLit = {
      wireframe: !!mat.wireframe, unlit: !!mat.unlit, disableLighting: !!mat.disableLighting, emissiveColor: mat.emissiveColor,
    });
    const flat = mode === "unlit";
    mat.wireframe = mode === "wireframe" || lit.wireframe;
    if (/PBR/.test(mat.getClassName ? mat.getClassName() : "")) {
      mat.unlit = flat || lit.unlit;
    } else if ("disableLighting" in mat) {
      mat.disableLighting = flat || lit.disableLighting;
      mat.emissiveColor = flat && mat.diffuseColor ? mat.diffuseColor : lit.emissiveColor;
    }
  }

  function applyViewMode() {
    if (!scene) return;
    const mode = viewMode();
    order.forEach(function (id) {
      const n = nodes[id];
      itemMeshes(n).forEach(function (m) {
        if (n.entry.kind !== "mesh") {   // points and splats: as they are, gone under collision
          m.isVisible = mode !== "collision";
          return;
        }
        if (m._spectoMat === undefined) m._spectoMat = m.material;
        const col = !!(m.metadata && m.metadata.collision);
        m.isVisible = col ? mode === "collision" : true;
        m.material = col && mode === "collision" ? collisionMaterial() : m._spectoMat;
        m.visibility = mode === "collision" && !col ? GHOST : 1;
        materialMode(m._spectoMat, mode === "collision" ? "lit" : mode);
      });
    });
    scene.clearColor = mode === "wireframe"
      ? new BABYLON.Color4(WIRE_BG[0], WIRE_BG[1], WIRE_BG[2], 1)
      : color4(cssVar("--color-surface-alt", "#faf7f1"));
    if (modeButton) {
      // always in sight (#158); a mode other than lit in accent
      modeButton.textContent = MODE_LABELS[mode];
      modeButton.classList.toggle("on", mode !== "lit");
    }
    applyEnvironment();   // wireframe hides the environment's background
    requestRender();
  }
  /** A user's pick: shown now, kept (one user submit) in the view param. */
  function setViewMode(mode) {
    if (mode === viewMode()) return;
    params = Object.assign({}, params, { view_mode: mode });
    applyViewMode(); report();
    Specto.setParams({ view_mode: mode });
  }
  function openModeMenu() {
    modeMenu.textContent = "";
    viewModes().forEach(function (m) {
      const b = document.createElement("button");
      b.className = "mode-item" + (m === viewMode() ? " on" : "");
      b.setAttribute("data-mode", m);
      b.textContent = MODE_LABELS[m];
      b.addEventListener("click", function () { setViewMode(m); closeModeMenu(); });
      modeMenu.appendChild(b);
    });
    closeEnvMenu();
    modeMenu.classList.remove("hidden");
    modeButton.classList.add("open");
    report();
  }
  function closeModeMenu() {
    if (modeMenu.classList.contains("hidden")) return;
    modeMenu.classList.add("hidden");
    modeButton.classList.remove("open");
    report();
  }

  /* ---- HDRI environments (#175) ------------------------------------------------------------ */
  const ENV_NONE = "none";
  const ENV_INTENSITY_MAX = 4;
  const HDR_CUBE = 256;        // a raw .hdr (build.py could not bake it) filters to this per face
  let environments = [];       // the scene payload's list: the footer menu's order
  const envTextures = {};      // name -> {tex, ready, error, ms}: loaded once, kept
  let skybox = null, skyboxFor = null;
  let envButton = null, envMenu = null;
  let envIntensityInput = null, envIntensityValue = null, envRotationInput = null, envRotationValue = null, envBackgroundBox = null;

  function envEntry(name) { return environments.find(function (e) { return e.name === name; }) || null; }
  /** The view param `environment` when it names a bundled one, else none. */
  function environmentName() {
    const n = params.environment;
    return n && n !== ENV_NONE && envEntry(n) ? n : ENV_NONE;
  }
  function envIntensity() {
    const v = Number(params.environment_intensity);
    return isFinite(v) && v >= 0 ? Math.min(ENV_INTENSITY_MAX, v) : 1;
  }
  function envRotation() {
    const v = Number(params.environment_rotation);
    return isFinite(v) ? ((v % 360) + 360) % 360 : 0;
  }
  function envState() { const n = environmentName(); return n === ENV_NONE ? null : envTextures[n] || null; }

  /** The environment's texture, loading on first use: a prefiltered .env as it is, a
   * .hdr filtered here (slower: build.py bakes .env where it can). */
  function envTexture(e) {
    if (envTextures[e.name]) return envTextures[e.name];
    const t = envTextures[e.name] = { tex: null, ready: false, error: null, ms: null };
    const t0 = performance.now();
    const done = function () {
      t.ready = true;
      t.ms = Math.round(performance.now() - t0);
      applyEnvironment(); report();
    };
    const fail = function (msg, ex) {
      t.error = String(msg || (ex && ex.message) || "unreadable");
      setError(e.label + " environment: " + t.error);
      syncEnvControls(); report();
    };
    if (/\.hdr$/i.test(e.url)) {
      // size, mipmaps, harmonics, linear, prefiltered on load
      t.tex = new BABYLON.HDRCubeTexture(e.url, scene, HDR_CUBE, false, true, false, true, done, fail);
    } else {
      t.tex = new BABYLON.CubeTexture(e.url, scene, null, false, null, done, fail, undefined, true, ".env");
    }
    t.tex.name = "env:" + e.name;
    return t;
  }

  /** The environment params onto the scene: IBL, intensity and rotation; the lights off
   * once it has loaded (the look before #175 under none and while it loads); the
   * background drawn behind everything except in wireframe. */
  function applyEnvironment() {
    if (!scene) return;
    const name = environmentName();
    const e = envEntry(name);
    let t = null;
    if (e && e.url) t = envTexture(e);
    else if (e) setError(e.label + " environment is not vendored; run packages/specto-web/build.py --hdri-only");
    const on = !!(t && t.ready);
    scene.environmentTexture = on ? t.tex : null;
    scene.environmentIntensity = envIntensity();
    if (on) t.tex.rotationY = envRotation() * Math.PI / 180;
    lights.forEach(function (l) { l.setEnabled(!on); });
    const sky = on && params.environment_background === true && viewMode() !== "wireframe";
    if (sky && skyboxFor !== name) {
      if (skybox) skybox.dispose(false, true);
      // a unit box at infinite distance, sized to the far plane each frame (sizeSkybox),
      // drawn first without depth so the scene always covers it
      skybox = scene.createDefaultSkybox(t.tex, true, 1, 0, false);
      skybox.isPickable = false;
      skybox.material.disableDepthWrite = true;
      skyboxFor = name;
    }
    if (skybox) {
      skybox.setEnabled(sky);
      if (sky && skybox.material.reflectionTexture) skybox.material.reflectionTexture.rotationY = t.tex.rotationY;
    }
    syncEnvControls();
    requestRender();
  }
  function sizeSkybox() {
    if (skybox && skybox.isEnabled()) skybox.scaling.setAll(camera.maxZ);   // half-diagonal 0.87 x far
  }
  function skyboxFirst(a, b) {
    return (b.getMesh() === skybox ? 1 : 0) - (a.getMesh() === skybox ? 1 : 0);
  }

  /** A user's pick or slider: shown now, kept (one user submit) in the view params. */
  function setEnvironmentParams(patch, keep) {
    params = Object.assign({}, params, patch);
    applyEnvironment(); report();
    if (keep) Specto.setParams(patch);
  }
  function envLabel(name) { const e = envEntry(name); return e ? e.label : "No HDRI"; }
  function syncEnvControls() {
    const name = environmentName(), off = name === ENV_NONE, t = envState();
    if (envButton) {
      envButton.textContent = envLabel(name) + (t && !t.ready && !t.error ? "…" : "");
      envButton.classList.toggle("on", !off);
    }
    if (envIntensityInput) {
      envIntensityInput.value = String(envIntensity());
      envIntensityValue.textContent = envIntensity().toFixed(2) + "x";
      envRotationInput.value = String(envRotation());
      envRotationValue.textContent = Math.round(envRotation()) + "°";
      envBackgroundBox.checked = params.environment_background === true;
      [envIntensityInput, envRotationInput, envBackgroundBox].forEach(function (c) { c.disabled = off; });
    }
  }
  function openEnvMenu() {
    closeModeMenu();
    envMenu.textContent = "";
    [{ name: ENV_NONE, label: "None (lights)", look: "the two lights, no HDRI", url: true }].concat(environments).forEach(function (e) {
      const b = document.createElement("button");
      b.className = "mode-item" + (e.name === environmentName() ? " on" : "");
      b.setAttribute("data-env", e.name);
      b.textContent = e.label;
      b.title = (e.look || "") + (e.author ? "\n" + e.author + ", Poly Haven (CC0)" : "") + (e.url ? "" : "\nnot vendored: run build.py --hdri-only");
      b.disabled = !e.url;
      b.addEventListener("click", function () { setEnvironmentParams({ environment: e.name }, true); closeEnvMenu(); });
      envMenu.appendChild(b);
    });
    // right-aligned with its pill
    if (envButton.getBoundingClientRect && window.innerWidth) {
      envMenu.style.right = Math.max(0, window.innerWidth - envButton.getBoundingClientRect().right) + "px";
    }
    envMenu.classList.remove("hidden");
    envButton.classList.add("open");
    report();
  }
  function closeEnvMenu() {
    if (!envMenu || envMenu.classList.contains("hidden")) return;
    envMenu.classList.add("hidden");
    envButton.classList.remove("open");
    report();
  }
  function envReport() {
    const t = envState();
    return {
      name: environmentName(),
      ready: !!(t && t.ready),
      error: t ? t.error : null,
      load_ms: t ? t.ms : null,
      intensity: envIntensity(),
      rotation: envRotation(),
      background: !!(skybox && skybox.isEnabled()),
      lights: lights.length ? lights[0].isEnabled() : true,
    };
  }

  /* ---- the footer and the ITEMS panel ------------------------------------------------------ */
  function pickedText() {
    if (!picked) return "";
    const n = nodes[picked.item];
    let t = (n ? n.entry.label : picked.item) + (picked.index !== null && picked.index !== undefined ? " #" + picked.index : "");
    if (picked.position) t += " at " + picked.position.map(function (v) { return v.toFixed(2); }).join(", ");
    return t;
  }
  function updateCount() {
    const pts = order.reduce(function (a, id) { return a + ((nodes[id].entry.kind === "pointcloud" && nodes[id].entry.count) || 0); }, 0);
    const count = order.length ? order.length + " item" + (order.length > 1 ? "s" : "") : "no items";
    const p = pickedText();
    caption.textContent = count + (p ? " \u00b7 " + p : "");
    caption.title = pts ? fmt(pts) + " points" : "";
  }
  function fmt(n) { return n >= 1e6 ? (n / 1e6).toFixed(1) + " M" : n >= 1e3 ? (n / 1e3).toFixed(0) + " k" : String(n); }

  function setEnabled(n, on) {
    n.enabled = on;
    if (n.mesh) n.mesh.setEnabled(on);
    if (n.li) {
      n.li.classList.toggle("off", !on);
      n.box.classList.toggle("on", on);
    }
  }
  function showAll(on) {
    order.forEach(function (id) { setEnabled(nodes[id], on); });
    requestRender(); report();
  }

  function frameItem(n) {
    if (!n.mesh) return;
    const b = n.mesh.getHierarchyBoundingVectors(true);
    frameBox(b.min, b.max);
  }

  function renderList() {
    const list = itemsPanel.body;
    list.textContent = "";
    order.forEach(function (id) {
      const n = nodes[id], e = n.entry;
      const err = e.error || n.error;
      const what = (e.kind === "pointcloud" ? "points" : e.kind) + (e.count ? ", " + fmt(e.count) + (e.kind === "pointcloud" ? " points" : "") : "");
      // a row: the check shows or hides the item, the rest picks it, a double-click frames it
      const row = document.createElement("div");
      row.className = "panel-row" + (n.enabled ? "" : " off") + (err ? " broken" : "");
      row.setAttribute("data-item", id);
      row.title = (err || what) + "\ndouble-click to frame it";
      const box = document.createElement("span");
      box.className = "check" + (n.enabled ? " on" : "");
      const text = document.createElement("span");
      text.className = "item-text";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = e.label;
      const sub = document.createElement("span");
      sub.className = "sub";
      sub.textContent = err ? "unreadable" : (e.size_label || "");
      text.append(name, sub);
      row.append(box, text);
      box.addEventListener("click", function (ev) {
        ev.stopPropagation();
        setEnabled(n, !n.enabled);
        requestRender(); report();
      });
      row.addEventListener("click", function () { Specto.send("select", { item: id, position: null }); });
      row.addEventListener("dblclick", function () { frameItem(n); });
      list.appendChild(row);
      n.li = row;
      n.box = box;
    });
    applySelection({ keys: selectedKeys });
  }

  function boundsOf(n) {
    if (!n.mesh) return null;
    const b = n.mesh.getHierarchyBoundingVectors(true);
    return [b.min.asArray().map(function (v) { return +v.toFixed(2); }), b.max.asArray().map(function (v) { return +v.toFixed(2); })];
  }

  function report() {
    const items = {};
    order.forEach(function (id) {
      const n = nodes[id];
      items[id] = {
        rect: Specto.rectOf(n.li), checkbox: Specto.rectOf(n.box),
        kind: n.entry.kind, loaded: !!n.mesh, enabled: n.enabled, error: n.error || n.entry.error || null,
        bounds: boundsOf(n),
      };
    });
    Specto.report({
      camera: camera ? cameraValue() : null,
      picked: picked,
      selection: selectedKeys,
      running: running,
      generation: generation,
      babylon: BABYLON.Engine.Version,
      canvas: Specto.rectOf($("c")),
      speed: cameraSpeed(),
      nav: nav ? nav.state() : null,
      caption: caption.textContent,
      view_mode: viewMode(),
      view_modes: viewModes(),
      environment: envReport(),
      environments: environments.map(function (e) { return e.name; }),
      buttons: {
        frameAll: Specto.rectOf(frameAllButton), origin: Specto.rectOf(originButton), speed: Specto.rectOf(speedInput),
        viewMode: Specto.rectOf(modeButton), environment: Specto.rectOf(envButton),
        envIntensity: Specto.rectOf(envIntensityInput), envRotation: Specto.rectOf(envRotationInput),
        envBackground: Specto.rectOf(envBackgroundBox),
      },
      modes: modeMenu && !modeMenu.classList.contains("hidden")
        ? Object.fromEntries(Array.prototype.map.call(modeMenu.children, function (b) { return [b.getAttribute("data-mode"), Specto.rectOf(b)]; }))
        : null,
      envs: envMenu && !envMenu.classList.contains("hidden")
        ? Object.fromEntries(Array.prototype.map.call(envMenu.children, function (b) { return [b.getAttribute("data-env"), Specto.rectOf(b)]; }))
        : null,
      panels: viewPanel ? { view: viewPanel.rects(), items: itemsPanel.rects() } : null,
      order: order,
      items: items,
    });
  }

  /* ---- bridge ------------------------------------------------------------------------------- */
  Specto.on("config", function (cfg) {
    params = Object.assign({}, params, cfg.params || {});
    applyParams();   // the clear colour too: the theme's, or the wireframe's dark
    report();
  });
  Specto.on("scene", function (p) { loadScene(p).catch(function (e) { setError(String(e)); Specto.setHealth("error", String(e)); }); });
  Specto.on("params", function (p) { params = Object.assign({}, params, p || {}); applyParams(); report(); });
  Specto.on("mesh_scale", function (m) {
    meshScale = m && typeof m.scale === "number" ? m.scale : 1;
    Object.keys(nodes).forEach(function (id) { applyUnit(nodes[id]); });
    requestRender();
    report();
  });
  Specto.on("port:camera", applyCamera);
  Specto.on("port:selection", function (sel) {
    const ks = (sel && sel.keys) || [];
    current = ks.length ? { keys: ks, position: null } : null;
    applySelection(sel);
    // a point selected from outside: where applySelection put the marker
    if (current && marker.isEnabled() && ks.some(function (k) { return k.indexOf("#") > 0; })) current.position = marker.position.asArray();
  });
  Specto.on("picked", onPicked);
  Specto.on("render", function (r) {
    running = !r || r.running !== false;
    if (running) { lastFrame = 0; requestRender(); }
    report();
  });

  /* ---- the panels and the footer (sidepanel.js, footer.js, icons.js) ------------------------ */
  // the panels keep their own state in the view param `panels` (keep: <name>, one user
  // submit per click or finished drag); the page only lays the canvas out again
  function relayout() {
    if (engine) { engine.resize(); requestRender(); }
    report();
  }

  const footer = SpectoFooter.mount($("footer"), { step: null });
  const caption = document.createElement("span");
  caption.className = "footer-pos";
  caption.id = "count";
  caption.textContent = "no items";
  footer.el.insertBefore(caption, footer.el.firstChild);
  const frameAllButton = footer.tool("target", "Frame the selection, else all (F)", frameSelection, "frameAll");
  // the view mode (#153): a pill with the mode's name before the target, its menu above it
  modeButton = document.createElement("button");
  modeButton.className = "mode-pill";
  modeButton.id = "viewMode";
  modeButton.title = "View mode: lit, unlit, wireframe (collision when the content has collision meshes)";
  modeButton.textContent = MODE_LABELS.lit;
  modeButton.addEventListener("click", function () {
    if (modeMenu.classList.contains("hidden")) openModeMenu(); else closeModeMenu();
  });
  footer.el.insertBefore(modeButton, frameAllButton);
  modeMenu = document.createElement("div");
  modeMenu.className = "mode-menu hidden";
  modeMenu.id = "viewModeMenu";
  // on the body, fixed above the footer: the footer clips what overflows it (#175)
  document.body.appendChild(modeMenu);
  // the environment (#175): a pill with the HDRI's name before the mode pill, its menu
  // above it; accent while one lights the scene
  envButton = document.createElement("button");
  envButton.className = "mode-pill";
  envButton.id = "environment";
  envButton.title = "Environment: image-based lighting from a bundled HDRI (Poly Haven, CC0), or the two lights";
  envButton.textContent = envLabel(ENV_NONE);
  envButton.addEventListener("click", function () {
    if (envMenu.classList.contains("hidden")) openEnvMenu(); else closeEnvMenu();
  });
  footer.el.insertBefore(envButton, modeButton);
  envMenu = document.createElement("div");
  envMenu.className = "mode-menu env-menu hidden";
  envMenu.id = "environmentMenu";
  document.body.appendChild(envMenu);
  document.addEventListener("pointerdown", function (ev) {
    if (!modeMenu.contains(ev.target) && !modeButton.contains(ev.target)) closeModeMenu();
    if (!envMenu.contains(ev.target) && !envButton.contains(ev.target)) closeEnvMenu();
  });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { closeModeMenu(); closeEnvMenu(); } });

  function mountPanels() {
    viewPanel = SpectoSidePanel.mount($("row"), {
      id: "view", title: "View", side: "left", keep: "view", width: 150, minWidth: 110, maxWidth: 320,
      onCollapse: relayout, onResize: relayout,
    });
    const speed = document.createElement("div");
    speed.className = "control";
    const label = document.createElement("span");
    label.className = "control-label";
    label.textContent = "Camera speed";
    const row = document.createElement("div");
    row.className = "speed-row";
    speedInput = document.createElement("input");
    speedInput.type = "range";
    speedInput.min = String(SPEED_MIN); speedInput.max = String(SPEED_MAX); speedInput.step = "0.25";
    speedInput.title = "how fast the camera flies, walks, pans and dollies (the wheel sets it while the right button is held)";
    speedValue = document.createElement("span");
    speedValue.className = "speed-value";
    // live while dragging, kept (one user submit) when let go
    speedInput.addEventListener("input", function () {
      params = Object.assign({}, params, { camera_speed: Math.pow(2, Number(speedInput.value)) });
      applySpeed(); report();
    });
    speedInput.addEventListener("change", function () { Specto.setParams({ camera_speed: cameraSpeed() }); });
    row.append(speedInput, speedValue);
    speed.append(label, row);
    originButton = document.createElement("button");
    originButton.className = "origin";
    originButton.id = "origin";
    originButton.title = "back to the first look: the world origin, everything in sight";
    originButton.appendChild(SpectoIcons.svg("home"));
    const word = document.createElement("span");
    word.textContent = "World origin";
    originButton.appendChild(word);
    originButton.addEventListener("click", goToOrigin);
    viewPanel.body.append(speed, originButton, environmentControls());

    itemsPanel = SpectoSidePanel.mount($("row"), {
      id: "items", title: "Items", side: "right", keep: "items", width: 180, minWidth: 120, maxWidth: 480,
      allNone: true, onAll: function () { showAll(true); }, onNone: function () { showAll(false); },
      onCollapse: relayout, onResize: relayout,
    });
    applySpeed();
  }

  /** The VIEW panel's environment group (#175): intensity and rotation sliders (live while
   * dragging, kept when let go) and the background toggle; off while no HDRI is on. */
  function environmentControls() {
    const group = document.createElement("div");
    group.className = "control env-controls";
    const title = document.createElement("span");
    title.className = "control-label";
    title.textContent = "Environment";
    group.appendChild(title);
    function slider(label, min, max, step, key, tip) {
      // the name and the value over a full-width slider: the panel is narrow
      const row = document.createElement("div");
      row.className = "env-row";
      const head = document.createElement("div");
      head.className = "env-head";
      const name = document.createElement("span");
      name.className = "env-name";
      name.textContent = label;
      const input = document.createElement("input");
      input.type = "range";
      input.min = String(min); input.max = String(max); input.step = String(step);
      input.title = tip;
      const value = document.createElement("span");
      value.className = "speed-value";
      const patch = function () { const p = {}; p[key] = Number(input.value); return p; };
      input.addEventListener("input", function () { setEnvironmentParams(patch(), false); });
      input.addEventListener("change", function () { setEnvironmentParams(patch(), true); });
      head.append(name, value);
      row.append(head, input);
      group.appendChild(row);
      return [input, value];
    }
    [envIntensityInput, envIntensityValue] = slider("Intensity", 0, ENV_INTENSITY_MAX, 0.05, "environment_intensity", "how strongly the HDRI lights the scene");
    [envRotationInput, envRotationValue] = slider("Rotation", 0, 360, 5, "environment_rotation", "turns the HDRI about the up axis (degrees)");
    const check = document.createElement("label");
    check.className = "env-check";
    check.title = "draw the HDRI behind the scene instead of the plain background";
    envBackgroundBox = document.createElement("input");
    envBackgroundBox.type = "checkbox";
    envBackgroundBox.id = "environmentBackground";
    envBackgroundBox.addEventListener("change", function () { setEnvironmentParams({ environment_background: !!envBackgroundBox.checked }, true); });
    const word = document.createElement("span");
    word.textContent = "Show as background";
    check.append(envBackgroundBox, word);
    group.appendChild(check);
    return group;
  }

  boot();
  mountPanels();
  syncEnvControls();
  Specto.connect().catch(function (e) {
    setError(e.message);
    console.error(e);
  });
})();
