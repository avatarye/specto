import QtQuick

// The project source (glossary, #83): the fixed node at the canvas origin, left of the
// file tree, with no card body. Nobody moves, resizes, deletes or locks it: it has no
// header, no handles and no menu. A rounded tile showing the identicon seeded "root"
// (#127) with no port of its own: the file tree's input circle already shows root, and
// the node's wires leave its right edge. Hovering it shows a port tip, "all files" and
// the count over "the project folder". A double-click opens the scratch pad (#184). Its refresh is a port-like circle at the node's
// bottom centre (chains.project_refresh_anchor). `node` is `controller.projectNode`; `ui`
// scales the border in the overworld (canvas px per screen px).
Item {
    id: root
    property var node: controller.projectNode
    property real ui: 1
    // the refresh circle (the overworld's copy leaves it out)
    property bool refreshable: true
    objectName: "projectNode"
    width: node.w || 52
    height: node.h || 52

    Rectangle {
        id: face
        anchors.fill: parent
        radius: theme.metrics.project_node_radius
        // the identicon's own ground, so the grid sits on the tile without a seam
        color: theme.colors.surface_alt
        border.color: faceHover.hovered ? theme.colors.text_muted : theme.colors.chain_none
        border.width: 2 * root.ui
        Image {
            // the 5x5 grid in whole pixels, a little over half the tile
            readonly property int cell: Math.floor(root.width * 0.6 / 5)
            objectName: "identicon:projectNode"
            anchors.centerIn: parent
            width: 5 * cell
            height: width
            source: root.node.icon || ""
            sourceSize: Qt.size(Math.round(width * Screen.devicePixelRatio),
                                Math.round(height * Screen.devicePixelRatio))
            smooth: false
        }
        HoverHandler { id: faceHover; cursorShape: Qt.ArrowCursor }
        // the scratch pad (#184): opened beside the file tree, or brought into view
        TapHandler { onDoubleTapped: controller.openScratch() }
        // the fly-to pulse: the project's name clicked in a tip (#127)
        Rectangle {
            id: pulse
            objectName: "flash:projectNode"
            anchors.fill: parent
            radius: parent.radius
            color: theme.colors.accent
            opacity: 0
        }
        SequentialAnimation {
            id: pulseAnim
            NumberAnimation { target: pulse; property: "opacity"; to: 0.35; duration: theme.metrics.anim_ms }
            NumberAnimation { target: pulse; property: "opacity"; to: 0; duration: theme.metrics.flash_ms }
        }
        Connections {
            target: controller
            function onFlash(vid) { if (vid === root.node.id) pulseAnim.restart() }
        }
        PortTip {
            tag: "projectNode"
            info: root.node.tip || ({})
            gap: root.refreshable ? theme.metrics.port_px / 2 : 0
            show: faceHover.hovered && !refreshHover.hovered && !!info.what
        }
    }
    // refresh (#102): the whole project rescans once (the file tree's intaker and every
    // pinned one); drawn like a port circle with the refresh glyph inside
    Rectangle {
        objectName: root.refreshable ? "refresh:project" : ""
        visible: root.refreshable
        readonly property var at: root.node.refresh || ({ x: 0, y: 0 })
        readonly property string tip: "refresh all · rescan the project"
        width: theme.metrics.port_px
        height: width
        radius: width / 2
        x: at.x - (root.node.x || 0) - width / 2
        y: at.y - (root.node.y || 0) - height / 2
        color: refreshHover.hovered ? theme.colors.surface_hover : theme.colors.surface
        border.color: refreshHover.hovered ? theme.colors.text_muted : theme.colors.chain_none
        border.width: 2
        scale: refreshHover.hovered ? 1.15 : 1
        Behavior on scale { NumberAnimation { duration: theme.metrics.anim_ms } }
        Icon {
            anchors.centerIn: parent
            name: "refresh"
            size: 12
            color: refreshHover.hovered ? theme.colors.text : theme.colors.text_muted
        }
        HoverHandler { id: refreshHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: controller.refreshProject() }
        Tip {
            tag: "refresh:project"
            visible: refreshHover.hovered && root.refreshable
            text: parent.tip
        }
    }
}
