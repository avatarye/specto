import QtQuick

// A port capsule (#160, glossary): the two port circles a link joins show the same
// identicon (#81, #92); when they come within the join distance a solid capsule in the
// chain's colour joins them, the identicon in a port circle's disc at its midpoint, and
// it holds until they pass the break distance (`canvas.capsules`; `pair` is the
// controller's capsuleMap entry). Joining, the capsule grows out of both circles towards
// the middle; splitting, it shrinks back into them (`progress`, anim_ms). Hovering it
// shows the link's tip: the input port's (#127), what flows from whom and the filter; the
// circles under it show none. With `openable` (the wiring layer, #82) a click opens the
// link view (`openLink`), as the link dot it stands for would. Presses go through it
// otherwise, to the borders and headers under it. `toX`/`toY` map canvas px to the
// layer's px.
Item {
    id: root
    property var pair: ({})
    property var toX: function (cx) { return cx }
    property var toY: function (cy) { return cy }
    property bool openable: false
    signal openLink()
    readonly property bool joined: pair.joined === true
    readonly property real size: theme.metrics.port_px
    readonly property real ring: 2
    readonly property color tone: theme.colors[pair.chain] || theme.colors.chain_none
    // the input circle the capsule ends on: its identicon (the source's) and its tip
    readonly property var port: {
        const v = controller.views[pair.inView]
        return (v && v.inPorts && v.inPorts[pair.inIndex]) || ({})
    }
    readonly property var tip: port.tip || ({})
    readonly property bool hovered: bodyHover.hovered || iconHover.hovered
    // 0 apart .. 1 joined, animated both ways
    property real progress: joined ? 1 : 0
    Behavior on progress { NumberAnimation { duration: theme.metrics.anim_ms; easing.type: Easing.OutCubic } }
    objectName: "capsule:" + (pair.id || "")
    visible: progress > 0
    anchors.fill: parent

    readonly property real x1: toX(pair.x1 || 0)
    readonly property real y1: toY(pair.y1 || 0)
    readonly property real x2: toX(pair.x2 || 0)
    readonly property real y2: toY(pair.y2 || 0)
    readonly property real span: Math.hypot(x2 - x1, y2 - y1)

    // the body along the line from the output circle's centre (0) to the input's (span):
    // two rounded halves, each reaching from its circle `progress` of the way to the
    // middle, meet there in one capsule
    Item {
        id: body
        objectName: "capsuleBody:" + (root.pair.id || "")
        readonly property color tone: root.tone
        readonly property real r: root.size / 2
        readonly property real reach: root.span / 2 * root.progress
        x: root.x1
        y: root.y1 - root.size / 2
        width: root.span
        height: root.size
        transformOrigin: Item.Left
        rotation: Math.atan2(root.y2 - root.y1, root.x2 - root.x1) * 180 / Math.PI
        opacity: root.progress
        Rectangle {
            x: -body.r
            width: 2 * body.r + body.reach
            height: root.size
            radius: body.r
            color: root.tone
        }
        Rectangle {
            x: root.span - body.reach - body.r
            width: 2 * body.r + body.reach
            height: root.size
            radius: body.r
            color: root.tone
        }
        HoverHandler { id: bodyHover; blocking: true; enabled: root.joined }
        TapHandler {
            enabled: root.openable && root.joined
            cursorShape: Qt.PointingHandCursor
            onTapped: root.openLink()
        }
    }

    // the identicon at the midpoint, upright: a port circle's disc on the capsule
    Rectangle {
        id: disc
        objectName: "capsuleIcon:" + (root.pair.id || "")
        readonly property string icon: root.port.icon || ""
        readonly property color tone: root.tone
        x: (root.x1 + root.x2) / 2 - width / 2
        y: (root.y1 + root.y2) / 2 - height / 2
        width: root.size
        height: root.size
        radius: width / 2
        scale: root.progress
        color: theme.colors.surface
        border.color: root.tone
        border.width: root.ring
        Image {
            readonly property int px: Math.round((root.size - 2 * root.ring) * Screen.devicePixelRatio)
            anchors.fill: parent
            anchors.margins: root.ring
            visible: disc.icon !== "" && status === Image.Ready
            source: disc.icon
            sourceSize: Qt.size(px, px)
            smooth: false
        }
        HoverHandler { id: iconHover; blocking: true; enabled: root.joined }
        TapHandler {
            enabled: root.openable && root.joined
            cursorShape: Qt.PointingHandCursor
            onTapped: root.openLink()
        }
        PortTip {
            tag: root.objectName
            info: root.tip
            show: root.hovered && root.joined && !!root.tip.what
        }
    }
}
