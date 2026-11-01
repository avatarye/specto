import QtQuick

// The walk-through's overlay (#174, design page 06): over the canvas area, in the normal
// view and the overworld alike (`walkStage.over`: the overworld at the stage's shot).
// The path's views keep a soft ring in the chain colour for the whole walk (the rest of
// the canvas dims: ViewSlot, OverCard and the scrim in CanvasArea); the current hop's
// two views get a stronger ring that fades in when the camera settles (220 ms). At 1:1
// the walked links show as curves, the current one solid with its kind chip and its
// pulse (WalkPulse); the summary hangs a chip of counts under each view. Any press or
// wheel on the canvas pauses the walk and goes on to what is under it. Tokens and wires
// draw here, never in the views.
Item {
    id: root
    objectName: "walkOverlay"
    readonly property bool on: walkStage.active
    readonly property bool inOver: walkStage.over && overworld.active
    readonly property var pan: controller.pan
    readonly property real pad: theme.metrics.pad
    readonly property real zoom: inOver ? walkStage.overZoom : 1
    readonly property real gutter: theme.metrics.gutter
    readonly property var hop: walkStage.hop
    readonly property var focusIds: walkStage.focusViews
    readonly property bool settledHere: walkStage.settled && walkStage.settledStep === walkStage.step
    readonly property color walkTone: theme.colors[(walkStage.faces[walkStage.target] || {}).chain]
                                      || theme.colors.chain_0
    visible: on || fade.running
    opacity: on ? 1 : 0
    Behavior on opacity { NumberAnimation { id: fade; duration: 220 } }

    // canvas px -> area px at the current camera level
    function sx(cx) { return inOver ? walkStage.overX + cx * walkStage.overZoom : pad + cx - pan.x }
    function sy(cy) { return inOver ? walkStage.overY + cy * walkStage.overZoom : pad + cy - pan.y }
    // a view's frame (canvas px, the gutter inset) or the project node's tile
    function frameOf(vid) {
        if (vid === "project") {
            var n = controller.projectNode
            return { x: n.x || 0, y: n.y || 0, w: n.w || 52, h: n.h || 52, floating: false }
        }
        var v = controller.views[vid]
        if (!v) return null
        return { x: v.x + gutter, y: v.y + gutter, w: v.w - 2 * gutter, h: v.h - 2 * gutter,
                 floating: v.floating === true, fx: (v.fx || 0) + gutter, fy: (v.fy || 0) + gutter }
    }

    // any canvas input pauses the walk; the press or the wheel goes on to the canvas
    MouseArea {
        objectName: "walkPauseCatcher"
        anchors.fill: parent
        enabled: root.on
        acceptedButtons: Qt.AllButtons
        onPressed: function (mouse) { walkStage.pause(); mouse.accepted = false }
        onWheel: function (wheel) { walkStage.pause(); wheel.accepted = false }
    }

    // the walked links at 1:1 (the overworld draws its own wires): the current hop's
    // solid, the rest of the path faint
    Repeater {
        model: root.on && !root.inOver && !walkStage.preparing ? walkStage.pathLinks : []
        delegate: LinkCurve {
            required property var modelData
            objectName: "walkWire:" + modelData
            readonly property bool current: modelData === root.hop.link
            wire: controller.wireMap[modelData] || ({})
            faint: !current
            strokeWidth: current ? 3 : 2
            toX: function (cx) { return root.sx(cx) }
            toY: function (cy) { return root.sy(cy) }
        }
    }

    // a ring round every view on the path, the stronger one on the current hop's ends
    Repeater {
        model: root.on ? walkStage.pathViews : []
        delegate: Rectangle {
            id: ring
            required property var modelData
            readonly property var f: root.frameOf(modelData)
            readonly property bool strong: root.focusIds.indexOf(modelData) >= 0
                                           && root.hop.from !== undefined
            readonly property color tone: theme.colors[(walkStage.faces[modelData] || {}).chain]
                                          || root.walkTone
            readonly property real spread: strong ? 5 : 4
            objectName: "walkRing:" + modelData
            visible: f !== null
            x: f ? (f.floating && !root.inOver ? root.pad + f.fx : root.sx(f.x)) - spread : 0
            y: f ? (f.floating && !root.inOver ? root.pad + f.fy : root.sy(f.y)) - spread : 0
            width: f ? f.w * root.zoom + 2 * spread : 0
            height: f ? f.h * root.zoom + 2 * spread : 0
            radius: (modelData === "project" ? theme.metrics.project_node_radius
                                             : theme.metrics.radius_card) * root.zoom + spread
            color: "transparent"
            border.width: 2
            border.color: Qt.alpha(tone, 0.45)
            Rectangle {
                objectName: "walkFocus:" + ring.modelData
                anchors.fill: parent
                radius: parent.radius
                color: "transparent"
                border.width: 3
                border.color: ring.tone
                opacity: ring.strong && root.settledHere ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 220 } }
            }
        }
    }

    // the current hop's link: its kind chip over the node, its pulse
    WalkPulse {
        id: pulse
        anchors.fill: parent
        visible: root.on && !root.inOver && root.hop.link !== undefined
        wire: controller.wireMap[root.hop.link || ""] || ({})
        tone: root.walkTone
        kind: root.hop.kind || ""
        ratio: root.hop.ratio === undefined ? -1 : root.hop.ratio
        running: visible && root.settledHere
        still: walkStage.reducedMotion
        toX: function (cx) { return root.sx(cx) }
        toY: function (cy) { return root.sy(cy) }
        // the pulse restarts whenever the hop is entered again or play resumes
        readonly property bool playing: walkStage.playing
        onPlayingChanged: if (playing) restart()
        Connections {
            target: walkStage
            function onSettledOn(step) { pulse.restart() }
        }
    }
    Rectangle {
        objectName: "walkKindChip"
        visible: pulse.visible && pulse.length > 0 && (root.hop.kind_label || "") !== ""
        readonly property string tip: root.hop.kind_label || ""
        x: pulse.node.x - width / 2
        y: pulse.node.y - height - 12
        width: kindText.width + 16
        height: kindText.implicitHeight + 6
        radius: height / 2
        color: theme.colors.tip_port_bg
        border.width: 1
        border.color: theme.colors.tip_port_border
        HoverHandler { id: kindHover }
        Tip { visible: kindHover.hovered && kindText.truncated; text: parent.tip; tag: "walkKindChip" }
        Text {
            id: kindText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, 260)  // a long path: the middle elided (#183)
            elide: Text.ElideMiddle
            text: root.hop.kind_label || ""
            color: theme.colors.tip_port_text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            font.weight: Font.DemiBold
        }
    }

    // the summary: a chip of counts under each view (screen size, whatever the zoom)
    Repeater {
        model: root.on && walkStage.atSummary && root.settledHere ? walkStage.pathViews : []
        delegate: Rectangle {
            required property var modelData
            readonly property var f: root.frameOf(modelData)
            readonly property string words: walkStage.chips[modelData] || ""
            readonly property string tip: words
            objectName: "walkChip:" + modelData
            visible: f !== null && words !== ""
            x: f ? Math.max(4, Math.min(root.width - width - 4, root.sx(f.x + f.w / 2) - width / 2)) : 0
            y: f ? root.sy(f.y + f.h) + 10 : 0
            width: chipText.width + 18
            height: chipText.implicitHeight + 6
            radius: height / 2
            color: root.walkTone
            border.width: 1.5
            border.color: theme.colors.surface
            HoverHandler { id: chipHover }
            Tip { visible: chipHover.hovered && chipText.truncated; text: parent.tip; tag: parent.objectName }
            Text {
                id: chipText
                anchors.centerIn: parent
                width: Math.min(implicitWidth, 260)  // a long path: the middle elided (#183)
                elide: Text.ElideMiddle
                text: parent.words
                color: theme.colors.surface
                font.family: theme.fonts.mono
                font.pixelSize: 12
            }
        }
    }

    // reduced motion: the camera cuts under a 150 ms cross-fade
    Rectangle {
        id: cutFade
        anchors.fill: parent
        color: theme.colors.bg
        opacity: 0
        Connections {
            target: walkStage
            function onCut() { cutAnim.restart() }
        }
        SequentialAnimation {
            id: cutAnim
            PropertyAction { target: cutFade; property: "opacity"; value: 0.85 }
            NumberAnimation { target: cutFade; property: "opacity"; to: 0; duration: 150 }
        }
    }
}
