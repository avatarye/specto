import QtQuick
import QtQuick.Controls

// A port tip (#127, Figma page 05 section 3; ports_info.Tip): a box hanging centred under
// its parent with a caret pointing up at the parent's centre, at most `tip_max_w` wide and
// wrapping, on the port tips' own tinted background (#111). A data section (`info.what`
// over `info.parts`) led by the data icon and, when `info.filter` is set, a filter
// section led by the funnel, each text indented past its icon (wrapped lines too). A tip
// with no `what` (a link dot's) is its parts alone. A part with a `view` is that view's
// name as a link: clicking it jumps there (`controller.jumpTo`). So the pointer can reach
// the links, the tip waits `tip_grace_ms` after `show` drops and stays while the pointer is
// inside it (`lingers`), until another tip takes the slot (`TipState`); while one is open,
// another port's tip takes it only after the pointer rests on that port as long (on the
// way into a tip the pointer may cross a stacked port's circle). Drawn in the
// window's overlay, kept inside the window, the caret still on the port. `tag` names the
// items for locate/tests: "tip:<tag>" on the box, "tipCaret:<tag>", "tipWhat:<tag>",
// "tipFilter:<tag>", "tipDataIcon:<tag>", "tipFilterIcon:<tag>", "tipLink:<tag>:<view>".
Popup {
    id: tip
    property string tag: ""
    property var info: ({})
    // the owner is hovered
    property bool show: false
    // may wait for the pointer and stay while it is inside
    property bool lingers: true
    // extra room between the parent's bottom and the caret (what hangs there: the root
    // node's refresh circle)
    property real gap: 0
    readonly property bool hasData: !!(info && info.what)
    readonly property bool filtered: !!(info && info.filter)
    readonly property var parts: (info && info.parts) || []
    readonly property real cap: theme.metrics.tip_max_w
    readonly property real caret: theme.metrics.tip_caret
    readonly property real icon: theme.metrics.tip_icon_px
    readonly property real indent: hasData ? icon + theme.metrics.pad_tight : 0
    readonly property bool claimed: TipState.owner === tag
    // the window's edge keeps the box this far in
    readonly property real edge: 4
    // where the box goes, centred under the parent and shifted inside the window; read
    // again each time the tip shows (the port does not move while it is hovered)
    readonly property real wantX: parent ? (parent.width - width) / 2 : 0
    readonly property real placedX: {
        if (!parent || !visible)
            return wantX
        const left = parent.mapToItem(null, 0, 0).x
        const room = parent.Window.width - width - edge
        return Math.max(edge, Math.min(left + wantX, room)) - left
    }

    function jump(viewId) {
        TipState.owner = ""
        TipState.open = false
        if (overworld.active)
            overworld.hide()
        controller.jumpTo(viewId)
    }

    function take() {
        TipState.owner = tag
        TipState.open = true
    }

    // the pointer is in the box: the padding (background) or the content, whose links
    // keep the hover from the background behind them
    readonly property bool inside: edgeHover.hovered || bodyHover.hovered
    visible: claimed && (show || (lingers && (grace.running || inside)))
    onShowChanged: {
        if (show) {
            if (claimed || !TipState.open)
                take()
            else
                dwell.restart()
        } else {
            dwell.stop()
            grace.restart()
        }
    }
    onVisibleChanged: if (!visible && claimed) TipState.open = false
    Timer { id: grace; interval: theme.metrics.tip_grace_ms }
    Timer {
        id: dwell
        interval: theme.metrics.tip_grace_ms
        onTriggered: if (tip.show) tip.take()
    }

    modal: false
    focus: false
    margins: 0                      // placedX keeps it inside the window
    closePolicy: Popup.NoAutoClose
    x: placedX
    // below the port, clear of the circle grown on hover
    y: parent ? parent.height + gap + caret + 2 : 0
    topPadding: 7
    bottomPadding: 8
    leftPadding: 10
    rightPadding: 10
    background: Item {
        HoverHandler { id: edgeHover }
        Rectangle {
            objectName: "tip:" + tip.tag
            anchors.fill: parent
            radius: theme.metrics.radius_inner
            color: theme.colors.tip_port_bg
            border.color: theme.colors.tip_port_border
            border.width: 1
        }
        // the caret: a square turned 45 degrees, its lower half under the box's fill so
        // the box's top edge opens where the caret leaves it
        Rectangle {
            objectName: "tipCaret:" + tip.tag
            width: tip.caret * Math.SQRT2
            height: width
            x: tip.parent ? tip.parent.width / 2 - tip.placedX - width / 2 : 0
            y: -height / 2
            rotation: 45
            color: theme.colors.tip_port_bg
            border.color: theme.colors.tip_port_border
            border.width: 1
        }
        Rectangle {
            x: tip.parent ? tip.parent.width / 2 - tip.placedX - tip.caret - 1 : 0
            y: 1
            width: 2 * tip.caret + 2
            height: tip.caret
            color: theme.colors.tip_port_bg
        }
    }
    // the popup sizes its content from the implicit size: the widest line plus the indent,
    // capped (wrapped text then fills the cap)
    contentItem: Item {
        readonly property real inner: tip.cap - tip.leftPadding - tip.rightPadding
        implicitWidth: Math.min(inner, tip.indent + Math.max(tip.hasData ? what.implicitWidth : 0,
                                                             measure.implicitWidth,
                                                             tip.filtered ? filter.implicitWidth : 0))
        implicitHeight: body.implicitHeight
        HoverHandler { id: bodyHover }
        Column {
            id: body
            width: parent.width
            spacing: 6
            // the data section: its icon, then what over the parts
            Item {
                width: parent.width
                height: dataText.height
                // the parts' one-line width, unwrapped (the Flow below wraps)
                Row {
                    id: measure
                    opacity: 0
                    enabled: false
                    Repeater {
                        model: tip.parts
                        delegate: Text {
                            required property var modelData
                            text: modelData.text
                            font.family: theme.fonts.ui
                            font.pixelSize: theme.metrics.font_px_small
                        }
                    }
                }
                Icon {
                    objectName: "tipDataIcon:" + tip.tag
                    visible: tip.hasData
                    name: "data"
                    size: tip.icon
                    color: theme.colors.tip_port_text
                    y: 1
                }
                Column {
                    id: dataText
                    x: tip.indent
                    width: parent.width - x
                    spacing: 1
                    Text {
                        id: what
                        objectName: "tipWhat:" + tip.tag
                        width: parent.width
                        visible: tip.hasData
                        text: (tip.info && tip.info.what) || ""
                        wrapMode: Text.Wrap
                        color: theme.colors.tip_port_text
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                        font.weight: Font.DemiBold
                    }
                    Flow {
                        width: parent.width
                        Repeater {
                            model: tip.parts
                            delegate: Text {
                                required property var modelData
                                readonly property string viewId: modelData.view || ""
                                objectName: viewId ? "tipLink:" + tip.tag + ":" + viewId : ""
                                text: modelData.text
                                color: viewId ? theme.colors.accent : theme.colors.tip_port_text
                                font.family: theme.fonts.ui
                                font.pixelSize: theme.metrics.font_px_small
                                font.underline: viewId !== "" && linkHover.hovered
                                HoverHandler {
                                    id: linkHover
                                    enabled: parent.viewId !== ""
                                    cursorShape: Qt.PointingHandCursor
                                }
                                TapHandler {
                                    enabled: parent.viewId !== ""
                                    onTapped: tip.jump(parent.viewId)
                                }
                            }
                        }
                    }
                }
            }
            // the filter section: the funnel, then the link summary
            Item {
                visible: tip.filtered
                width: parent.width
                height: filter.height
                Icon {
                    objectName: "tipFilterIcon:" + tip.tag
                    visible: tip.filtered
                    name: "filter"
                    size: tip.icon
                    color: theme.colors.tip_port_text
                    y: 1
                }
                Text {
                    id: filter
                    objectName: "tipFilter:" + tip.tag
                    x: tip.indent
                    width: parent.width - x
                    visible: tip.filtered
                    text: (tip.info && tip.info.filter) || ""
                    wrapMode: Text.Wrap
                    color: theme.colors.tip_port_text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    font.weight: Font.DemiBold
                }
            }
        }
    }
}
