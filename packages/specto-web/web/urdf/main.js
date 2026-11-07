/* URDF view (SPEC-43.2): one robot in Babylon.js 9.31, beside the 3d view and on its
 * modules (backlog/docs/urdf-view-design.md sections 4, 5, 10 and 11).
 *
 * Python parses the URDF and sends `model {generation, model, environments}` (section 11's
 * shape; environments as the 3d view's); the page builds the robot and does the forward
 * kinematics (kin.js): a TransformNode per link, under the joint origin that holds it,
 * the joint's motion on the child link's node. `pose {joints, source, track?}` sets the
 * joint values (mimic joints follow their masters); `params` the view's params;
 * `port:camera` and `port:selection` move the camera and the selection without an echo.
 *
 * Geometry: box, cylinder and sphere through MeshBuilder (URDF's cylinder stands on Z);
 * meshes through Babylon's loaders by `ext` (stl, obj, glb, gltf), each file loaded once
 * and cloned per use (the clones share its geometry); collision geometry built the first
 * time a collision mode shows. A mesh that is missing (url null) or fails to load draws a
 * placeholder: the link's collision primitive when it has one, else a 5 cm magenta wire
 * box at the visual's origin. STL keeps its file coordinates (the robot's frame).
 *
 * View modes (`view_mode`): lit, unlit, wireframe, collision (visuals hidden, each link's
 * colliders flat in its own tint, the usd view's TINTS; hovering one shows "link ·
 * shape") and visual_collision (visuals lit, colliders as 35 % translucent shells with
 * wire edges). Environment: the two lights, or a bundled HDRI with intensity, rotation
 * and background (the 3d view's params); a ground grid (`show_grid`, on by default).
 * Overlays: link frames, joint axes, centre of mass and inertia boxes (`show_frames`,
 * `show_joint_axes`, `show_com`, `show_inertia`: the solid box with the link's mass and
 * principal moments, on the principal axes). `up` (x, y, z, each negatable; default z, REP 103) turns
 * the robot's up onto Babylon's +Y at the root; navigation is viewnav.js's, free or orbit
 * (`camera_mode`).
 *
 * Chrome: the VIEW panel (left: camera, environment, overlays, up axis, Reset pose) and
 * the ROBOT panel (right: search, the link-under-joint tree, JOINTS sliders in degrees or
 * millimetres, PROPERTIES), both the side panel standard kept in `panels`; the footer
 * steps through the collection and holds the frame target, the robot's name, the view
 * mode pill and where the joint values come from.
 *
 * To Python: `select {link, joint, add}`, `joints {name: value}` (every actuated joint, on
 * a slider's release, a typed value or a reset: one user submit), `frame {link}` (a
 * double-click), `loaded {ms, meshes, failed}`; the camera as a port emit (throttled);
 * view params through Specto.setParams (one user submit each), the selected links in
 * `selected_links` among them. The host owns the health dot: the page only says
 * "loading" while its meshes load and reports what its loaders could not read in
 * `loaded`, which the host folds into its notes. */
(function () {
  "use strict";
  const $ = function (id) { return document.getElementById(id); };
  const K = window.SpectoUrdfKin;
  const CAMERA_EMIT_MS = 120;
  const IDLE_FRAMES = 12;
  const SPEED_MIN = -3, SPEED_MAX = 3;
  const PLACEHOLDER = 0.05;            // m: the missing mesh's wire box
  const SHELL_ALPHA = 0.35;            // visual_collision's translucent colliders
  const WIRE_BG = [0.11, 0.115, 0.125];
  // the usd view's collision tints (specto_app/usd/collision.py TINTS), one per link in turn
  const TINTS = [[0.20, 0.80, 0.45], [0.25, 0.65, 0.95], [0.95, 0.70, 0.20], [0.85, 0.40, 0.85], [0.30, 0.85, 0.85], [0.90, 0.45, 0.35]];
  const VIEW_MODES = ["lit", "unlit", "wireframe", "collision", "visual_collision"];
  const MODE_LABELS = { lit: "Lit", unlit: "Unlit", wireframe: "Wireframe", collision: "Collision", visual_collision: "Visual + collision" };
  const ENV_NONE = "none";
  const ENV_INTENSITY_MAX = 4;
  const HDR_CUBE = 256;
  const DEFAULTS = {
    view_mode: "lit", up: "z", camera_speed: 1, camera_mode: "free",
    environment: ENV_NONE, environment_intensity: 1, environment_background: false, environment_rotation: 0,
    show_grid: true, show_frames: false, show_joint_axes: false, show_com: false, show_inertia: false, selected_links: [],
  };

  let engine = null, scene = null, camera = null, world = null, lights = [];
  let params = Object.assign({}, DEFAULTS);
  let running = true, dirty = true, idle = IDLE_FRAMES, lastFrame = 0;
  let nav = null;
  let cameraTimer = 0, cameraDirty = false, applyingCamera = false;
  let originCamera = null, cameraFromPort = false, fittedFor = null;

  /* the robot: what the model message built */
  let model = null, generation = -1, tree = [];
  let robot = null;                    // the TransformNode under `world` the robot hangs from
  const links = {};                    // name -> {link, node, index, visuals, collisions, broken}
  const joints = {};                   // name -> {joint, origin}
  let values = {};                     // actuated joint values (radians, metres)
  let source = "zero", track = null;   // where they come from
  let reach = 1;                       // the robot's size at the zero pose (m), for overlays
  let loading = 0, loadStarted = 0, loadSeq = 0;
  let collisionsBuilt = false;
  const failures = [];                 // [{link, kind, url, error}] from Babylon's loaders
  const templates = {};                // mesh url -> {promise, holder, error}
  const materials = {};                // colour key -> material, shared by every visual using it
  let tintFlat = [], tintShell = [];
  let placeholderMat = null, gridLines = [], overlays = { frames: [], axes: {}, com: [], inertia: [] };

  /* selection: [{kind: "link" | "joint", name}] */
  let selection = [];

  function cssVar(name, fallback) {
    return (getComputedStyle(document.documentElement).getPropertyValue(name) || fallback).trim();
  }
  function color4(hex, a) {
    const c = BABYLON.Color3.FromHexString(hex);
    return new BABYLON.Color4(c.r, c.g, c.b, a === undefined ? 1 : a);
  }
  function v3(a) { return new BABYLON.Vector3(a[0], a[1], a[2]); }
  function quat(q) { return new BABYLON.Quaternion(q[0], q[1], q[2], q[3]); }
  function setLocal(node, t) {
    node.position = v3(t.p);
    node.rotationQuaternion = quat(t.q);
  }
  function requestRender() { dirty = true; idle = IDLE_FRAMES; }
  /** A number for the panels: n decimals (3), trailing zeros dropped, never "-0". */
  function fmt(v, n) {
    const d = n === undefined ? 3 : n;
    const t = (Math.abs(v) < 0.5 * Math.pow(10, -d) ? 0 : Number(v)).toFixed(d);
    return t.indexOf(".") >= 0 ? t.replace(/0+$/, "").replace(/\.$/, "") : t;
  }
  function vec(a, n) { return (a || []).map(function (x) { return fmt(Number(x), n); }).join(" "); }

  /* ---- engine ------------------------------------------------------------------------- */
  function boot() {
    const canvas = $("c");
    BABYLON.Tools.ScriptBaseUrl = location.origin + "/vendor/babylon/";
    // a URDF mesh is in its link's frame: STL keeps its file coordinates (Babylon would
    // swap Y and Z for its own Y-up); the swapped winding is why visuals draw both sides
    if (BABYLON.STLFileLoader) BABYLON.STLFileLoader.DO_NOT_ALTER_FILE_COORDINATES = true;
    engine = new BABYLON.Engine(canvas, true, { preserveDrawingBuffer: false, stencil: true, antialias: true, premultipliedAlpha: false });
    scene = new BABYLON.Scene(engine);
    scene.useRightHandedSystem = true;
    scene.clearColor = color4(cssVar("--color-surface-alt", "#faf7f1"));
    scene.skipPointerMovePicking = true;
    world = new BABYLON.TransformNode("world", scene);
    camera = new BABYLON.ArcRotateCamera("cam", -Math.PI / 3, 1.1, 4, BABYLON.Vector3.Zero(), scene);
    camera.minZ = 0.005;
    camera.maxZ = 1000;
    camera.lowerRadiusLimit = 0.01;
    camera.onViewMatrixChangedObservable.add(function () {
      requestRender();
      if (!applyingCamera) scheduleCameraEmit();
    });
    const light = new BABYLON.HemisphericLight("sky", new BABYLON.Vector3(0.3, 1, 0.2), scene);
    light.intensity = 0.9;
    light.groundColor = new BABYLON.Color3(0.45, 0.42, 0.38);
    const sun = new BABYLON.DirectionalLight("sun", new BABYLON.Vector3(-0.5, -1, -0.4), scene);
    sun.intensity = 0.6;
    lights = [light, sun];
    scene.setRenderingOrder(0, skyboxFirst);

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
      anchor: function () { const b = selectionBox(); return b ? b.min.add(b.max).scale(0.5).asArray() : null; },
      mode: cameraMode,
      frame: frameSelection,
      click: pick,
      changed: requestRender,
    });
    canvas.addEventListener("pointerdown", function (ev) { pressMods = { add: !!(ev.ctrlKey || ev.metaKey || ev.shiftKey) }; });
    canvas.addEventListener("dblclick", function (ev) { doubleClick(offset(ev)); });
    canvas.addEventListener("pointermove", function (ev) { if (!ev.buttons) hoverAt(offset(ev)); });
    canvas.addEventListener("pointerleave", function () { showHover(null); });
    window.addEventListener("blur", function () { nav.release(); });
    window.addEventListener("resize", function () { engine.resize(); requestRender(); });
    new ResizeObserver(function () { engine.resize(); requestRender(); }).observe(canvas);
    engine.runRenderLoop(frame);
  }
  let pressMods = { add: false };
  function offset(ev) { return [ev.offsetX !== undefined ? ev.offsetX : ev.clientX, ev.offsetY !== undefined ? ev.offsetY : ev.clientY]; }

  function frame() {
    if (!running) return;
    const now = performance.now();
    const dt = lastFrame ? Math.min(0.1, (now - lastFrame) / 1000) : 0;
    lastFrame = now;
    if (nav.tick(dt)) requestRender();
    if (!dirty && idle <= 0) return;
    if (!dirty) idle -= 1;
    dirty = false;
    sizeSkybox();
    scene.render();
  }

  /* ---- the robot ------------------------------------------------------------------------ */
  function disposeRobot() {
    if (robot) robot.dispose(false, false);
    robot = null;
    Object.keys(links).forEach(function (k) { delete links[k]; });
    Object.keys(joints).forEach(function (k) { delete joints[k]; });
    overlays = { frames: [], axes: {}, com: [], inertia: [] };
    collisionsBuilt = false;
    failures.length = 0;
  }

  function buildModel(msg) {
    const m = msg && msg.model;
    if (msg && msg.environments) environments = msg.environments;
    disposeRobot();
    model = m || null;
    generation = msg && msg.generation !== undefined ? msg.generation : generation + 1;
    const seq = ++loadSeq;
    tree = model ? K.tree(model) : [];
    if (!model || !scene) {
      if (model) values = Object.assign(K.zeroPose(model), pendingPose ? pendingPose.joints : {});
      renderTree(); renderJoints(); renderProps(); updateFooter(); report();
      return;
    }
    pruneTemplates(model);
    robot = new BABYLON.TransformNode("robot", scene);
    robot.parent = world;
    (model.links || []).forEach(function (l, i) {
      const node = new BABYLON.TransformNode("link:" + l.name, scene);
      node.metadata = { link: l.name };
      node.parent = robot;
      links[l.name] = { link: l, node: node, index: i, visuals: [], collisions: [], broken: [] };
    });
    (model.joints || []).forEach(function (j) {
      const parent = links[j.parent], child = links[j.child];
      if (!parent || !child) return;
      const origin = new BABYLON.TransformNode("joint:" + j.name, scene);
      setLocal(origin, K.originOf(j.origin));
      origin.parent = parent.node;
      child.node.parent = origin;
      joints[j.name] = { joint: j, origin: origin };
    });
    (model.missing || []).forEach(function (x) { if (links[x.link]) links[x.link].broken.push(x); });
    reach = robotReach();
    values = Object.assign(K.zeroPose(model), pendingPose ? pendingPose.joints : {});
    if (pendingPose) { source = pendingPose.source || "params"; track = pendingPose.track || null; }
    applyPose();
    const jobs = [];
    Object.keys(links).forEach(function (name) {
      const L = links[name];
      (L.link.visuals || []).forEach(function (vis, i) { jobs.push(buildPart(L, vis, i, "visual", seq)); });
    });
    if (VIEW_MODES.indexOf(viewMode()) >= 3) buildCollisions();
    applyUp();
    applyGrid();
    applyOverlays();
    applyViewMode();
    renderTree(); renderJoints(); renderProps(); updateFooter();
    applySelectionParam();
    startLoading(jobs, seq);
    report();
  }
  let pendingPose = null;

  /** The robot's size at the zero pose: the farthest link origin and primitive, at least
   * 10 cm (meshes are not loaded yet; sceneRadius() measures them once they are). */
  function robotReach() {
    const t = K.forward(model, {});
    let r = 0.1;
    Object.keys(t).forEach(function (name) {
      const p = t[name].p;
      r = Math.max(r, Math.hypot(p[0], p[1], p[2]));
    });
    return r;
  }

  /** Every moving joint's value: a track's as recorded (never clamped, amendment 17),
   * otherwise kept in the limits. */
  function resolved() { return K.resolve(model, values, { clamp: source !== "track" }); }
  function applyPose() {
    if (!model) return;
    const v = resolved();
    Object.keys(joints).forEach(function (name) {
      const J = joints[name];
      if (!K.moving(J.joint)) return;
      setLocal(links[J.joint.child].node, K.motion(J.joint, v[name]));
    });
    requestRender();
  }

  /* ---- geometry ---------------------------------------------------------------------------- */
  /** A visual or collision: a holder at its origin under the link, its shape inside. */
  function buildPart(L, part, i, kind, seq) {
    const holder = new BABYLON.TransformNode(kind + ":" + L.link.name + ":" + i, scene);
    setLocal(holder, K.originOf(part.origin));
    holder.parent = L.node;
    const entry = { holder: holder, meshes: [], kind: kind, part: part, shape: shapeName(part.geometry), placeholder: false };
    (kind === "visual" ? L.visuals : L.collisions).push(entry);
    const g = part.geometry || {};
    if (g.type === "mesh") {
      if (!g.url) { placeholder(L, entry); return Promise.resolve(); }
      return meshTemplate(g.url, g.ext).then(function (tpl) {
        if (seq !== loadSeq || holder.isDisposed && holder.isDisposed()) return;
        const geom = new BABYLON.TransformNode("scale", scene);
        const s = g.scale || [1, 1, 1];
        geom.scaling = new BABYLON.Vector3(s[0], s[1], s[2]);
        geom.parent = holder;
        const copy = tpl.holder.clone("mesh:" + L.link.name + ":" + i, geom);
        if (copy) copy.setEnabled(true);
        const meshes = copy ? [copy].concat(copy.getChildMeshes ? copy.getChildMeshes(false) : []).filter(isMesh) : [];
        meshes.forEach(function (mesh) {
          if (kind === "visual" && (!mesh.material || g.ext === "stl")) mesh.material = visualMaterial(part.material);
        });
        adopt(L, entry, meshes);
        applyViewMode();
        applySelection();
      }, function (err) {
        if (seq !== loadSeq) return;
        failures.push({ link: L.link.name, kind: kind, url: g.url, error: String(err && err.message || err) });
        Specto.log("warn", "mesh " + g.url + ": " + (err && err.message || err));
        placeholder(L, entry);
      });
    }
    const mesh = primitive(g, kind + "-shape:" + L.link.name + ":" + i);
    if (!mesh) { placeholder(L, entry); return Promise.resolve(); }
    mesh.parent = holder;
    if (kind === "visual") mesh.material = visualMaterial(part.material);
    adopt(L, entry, [mesh]);
    return Promise.resolve();
  }
  function isMesh(m) { return m && "isVisible" in m && (!m.getTotalVertices || m.getTotalVertices() > 0); }
  function shapeName(g) {
    if (!g) return "?";
    if (g.type === "mesh") return "mesh" + (g.ext ? " ." + g.ext : "");
    return g.type;
  }
  /** Meshes join their part: tagged with the link and the part's kind for picking, the
   * view modes and the bounds. */
  function adopt(L, entry, meshes) {
    meshes.forEach(function (m) {
      m.metadata = Object.assign({}, m.metadata || {}, { link: L.link.name, part: entry.kind, shape: entry.shape });
      m.isPickable = true;
      if (m._spectoMat === undefined) m._spectoMat = m.material;
    });
    entry.meshes = entry.meshes.concat(meshes);
    requestRender();
  }

  function primitive(g, name) {
    if (!g) return null;
    if (g.type === "box") {
      const s = g.size || [0.1, 0.1, 0.1];
      return BABYLON.MeshBuilder.CreateBox(name, { width: s[0], height: s[1], depth: s[2] }, scene);
    }
    if (g.type === "cylinder") {
      const c = BABYLON.MeshBuilder.CreateCylinder(name, { height: g.length || 0.1, diameter: 2 * (g.radius || 0.05), tessellation: 32 }, scene);
      c.rotation = new BABYLON.Vector3(Math.PI / 2, 0, 0);   // Babylon's stands on Y, URDF's on Z
      return c;
    }
    if (g.type === "sphere") return BABYLON.MeshBuilder.CreateSphere(name, { diameter: 2 * (g.radius || 0.05), segments: 24 }, scene);
    return null;
  }

  /** A missing or unreadable mesh: the link's collision primitive when it has one (a
   * visual's stand-in), else a 5 cm magenta wire box at the part's origin. */
  function placeholder(L, entry) {
    let mesh = null;
    if (entry.kind === "visual") {
      const c = (L.link.collisions || []).find(function (x) { return x.geometry && x.geometry.type !== "mesh"; });
      if (c) {
        mesh = primitive(c.geometry, "placeholder:" + L.link.name);
        const at = new BABYLON.TransformNode("placeholder-origin", scene);
        setLocal(at, K.originOf(c.origin));
        at.parent = L.node;
        mesh.parent = at;
        entry.holder.setEnabled(false);
        entry.standin = at;
      }
    }
    if (!mesh) {
      mesh = BABYLON.MeshBuilder.CreateBox("placeholder:" + L.link.name, { size: PLACEHOLDER }, scene);
      mesh.parent = entry.holder;
    }
    mesh.material = placeholderMaterial();
    entry.placeholder = true;
    adopt(L, entry, [mesh]);
    mesh._spectoMat = mesh.material;
  }

  /** The templates of mesh files the new robot does not use go (stepping through many
   * robots keeps only the shown one's). */
  function pruneTemplates(m) {
    const used = {};
    (m.links || []).forEach(function (l) {
      (l.visuals || []).concat(l.collisions || []).forEach(function (p) {
        if (p.geometry && p.geometry.url) used[p.geometry.url] = true;
      });
    });
    Object.keys(templates).forEach(function (url) {
      if (used[url]) return;
      const t = templates[url];
      delete templates[url];
      if (t.holder) t.holder.dispose(false, true);
    });
  }
  /** Each mesh file once: a disabled template the parts clone (sharing its geometry). */
  function meshTemplate(url, ext) {
    if (templates[url]) return templates[url].promise;
    const slash = url.lastIndexOf("/");
    const options = { rootUrl: url.slice(0, slash + 1) };
    const e = String(ext || "").replace(/^\./, "").toLowerCase();
    if (e) options.pluginExtension = "." + e;
    const t = templates[url] = { promise: null, holder: null, error: null };
    t.promise = BABYLON.ImportMeshAsync(url.slice(slash + 1), scene, options).then(function (res) {
      if (e === "stl") res.meshes.forEach(stlNormals);
      const holder = new BABYLON.TransformNode("template:" + url, scene);
      res.meshes.forEach(function (m) { if (!m.parent) m.parent = holder; });
      holder.setEnabled(false);
      t.holder = holder;
      return t;
    }, function (err) { t.error = String(err && err.message || err); throw err; });
    return t.promise;
  }

  /** STL normals from the geometry, turned outward: many exporters write zero normals,
   * and the file's winding is reversed by the unaltered coordinates. Outward is judged
   * against the centroid (a closed part's normals point away from it on the whole). */
  function stlNormals(m) {
    if (!m.getVerticesData || !m.getIndices || !BABYLON.VertexData) return;
    const pos = m.getVerticesData(BABYLON.VertexBuffer.PositionKind);
    const idx = m.getIndices();
    if (!pos || !idx || !pos.length) return;
    const nrm = new Array(pos.length).fill(0);
    BABYLON.VertexData.ComputeNormals(pos, idx, nrm);
    const n = pos.length / 3;
    const c = [0, 0, 0];
    for (let i = 0; i < n; i++) { c[0] += pos[3 * i]; c[1] += pos[3 * i + 1]; c[2] += pos[3 * i + 2]; }
    c[0] /= n; c[1] /= n; c[2] /= n;
    let out = 0;
    for (let i = 0; i < n; i++) {
      out += nrm[3 * i] * (pos[3 * i] - c[0]) + nrm[3 * i + 1] * (pos[3 * i + 1] - c[1]) + nrm[3 * i + 2] * (pos[3 * i + 2] - c[2]);
    }
    if (out < 0) for (let i = 0; i < nrm.length; i++) nrm[i] = -nrm[i];
    m.setVerticesData(BABYLON.VertexBuffer.NormalKind, nrm);
  }

  function visualMaterial(mat) {
    const c = mat && mat.color && mat.color.length >= 3 ? mat.color : null;
    const tex = mat && mat.texture_url ? mat.texture_url : null;
    const key = (c ? c.join(",") : "default") + "|" + (tex || "");
    if (materials[key]) return materials[key];
    const m = new BABYLON.PBRMaterial("urdf:" + ((mat && mat.name) || key), scene);
    const rgb = c || [0.6, 0.6, 0.6, 1];
    m.albedoColor = new BABYLON.Color3(rgb[0], rgb[1], rgb[2]);
    m.metallic = 0;
    m.roughness = 0.55;
    m.alpha = rgb.length > 3 ? rgb[3] : 1;
    m.backFaceCulling = false;
    if (tex) m.albedoTexture = new BABYLON.Texture(tex, scene);
    materials[key] = m;
    return m;
  }
  function placeholderMaterial() {
    if (!placeholderMat) {
      placeholderMat = new BABYLON.StandardMaterial("placeholder", scene);
      placeholderMat.disableLighting = true;
      placeholderMat.emissiveColor = new BABYLON.Color3(1, 0, 1);
      placeholderMat.wireframe = true;
    }
    return placeholderMat;
  }
  function tintMaterials() {
    if (tintFlat.length) return;
    TINTS.forEach(function (t, i) {
      const flat = new BABYLON.StandardMaterial("tint:" + i, scene);
      flat.disableLighting = true;
      flat.emissiveColor = new BABYLON.Color3(t[0], t[1], t[2]);
      flat.backFaceCulling = false;
      tintFlat.push(flat);
      const shell = new BABYLON.StandardMaterial("shell:" + i, scene);
      shell.disableLighting = true;
      shell.emissiveColor = new BABYLON.Color3(t[0], t[1], t[2]);
      shell.alpha = SHELL_ALPHA;
      shell.backFaceCulling = false;
      tintShell.push(shell);
    });
  }

  /** Collision geometry, the first time a collision mode shows it. */
  function buildCollisions() {
    if (collisionsBuilt || !model) return;
    collisionsBuilt = true;
    const seq = loadSeq;
    const jobs = [];
    Object.keys(links).forEach(function (name) {
      const L = links[name];
      (L.link.collisions || []).forEach(function (c, i) { jobs.push(buildPart(L, c, i, "collision", seq)); });
    });
    startLoading(jobs, seq);
  }

  function startLoading(jobs, seq) {
    if (!loading) loadStarted = performance.now();
    setLoading(jobs.length);
    if (jobs.length) Specto.setHealth("loading", "loading " + jobs.length + " part" + (jobs.length > 1 ? "s" : ""));
    Promise.allSettled(jobs).then(function () {
      setLoading(-jobs.length);
      if (seq !== loadSeq) return;
      applyViewMode();
      applySelection();
      if (!loading) {
        Specto.send("loaded", { ms: Math.round(performance.now() - loadStarted), meshes: meshCount(), failed: failures.slice() });
        fitFirst();
      }
      requestRender(); report();
    });
  }
  function setLoading(delta) {
    loading = Math.max(0, loading + delta);
    $("loading").classList.toggle("hidden", loading <= 0);
    $("loading").textContent = loading > 0 ? "loading " + loading + " part" + (loading > 1 ? "s" : "") : "";
  }
  function meshCount() {
    let n = 0;
    Object.keys(links).forEach(function (k) { links[k].visuals.concat(links[k].collisions).forEach(function (e) { n += e.meshes.length; }); });
    return n;
  }
  function allParts(kind) {
    const out = [];
    Object.keys(links).forEach(function (k) {
      const L = links[k];
      (kind === "collision" ? L.collisions : kind === "visual" ? L.visuals : L.visuals.concat(L.collisions)).forEach(function (e) { out.push([L, e]); });
    });
    return out;
  }

  /* ---- view modes ------------------------------------------------------------------------ */
  function viewMode() { return VIEW_MODES.indexOf(params.view_mode) >= 0 ? params.view_mode : "lit"; }
  /** A visual's material in a mode, from what it was when first seen (lit). */
  function materialMode(mat, mode) {
    if (!mat || mat === placeholderMat || tintFlat.indexOf(mat) >= 0 || tintShell.indexOf(mat) >= 0) return;
    const lit = mat._spectoLit || (mat._spectoLit = {
      wireframe: !!mat.wireframe, unlit: !!mat.unlit, disableLighting: !!mat.disableLighting, emissiveColor: mat.emissiveColor,
    });
    mat.wireframe = mode === "wireframe" || lit.wireframe;
    if (/PBR/.test(mat.getClassName ? mat.getClassName() : "")) {
      mat.unlit = mode === "unlit" || lit.unlit;
    } else if ("disableLighting" in mat) {
      mat.disableLighting = mode === "unlit" || lit.disableLighting;
      mat.emissiveColor = mode === "unlit" && mat.diffuseColor ? mat.diffuseColor : lit.emissiveColor;
    }
  }
  function applyViewMode() {
    if (!scene) return;
    const mode = viewMode();
    const colliding = mode === "collision" || mode === "visual_collision";
    if (colliding) { buildCollisions(); tintMaterials(); }
    allParts().forEach(function (pe) {
      const L = pe[0], e = pe[1];
      const tint = L.index % TINTS.length;
      e.meshes.forEach(function (m) {
        if (e.kind === "visual") {
          m.isVisible = mode !== "collision";
          if (!e.placeholder) materialMode(m._spectoMat, colliding ? "lit" : mode);
          m.isPickable = mode !== "collision";
        } else {
          m.isVisible = colliding;
          m.isPickable = colliding;
          if (e.placeholder) return;
          m.material = mode === "collision" ? tintFlat[tint] : tintShell[tint];
          if (mode === "visual_collision") edges(m, true, TINTS[tint]); else edges(m, mode === "collision", [0, 0, 0]);
        }
      });
    });
    scene.clearColor = mode === "wireframe"
      ? new BABYLON.Color4(WIRE_BG[0], WIRE_BG[1], WIRE_BG[2], 1)
      : color4(cssVar("--color-surface-alt", "#faf7f1"));
    if (modeButton) {
      modeButton.textContent = MODE_LABELS[mode];
      modeButton.classList.toggle("on", mode !== "lit");
    }
    if (!colliding) showHover(null);
    applyEnvironment();
    requestRender();
  }
  function edges(m, on, rgb) {
    if (!m.enableEdgesRendering) return;
    if (on) {
      m.enableEdgesRendering(0.95);
      m.edgesWidth = 1.5;
      m.edgesColor = new BABYLON.Color4(rgb[0] * 0.6, rgb[1] * 0.6, rgb[2] * 0.6, 1);
    } else if (m.disableEdgesRendering) {
      m.disableEdgesRendering();
    }
  }
  function setViewMode(mode) {
    if (mode === viewMode()) return;
    params = Object.assign({}, params, { view_mode: mode });
    applyViewMode(); applySelection(); report();
    Specto.setParams({ view_mode: mode });
  }

  /* collision modes: what is under the cursor, "link · shape" */
  let hoverText = null, hoverPending = null;
  function hoverAt(p) {
    const mode = viewMode();
    if (mode !== "collision" && mode !== "visual_collision") return;
    if (hoverPending) { hoverPending = p; return; }
    hoverPending = p;
    window.setTimeout(function () {
      const q = hoverPending;
      hoverPending = null;
      const hit = scene.pick(q[0], q[1], function (m) { return m.isVisible && m.isPickable && m.metadata && m.metadata.part === "collision"; }, false, camera);
      const md = hit && hit.hit && hit.pickedMesh && hit.pickedMesh.metadata;
      showHover(md ? md.link + " · " + md.shape : null, q);
    }, 30);
  }
  function showHover(text, p) {
    hoverText = text || null;
    const el = $("hover");
    if (!el) return;
    el.classList.toggle("hidden", !text);
    if (text) {
      el.textContent = text;
      el.style.left = (p[0] + 14) + "px";
      el.style.top = (p[1] + 10) + "px";
    }
    report();
  }

  /* ---- up axis, ground grid and overlays ------------------------------------------------------ */
  function upAxis() { return K.upOf(params.up); }
  function applyUp() {
    if (!world) return;
    world.rotationQuaternion = quat(K.upRotation(upAxis()));
    syncViewControls();
    requestRender();
  }

  /** The ground grid: Babylon's XZ plane (the robot's plane square to its up, through its
   * origin), minor lines a step apart and a major line every ten. */
  let gridFor = null;
  function applyGrid() {
    const want = params.show_grid === false || !scene ? "off" : String(Math.max(reach, 0.1));
    if (want === gridFor) return;
    gridFor = want;
    gridLines.forEach(function (g) { g.dispose(); });
    gridLines = [];
    if (want === "off") { requestRender(); return; }
    const r = Math.max(reach, 0.1);
    const step = Math.pow(10, Math.round(Math.log10(r)) - 1);
    const n = Math.min(100, Math.ceil(3 * r / step));
    const extent = n * step;
    const minor = [], major = [];
    for (let i = -n; i <= n; i++) {
      const d = i * step;
      const set = i % 10 === 0 ? major : minor;
      set.push([new BABYLON.Vector3(d, 0, -extent), new BABYLON.Vector3(d, 0, extent)]);
      set.push([new BABYLON.Vector3(-extent, 0, d), new BABYLON.Vector3(extent, 0, d)]);
    }
    [[minor, 0.35], [major, 0.7]].forEach(function (s, i) {
      if (!s[0].length) return;
      const g = BABYLON.MeshBuilder.CreateLineSystem(i ? "grid-major" : "grid-minor", { lines: s[0] }, scene);
      g.color = BABYLON.Color3.FromHexString(cssVar("--color-border-strong", "#b9b2a6"));
      g.alpha = s[1];
      g.isPickable = false;
      g.metadata = { grid: true };
      gridLines.push(g);
    });
    report();
    requestRender();
  }

  function overlaySize() { return Math.max(0.03, reach * 0.12); }
  function lineMesh(name, lines, colors, parent) {
    const m = BABYLON.MeshBuilder.CreateLineSystem(name, { lines: lines, colors: colors }, scene);
    m.isPickable = false;
    m.renderingGroupId = 1;
    m.metadata = { overlay: true };
    m.parent = parent;
    return m;
  }
  function applyOverlays() {
    if (!model) return;
    const s = overlaySize();
    // link frames: RGB axes at every link's origin (built once per robot while on)
    if (!params.show_frames) {
      overlays.frames.forEach(function (m) { m.dispose(); });
      overlays.frames = [];
    } else if (!overlays.frames.length) {
      const O = BABYLON.Vector3.Zero();
      const red = new BABYLON.Color4(0.9, 0.2, 0.2, 1), green = new BABYLON.Color4(0.2, 0.75, 0.3, 1), blue = new BABYLON.Color4(0.25, 0.45, 0.95, 1);
      Object.keys(links).forEach(function (name) {
        overlays.frames.push(lineMesh("frame:" + name, [
          [O, new BABYLON.Vector3(s, 0, 0)], [O, new BABYLON.Vector3(0, s, 0)], [O, new BABYLON.Vector3(0, 0, s)],
        ], [[red, red], [green, green], [blue, blue]], links[name].node));
      });
    }
    // joint axes: an arrow along each moving joint's axis, at the joint (its child's frame);
    // the selected joint's shows even with the overlay off
    const wanted = {};
    Object.keys(joints).forEach(function (name) {
      if (K.moving(joints[name].joint) && (params.show_joint_axes || selectedJoint() === name)) wanted[name] = true;
    });
    Object.keys(overlays.axes).forEach(function (name) {
      if (!wanted[name]) { overlays.axes[name].forEach(function (m) { m.dispose(); }); delete overlays.axes[name]; }
    });
    Object.keys(wanted).forEach(function (name) { if (!overlays.axes[name]) overlays.axes[name] = jointArrow(joints[name].joint, s); });
    // centre of mass: a marker per link, sized by its mass
    if (!params.show_com) {
      overlays.com.forEach(function (m) { m.dispose(); });
      overlays.com = [];
    } else if (!overlays.com.length) {
      let heaviest = 0;
      Object.keys(links).forEach(function (n) { const i = links[n].link.inertial; if (i && i.mass > heaviest) heaviest = i.mass; });
      Object.keys(links).forEach(function (name) {
        const i = links[name].link.inertial;
        if (!i || !(i.mass > 0)) return;
        const d = s * 0.3 * Math.max(0.3, Math.cbrt(i.mass / (heaviest || 1)));
        const m = BABYLON.MeshBuilder.CreateSphere("com:" + name, { diameter: d, segments: 12 }, scene);
        m.material = comMaterial();
        m.position = v3((i.origin && i.origin.xyz) || [0, 0, 0]);
        m.parent = links[name].node;
        m.isPickable = false;
        m.renderingGroupId = 1;
        m.metadata = { overlay: true, com: name };
        overlays.com.push(m);
      });
    }
    // inertia boxes: the solid box of equivalent inertia per link with a mass
    if (!params.show_inertia) {
      overlays.inertia.forEach(function (m) { m.dispose(); });
      overlays.inertia = [];
    } else if (!overlays.inertia.length) {
      Object.keys(links).forEach(function (name) {
        const b = K.inertiaBox(links[name].link.inertial);
        if (!b) return;
        const m = BABYLON.MeshBuilder.CreateBox("inertia:" + name, { width: b.size[0], height: b.size[1], depth: b.size[2] }, scene);
        m.material = inertiaMaterial();
        m.position = v3(b.p);
        m.rotationQuaternion = quat(b.q);
        m.parent = links[name].node;
        m.isPickable = false;
        m.metadata = { overlay: true, inertia: name };
        overlays.inertia.push(m);
      });
    }
    requestRender();
  }
  let comMat = null, axisMat = null, inertiaMat = null;
  function inertiaMaterial() {
    if (!inertiaMat) {
      inertiaMat = new BABYLON.StandardMaterial("inertia", scene);
      inertiaMat.disableLighting = true;
      inertiaMat.emissiveColor = new BABYLON.Color3(0.62, 0.36, 0.95);
      inertiaMat.alpha = 0.3;
      inertiaMat.backFaceCulling = false;
    }
    return inertiaMat;
  }
  function comMaterial() {
    if (!comMat) {
      comMat = new BABYLON.StandardMaterial("com", scene);
      comMat.disableLighting = true;
      comMat.emissiveColor = new BABYLON.Color3(0.98, 0.78, 0.15);
    }
    return comMat;
  }
  function jointArrow(j, s) {
    const node = links[j.child] && links[j.child].node;
    if (!node) return [];
    const a = j.axis && j.axis.length === 3 ? j.axis : [1, 0, 0];
    const l = Math.hypot(a[0], a[1], a[2]) || 1;
    const u = [a[0] / l, a[1] / l, a[2] / l];
    const len = s * 1.4;
    const col = BABYLON.Color3.FromHexString(cssVar("--color-accent", "#2f6fab"));
    const c4 = new BABYLON.Color4(col.r, col.g, col.b, 1);
    const shaft = lineMesh("axis:" + j.name, [[BABYLON.Vector3.Zero(), new BABYLON.Vector3(u[0] * len, u[1] * len, u[2] * len)]], [[c4, c4]], node);
    const cone = BABYLON.MeshBuilder.CreateCylinder("axis-tip:" + j.name, { diameterTop: 0, diameterBottom: s * 0.16, height: s * 0.3, tessellation: 12 }, scene);
    if (!axisMat) {
      axisMat = new BABYLON.StandardMaterial("axis", scene);
      axisMat.disableLighting = true;
    }
    axisMat.emissiveColor = col;
    cone.material = axisMat;
    cone.rotationQuaternion = quat(K.fromTo([0, 1, 0], u));
    cone.position = new BABYLON.Vector3(u[0] * len, u[1] * len, u[2] * len);
    cone.parent = node;
    cone.isPickable = false;
    cone.renderingGroupId = 1;
    cone.metadata = { overlay: true, axis: j.name };
    return [shaft, cone];
  }

  /* ---- HDRI environments (the 3d view's, #175) ----------------------------------------------- */
  let environments = [];
  const envTextures = {};
  let skybox = null, skyboxFor = null;
  function envEntry(name) { return environments.find(function (e) { return e.name === name; }) || null; }
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
  function envTexture(e) {
    if (envTextures[e.name]) return envTextures[e.name];
    const t = envTextures[e.name] = { tex: null, ready: false, error: null, ms: null };
    const t0 = performance.now();
    const done = function () { t.ready = true; t.ms = Math.round(performance.now() - t0); applyEnvironment(); report(); };
    const fail = function (msg, ex) {
      t.error = String(msg || (ex && ex.message) || "unreadable");
      Specto.log("error", e.label + " environment: " + t.error);
      syncViewControls(); report();
    };
    if (/\.hdr$/i.test(e.url)) t.tex = new BABYLON.HDRCubeTexture(e.url, scene, HDR_CUBE, false, true, false, true, done, fail);
    else t.tex = new BABYLON.CubeTexture(e.url, scene, null, false, null, done, fail, undefined, true, ".env");
    t.tex.name = "env:" + e.name;
    return t;
  }
  function applyEnvironment() {
    if (!scene) return;
    const name = environmentName();
    const e = envEntry(name);
    const t = e && e.url ? envTexture(e) : null;
    const on = !!(t && t.ready);
    scene.environmentTexture = on ? t.tex : null;
    scene.environmentIntensity = envIntensity();
    if (on) t.tex.rotationY = envRotation() * Math.PI / 180;
    lights.forEach(function (l) { l.setEnabled(!on); });
    const sky = on && params.environment_background === true && viewMode() !== "wireframe";
    if (sky && skyboxFor !== name) {
      if (skybox) skybox.dispose(false, true);
      skybox = scene.createDefaultSkybox(t.tex, true, 1, 0, false);
      skybox.isPickable = false;
      skybox.material.disableDepthWrite = true;
      skyboxFor = name;
    }
    if (skybox) {
      skybox.setEnabled(sky);
      if (sky && skybox.material.reflectionTexture) skybox.material.reflectionTexture.rotationY = t.tex.rotationY;
    }
    syncViewControls();
    requestRender();
  }
  function sizeSkybox() { if (skybox && skybox.isEnabled()) skybox.scaling.setAll(camera.maxZ); }
  function skyboxFirst(a, b) { return (b.getMesh() === skybox ? 1 : 0) - (a.getMesh() === skybox ? 1 : 0); }
  function envReport() {
    const t = envState();
    return {
      name: environmentName(), ready: !!(t && t.ready), error: t ? t.error : null, load_ms: t ? t.ms : null,
      intensity: envIntensity(), rotation: envRotation(),
      background: !!(skybox && skybox.isEnabled()), lights: lights.length ? lights[0].isEnabled() : true,
    };
  }

  /** A user's change to the view params: shown now, kept (one user submit). */
  function setViewParams(patch, keep) {
    params = Object.assign({}, params, patch);
    applyParams();
    report();
    if (keep) Specto.setParams(patch);
  }

  /* ---- camera, framing and picking ------------------------------------------------------------ */
  function cameraMode() { return params.camera_mode === "orbit" ? "orbit" : "free"; }
  function cameraSpeed() {
    const v = Number(params.camera_speed);
    return v > 0 ? Math.max(Math.pow(2, SPEED_MIN), Math.min(Math.pow(2, SPEED_MAX), v)) : 1;
  }
  function applySpeed() {
    const v = cameraSpeed();
    if (speedInput) speedInput.value = String(Math.log2(v));
    if (speedValue) speedValue.textContent = v.toFixed(v < 1 ? 2 : 1) + "x";
  }
  function partVisible(m) {
    if (!m.metadata || !m.metadata.link || !m.isVisible) return false;
    return m.metadata.part === "visual" || viewMode() === "collision" || viewMode() === "visual_collision";
  }
  /** World bounds of what a set of links (null: every link) draws, else null. */
  function boundsOf(names) {
    if (!robot) return null;
    const want = names ? {} : null;
    if (names) names.forEach(function (n) { want[n] = true; });
    let min = null, max = null;
    allParts().forEach(function (pe) {
      if (want && !want[pe[0].link.name]) return;
      pe[1].meshes.forEach(function (m) {
        if (!partVisible(m) || (m.isEnabled && !m.isEnabled(true))) return;
        if (m.computeWorldMatrix) m.computeWorldMatrix(true);
        const b = m.getHierarchyBoundingVectors(false);
        min = min ? BABYLON.Vector3.Minimize(min, b.min) : b.min.clone();
        max = max ? BABYLON.Vector3.Maximize(max, b.max) : b.max.clone();
      });
    });
    if (!min && names) {
      // nothing drawn (a frame-only link like tool0): a small box around its origin
      names.forEach(function (n) {
        const L = links[n];
        if (!L) return;
        if (L.node.computeWorldMatrix) L.node.computeWorldMatrix(true);
        const p = L.node.getAbsolutePosition ? L.node.getAbsolutePosition() : BABYLON.Vector3.Zero();
        const h = overlaySize();
        const lo = p.subtract(new BABYLON.Vector3(h, h, h)), hi = p.add(new BABYLON.Vector3(h, h, h));
        min = min ? BABYLON.Vector3.Minimize(min, lo) : lo;
        max = max ? BABYLON.Vector3.Maximize(max, hi) : hi;
      });
    }
    return min ? { min: min, max: max } : null;
  }
  function sceneRadius() {
    const b = boundsOf(null);
    return b ? Math.max(0.05, b.max.subtract(b.min).length() / 2) : Math.max(reach, 0.5);
  }
  function selectedLinks() {
    const out = [];
    selection.forEach(function (s) {
      const n = s.kind === "joint" ? (joints[s.name] && joints[s.name].joint.child) : s.name;
      if (n && links[n] && out.indexOf(n) < 0) out.push(n);
    });
    return out;
  }
  function selectedJoint() { const j = selection.find(function (s) { return s.kind === "joint"; }); return j ? j.name : null; }
  function selectionBox() { const l = selectedLinks(); return l.length ? boundsOf(l) : null; }
  function frameSelection() {
    const b = selectionBox() || boundsOf(null);
    if (b) frameBox(b.min, b.max);
  }
  function fitAll() { const b = boundsOf(null); if (b) frameBox(b.min, b.max, true); }
  /** The first look at a robot: everything in sight from a three-quarter view, unless a
   * camera came in on the port; kept as the world origin. */
  function fitFirst() {
    if (!model) return;
    const id = model.name + "|" + (model.links || []).length;
    if (fittedFor === id) return;
    fittedFor = id;
    if (!cameraFromPort) fitAll();
    originCamera = cameraValue();
  }
  function frameBox(min, max, fresh) {
    const center = min.add(max).scale(0.5);
    const radius = Math.max(0.01, max.subtract(min).length() / 2);
    const distance = radius / Math.sin(camera.fov / 2) * 1.05;
    let dir = camera.target.subtract(camera.position);
    if (fresh || dir.length() < 1e-6) dir = new BABYLON.Vector3(-0.55, -0.45, -0.7);
    dir.normalize();
    applyingCamera = true;
    camera.setTarget(center);
    camera.setPosition(center.subtract(dir.scale(distance)));
    camera.maxZ = Math.max(50, radius * 100);
    camera.minZ = Math.max(0.001, radius / 2000);
    applyingCamera = false;
    requestRender();
    scheduleCameraEmit();
  }
  function goToOrigin() {
    if (originCamera) { applyCamera(originCamera); scheduleCameraEmit(); } else fitAll();
  }
  function cameraValue() {
    return {
      position: camera.position.asArray(), target: camera.target.asArray(), up: camera.upVector.asArray(),
      fov_y: camera.fov, near: camera.minZ, far: camera.maxZ,
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
  function pickLink(x, y) {
    const hit = scene.pick(x, y, function (m) { return m.isPickable && partVisible(m); }, false, camera);
    return hit && hit.hit && hit.pickedMesh && hit.pickedMesh.metadata ? { link: hit.pickedMesh.metadata.link, point: hit.pickedPoint } : null;
  }
  function pivotAt(x, y) {
    const box = selectionBox();
    if (box) return box.min.add(box.max).scale(0.5).asArray();
    const hit = pickLink(x, y);
    if (hit && hit.point) return hit.point.asArray();
    const ray = scene.createPickingRay(x, y, BABYLON.Matrix.Identity(), camera, false);
    const d = camera.target.subtract(camera.position).length();
    return ray.origin.add(ray.direction.scale(d)).asArray();
  }
  /** A click in the picture: the link under it (Ctrl adds), or nothing. */
  function pick(x, y) {
    const hit = pickLink(x, y);
    const add = pressMods.add;
    if (!hit) { if (!add) select([], false); return; }
    select([{ kind: "link", name: hit.link }], add);
  }
  function doubleClick(p) {
    const hit = pickLink(p[0], p[1]);
    if (!hit) return;
    select([{ kind: "link", name: hit.link }], false);
    frameLink(hit.link);
  }
  function frameLink(name) {
    const b = boundsOf([name]);
    if (b) frameBox(b.min, b.max);
    Specto.send("frame", { link: name });
  }

  /* ---- selection ---------------------------------------------------------------------------- */
  function sameSel(a, b) { return a.kind === b.kind && a.name === b.name; }
  /** The user's selection: shown now and sent (`select`; the host keys it and never echoes it). */
  function select(items, add) {
    let next;
    if (add) {
      next = selection.slice();
      items.forEach(function (it) {
        const i = next.findIndex(function (s) { return sameSel(s, it); });
        if (i >= 0) next.splice(i, 1); else next.push(it);
      });
    } else {
      next = items.slice();
    }
    selection = next;
    const last = items[items.length - 1] || null;
    Specto.send("select", {
      link: last && last.kind === "link" ? last.name : null,
      joint: last && last.kind === "joint" ? last.name : null,
      add: !!add,
    });
    // kept: the links in `selected_links` (one user submit), unless that is what it says
    const kept = selectedLinks();
    if (JSON.stringify(kept) !== JSON.stringify(linksParam())) {
      params = Object.assign({}, params, { selected_links: kept });
      Specto.setParams({ selected_links: kept });
    }
    applySelection(true);
  }
  function linksParam() { return Array.isArray(params.selected_links) ? params.selected_links.map(String) : []; }
  /** Keys from outside (`<item key>#<link>`) or link names (`selected_links`). */
  function selectionFrom(keys) {
    const out = [];
    (keys || []).forEach(function (k) {
      const s = String(k);
      const name = s.indexOf("#") >= 0 ? s.slice(s.lastIndexOf("#") + 1) : s;
      if (links[name]) out.push({ kind: "link", name: name });
      else if (joints[name]) out.push({ kind: "joint", name: name });
    });
    return out;
  }
  /** `selected_links` (the agent's, or the page's own write coming back): those links
   * selected, unless they are the selection already (a joint row keeps its joint).
   * (`selected` is the shown robot's item key: the host's, not the page's.) */
  function applySelectionParam() {
    const want = linksParam().filter(function (n) { return links[n]; });
    if (JSON.stringify(want) === JSON.stringify(selectedLinks())) return;
    selection = want.map(function (n) { return { kind: "link", name: n }; });
    applySelection(true);
  }
  function applySelection(reveal) {
    const chosen = {};
    selectedLinks().forEach(function (n) { chosen[n] = true; });
    const accent = BABYLON.Color3.FromHexString(cssVar("--color-accent", "#2f6fab"));
    allParts().forEach(function (pe) {
      const on = !!chosen[pe[0].link.name];
      pe[1].meshes.forEach(function (m) {
        m.renderOverlay = on;
        m.overlayColor = accent;
        m.overlayAlpha = 0.25;
        m.renderOutline = on;
        m.outlineColor = accent;
        m.outlineWidth = overlaySize() * 0.03;
      });
    });
    applyOverlays();   // the selected joint's axis
    if (reveal) revealSelection();
    markRows();
    renderProps();
    updateFooter();
    requestRender();
    report();
  }

  /* ---- the panels and the footer ------------------------------------------------------------- */
  let viewPanel = null, robotPanel = null;
  let speedInput = null, speedValue = null, originButton = null, frameButton = null;
  let camButtons = {}, envButtons = {}, envSelect = null, envIntensityInput = null, envIntensityValue = null;
  let envRotationInput = null, envRotationValue = null, envBackgroundBox = null, gridBox = null;
  let framesBox = null, axesBox = null, comBox = null, inertiaBox = null, upButtons = {}, upNegate = null, resetButton = null;
  let searchInput = null, treeEl = null, jointsEl = null, propsEl = null, followingEl = null, jointsReset = null, propsTitle = null;
  let query = "";
  const open = {};                     // tree key -> expanded (default open)
  const rows = {};                     // tree key -> row element
  const sliders = {};                  // joint name -> {row, input, value, joint}
  let dragging = null;                 // the joint whose slider is held
  let modeButton = null, modeMenu = null, footer = null, robotCaption = null, sourceCaption = null, targetButton = null;

  function relayout() { if (engine) { engine.resize(); requestRender(); } report(); }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function seg(options, onPick) {
    const wrap = el("div", "seg");
    const buttons = {};
    options.forEach(function (o) {
      const b = el("button", "quiet", o[1]);
      b.setAttribute("data-value", o[0]);
      b.title = o[2] || "";
      b.addEventListener("click", function () { onPick(o[0]); });
      wrap.appendChild(b);
      buttons[o[0]] = b;
    });
    return [wrap, buttons];
  }
  function checkRow(label, tip, id, onChange) {
    const row = el("label", "check-row");
    row.title = tip;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = id;
    box.addEventListener("change", function () { onChange(!!box.checked); });
    row.append(box, el("span", "", label));
    return [row, box];
  }
  function sliderRow(label, min, max, step, tip, onInput, onChange) {
    const row = el("div", "env-row");
    const head = el("div", "env-head");
    const value = el("span", "speed-value");
    head.append(el("span", "env-name", label), value);
    const input = document.createElement("input");
    input.type = "range";
    input.min = String(min); input.max = String(max); input.step = String(step);
    input.title = tip;
    input.addEventListener("input", function () { onInput(Number(input.value)); });
    input.addEventListener("change", function () { onChange(Number(input.value)); });
    row.append(head, input);
    return [row, input, value];
  }

  function mountViewPanel() {
    viewPanel = SpectoSidePanel.mount($("row"), {
      id: "view", title: "View", side: "left", keep: "view", width: 168, minWidth: 120, maxWidth: 320,
      onCollapse: relayout, onResize: relayout,
    });
    // camera
    const cam = el("div", "control");
    cam.appendChild(el("span", "control-label", "Camera speed"));
    const row = el("div", "speed-row");
    speedInput = document.createElement("input");
    speedInput.type = "range";
    speedInput.min = String(SPEED_MIN); speedInput.max = String(SPEED_MAX); speedInput.step = "0.25";
    speedInput.title = "how fast the camera flies, walks, pans and dollies (the wheel sets it while the right button is held)";
    speedValue = el("span", "speed-value");
    speedInput.addEventListener("input", function () {
      params = Object.assign({}, params, { camera_speed: Math.pow(2, Number(speedInput.value)) });
      applySpeed(); report();
    });
    speedInput.addEventListener("change", function () { Specto.setParams({ camera_speed: cameraSpeed() }); });
    row.append(speedInput, speedValue);
    const pills = el("div", "pills");
    originButton = el("button", "pill");
    originButton.id = "origin";
    originButton.title = "back to the first look: the whole robot in sight";
    originButton.append(SpectoIcons.svg("home"), el("span", "", "World origin"));
    originButton.addEventListener("click", goToOrigin);
    frameButton = el("button", "pill", "Frame selected");
    frameButton.id = "frameSelected";
    frameButton.title = "frame the selected links, else the whole robot (F)";
    frameButton.addEventListener("click", frameSelection);
    pills.append(originButton, frameButton);
    const camSeg = seg([["free", "Free", "Unreal Editor's viewport: drag walks, right-drag looks"], ["orbit", "Orbit", "drag orbits the selection, right-drag and the wheel close on it"]],
      function (v) { setViewParams({ camera_mode: v }, true); });
    camButtons = camSeg[1];
    const camRow = el("div", "seg-row");
    camRow.append(el("span", "", "Camera"), camSeg[0]);
    cam.append(row, pills, camRow);

    // environment
    const env = el("div", "control");
    env.appendChild(el("span", "control-label", "Environment"));
    const envSeg = seg([["default", "Default", "the two lights"], ["hdri", "HDRI", "image-based lighting from a bundled HDRI (Poly Haven, CC0)"]], function (v) {
      if (v === "default") setViewParams({ environment: ENV_NONE }, true);
      else setViewParams({ environment: lastHdri() }, true);
    });
    envButtons = envSeg[1];
    env.appendChild(envSeg[0]);
    const pickRow = el("div", "env-row");
    envSelect = document.createElement("select");
    envSelect.id = "environment";
    envSelect.title = "the HDRI that lights the robot";
    envSelect.addEventListener("change", function () { setViewParams({ environment: envSelect.value }, true); });
    pickRow.appendChild(envSelect);
    env.appendChild(pickRow);
    let s = sliderRow("Intensity", 0, ENV_INTENSITY_MAX, 0.05, "how strongly the HDRI lights the robot",
      function (v) { setViewParams({ environment_intensity: v }, false); }, function (v) { setViewParams({ environment_intensity: v }, true); });
    envIntensityInput = s[1]; envIntensityValue = s[2]; env.appendChild(s[0]);
    s = sliderRow("Rotation", 0, 360, 5, "turns the HDRI about the up axis (degrees)",
      function (v) { setViewParams({ environment_rotation: v }, false); }, function (v) { setViewParams({ environment_rotation: v }, true); });
    envRotationInput = s[1]; envRotationValue = s[2]; env.appendChild(s[0]);
    let c = checkRow("Background", "draw the HDRI behind the robot instead of the plain background", "environmentBackground",
      function (on) { setViewParams({ environment_background: on }, true); });
    envBackgroundBox = c[1]; env.appendChild(c[0]);
    c = checkRow("Ground grid", "a grid on the robot's ground plane", "showGrid", function (on) { setViewParams({ show_grid: on }, true); });
    gridBox = c[1]; env.appendChild(c[0]);

    // overlays, the up axis and the pose
    const ov = el("div", "control");
    ov.appendChild(el("span", "control-label", "Overlays"));
    c = checkRow("Link frames", "RGB axes at each link's origin", "showFrames", function (on) { setViewParams({ show_frames: on }, true); });
    framesBox = c[1]; ov.appendChild(c[0]);
    c = checkRow("Joint axes", "an arrow along each moving joint's axis", "showJointAxes", function (on) { setViewParams({ show_joint_axes: on }, true); });
    axesBox = c[1]; ov.appendChild(c[0]);
    c = checkRow("Centre of mass", "a marker at each link's centre of mass, sized by its mass", "showCom", function (on) { setViewParams({ show_com: on }, true); });
    comBox = c[1]; ov.appendChild(c[0]);
    c = checkRow("Inertia boxes", "a box of equivalent inertia per link: its mass and principal moments, on the principal axes", "showInertia", function (on) { setViewParams({ show_inertia: on }, true); });
    inertiaBox = c[1]; ov.appendChild(c[0]);
    const upSeg = seg([["x", "X"], ["y", "Y"], ["z", "Z"]], function (a) {
      setViewParams({ up: (upAxis().charAt(0) === "-" ? "-" : "") + a }, true);
      fitAll();
    });
    upButtons = upSeg[1];
    upNegate = el("button", "quiet", "−");
    upNegate.id = "upNegate";
    upNegate.title = "point the up axis the other way";
    upNegate.addEventListener("click", function () {
      const u = upAxis();
      setViewParams({ up: u.charAt(0) === "-" ? u.slice(1) : "-" + u }, true);
      fitAll();
    });
    upSeg[0].appendChild(upNegate);
    const upRow = el("div", "seg-row");
    upRow.title = "the robot's up axis (ROS is Z-up, REP 103)";
    upRow.append(el("span", "", "Up axis"), upSeg[0]);
    ov.appendChild(upRow);
    resetButton = el("button", "pill", "Reset pose");
    resetButton.id = "resetPose";
    resetButton.title = "every joint to 0, kept inside its limits (one undo step)";
    resetButton.addEventListener("click", resetPose);
    ov.appendChild(resetButton);
    viewPanel.body.append(cam, env, ov);
  }
  function lastHdri() {
    const n = environmentName();
    if (n !== ENV_NONE) return n;
    const first = environments.find(function (e) { return e.url; });
    return lastPicked && envEntry(lastPicked) ? lastPicked : first ? first.name : ENV_NONE;
  }
  let lastPicked = null;

  function syncViewControls() {
    if (!speedInput) return;
    Object.keys(camButtons).forEach(function (k) { camButtons[k].classList.toggle("on", k === cameraMode()); });
    const name = environmentName(), off = name === ENV_NONE, t = envState();
    if (!off) lastPicked = name;
    envButtons.default.classList.toggle("on", off);
    envButtons.hdri.classList.toggle("on", !off);
    envButtons.hdri.disabled = !environments.some(function (e) { return e.url; });
    envSelect.textContent = "";
    environments.forEach(function (e) {
      const o = document.createElement("option");
      o.value = e.name;
      o.textContent = e.label + (t && e.name === name && !t.ready && !t.error ? "…" : "");
      o.disabled = !e.url;
      envSelect.appendChild(o);
    });
    envSelect.value = off ? lastHdri() : name;
    envSelect.disabled = off;
    envIntensityInput.value = String(envIntensity());
    envIntensityValue.textContent = envIntensity().toFixed(2) + "x";
    envRotationInput.value = String(envRotation());
    envRotationValue.textContent = Math.round(envRotation()) + "°";
    envBackgroundBox.checked = params.environment_background === true;
    [envIntensityInput, envRotationInput, envBackgroundBox].forEach(function (x) { x.disabled = off; });
    gridBox.checked = params.show_grid !== false;
    framesBox.checked = !!params.show_frames;
    axesBox.checked = !!params.show_joint_axes;
    comBox.checked = !!params.show_com;
    inertiaBox.checked = !!params.show_inertia;
    const u = upAxis();
    Object.keys(upButtons).forEach(function (k) { upButtons[k].classList.toggle("on", u.replace("-", "") === k); });
    upNegate.classList.toggle("on", u.charAt(0) === "-");
  }

  function mountRobotPanel() {
    robotPanel = SpectoSidePanel.mount($("row"), {
      id: "robot", title: "Robot", side: "right", keep: "robot", width: 220, minWidth: 150, maxWidth: 480,
      onCollapse: relayout, onResize: relayout,
    });
    const search = el("div", "search");
    searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.id = "search";
    searchInput.placeholder = "Search links and joints";
    searchInput.addEventListener("input", function () { query = searchInput.value; renderTree(); report(); });
    search.appendChild(searchInput);
    treeEl = el("div", "tree");
    treeEl.id = "tree";
    const jh = el("div", "section-head");
    jh.append(el("span", "grow", "JOINTS"));
    jointsReset = el("button", "quiet", "Reset");
    jointsReset.id = "jointsReset";
    jointsReset.title = "every joint to 0, kept inside its limits (one undo step)";
    jointsReset.addEventListener("click", resetPose);
    jh.appendChild(jointsReset);
    followingEl = el("div", "following hidden");
    jointsEl = el("div", "joints");
    jointsEl.id = "joints";
    const ph = el("div", "section-head");
    propsTitle = el("span", "grow", "PROPERTIES");
    ph.appendChild(propsTitle);
    propsEl = el("div", "props");
    propsEl.id = "props";
    robotPanel.body.append(search, treeEl, jh, followingEl, jointsEl, ph, propsEl);
  }

  /* the tree: links under the joints that hold them, R/P/C/F markers, search keeps ancestors */
  function renderTree() {
    if (!treeEl) return;
    treeEl.textContent = "";
    Object.keys(rows).forEach(function (k) { delete rows[k]; });
    const keep = K.filter(tree, query);
    const q = String(query || "").trim().toLowerCase();
    const walk = function (n, depth) {
      const k = K.key(n);
      if (keep && !keep[k]) return;
      const r = el("div", "tree-row " + n.kind);
      r.setAttribute("data-key", k);
      r.style.paddingLeft = (4 + depth * 10) + "px";
      const opened = keep ? true : open[k] !== false;
      const chev = el("span", "chev", n.children.length ? (opened ? "▾" : "▸") : "");
      chev.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (keep) return;
        open[k] = !opened;
        renderTree(); report();
      });
      r.append(chev, el("span", "glyph", n.kind === "link" ? "(o)" : "-o-"), el("span", "name", n.name));
      if (n.kind === "joint") r.appendChild(el("span", "type", K.typeLetter(n.type)));
      const L = n.kind === "link" ? links[n.name] : null;
      if (L && L.broken.length) {
        r.classList.add("broken");
        r.title = L.broken.map(function (b) { return b.kind + " mesh missing: " + b.uri + " (" + b.cause + ")"; }).join("\n");
      } else {
        r.title = (n.kind === "link" ? "link " : K.typeLetter(n.type) + " joint ") + n.name + "\ndouble-click to frame it";
      }
      if (q && n.name.toLowerCase().indexOf(q) >= 0) r.classList.add("match");
      r.addEventListener("click", function (ev) { select([{ kind: n.kind, name: n.name }], !!(ev.ctrlKey || ev.metaKey)); });
      r.addEventListener("dblclick", function () {
        const link = n.kind === "link" ? n.name : joints[n.name] && joints[n.name].joint.child;
        if (link) frameLink(link);
      });
      treeEl.appendChild(r);
      rows[k] = r;
      if (opened) n.children.forEach(function (c) { walk(c, depth + 1); });
    };
    tree.forEach(function (n) { walk(n, 0); });
    markRows();
  }
  function markRows() {
    Object.keys(rows).forEach(function (k) {
      rows[k].classList.toggle("selected", selection.some(function (s) { return s.kind + ":" + s.name === k; }));
    });
    Object.keys(sliders).forEach(function (n) {
      sliders[n].row.classList.toggle("selected", selection.some(function (s) { return s.kind === "joint" && s.name === n; }));
    });
  }
  /** Open the tree down to the newest selected row and scroll it into view. */
  function revealSelection() {
    const last = selection[selection.length - 1];
    if (!last) return;
    const k = last.kind + ":" + last.name;
    const path = K.pathTo(tree, k);
    let changed = false;
    path.slice(0, -1).forEach(function (p) { if (open[p] === false) { open[p] = true; changed = true; } });
    if (changed) renderTree();
    if (rows[k] && rows[k].scrollIntoView) rows[k].scrollIntoView({ block: "nearest" });
  }

  /* the JOINTS section: a slider per actuated joint, mimic rows greyed */
  function locked() { return source === "track"; }
  function renderJoints() {
    if (!jointsEl) return;
    jointsEl.textContent = "";
    Object.keys(sliders).forEach(function (k) { delete sliders[k]; });
    if (!model) return;
    (model.joints || []).filter(K.moving).forEach(function (j) {
      const row = el("div", "joint-row" + (j.mimic ? " mimic" : ""));
      row.setAttribute("data-joint", j.name);
      const head = el("div", "head");
      const name = el("span", "name", (j.mimic ? "~" : "") + j.name);
      const value = el("span", "value");
      head.append(name, el("span", "type", K.typeLetter(j.type)), value);
      row.appendChild(head);
      if (j.mimic) row.appendChild(el("div", "follows", "= " + j.mimic.joint + (j.mimic.multiplier !== 1 ? " x " + fmt(j.mimic.multiplier) : "") + (j.mimic.offset ? (j.mimic.offset > 0 ? " + " : " - ") + fmt(Math.abs(j.mimic.offset)) : "")));
      const spec = K.sliderSpec(j);
      let input = null;
      if (j.mimic) {
        row.title = j.name + " follows " + j.mimic.joint + " (x " + j.mimic.multiplier + (j.mimic.offset ? " + " + j.mimic.offset : "") + ")";
      } else {
        input = document.createElement("input");
        input.type = "range";
        input.min = String(spec.min); input.max = String(spec.max); input.step = String(spec.step);
        input.title = j.name + ": " + fmt(spec.min, 1) + " to " + fmt(spec.max, 1) + " " + (spec.unit === "mm" ? "mm" : "degrees") + (j.type === "continuous" ? " (continuous: wraps)" : "");
        input.addEventListener("pointerdown", function () { dragging = j.name; });
        input.addEventListener("input", function () {
          dragging = j.name;
          values = Object.assign({}, values);
          values[j.name] = K.fromDisplay(j, Number(input.value));
          source = "slider";
          applyPose(); syncJoints(); updateFooter(); report();
        });
        input.addEventListener("change", function () {
          values = Object.assign({}, values);
          values[j.name] = K.fromDisplay(j, Number(input.value));
          dragging = null;
          commitJoints();
        });
        row.appendChild(input);
        value.title = "click to type a value";
        value.addEventListener("click", function () { typeValue(j, value); });
      }
      jointsEl.appendChild(row);
      sliders[j.name] = { row: row, input: input, value: value, joint: j };
    });
    syncJoints();
    markRows();
  }
  function syncJoints() {
    if (!model) return;
    const v = resolved();
    Object.keys(sliders).forEach(function (n) {
      const s = sliders[n];
      if (s.typing) return;
      s.value.textContent = K.displayText(s.joint, v[n]);
      if (s.input) {
        if (dragging !== n) s.input.value = String(K.toDisplay(s.joint, v[n]));
        s.input.disabled = locked();
      }
      s.row.classList.toggle("locked", locked());
    });
    if (followingEl) {
      followingEl.classList.toggle("hidden", !locked());
      followingEl.textContent = locked() ? "following " + (track || "the joint track") : "";
    }
    if (jointsReset) jointsReset.disabled = locked() || !model || !K.actuated(model).length;
    if (resetButton) resetButton.disabled = locked() || !model || !K.actuated(model).length;
  }
  /** A click on a value: type one, Enter or leaving keeps it, Escape drops it. */
  function typeValue(j, value) {
    const s = sliders[j.name];
    if (!s || locked() || s.typing) return;
    s.typing = true;
    const input = document.createElement("input");
    input.className = "typed";
    input.type = "number";
    input.value = String(Math.round(K.toDisplay(j, values[j.name] || 0) * 10) / 10);
    value.textContent = "";
    value.appendChild(input);
    if (input.focus) input.focus();
    let done = false;
    const finish = function (keep) {
      if (done) return;
      done = true;
      s.typing = false;
      if (keep && input.value !== "" && isFinite(Number(input.value))) {
        values = Object.assign({}, values);
        values[j.name] = K.fromDisplay(j, Number(input.value));
        commitJoints();
      } else {
        syncJoints(); report();
      }
    };
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
      else if (ev.key === "Escape") { ev.preventDefault(); if (ev.stopPropagation) ev.stopPropagation(); finish(false); }
    });
    input.addEventListener("blur", function () { finish(true); });
    report();
  }
  /** The user's pose: every actuated joint in one `joints` message (one user submit). */
  function commitJoints() {
    if (!model || locked()) return;
    const out = {};
    K.actuated(model).forEach(function (j) { out[j.name] = K.clampJoint(j, values[j.name] || 0); });
    values = Object.assign({}, values, out);
    source = "slider";
    applyPose(); syncJoints(); updateFooter();
    Specto.send("joints", out);
    report();
  }
  function resetPose() {
    if (!model || locked()) return;
    values = K.zeroPose(model);
    commitJoints();
  }
  function applyPoseMessage(p) {
    if (!p) return;
    pendingPose = p;
    if (!model) return;
    // the whole pose: what it names, the zero pose for the rest; the slider the user
    // holds keeps its value until it is let go (a track takes over regardless)
    const held = dragging && p.source !== "track" ? { [dragging]: values[dragging] } : {};
    values = Object.assign(K.zeroPose(model), p.joints || {}, held);
    source = p.source || "params";
    track = p.track || null;
    if (locked()) dragging = null;
    applyPose(); syncJoints(); updateFooter(); report();
  }

  /* the PROPERTIES section: the newest selected link or joint */
  function prop(k, v, bad) {
    const r = el("div", "prop");
    r.append(el("span", "k", k), el("span", "v" + (bad ? " bad" : ""), v));
    propsEl.appendChild(r);
  }
  function geometryText(g) {
    if (!g) return ["?", false];
    if (g.type === "box") return ["box " + vec(g.size), false];
    if (g.type === "cylinder") return ["cylinder r " + fmt(g.radius) + " l " + fmt(g.length), false];
    if (g.type === "sphere") return ["sphere r " + fmt(g.radius), false];
    const sc = g.scale && g.scale.some(function (x) { return x !== 1; }) ? " x " + vec(g.scale) : "";
    const what = g.uri ? g.uri : "mesh ." + (g.ext || "?");
    return g.url ? [what + sc + " ok", false] : [what + " missing", true];
  }
  function hex(c) {
    return "#" + c.slice(0, 3).map(function (x) { const h = Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16); return h.length < 2 ? "0" + h : h; }).join("");
  }
  function renderProps() {
    if (!propsEl) return;
    propsEl.textContent = "";
    const last = selection[selection.length - 1];
    propsTitle.textContent = "PROPERTIES" + (last ? " " + last.name : "");
    if (!last || !model) { propsEl.appendChild(el("div", "empty", model ? "select a link or joint" : "no robot")); return; }
    if (last.kind === "link" && links[last.name]) {
      const L = links[last.name], l = L.link;
      const holder = (model.joints || []).find(function (j) { return j.child === l.name; });
      prop("origin", holder ? vec(holder.origin && holder.origin.xyz) + (holder.origin && holder.origin.rpy && holder.origin.rpy.some(Boolean) ? "  rpy " + vec(holder.origin.rpy) : "") : "root");
      if (l.inertial) {
        prop("mass", fmt(l.inertial.mass) + " kg");
        prop("com", vec(l.inertial.origin && l.inertial.origin.xyz));
        const I = l.inertial.inertia || {};
        prop("inertia", ["ixx", "ixy", "ixz", "iyy", "iyz", "izz"].map(function (k) { return k + " " + fmt(I[k] || 0, 4); }).join("  "));
      } else {
        prop("mass", "no inertial");
      }
      (l.visuals || []).forEach(function (v, i) {
        const gt = geometryText(v.geometry);
        const miss = L.broken.find(function (b) { return b.kind === "visual"; });
        const failed = failures.find(function (f) { return f.link === l.name && f.kind === "visual"; });
        const why = miss && gt[1] && !(v.geometry && v.geometry.uri) ? ": " + miss.uri : "";
        prop("visual" + (l.visuals.length > 1 ? " " + (i + 1) : ""), gt[0] + why + (failed ? " (unreadable)" : ""), gt[1] || !!failed);
        if (v.material) prop("material", (v.material.name || "") + (v.material.color ? " " + hex(v.material.color) : "") + (v.material.texture_url ? " textured" : ""));
      });
      if (!(l.visuals || []).length) prop("visual", "none");
      (l.collisions || []).forEach(function (c, i) {
        const gt = geometryText(c.geometry);
        prop("collision" + (l.collisions.length > 1 ? " " + (i + 1) : ""), gt[0], gt[1]);
      });
      if (!(l.collisions || []).length) prop("collision", "none");
    } else if (last.kind === "joint" && joints[last.name]) {
      const j = joints[last.name].joint;
      prop("type", j.type);
      prop("parent", j.parent);
      prop("child", j.child);
      prop("origin", vec(j.origin && j.origin.xyz) + "  rpy " + vec(j.origin && j.origin.rpy));
      prop("axis", vec(j.axis));
      if (K.moving(j)) {
        const lim = K.limits(j);
        prop("limits", j.type === "continuous" ? "continuous" : K.displayText(j, lim[0]) + " to " + K.displayText(j, lim[1]) + "  (" + fmt(lim[0]) + " .. " + fmt(lim[1]) + ")");
        if (j.limit) prop("effort", fmt(j.limit.effort) + "  velocity " + fmt(j.limit.velocity));
        prop("value", K.displayText(j, resolved()[j.name]));
      } else {
        prop("slider", K.NO_SLIDER[j.type] || "none");
      }
      if (j.dynamics) prop("dynamics", "damping " + fmt(j.dynamics.damping) + "  friction " + fmt(j.dynamics.friction));
      if (j.mimic) prop("mimic", j.mimic.joint + " x " + fmt(j.mimic.multiplier) + " + " + fmt(j.mimic.offset));
    }
  }

  function mountFooter() {
    footer = SpectoFooter.mount($("footer"), { step: ["PageUp", "PageDown"] });
    const name = $("footerName");
    targetButton = SpectoIcons.button("target", "Frame the selection, else the robot (F)", "frameAll");
    targetButton.addEventListener("click", frameSelection);
    footer.el.insertBefore(targetButton, name);
    robotCaption = el("span", "footer-robot");
    robotCaption.id = "robotCaption";
    footer.el.insertBefore(robotCaption, name ? name.nextElementSibling : null);
    modeButton = el("button", "mode-pill", MODE_LABELS.lit);
    modeButton.id = "viewMode";
    modeButton.title = "View mode: lit, unlit, wireframe, collision, visual + collision";
    modeButton.addEventListener("click", function () { if (modeMenu.classList.contains("hidden")) openModeMenu(); else closeModeMenu(); });
    footer.el.appendChild(modeButton);
    sourceCaption = el("span", "footer-source");
    sourceCaption.id = "jointSource";
    footer.el.appendChild(sourceCaption);
    modeMenu = el("div", "mode-menu hidden");
    modeMenu.id = "viewModeMenu";
    document.body.appendChild(modeMenu);
    document.addEventListener("pointerdown", function (ev) {
      if (!modeMenu.contains(ev.target) && !modeButton.contains(ev.target)) closeModeMenu();
    });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closeModeMenu(); });
  }
  function openModeMenu() {
    modeMenu.textContent = "";
    VIEW_MODES.forEach(function (m) {
      const b = el("button", "mode-item" + (m === viewMode() ? " on" : ""), MODE_LABELS[m]);
      b.setAttribute("data-mode", m);
      b.addEventListener("click", function () { setViewMode(m); closeModeMenu(); });
      modeMenu.appendChild(b);
    });
    if (modeButton.getBoundingClientRect && window.innerWidth) {
      modeMenu.style.right = Math.max(0, window.innerWidth - modeButton.getBoundingClientRect().right) + "px";
    }
    modeMenu.classList.remove("hidden");
    modeButton.classList.add("open");
    report();
  }
  function closeModeMenu() {
    if (!modeMenu || modeMenu.classList.contains("hidden")) return;
    modeMenu.classList.add("hidden");
    modeButton.classList.remove("open");
    report();
  }
  function updateFooter() {
    if (!footer) return;
    const links_ = selectedLinks();
    footer.setName(links_.length ? links_[links_.length - 1] + (links_.length > 1 ? " +" + (links_.length - 1) : "") : "", links_.join(", "));
    robotCaption.textContent = model ? (model.name || "robot") + " (" + (model.links || []).length + " links, " + (model.joints || []).length + " joints)" : "no robot";
    const word = { track: "track", params: "params", zero: "zero", slider: "slider" }[source] || source;
    sourceCaption.textContent = model ? "joints: " + word : "";
    sourceCaption.title = source === "track" ? "following " + (track || "the joint track") : "";
  }

  /* ---- params --------------------------------------------------------------------------------- */
  function applyParams() {
    applySpeed();
    applyUp();
    applyGrid();
    applyOverlays();
    applyViewMode();
    applySelection();
    SpectoSidePanel.restore(params.panels);
    syncViewControls();
    requestRender();
  }

  /* ---- what the page shows, for snap and the flows -------------------------------------------- */
  function report() {
    const r = Specto.rectOf;
    const treeRows = {};
    Object.keys(rows).forEach(function (k) { treeRows[k] = r(rows[k]); });
    const joint = {};
    if (model) {
      const v = resolved();
      Object.keys(sliders).forEach(function (n) {
        const s = sliders[n];
        joint[n] = {
          value: v[n], text: s.value.textContent, mimic: !!s.joint.mimic,
          slider: r(s.input), value_rect: r(s.value), disabled: s.input ? !!s.input.disabled : true, typing: !!s.typing,
          min: s.input ? Number(s.input.min) : null, max: s.input ? Number(s.input.max) : null,
        };
      });
    }
    let visible = 0, placeholders = 0, collisions = 0;
    allParts().forEach(function (pe) {
      pe[1].meshes.forEach(function (m) { if (m.isVisible) visible += 1; });
      if (pe[1].placeholder) placeholders += 1;
      if (pe[1].kind === "collision") collisions += 1;
    });
    const box = loading ? null : boundsOf(null);
    const round = function (v) { return v.asArray().map(function (x) { return +x.toFixed(3); }); };
    Specto.report({
      generation: generation,
      bounds: box ? [round(box.min), round(box.max)] : null,
      robot: model ? { name: model.name, root: K.rootOf(model), links: (model.links || []).length, joints: (model.joints || []).length } : null,
      joints: joint,
      values: model ? resolved() : {},
      source: source,
      track: track,
      locked: locked(),
      selection: selection.map(function (s) { return s.kind + ":" + s.name; }),
      selected_links: selectedLinks(),
      view_mode: viewMode(),
      view_modes: VIEW_MODES,
      up: upAxis(),
      camera_mode: cameraMode(),
      environment: envReport(),
      environments: environments.map(function (e) { return e.name; }),
      grid: gridLines.length > 0,
      overlays: { frames: overlays.frames.length, axes: Object.keys(overlays.axes), com: overlays.com.length, inertia: overlays.inertia.length },
      meshes: { loaded: meshCount(), visible: visible, placeholders: placeholders, collision_parts: collisions, collisions_built: collisionsBuilt, loading: loading, failed: failures.slice() },
      hover: hoverText,
      search: query,
      tree: treeRows,
      tree_order: Object.keys(rows),
      props: propsEl ? Array.prototype.map.call(propsEl.children, function (c) { return c.textContent; }) : [],
      footer: { robot: robotCaption ? robotCaption.textContent : "", source: sourceCaption ? sourceCaption.textContent : "", name: $("footerName") ? $("footerName").textContent : "" },
      following: followingEl && !followingEl.classList.contains("hidden") ? followingEl.textContent : null,
      camera: camera ? cameraValue() : null,
      speed: cameraSpeed(),
      nav: nav ? nav.state() : null,
      running: running,
      babylon: BABYLON.Engine.Version,
      canvas: r($("c")),
      buttons: {
        frameAll: r(targetButton), origin: r(originButton), frameSelected: r(frameButton), speed: r(speedInput),
        viewMode: r(modeButton), reset: r(resetButton), jointsReset: r(jointsReset), search: r(searchInput),
        cameraFree: r(camButtons.free), cameraOrbit: r(camButtons.orbit),
        envDefault: r(envButtons.default), envHdri: r(envButtons.hdri), environment: r(envSelect),
        envIntensity: r(envIntensityInput), envRotation: r(envRotationInput), envBackground: r(envBackgroundBox),
        grid: r(gridBox), frames: r(framesBox), axes: r(axesBox), com: r(comBox), inertia: r(inertiaBox),
        upX: r(upButtons.x), upY: r(upButtons.y), upZ: r(upButtons.z), upNegate: r(upNegate),
      },
      modes: modeMenu && !modeMenu.classList.contains("hidden")
        ? Object.fromEntries(Array.prototype.map.call(modeMenu.children, function (b) { return [b.getAttribute("data-mode"), r(b)]; }))
        : null,
      panels: viewPanel ? { view: viewPanel.rects(), robot: robotPanel.rects() } : null,
    });
  }

  /* ---- bridge ----------------------------------------------------------------------------------- */
  Specto.on("config", function (cfg) {
    params = Object.assign({}, params, (cfg && cfg.params) || {});
    applyParams();
    report();
  });
  Specto.on("model", function (m) { buildModel(m); });
  Specto.on("pose", applyPoseMessage);
  Specto.on("params", function (p) {
    const had = linksParam();
    params = Object.assign({}, params, p || {});
    applyParams();
    if (JSON.stringify(had) !== JSON.stringify(linksParam())) applySelectionParam();
    report();
  });
  Specto.on("port:camera", function (v) { cameraFromPort = true; applyCamera(v); });
  Specto.on("port:selection", function (sel) {
    selection = selectionFrom((sel && sel.keys) || []);
    applySelection(true);
  });
  Specto.on("render", function (r) {
    running = !r || r.running !== false;
    if (running) { lastFrame = 0; requestRender(); }
    report();
  });

  // the chrome first: without WebGL the panels and the footer still show, and say why
  mountViewPanel();
  mountRobotPanel();
  mountFooter();
  try {
    boot();
  } catch (e) {
    engine = scene = camera = null;
    Specto.setHealth("error", "no 3D here: " + (e && e.message || e));
    Specto.log("error", "urdf page: " + (e && e.message || e));
  }
  applyParams();
  renderTree(); renderJoints(); renderProps(); updateFooter();
  Specto.connect().catch(function (e) {
    Specto.log("error", e.message);
    console.error(e);
  });
})();
