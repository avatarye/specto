"""The Storm renderer of ``specto-usd-source`` (#143): Hydra Storm (``UsdImagingGL``) from
the OpenUSD imaging build, as the spike (#134)
drove it: 221 fps at 1080p on the depot world against RTX's 17-19.

No window: a ``QOffscreenSurface`` with its GL context, an FBO with depth, and one
pixel-buffer object (PBO) per frame in flight. :meth:`StormRenderer.submit` draws and
starts the read into a PBO, :meth:`StormRenderer.finish` maps it into the ring.

What the spike found and this follows:

- the depth buffer is cleared before ``Render``: Storm's present pass depth-tests against
  the bound FBO, and an uncleared one silently drops the whole image;
- a camera headlight, not the stage's lights: Kit's lights (a 5000 DistantLight, an MDL
  dome) wash Storm's image out to flat grey;
- every Omniverse material is MDL, which Storm cannot draw: they all show grey, foliage
  as solid cards, and there is no sky. :data:`NOTE` says so to the user.

The camera comes in as :func:`specto_app.usd.nav.gf_frustum` makes it, in the stage's
frame; a pick narrows that frustum to one pixel for ``TestIntersection``. A selection
(#140) is Storm's own highlight in the host's colour, not ovrtx's outline.

View modes (#153) are render params plus edits in a layer of the session layer's own
(the file is never written): unlit draws with lighting and scene materials off, which
draws ``displayColor``, so each gprim bound to a UsdPreviewSurface gets its constant
``diffuseColor`` as ``displayColor`` there (textures are not shown, MDL stays grey);
wireframe is the ``DRAW_WIREFRAME`` draw mode with the same colours over near-black;
collision references :mod:`.collision`'s overlay under its root, hides the stage's gprims
with ``visibility`` overs and draws unlit, the overlay's baked tint. A mesh whose materials
are bound to its GeomSubsets gets a ``uniform`` ``displayColor``, each face its subset's
colour.

OpenUSD posts the errors of a draw as a ``Tf.ErrorException`` once the draw is done, so
one never ends the render loop (:meth:`StormRenderer._tolerate`): the frame is drawn, the
error is noted once (:meth:`StormRenderer.take_warnings`) and drawing goes on. 26.08 (and
dev) raises one on every switch from lit to the other modes on a stage with
material-bound GeomSubsets: turning scene materials off changes the subsets' material
ids, and ``HdStMesh::_UpdateDrawItemsForGeomSubsets`` clears the old subset draw items
before it looks them up to drop their material tags, failing a ``TF_VERIFY`` per subset
(hdSt/mesh.cpp, line 420). The new draw items are made right after, so the frame is
whole; only the material-tag counts it meant to lower stay up.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from collections.abc import Callable
from typing import Any

import numpy as np

from .environment import Assets
from .environment import author as author_environment
from .nav import gf_frustum

#: the renderer readout's note: what Storm cannot show of Omniverse content
NOTE = "MDL materials show grey; no sky"
CLEAR = (0.16, 0.16, 0.17, 1.0)  # no sky: a neutral grey behind the stage
CLEAR_DARK = (0.02, 0.02, 0.025, 1.0)  # wireframe and collision, near-black as under RTX
UNLIT_NOTE = "UsdPreviewSurface colours, no textures"
PREVIEW_FALLBACK = (0.18, 0.18, 0.18)  # UsdPreviewSurface's diffuseColor fallback


#: one error of a ``Tf.ErrorException``'s text: the function and its message
TF_ERROR = re.compile(r"Error in '(?P<where>[^']*)' at line \d+ in file [^\n]*? : (?P<what>.*)")


def tf_error_summary(text: str) -> str:
    """One line for a ``Tf.ErrorException``: each distinct error (function without its
    namespace, message without Tf's quoting) once, with how many times it came."""
    counts: dict[str, int] = {}
    for m in TF_ERROR.finditer(text):
        where = m["where"].rsplit("::", 2)
        where = "::".join(where[-2:]) if len(where) > 1 else where[0]
        what = " ".join(m["what"].replace("'", " ").split())
        key = f"{where}: {what}"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return " ".join(text.split())[:200] or "an OpenUSD error"
    return "; ".join(f"{k} (x{n})" if n > 1 else k for k, n in counts.items())


def use_build(prefix: str) -> None:
    """Make ``import pxr`` find the OpenUSD build at ``prefix`` (usd-core's pxr from the
    rtx extra has no UsdImagingGL): its site-packages first on ``sys.path``, its ``bin``
    and ``lib`` as the folders ``pxr.Tf`` adds as DLL directories. Those come from
    ``PXR_USD_WINDOWS_DLL_PATH``, else PATH; usd-core's pxr sets the variable to its own
    folder, and a parent that imported it hands it down, so it is set here too. Called
    before anything imports pxr. Elsewhere the build's modules find its libraries by
    their RUNPATH, so the path is all it takes."""
    from specto_core.machine import openusd_site_packages

    sys.path.insert(0, openusd_site_packages(prefix))
    if sys.platform != "win32":
        return
    dlls = os.pathsep.join([os.path.join(prefix, "bin"), os.path.join(prefix, "lib")])
    os.environ["PXR_USD_WINDOWS_DLL_PATH"] = dlls
    os.environ["PATH"] = os.pathsep.join([dlls, os.environ.get("PATH", "")])


class StormRenderer:
    """One ``UsdImagingGL.Engine`` on an offscreen GL context. Used from the thread that
    made it, which is the process's main thread (Qt makes the offscreen surface there)."""

    name = "storm"
    note = NOTE
    renders_mdl = False  # the environment's sky falls back to the HDRI (SPEC-31)
    #: (camera state, aspect) -> the camera value :meth:`set_camera` takes
    camera_of = staticmethod(gf_frustum)

    def __init__(self) -> None:
        from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext

        if QGuiApplication.instance() is None:
            from specto_app.qt_setup import use_core_profile

            use_core_profile()  # macOS: Storm needs 4.1 core, as usdview asks for there
            # the platform with GL: a test or the app may have handed down "offscreen"
            native = {"win32": "windows", "darwin": "cocoa"}.get(sys.platform)
            platform = ["-platform", native] if native else []
            self._app = QGuiApplication([sys.argv[0], *platform])
        self._surface = QOffscreenSurface()
        self._surface.create()
        self._context = QOpenGLContext()
        if not self._context.create() or not self._context.makeCurrent(self._surface):
            raise RuntimeError("no OpenGL context for Storm")
        from OpenGL import GL  # after the context, as usdrecord does
        from pxr import Gf, Glf, Sdf, Tf, Usd, UsdGeom, UsdImagingGL, UsdShade, Vt

        self.GL, self.Gf, self.Glf, self.Usd, self.UsdImagingGL = GL, Gf, Glf, Usd, UsdImagingGL
        self.Sdf, self.UsdGeom, self.UsdShade, self.Vt, self.Tf = Sdf, UsdGeom, UsdShade, Vt, Tf
        self._noted: set[str] = set()  # the Tf errors seen, without counts
        self._warnings: list[str] = []  # the new ones, until taken
        self.clear = CLEAR
        self._mode_layer: Any = None  # the view mode's edits, in the session layer
        self._environment_layer: Any = None  # the environment light's (SPEC-31), beside it
        self._environment = "stage"
        self._environment_hdri: str | None = None  # the HDRI the dome shows
        self._selection_color: str | None = None
        self.engine: Any = None
        self.stage: Any = None
        self.camera: dict[str, Any] | None = None
        self.width = self.height = 0
        self._fbo: Any = None
        self._pbos: list[int] = []  # free PBOs of the current size
        self.params = UsdImagingGL.RenderParams()
        self.params.enableLighting = True
        self.params.enableSceneMaterials = True
        self.params.enableSceneLights = False  # the headlight instead
        self.params.clearColor = Gf.Vec4f(*CLEAR)
        self.params.frame = Usd.TimeCode.Default()

    # -- the stage -----------------------------------------------------------------------
    def load(
        self,
        path: str,
        up_axis: str,
        meters_per_unit: float,
        camera: dict[str, Any],
        width: int,
        height: int,
        time_codes: tuple[float, float, float] | None = None,
        lighting: Any = None,
    ) -> None:
        """``time_codes`` and ``lighting`` are for rtx's inline root: Storm opens the file
        itself, so the stage already has its own time codes, and lights it with a
        headlight."""
        self._drop_engine()
        self.params.frame = self.Usd.TimeCode.Default()
        self.stage = self.Usd.Stage.Open(path, self.Usd.Stage.LoadAll)
        if self.stage is None:
            raise ValueError(f"cannot open {path}")
        self.engine = self.UsdImagingGL.Engine()
        self.engine.SetRendererAov("color")
        self.params.highlight = False
        self._mode_layer = self._environment_layer = None
        self._environment, self._environment_hdri = "stage", None
        self.params.enableSceneLights = False
        self._noted, self._warnings = set(), []
        self.set_view_mode("lit")
        self.resize(width, height, camera)

    def _drop_engine(self) -> None:
        self.engine = None  # its GL resources go while the context is current
        self.stage = None

    # -- camera and size -----------------------------------------------------------------
    def frustum(self) -> Any:
        """The camera as a ``Gf.Frustum``."""
        Gf, cam = self.Gf, self.camera
        (x0, y0), (x1, y1) = cam["window"]
        w, x, y, z = cam["rotation"]
        return Gf.Frustum(
            Gf.Vec3d(*cam["position"]),
            Gf.Rotation(Gf.Quatd(w, Gf.Vec3d(x, y, z))),
            Gf.Range2d(Gf.Vec2d(x0, y0), Gf.Vec2d(x1, y1)),
            Gf.Range1d(*cam["near_far"]),
            Gf.Frustum.Perspective,
            float(cam["view_distance"]),
        )

    def set_camera(self, camera: dict[str, Any], cut: bool = False) -> None:
        """Storm keeps no history between frames, so a cut is just a move."""
        self.camera = camera

    def set_time(self, seconds: float, cut: bool = False) -> None:
        """USD time (#141), in seconds as rtx takes it: the next render's frame is the
        timecode there, through the stage's timeCodesPerSecond. Storm has no simulation
        clock and no history, so a cut changes nothing."""
        tcps = self.stage.GetTimeCodesPerSecond() if self.stage is not None else 24.0
        self.params.frame = self.Usd.TimeCode(float(seconds) * tcps)

    def resize(self, width: int, height: int, camera: dict[str, Any]) -> None:
        from PySide6.QtCore import QSize
        from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat

        self.camera = camera
        if (width, height) == (self.width, self.height) and self._fbo is not None:
            return
        self.width, self.height = width, height
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        self._fbo = QOpenGLFramebufferObject(QSize(width, height), fmt)
        self._free_pbos()
        if self.engine is not None:
            self.engine.SetRenderBufferSize(self.Gf.Vec2i(width, height))

    # -- frames --------------------------------------------------------------------------
    def _headlight(self, view) -> None:
        """The camera headlight; under an environment light (SPEC-31) the dome lights the
        stage alone, so the simple lights are none."""
        Gf, Glf = self.Gf, self.Glf
        lights = []
        if self._environment == "stage":
            light = Glf.SimpleLight()
            light.position = Gf.Vec4f(*[float(v) for v in self.camera["position"]], 1.0)
            light.transform = view.GetInverse()
            light.ambient = Gf.Vec4f(0.01, 0.01, 0.01, 1.0)
            lights.append(light)
        material = Glf.SimpleMaterial()
        material.ambient = Gf.Vec4f(0.2, 0.2, 0.2, 1.0)
        material.specular = Gf.Vec4f(0.1, 0.1, 0.1, 1.0)
        material.shininess = 32.0
        self.engine.SetLightingState(lights, material, Gf.Vec4f(0.01, 0.01, 0.01, 1.0))

    # -- the environment light (SPEC-31) -----------------------------------------------------
    def set_environment(
        self, environment: str, up_axis: str, assets: Assets, *_controls: Any, **_sky: Any
    ) -> None:
        """Draw ``environment`` (``stage`` or ``hdri``; the sky never reaches Storm, nor
        its controls or ground) from the next frame: the dome light composed in a
        sublayer of the session layer (:func:`.environment.author`) beside the stage's own
        lights, scene lights on (the stage's and the dome) and the headlight off;
        ``stage`` clears all of that."""
        if (environment, assets.hdri) == (self._environment, self._environment_hdri):
            return
        self._environment_hdri = assets.hdri
        if self._environment_layer is None:
            self._environment_layer = self.Sdf.Layer.CreateAnonymous("specto-environment")
            self.stage.GetSessionLayer().subLayerPaths.insert(0, self._environment_layer.identifier)
        author_environment(self._environment_layer, environment, up_axis, assets)
        self._environment = environment
        self.params.enableSceneLights = environment != "stage"

    def submit(self, delta_time: float | None = None) -> tuple[int, int, int]:
        """Draw one frame and start reading it into a PBO; :meth:`finish` maps it.
        ``delta_time`` is rtx's simulation clock: Storm has none."""
        GL, Gf = self.GL, self.Gf
        w, h = self.width, self.height
        frustum = self.frustum()
        view = frustum.ComputeViewMatrix()
        self._fbo.bind()
        GL.glViewport(0, 0, w, h)
        self.engine.SetRenderViewport(Gf.Vec4d(0, 0, w, h))
        self.engine.SetCameraState(view, frustum.ComputeProjectionMatrix())
        self._headlight(view)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glClearColor(*self.clear)
        GL.glClearDepth(1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        try:
            self.engine.Render(self.stage.GetPseudoRoot(), self.params)
        except self.Tf.ErrorException as e:
            self._tolerate(e)
        pbo = self._pbos.pop() if self._pbos else self._new_pbo(w * h * 4)
        # Hgi's present pass leaves another framebuffer bound: read from ours
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self._fbo.handle())
        GL.glBindBuffer(GL.GL_PIXEL_PACK_BUFFER, pbo)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, ctypes.c_void_p(0))
        GL.glBindBuffer(GL.GL_PIXEL_PACK_BUFFER, 0)
        return pbo, w, h

    def _tolerate(self, e: Exception) -> None:
        """A draw's Tf errors: OpenUSD raised them after the draw, so the frame stands;
        each distinct one becomes a warning once."""
        summary = tf_error_summary(str(e))
        key = re.sub(r" \(x\d+\)", "", summary)
        if key not in self._noted:
            self._noted.add(key)
            self._warnings.append(f"OpenUSD reported errors, drawing goes on: {summary}")

    def take_warnings(self) -> list[str]:
        """The warnings noted since the last call, each once a stage."""
        out, self._warnings = self._warnings, []
        return out

    def _new_pbo(self, size: int) -> int:
        GL = self.GL
        pbo = int(GL.glGenBuffers(1))
        GL.glBindBuffer(GL.GL_PIXEL_PACK_BUFFER, pbo)
        GL.glBufferData(GL.GL_PIXEL_PACK_BUFFER, size, None, GL.GL_STREAM_READ)
        GL.glBindBuffer(GL.GL_PIXEL_PACK_BUFFER, 0)
        return pbo

    def _free_pbos(self) -> None:
        if self._pbos:
            self.GL.glDeleteBuffers(len(self._pbos), self._pbos)
        self._pbos = []

    def finish(self, op: tuple[int, int, int], sink: Callable[[np.ndarray], None]) -> None:
        """Wait for a frame's read and hand it to ``sink`` as (H, W, 4) uint8, top row
        first, while its PBO is mapped."""
        GL = self.GL
        pbo, w, h = op
        size = w * h * 4
        GL.glBindBuffer(GL.GL_PIXEL_PACK_BUFFER, pbo)
        try:
            address = GL.glMapBufferRange(GL.GL_PIXEL_PACK_BUFFER, 0, size, GL.GL_MAP_READ_BIT)
            if not address:
                raise RuntimeError("glMapBufferRange failed")
            address = address if isinstance(address, int) else address.value
            mapped = (ctypes.c_ubyte * size).from_address(address)
            try:
                sink(np.frombuffer(mapped, np.uint8).reshape(h, w, 4)[::-1])  # GL is bottom-up
            finally:
                GL.glUnmapBuffer(GL.GL_PIXEL_PACK_BUFFER)
        finally:
            GL.glBindBuffer(GL.GL_PIXEL_PACK_BUFFER, 0)
        if (w, h) == (self.width, self.height):
            self._pbos.append(pbo)
        else:
            GL.glDeleteBuffers(1, [pbo])  # drawn before a resize

    # -- pick ----------------------------------------------------------------------------
    def pick(self, x: float, y: float) -> dict[str, Any] | None:
        """The prim under NDC ``(x, y)`` (-1..1, +y up) of the current frame:
        ``{path, position, normal}`` in the stage's frame, or None over the background."""
        Gf = self.Gf
        narrow = self.frustum().ComputeNarrowedFrustum(
            Gf.Vec2d(x, y), Gf.Vec2d(1.0 / self.width, 1.0 / self.height)
        )
        args = (
            narrow.ComputeViewMatrix(),
            narrow.ComputeProjectionMatrix(),
            self.stage.GetPseudoRoot(),
            self.params,
        )
        try:
            hit = self.engine.TestIntersection(*args)
        except self.Tf.ErrorException as e:
            # the pick's own sync raised (see _tolerate); synced now, a second one answers
            self._tolerate(e)
            hit = self.engine.TestIntersection(*args)
        # 26.08: (point, normal, primPath, instanceIndex, topLevelPath, topLevelInstanceIndex)
        if not hit or hit[2].isEmpty:
            return None
        return {
            "path": str(hit[2]),
            "position": [float(v) for v in hit[0]],
            "normal": [float(v) for v in hit[1]],
        }

    # -- view modes (#153) ---------------------------------------------------------------
    def set_view_mode(self, mode: str, overlay: Any = None) -> str:
        """Draw ``mode`` from the next frame; ``overlay``: the stage's
        :class:`.collision.CollisionOverlay`, for collision. Answers a note on what the
        mode cannot show."""
        draw = self.UsdImagingGL.DrawMode
        layer = self._edits()
        layer.Clear()
        lit = mode == "lit"
        wire = mode == "wireframe"
        self.params.drawMode = draw.DRAW_WIREFRAME if wire else draw.DRAW_SHADED_SMOOTH
        self.params.enableLighting = lit
        self.params.enableSceneMaterials = lit
        self.clear = CLEAR if mode in ("lit", "unlit") else CLEAR_DARK
        self.params.clearColor = self.Gf.Vec4f(*self.clear)
        if mode in ("unlit", "wireframe"):
            self._preview_colors(layer)
            return UNLIT_NOTE
        if mode == "collision":
            self._collision(layer, overlay)
        return ""

    def _edits(self) -> Any:
        """The session layer's sublayer for the view mode's edits, made once a stage."""
        if self._mode_layer is None:
            self._mode_layer = self.Sdf.Layer.CreateAnonymous("specto-view-mode")
            self.stage.GetSessionLayer().subLayerPaths.insert(0, self._mode_layer.identifier)
        return self._mode_layer

    def _preview_colors(self, layer: Any) -> None:
        """Each gprim bound to a UsdPreviewSurface: its constant ``diffuseColor`` as a
        constant ``displayColor`` (what Storm draws with lighting and materials off)."""
        Sdf, Vt, Gf = self.Sdf, self.Vt, self.Gf
        gprims = [p for p in self.stage.Traverse() if p.IsA(self.UsdGeom.Gprim)]
        if not gprims:
            return
        binding = self.UsdShade.MaterialBindingAPI
        materials, _ = binding.ComputeBoundMaterials(gprims)
        colors: dict[str, Any] = {}

        def color_of(material: Any) -> tuple[float, float, float] | None:
            if not material:
                return None
            key = str(material.GetPath())
            if key not in colors:
                colors[key] = self._preview_color(material)
            return colors[key]

        color3 = Sdf.ValueTypeNames.Color3fArray
        with Sdf.ChangeBlock():
            for prim, material in zip(gprims, materials, strict=True):
                own = color_of(material)
                faces = self._subset_colors(prim, color_of, own)
                if faces is None and own is None:
                    continue
                spec = Sdf.CreatePrimInLayer(layer, prim.GetPath())
                attr = Sdf.AttributeSpec(spec, "primvars:displayColor", color3)
                if faces is not None:
                    attr.default = Vt.Vec3fArray([Gf.Vec3f(*c) for c in faces])
                    attr.SetInfo("interpolation", "uniform")
                else:
                    attr.default = Vt.Vec3fArray([Gf.Vec3f(*own)])
                    attr.SetInfo("interpolation", "constant")

    def _subset_colors(
        self, prim: Any, color_of: Callable[[Any], Any], own: Any
    ) -> list[tuple[float, float, float]] | None:
        """A mesh's per-face colours from the materials bound to its ``materialBind``
        GeomSubsets (the rest of its faces: its own material's colour, else the preview
        fallback); None for a mesh with no such subset that has a colour."""
        if not prim.IsA(self.UsdGeom.Mesh):
            return None
        binding = self.UsdShade.MaterialBindingAPI
        subsets = binding(prim).GetMaterialBindSubsets()
        if not subsets:
            return None
        count = len(self.UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get() or [])
        faces = [own or PREVIEW_FALLBACK] * count
        found = False
        for subset in subsets:
            color = color_of(binding(subset.GetPrim()).ComputeBoundMaterial()[0])
            if color is None:
                continue
            for i in subset.GetIndicesAttr().Get() or []:
                if 0 <= i < count:
                    faces[i] = color
                    found = True
        return faces if found else None

    def _preview_color(self, material: Any) -> tuple[float, float, float] | None:
        """A material's UsdPreviewSurface ``diffuseColor`` when it is a constant."""
        shader = material.ComputeSurfaceSource()[0]
        if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
            return None
        diffuse = shader.GetInput("diffuseColor")
        if not diffuse:
            return PREVIEW_FALLBACK
        if diffuse.HasConnectedSource():
            return None  # a texture
        value = diffuse.Get()
        return PREVIEW_FALLBACK if value is None else tuple(float(v) for v in value)

    def _collision(self, layer: Any, overlay: Any) -> None:
        """The overlay referenced at its root, every other gprim hidden by an over."""
        from .collision import ROOT

        Sdf = self.Sdf
        with Sdf.ChangeBlock():
            for path in overlay.hidden:
                spec = Sdf.CreatePrimInLayer(layer, path)
                vis = Sdf.AttributeSpec(spec, "visibility", Sdf.ValueTypeNames.Token)
                vis.default = "invisible"
            root = Sdf.CreatePrimInLayer(layer, ROOT)
            root.specifier = Sdf.SpecifierDef
            root.typeName = "Xform"
            root.nameParent.specifier = Sdf.SpecifierDef
            root.nameParent.typeName = "Scope"
            root.referenceList.Prepend(Sdf.Reference(overlay.layer.identifier))

    def outline(self, paths: list[str], color: str) -> None:
        """The selection (#140): Storm's own highlight, a tint of ``color`` (``#rrggbb``)
        over the geometry at ``paths`` (gprims), drawn from the next frame on."""
        if self.engine is None:
            return
        if color != self._selection_color:
            rgb = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
            self.engine.SetSelectionColor(self.Gf.Vec4f(*rgb, 1.0))
            self._selection_color = color
        self.engine.SetSelected([self.Sdf.Path(p) for p in paths])
        self.params.highlight = bool(paths)

    def close(self) -> None:
        if self._context.makeCurrent(self._surface):
            self._free_pbos()
            self._drop_engine()
            self._fbo = None
            self._context.doneCurrent()
