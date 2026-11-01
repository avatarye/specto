import QtQuick
import QtQuick.Shapes

// The S character (#170), Specto's mark: a head with two eyes, drawn in the mark's 100-unit
// grid scaled to this item's width. The art is data: `logoArt` (specto_app/logo.py reading
// the designer's assets/logo/*.svg) gives the path (a stroked centre line or a filled
// outline, as the file draws it) and the eyes. `small` is the 16 px drawing that keeps the
// mark legible below about 24 px. The curve renderer keeps the edges sharp at any scale
// and device pixel ratio. AgentIcon.qml draws its own animated eyes over a
// body-only mark (`eyes: false`).
Item {
    id: root
    property color ink: theme.colors.text
    property color eyeColor: theme.colors.agent
    property bool eyes: true
    property bool small: false
    readonly property var art: small ? logoArt.small : logoArt.body
    readonly property real unit: width / 100
    implicitWidth: 100
    implicitHeight: 100

    function capStyle(cap) {
        return cap === "round" ? ShapePath.RoundCap : cap === "square" ? ShapePath.SquareCap : ShapePath.FlatCap
    }
    function joinStyle(join) {
        return join === "round" ? ShapePath.RoundJoin : join === "bevel" ? ShapePath.BevelJoin : ShapePath.MiterJoin
    }

    Shape {
        objectName: "sBody"
        width: 100
        height: 100
        scale: root.unit
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        // a stroked centre line, or a filled outline, as the design file draws it
        ShapePath {
            strokeColor: root.art.filled ? "transparent" : root.ink
            strokeWidth: root.art.filled ? -1 : root.art.width
            fillColor: root.art.filled ? root.ink : "transparent"
            fillRule: root.art.fillRule === "evenodd" ? ShapePath.OddEvenFill : ShapePath.WindingFill
            capStyle: root.capStyle(root.art.cap)
            joinStyle: root.joinStyle(root.art.join)
            PathSvg { path: root.art.path }
        }
    }
    Repeater {
        model: root.eyes ? (root.small ? logoArt.small.eyes : logoArt.open) : []
        Rectangle {
            required property var modelData
            readonly property real r: modelData.r * root.unit
            x: modelData.cx * root.unit - r
            y: modelData.cy * root.unit - r
            width: 2 * r
            height: 2 * r
            radius: r
            color: root.eyeColor
            antialiasing: true
        }
    }
}
