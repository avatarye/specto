import QtQuick

// One view as an overworld card: its last snapshot with the age, framed in its link
// chain's colour. It follows the normal view's rules (#90): the header holds
// `<name>: <label>`, the health dot and the lock (#80; the hints are the status bar's),
// and the port circles show identicons, the upstream view's on each linked input and the
// view's own on the output, hollow when unlinked (#81). The header drags the card and
// the borders resize it, free while held (`dragRect`, canvas px) and resolved snapped by
// the controller on release, one user submit each. Text keeps its size on screen (`ui`
// = canvas px per screen px). `canvasItem` is the scaled canvas item the card lives in.
// A collapsed view (#114) is a card the size of its title bar: the header fills it (its
// text shrinks to fit), a collapsed mark replaces the snapshot, the ports sit on the
// bar's middle (extra inputs rightwards, the title after them) and only the left and
// right borders resize.
Item {
    id: root
    property var card: ({})
    property real ui: 1
    property Item canvasItem: parent
    readonly property string viewId: card.id || ""
    readonly property var rect: card.rect || ({ x: 0, y: 0, w: 100, h: 100 })
    readonly property string chain: card.chain || ""
    readonly property color tone: theme.colors[chain] || theme.colors.border_strong
    readonly property string health: card.health || "ok"
    readonly property bool collapsed: card.collapsed === true
    objectName: "overCard:" + viewId
    // a walk-through (#174) dims every card off its path to 22 %
    opacity: walkStage.active && walkStage.pathViews.indexOf(viewId) < 0 ? 0.22 : 1

    property bool dragging: false
    property rect dragRect: Qt.rect(0, 0, 0, 0)
    x: dragging ? dragRect.x : rect.x
    y: dragging ? dragRect.y : rect.y
    width: dragging ? dragRect.width : rect.w
    height: dragging ? dragRect.height : rect.h

    function begin(ma, mouse) {
        ma.start = ma.mapToItem(root.canvasItem, mouse.x, mouse.y)
        ma.startRect = Qt.rect(root.rect.x, root.rect.y, root.rect.w, root.rect.h)
    }
    function delta(ma, mouse) {
        var p = ma.mapToItem(root.canvasItem, mouse.x, mouse.y)
        return Qt.point(p.x - ma.start.x, p.y - ma.start.y)
    }
    function follow(r) {
        if (r === undefined || r.x === undefined) return
        root.dragRect = Qt.rect(r.x, r.y, r.w, r.h)
        root.dragging = true
    }
    function end() {
        root.dragging = false
        overworld.cancelDrag(root.viewId)  // the wires settle (a no-op after a drop)
    }

    // the frame sits inside the same gutter as in the normal view (ViewFrame.qml), in
    // canvas px, so the ports and the wires meet at the same spots in both levels
    Rectangle {
        id: body
        anchors.fill: parent
        anchors.margins: theme.metrics.gutter
        radius: 18
        color: theme.colors.surface
        border.width: (root.chain ? 3 : 1.5) * root.ui
        border.color: root.tone
        clip: true

        Rectangle {
            anchors.fill: parent
            anchors.topMargin: 6
            radius: parent.radius
            color: theme.colors.shadow
            z: -1
        }

        // header: the move handle
        Item {
            id: header
            objectName: "overHeader:" + root.viewId
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: root.collapsed ? parent.height : 30 * root.ui
            // a title bar's text shrinks to fit it; otherwise it keeps its screen size
            readonly property real fit: root.collapsed ? Math.min(1, height / (30 * root.ui)) : 1
            MouseArea {
                id: moveArea
                anchors.fill: parent
                property point start
                property rect startRect
                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                onPressed: function (m) { root.begin(moveArea, m) }
                onPositionChanged: function (m) {
                    if (!pressed) return
                    var d = root.delta(moveArea, m)
                    root.follow(overworld.dragMove(root.viewId, startRect.x + d.x, startRect.y + d.y))
                }
                onReleased: function (m) {
                    // the gesture ends before the submit (the wires settle), whatever
                    // the drop does the card never stays stuck on its drag rect
                    var was = root.dragging, r = root.dragRect
                    root.end()
                    if (was) overworld.endMove(root.viewId, r.x, r.y)
                }
                onCanceled: root.end()
            }
            Row {
                anchors.left: parent.left
                anchors.leftMargin: 12 * root.ui + (root.collapsed
                    ? Math.max(0, root.inPorts.length - 1) * (theme.metrics.port_px + theme.metrics.port_stack) : 0)
                anchors.right: marks.left
                anchors.rightMargin: 6 * root.ui
                anchors.verticalCenter: parent.verticalCenter
                clip: true
                // the same header rule as the normal view (#80): `<name>: <label>`
                spacing: 6 * root.ui
                Text {
                    objectName: "overTitle:" + root.viewId
                    text: root.viewId + (root.card.label && root.card.label !== root.viewId
                                         ? ": " + root.card.label : "")
                    color: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: 13 * root.ui * header.fit
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
                // the contextual tag (#43), as in the normal view's header
                Rectangle {
                    objectName: "overContextual:" + root.viewId
                    visible: root.card.contextual === true
                    width: contextualText.implicitWidth + 12 * root.ui
                    height: 18 * root.ui * header.fit
                    radius: height / 2
                    color: theme.colors.accent_soft
                    border.color: theme.colors.accent
                    border.width: 1
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        id: contextualText
                        anchors.centerIn: parent
                        text: "Contextual"
                        color: theme.colors.accent
                        font.family: theme.fonts.ui
                        font.pixelSize: 11 * root.ui * header.fit
                    }
                }
            }
            // the snapshot's age, the health dot (a pause glyph while paused: every
            // view is, here) and the lock when the view is locked
            Row {
                id: marks
                anchors.right: parent.right
                anchors.rightMargin: 12 * root.ui
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6 * root.ui
                // collapsed to its title bar (#114): the normal view's expand glyph
                Icon {
                    objectName: "overCollapsed:" + root.viewId
                    visible: root.collapsed
                    name: "chevron-right"
                    size: 12 * root.ui * header.fit
                    color: theme.colors.accent
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: root.card.hasSnapshot && !root.collapsed ? root.card.age : ""
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: 11 * root.ui
                    anchors.verticalCenter: parent.verticalCenter
                }
                Item {
                    objectName: "overHealth:" + root.viewId
                    width: 14 * root.ui
                    height: 14 * root.ui
                    anchors.verticalCenter: parent.verticalCenter
                    readonly property bool quiet: root.health === "ok" || root.health === "paused"
                    Icon {
                        visible: parent.quiet
                        name: "pause"
                        size: 11 * root.ui
                        color: theme.colors.paused
                        anchors.centerIn: parent
                    }
                    Rectangle {
                        visible: !parent.quiet
                        width: 7 * root.ui; height: 7 * root.ui; radius: 4 * root.ui
                        color: theme.colors[root.health] || theme.colors.text_faint
                        anchors.centerIn: parent
                    }
                }
                Icon {
                    objectName: "overLock:" + root.viewId
                    visible: root.card.locked === true
                    name: "lock"
                    size: 12 * root.ui
                    color: theme.colors.lock
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        // the snapshot, or what stands in for one
        Item {
            id: picture
            visible: !root.collapsed
            anchors.top: header.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 10 * root.ui
            anchors.topMargin: 0
            Rectangle {
                anchors.fill: parent
                radius: 10
                color: theme.colors.surface_alt
                border.color: theme.colors.border
                border.width: root.ui
            }
            Image {
                objectName: "overSnapshot:" + root.viewId
                anchors.fill: parent
                anchors.margins: root.ui
                source: root.card.snapshot || ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: false
                visible: status === Image.Ready
                smooth: true
            }
            Column {
                anchors.centerIn: parent
                spacing: 4 * root.ui
                visible: !root.card.hasSnapshot
                Icon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    name: "image"
                    size: 24 * root.ui
                    color: theme.colors.text_faint
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "no snapshot yet (it was off screen)"
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: 12 * root.ui
                }
            }
        }
    }

    // the ports (#81), where the wires attach, at the same canvas spots as in the normal
    // view: one input circle per linked input stacked down the frame's left edge (one
    // hollow circle when nothing is linked), the output circle on the right edge
    readonly property var inPorts: card.inPorts || []
    Repeater {
        model: Math.max(1, root.inPorts.length)
        delegate: PortCircle {
            readonly property var port: root.inPorts[model.index] || ({})
            viewId: root.viewId
            direction: "in"
            index: model.index
            below: root.collapsed ? 0 : root.inPorts.length - 1 - model.index
            tag: "overPort:" + root.viewId + ":in" + (model.index > 0 ? ":" + model.index : "")
            tone: root.chain ? root.tone : theme.colors.chain_none
            linked: !!port.link
            icon: port.icon || ""
            tip: port.tip || root.card.inTip || ""
            readonly property real step: size + theme.metrics.port_stack
            x: theme.metrics.gutter - size / 2 + (root.collapsed ? index * step : 0)
            y: root.collapsed ? root.height / 2 - size / 2
                              : theme.metrics.gutter + theme.metrics.port_inset - size / 2 + index * step
            z: 35   // above the resize handles, as in the normal view
        }
    }
    PortCircle {
        viewId: root.viewId
        direction: "out"
        tag: "overPort:" + root.viewId + ":out"
        tone: root.chain ? root.tone : theme.colors.chain_none
        linked: root.card.outLinked === true
        icon: root.card.icon || ""
        tip: root.card.outTip || ""
        x: root.width - theme.metrics.gutter - size / 2
        y: root.collapsed ? root.height / 2 - size / 2 : theme.metrics.gutter + theme.metrics.port_inset - size / 2
        z: 35
    }

    // resize handles on the borders and corners (`handle_px` on screen)
    component ResizeHandle: MouseArea {
        id: handle
        property var edges: []
        property point start
        property rect startRect
        readonly property real hp: theme.metrics.handle_px * root.ui
        readonly property bool horiz: edges.indexOf("left") >= 0 || edges.indexOf("right") >= 0
        readonly property bool vert: edges.indexOf("top") >= 0 || edges.indexOf("bottom") >= 0
        objectName: "overResize:" + root.viewId + ":" + edges.join("-")
        visible: !(root.collapsed && vert)  // a title bar: its width only (#114)
        z: 30
        cursorShape: horiz && vert
                     ? ((edges.indexOf("left") >= 0) === (edges.indexOf("top") >= 0) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor)
                     : (horiz ? Qt.SizeHorCursor : Qt.SizeVerCursor)
        onPressed: function (m) { root.begin(handle, m) }
        onPositionChanged: function (m) {
            if (!pressed) return
            var d = root.delta(handle, m)
            var r = startRect
            var left = r.x, top = r.y, right = r.x + r.width, bottom = r.y + r.height
            if (edges.indexOf("left") >= 0) left += d.x
            if (edges.indexOf("right") >= 0) right += d.x
            if (edges.indexOf("top") >= 0) top += d.y
            if (edges.indexOf("bottom") >= 0) bottom += d.y
            root.follow(overworld.dragResize(root.viewId, left, top, right - left, bottom - top, edges))
        }
        onReleased: function (m) {
            var was = root.dragging, dr = root.dragRect
            root.end()
            if (was) overworld.endResize(root.viewId, dr.x, dr.y, dr.width, dr.height, edges)
        }
        onCanceled: root.end()
    }
    ResizeHandle { edges: ["left"]; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: root.collapsed ? 0 : hp; anchors.bottomMargin: root.collapsed ? 0 : hp; width: hp }
    ResizeHandle { edges: ["right"]; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: root.collapsed ? 0 : hp; anchors.bottomMargin: root.collapsed ? 0 : hp; width: hp }
    ResizeHandle { edges: ["top"]; anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: hp; anchors.rightMargin: hp; height: hp }
    ResizeHandle { edges: ["bottom"]; anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: hp; anchors.rightMargin: hp; height: hp }
    ResizeHandle { edges: ["top", "left"]; anchors.top: parent.top; anchors.left: parent.left; width: hp * 2; height: hp * 2 }
    ResizeHandle { edges: ["top", "right"]; anchors.top: parent.top; anchors.right: parent.right; width: hp * 2; height: hp * 2 }
    ResizeHandle { edges: ["bottom", "left"]; anchors.bottom: parent.bottom; anchors.left: parent.left; width: hp * 2; height: hp * 2 }
    ResizeHandle { edges: ["bottom", "right"]; anchors.bottom: parent.bottom; anchors.right: parent.right; width: hp * 2; height: hp * 2 }
}
