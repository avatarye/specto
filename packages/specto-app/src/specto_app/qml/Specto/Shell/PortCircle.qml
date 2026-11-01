import QtQuick

// A port circle (glossary: port, identicon; #81): a circle centred on the view frame's
// vertical edge, input circles on the left edge and the output circle on the right, the
// first of each centred on the header/body separator (#121). A linked circle is a
// stroke ring in the chain's colour with an identicon inset inside it (#92): an input
// circle the upstream view's (the link is the filter on that input port), the output
// circle the view's own; the image is a disc of the identicon's ground with the whole
// 5x5 grid inside, so no corner cell is cut. An unlinked circle is hollow, its ring the
// neutral token. Hovering shows `tip` (#127, ports_info.Tip) in a PortTip hanging centred
// under the circle: what flows and from or to whom (the names link to their views) over
// the link's summary, when it filters. The normal view draws no curves from here; wires
// belong to the wiring layer (#82) and the overworld.
Item {
    id: root
    property string viewId: ""
    property string direction: "in"
    property int index: 0            // the input circle's place in the stack (0 = first)
    // the input circles stacked under this one: its tip hangs below them, not over them
    property int below: 0
    property color tone: theme.colors.chain_none
    property bool linked: false
    property string icon: ""         // image://identicon/<name>?chain=<n>&round=1
    property var tip: ({})          // {what, parts, filter}
    // a view's port circle is port_px wide
    property real size: theme.metrics.port_px
    property real ring: 2
    property real hoverScale: 1.15
    readonly property color ringColor: linked ? tone : theme.colors.chain_none
    // the locate name; the overworld's cards set their own (`overPort:<view>:<dir>`)
    property string tag: "port:" + viewId + ":" + direction + (index > 0 ? ":" + index : "")
    objectName: tag
    width: size
    height: size

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: theme.colors.surface
        border.color: root.ringColor
        border.width: root.ring
        scale: hover.hovered ? root.hoverScale : 1
        Behavior on scale { NumberAnimation { duration: theme.metrics.anim_ms } }
        Image {
            readonly property int px: Math.round((root.size - 2 * root.ring) * Screen.devicePixelRatio)
            objectName: "identicon:" + root.tag
            anchors.fill: parent
            anchors.margins: root.ring
            visible: root.linked && root.icon !== "" && status === Image.Ready
            source: root.linked ? root.icon : ""
            sourceSize: Qt.size(px, px)
            smooth: false
        }
    }
    HoverHandler {
        id: hover
        cursorShape: Qt.ArrowCursor
    }
    PortTip {
        tag: root.tag
        info: root.tip
        show: hover.hovered && !!root.tip && !!root.tip.what
        gap: root.below * (root.size + theme.metrics.port_stack)
    }
}
