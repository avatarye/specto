import QtQuick

// The link node (glossary): a small dot on its wire in the chain's colour, no label
// (#121): what the link does is the tip of the input port it filters; hovering the dot
// grows it and names the ends (`tip`, ports_info.link_tip; #127: the names link to their
// views, so the tip waits for the pointer like a port's). Clicking it selects the link
// and opens the link view (`openLink`); the selected link's dot is ringed in its colour. The item is the dot's click area (`link_dot_hit`), which the
// node placement keeps clear of headers and port circles. Keeps its size on screen
// (`ui` = canvas px per screen px). Drawn by the overworld (`linkNode:<id>`) and the
// wiring layer (`wireNode:<id>`, #82).
Item {
    id: root
    property var wire: ({})
    property real ui: 1
    signal openLink()
    readonly property bool selected: overworld.selectedLink === wire.id
    readonly property color tone: theme.colors[wire.chain] || theme.colors.chain_none
    readonly property var tip: wire.tip || ({})
    objectName: "linkNode:" + (wire.id || "")
    x: (wire.nx || 0) - width / 2
    y: (wire.ny || 0) - height / 2
    width: theme.metrics.link_dot_hit * root.ui
    height: width

    // the selection ring around the dot
    Rectangle {
        visible: root.selected
        anchors.centerIn: parent
        width: dot.width + 8 * root.ui
        height: width
        radius: width / 2
        color: "transparent"
        border.color: root.tone
        border.width: 2 * root.ui
    }
    Rectangle {
        id: dot
        objectName: "dot:" + root.objectName
        anchors.centerIn: parent
        width: theme.metrics.link_dot_px * root.ui
        height: width
        radius: width / 2
        color: root.tone
        border.color: theme.colors.surface
        border.width: 1.5 * root.ui
        scale: hover.hovered ? 1.3 : 1
        Behavior on scale { NumberAnimation { duration: theme.metrics.anim_ms } }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    PortTip {
        tag: root.objectName
        info: root.tip
        show: hover.hovered && !root.selected && !!root.tip.parts
        lingers: !root.selected
    }
    TapHandler {
        // the tap is this node's alone: a port circle under the dot must not see it
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: {
            // the view first: selecting may rebuild the wires and this node with them
            root.openLink()
            overworld.selectLink(root.wire.id)
        }
    }
}
