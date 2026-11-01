// A recording stand-in for the Babylon.js API the 3D page uses: every mesh, transform
// node, material and vertex buffer it creates is kept in `created` with a `disposed`
// flag, so a test can say what is still alive in the scene after a source switch.
// Geometry is not computed; vectors do just enough arithmetic for the camera code.

export class Vector3 {
  constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
  static Zero() { return new Vector3(0, 0, 0); }
  static Up() { return new Vector3(0, 1, 0); }
  static FromArray(a) { return new Vector3(a[0], a[1], a[2]); }
  static Minimize(a, b) { return new Vector3(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.min(a.z, b.z)); }
  static Maximize(a, b) { return new Vector3(Math.max(a.x, b.x), Math.max(a.y, b.y), Math.max(a.z, b.z)); }
  static Distance(a, b) { return a.subtract(b).length(); }
  asArray() { return [this.x, this.y, this.z]; }
  clone() { return new Vector3(this.x, this.y, this.z); }
  add(o) { return new Vector3(this.x + o.x, this.y + o.y, this.z + o.z); }
  subtract(o) { return new Vector3(this.x - o.x, this.y - o.y, this.z - o.z); }
  scale(s) { return new Vector3(this.x * s, this.y * s, this.z * s); }
  addInPlace(o) { this.x += o.x; this.y += o.y; this.z += o.z; return this; }
  length() { return Math.hypot(this.x, this.y, this.z); }
  normalize() { const l = this.length() || 1; this.x /= l; this.y /= l; this.z /= l; return this; }
  setAll(v) { this.x = this.y = this.z = v; return this; }
}

export class Color3 {
  constructor(r = 0, g = 0, b = 0) { this.r = r; this.g = g; this.b = b; }
  static FromHexString() { return new Color3(0.2, 0.4, 0.6); }
  static White() { return new Color3(1, 1, 1); }
}
export class Color4 { constructor(r, g, b, a) { this.r = r; this.g = g; this.b = b; this.a = a; } }

const observable = () => ({ add() {} });

export function makeBabylon() {
  const created = [];
  const scene = {
    materials: [],
    meshes: [],
    lights: [],
    useRightHandedSystem: false,
    clearColor: null,
    skipPointerMovePicking: false,
    autoClear: true,
    pointerX: 0,
    pointerY: 0,
    onPointerObservable: observable(),
    renders: 0,              // scene.render() calls: what the page's frame loop actually drew
    render() { this.renders += 1; },
    // image-based lighting (#175): what the page set, and the skyboxes it made
    environmentTexture: null,
    environmentIntensity: 1,
    renderingOrder: {},
    setRenderingOrder(group, opaque) { this.renderingOrder[group] = opaque; },
    createDefaultSkybox(tex, pbr, size, blur, setGlobal) {
      const box = new Mesh("hdrSkyBox");
      box.material = new PBRMaterial("skyBox");
      box.material.reflectionTexture = { of: tex, rotationY: 0 };
      box.skybox = { tex, pbr, size, blur, setGlobal };
      return box;
    },
    pick() { return null; },
    createPickingRay() { return { origin: Vector3.Zero(), direction: new Vector3(0, 0, -1) }; },
  };

  class TransformNode {
    constructor(name) {
      this.name = name;
      this._parent = null;
      this.metadata = null;
      this.disposed = false;
      this.enabled = true;
      this.rotation = Vector3.Zero();
      this.position = Vector3.Zero();
      this.scaling = Vector3.Zero();
      this.children = [];
      created.push(this);
      scene.meshes.push(this);
    }
    // as in Babylon, parenting is what dispose() recurses over
    get parent() { return this._parent; }
    set parent(p) {
      if (this._parent) this._parent.children = this._parent.children.filter((c) => c !== this);
      this._parent = p;
      if (p) p.children.push(this);
    }
    setEnabled(v) { this.enabled = !!v; }
    isEnabled() { return this.enabled; }
    getChildMeshes() { return this.children; }
    // every item spans -1..1 unless a test sets `bounds` = {min, max} on its node
    getHierarchyBoundingVectors() { return this.bounds ? { min: this.bounds.min.clone(), max: this.bounds.max.clone() } : { min: new Vector3(-1, -1, -1), max: new Vector3(1, 1, 1) }; }
    dispose(_recursive, materialsAndTextures) {
      this.disposed = true;
      scene.meshes = scene.meshes.filter((m) => m !== this);
      if (!_recursive) [...this.children].forEach((c) => c.dispose(_recursive, materialsAndTextures));
      if (materialsAndTextures && this.material) this.material.dispose();
    }
  }
  class Mesh extends TransformNode {
    constructor(name) {
      super(name);
      this.buffers = [];
      this.material = null;
      this.isPickable = true;
      this.isVisible = true;
      this.visibility = 1;
    }
    setVerticesBuffer(b) { this.buffers.push(b); }
    setBoundingInfo() {}
    dispose(recursive, materialsAndTextures) {
      this.buffers.forEach((b) => b.dispose());
      super.dispose(recursive, materialsAndTextures);
    }
  }
  // Babylon 9.31's GaussianSplattingMesh: after the import resolves it still sorts on a
  // worker and uploads the order on reply; until then `isReady()` is false and the
  // private `_isDepthSortSettled` getter (readyToDisplay && !sortIsDirty &&
  // canPostToWorker) is false. The test flips `settled` when the sort "lands".
  class GaussianSplattingMesh extends Mesh {
    constructor(name) { super(name); this.settled = false; }
    get _isDepthSortSettled() { return this.settled; }
    isReady() { return this.settled; }
  }
  class VertexBuffer {
    constructor(_engine, data, kind) { this.kind = kind; this.bytes = data.byteLength; this.disposed = false; created.push(this); }
    dispose() { this.disposed = true; }
  }
  VertexBuffer.PositionKind = "position";
  VertexBuffer.ColorKind = "color";
  VertexBuffer.FLOAT = 1;
  VertexBuffer.UNSIGNED_BYTE = 2;
  class StandardMaterial {
    constructor(name) {
      this.name = name; this.disposed = false; this.pointsCloud = false; this.wireframe = false;
      this.disableLighting = false; this.diffuseColor = new Color3(0.8, 0.7, 0.6); this.emissiveColor = new Color3(0, 0, 0);
      this.alpha = 1; this.backFaceCulling = true;
      created.push(this); scene.materials.push(this);
    }
    getClassName() { return "StandardMaterial"; }
    dispose() { this.disposed = true; scene.materials = scene.materials.filter((m) => m !== this); }
  }
  // what the glTF loader makes: `unlit` drops the lighting (KHR_materials_unlit)
  class PBRMaterial {
    constructor(name) { this.name = name; this.pointsCloud = false; this.wireframe = false; this.unlit = false; created.push(this); scene.materials.push(this); }
    getClassName() { return "PBRMaterial"; }
    dispose() { this.disposed = true; scene.materials = scene.materials.filter((m) => m !== this); }
  }
  class ArcRotateCamera {
    constructor(_name, _a, _b, radius, target) {
      this.target = target;
      this.position = new Vector3(0, 0, radius);
      this.upVector = Vector3.Up();
      this.fov = 0.8;
      this.minZ = 0.05;
      this.maxZ = 5000;
      this.inputs = { removeByType() {} };
      this.onViewMatrixChangedObservable = observable();
    }
    attachControl() {}
    setTarget(v) { this.target = v.clone(); }
    setPosition(v) { this.position = v.clone(); }
    getDirection() { return new Vector3(0, 0, 1); }
  }
  class Light {
    constructor() { this.intensity = 1; this.enabled = true; scene.lights.push(this); }
    setEnabled(v) { this.enabled = !!v; }
    isEnabled() { return this.enabled; }
  }
  // environment textures the page asks for (#175): each a deferred the test settles
  // through `textures` (load / fail call the page's onLoad / onError)
  const textures = [];
  class EnvTexture {
    constructor(kind, url, onLoad, onError, args) {
      this.kind = kind; this.url = url; this.args = args; this.rotationY = 0; this.disposed = false;
      textures.push({ kind, url, tex: this, load: () => onLoad && onLoad(), fail: (msg) => onError && onError(msg || "load failed") });
      created.push(this);
    }
    dispose() { this.disposed = true; }
  }
  class CubeTexture extends EnvTexture {
    constructor(url, _scene, _ext, _noMip, _files, onLoad, onError, _format, prefiltered, forcedExtension) {
      super("env", url, onLoad, onError, { prefiltered, forcedExtension });
    }
  }
  class HDRCubeTexture extends EnvTexture {
    constructor(url, _scene, size, noMipmap, harmonics, gamma, prefilter, onLoad, onError) {
      super("hdr", url, onLoad, onError, { size, noMipmap, harmonics, gamma, prefilter });
    }
  }

  // loads the page asks Babylon for: each one is a deferred the test settles
  const imports = [];
  // the render loop the page handed to engine.runRenderLoop: the test ticks it
  const loop = { fn: null, resizes: 0 };   // resizes: engine.resize() calls
  const babylon = {
    Vector3, Color3, Color4, TransformNode, Mesh, GaussianSplattingMesh, VertexBuffer, StandardMaterial, PBRMaterial,
    ArcRotateCamera, HemisphericLight: Light, DirectionalLight: Light, CubeTexture, HDRCubeTexture,
    Axis: { X: new Vector3(1, 0, 0), Y: new Vector3(0, 1, 0), Z: new Vector3(0, 0, 1) },
    Matrix: { Identity() { return {}; } },
    BoundingInfo: class { constructor(min, max) { this.min = min; this.max = max; } },
    BoundingBox: class { constructor(min, max) { this.min = min; this.max = max; } },
    PointerEventTypes: { POINTERDOWN: 1, POINTERUP: 2, POINTERDOUBLETAP: 3 },
    Tools: { ScriptBaseUrl: "" },
    MeshBuilder: { CreateSphere(name) { return new Mesh(name); } },
    Engine: class {
      constructor() {}
      runRenderLoop(fn) { loop.fn = fn; }
      resize() { loop.resizes += 1; }
      getRenderHeight() { return 600; }
    },
    Scene: class { constructor() { return scene; } },
    ImportMeshAsync(file, _scene, options) {
      let resolve, reject;
      const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
      imports.push({
        url: (options.rootUrl || "") + file,
        options,
        // parts: [{name, material: "pbr" | "standard"}] makes those meshes instead of one
        resolve(parts) {
          const meshes = (parts || [{ name: "imported:" + file }]).map((p) => {
            const m = new Mesh(p.name);
            if (p.material) m.material = p.material === "pbr" ? new PBRMaterial(p.name + ":mat") : new StandardMaterial(p.name + ":mat");
            return m;
          });
          resolve({ meshes });
          return meshes;
        },
        // a splat lands as a GaussianSplattingMesh that has not sorted yet; the test
        // sets `settled` on the returned mesh when the worker would have replied
        resolveSplat() { const m = new GaussianSplattingMesh("imported:" + file); resolve({ meshes: [m] }); return m; },
        reject(msg) { reject(new Error(msg || "import failed")); },
      });
      return promise;
    },
  };
  babylon.Engine.Version = "fake";
  const frame = () => loop.fn && loop.fn();
  return { babylon, scene, created, imports, frame, textures, loop };
}
