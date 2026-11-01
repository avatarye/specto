import QtQuick
import QtQuick.Window

// The canvas tile floor (#191, #197; v3 #201, design "08c Tile floor v3"): square tiles
// fixed to the screen under the views, 8 px cells (a 3 px tile at rest, 6 px lit), lit by
// the agent's status at the S icon, by new views and by the selection's footprint
// (`tileFloor`, canvas/tile_floor.py). One ShaderEffect renders into its layer at the
// area's full resolution with nearest sampling (a texture px is round(dpr) device px).
// The grid changes only with the area's size: a pan or a zoom moves nothing, and the
// texture re-renders only when a uniform changes (a frame of a running cue; the camera,
// while a cue sits on a view). Takes no input.
Item {
    id: root
    objectName: "tileFloor"
    // the canvas camera, for the cues placed in the world: the world origin in this item's
    // px, and the zoom (1 in the normal view)
    property real originX: 0
    property real originY: 0
    property real zoom: 1
    readonly property real dpr: Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1
    readonly property var grid: tileFloor.grid(width, height, dpr)
    readonly property var u: tileFloor.uniforms
    // the software backend (the tests' Qt Quick) runs no shaders
    readonly property bool gpu: GraphicsInfo.api !== GraphicsInfo.Software
                                && GraphicsInfo.api !== GraphicsInfo.Unknown
    clip: true

    ShaderEffect {
        id: fx
        objectName: "tileFloorEffect"
        width: root.grid.drawW
        height: root.grid.drawH
        visible: root.gpu
        layer.enabled: root.gpu
        layer.smooth: false
        layer.textureSize: Qt.size(root.grid.texW, root.grid.texH)
        property vector2d texSize: Qt.vector2d(root.grid.texW, root.grid.texH)
        // x, y: the first cell's corner, texture px; z: cell; w: area px per texture px
        property vector4d cells: Qt.vector4d(root.grid.marginX, root.grid.marginY, root.grid.n, root.grid.unit)
        property vector2d count: Qt.vector2d(root.grid.cols, root.grid.rows)
        // the canvas camera in texture px (origin, texture px per world px); only while
        // a cue sits in the world, so a pan otherwise changes no uniform
        property vector4d cam: root.u.worldLive
            ? Qt.vector4d(root.originX / root.grid.unit, root.originY / root.grid.unit,
                          root.zoom / root.grid.unit, 0)
            : Qt.vector4d(0, 0, 0, 0)
        property color rest: theme.colors.bg_tint
        property real dim: tileFloor.dim
        property vector4d g0: root.u.g0
        property vector4d p0: root.u.p0
        property vector4d c0: root.u.c0
        property vector4d g1: root.u.g1
        property vector4d p1: root.u.p1
        property vector4d c1: root.u.c1
        property vector4d g2: root.u.g2
        property vector4d p2: root.u.p2
        property vector4d c2: root.u.c2
        property vector4d g3: root.u.g3
        property vector4d p3: root.u.p3
        property vector4d c3: root.u.c3
        property vector4d g4: root.u.g4
        property vector4d p4: root.u.p4
        property vector4d c4: root.u.c4
        property vector4d g5: root.u.g5
        property vector4d p5: root.u.p5
        property vector4d c5: root.u.c5
        property vector4d g6: root.u.g6
        property vector4d p6: root.u.p6
        property vector4d c6: root.u.c6
        property vector4d g7: root.u.g7
        property vector4d p7: root.u.p7
        property vector4d c7: root.u.c7
        fragmentShader: "shaders/floor.frag.qsb"
    }
}
