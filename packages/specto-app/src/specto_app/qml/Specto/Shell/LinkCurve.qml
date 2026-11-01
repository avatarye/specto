import QtQuick
import QtQuick.Shapes

// One link as a curve from the source view's output circle to the input circle the link
// occupies on the target, in the chain's colour: clear in the overworld and in the wiring
// layer (#82; `faint` is kept for a quieter rendering). The normal view draws none (#81).
// It leaves the output rightwards and arrives at the input from the left, whatever the
// two views' relative position. `wire` is one entry of controller.wires /
// overworld.wires; `toX`/`toY` map canvas px into this item's parent. The parent names
// it (`overWire:<id>` in the overworld, `wire:<id>` in the wiring layer).
Item {
    id: root
    property var wire: ({})
    property bool faint: true
    property bool selected: false
    property real strokeWidth: faint ? 2 : 3
    property var toX: function (cx) { return cx }
    property var toY: function (cy) { return cy }
    readonly property color tone: theme.colors[wire.chain] || theme.colors.chain_none
    readonly property real x1: toX(wire.x1)
    readonly property real y1: toY(wire.y1)
    readonly property real x2: toX(wire.x2)
    readonly property real y2: toY(wire.y2)
    // the bend: a third of the run, at least a little so stubs and loops still curve
    readonly property real bend: Math.max(24, Math.abs(x2 - x1) / 3)
    opacity: faint ? 0.38 : 1

    Shape {
        // antialiased in the fragment shader, dashes and caps included, no multisampling (#96)
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.selected ? theme.colors.accent : root.tone
            strokeWidth: root.selected ? root.strokeWidth + 1.5 : root.strokeWidth
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            strokeStyle: root.wire.twoWay ? ShapePath.DashLine : ShapePath.SolidLine
            dashPattern: [4, 3]
            startX: root.x1
            startY: root.y1
            PathCubic {
                x: root.x2; y: root.y2
                control1X: root.x1 + root.bend
                control1Y: root.y1
                control2X: root.x2 - root.bend
                control2Y: root.y2
            }
        }
    }
    // end dots on the view edges (the target end is filled: data flows into it)
    Rectangle { x: root.x1 - 3; y: root.y1 - 3; width: 6; height: 6; radius: 3; color: theme.colors.surface; border.color: root.tone; border.width: 1.5 }
    Rectangle { x: root.x2 - 3; y: root.y2 - 3; width: 6; height: 6; radius: 3; color: root.tone }
}
