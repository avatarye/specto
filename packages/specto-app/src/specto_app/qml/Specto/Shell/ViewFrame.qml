import QtQuick
import Specto.Native

// The view frame (glossary: link chain): one rounded rectangle in the chain's colour
// around the whole view, header and body, rounded at all four corners. It is drawn
// inside a transparent gutter (theme.metrics.gutter) of the view's rect, so two snapped
// views, whose document rects touch, show a 2 * gutter gap. The content clips to the
// rounded shape on the scene graph (RoundedClip: a stencil clip, so it holds for the
// gallery's nodes, the video's and the web views' textures alike, and whatever lies
// behind the view, the canvas, its shadow, another view, shows through the corners);
// the stroke lies above everything and covers the clip's edge. Children of a ViewFrame
// land in `contentItem`, the area inside the stroke.
Item {
    id: root
    property string viewId: ""
    property color tone: theme.colors.border
    property real strokeWidth: 1.5
    property real radius: theme.metrics.radius_card
    readonly property real gutter: theme.metrics.gutter
    property bool shadow: true
    // a floating view (#112) casts a deeper shadow: it lies above the canvas
    property bool elevated: false
    property alias contentItem: inner
    default property alias content: inner.data
    objectName: "frame:" + viewId
    anchors.fill: parent
    anchors.margins: gutter

    SoftShadow {
        visible: root.shadow
        radius: root.radius
        blur: root.elevated ? 1.0 : 0.55
        strength: root.elevated ? 0.45 : 0.22
    }

    // the content sits one pixel inside the frame's edge, so the clip's (unantialiased)
    // curve lies under the stroke, concentric with it
    RoundedClip {
        id: inner
        objectName: "frameClip:" + root.viewId
        anchors.fill: parent
        anchors.margins: 1
        radius: root.radius - 1
        Rectangle {
            anchors.fill: parent
            color: theme.colors.surface
            z: -1
        }
    }

    Rectangle {
        objectName: "frameStroke:" + root.viewId
        anchors.fill: parent
        radius: root.radius
        color: "transparent"
        border.width: root.strokeWidth
        border.color: root.tone
        z: 21
    }
}
