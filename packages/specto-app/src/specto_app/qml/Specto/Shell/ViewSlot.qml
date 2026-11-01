import QtQuick
import QtQuick.Effects

// One view: a white card with a header (ViewHeader.qml: `<id>: <label>`, the health dot,
// the controls, #126) and the host's item in the content area. Its hints (health message, empty
// state, gestures) are the status bar's while the pointer is on it (#80; the controller
// resolves the hovered view from the pointer, hosts may swallow hover). The human
// gestures here are submits
// with origin user: close, lock and unlock, and (#40) dragging the header to move and the
// borders to resize, both snapped by the controller while the pointer is down. While a
// gesture runs the card follows `dragRect` (area pixels); the canvas area binds
// x/y/width/height to it. A header drag on a selected view moves the whole selection
// (#84): the controller shifts the other cards by the same delta (`controller.dragRects`)
// and the drop is one submit. Maximise (story 72) is navigation: the card fills the area
// until Esc or the button restores the layout; nothing in the document changes. Float
// (#112) puts the view on the float layer, fixed in the window, raised by a deeper
// shadow; its drags stay in the window and snap to its edges and the other floaters;
// Unfloat drops it on the canvas under it. Each is one user submit. Collapse (#114)
// shrinks the card to its title bar at the same width (the controller's rect is the
// bar's): the content pauses and hides, the ports sit on the bar (extra inputs line up
// rightwards along it, the title after them), only the left and right borders resize;
// one user submit.
Item {
    id: root
    property string viewId: ""
    readonly property var info: controller.views[viewId] || ({})
    readonly property bool focused: controller.focusedView === viewId
    readonly property bool selected: info.selected === true
    readonly property bool locked: info.locked === true
    // on the float layer (#112): fixed in the window above the pannable canvas
    readonly property bool floating: info.floating === true
    // the title bar alone (#114): content paused, links still delivering
    readonly property bool collapsed: info.collapsed === true && !maximized
    readonly property bool maximized: controller.maximized === viewId
    readonly property string health: info.health || "ok"
    // the view's link chain colours its frame (glossary: link chain); none is neutral
    readonly property string chain: info.chain || ""
    readonly property color chainTone: theme.colors[chain] || theme.colors.border
    objectName: "view:" + viewId
    // off screen: hidden, so nothing under it renders (its host is paused)
    visible: info.onScreen !== false
    // a walk-through (#174) dims every view off its path to 22 %
    readonly property bool walkDimmed: walkStage.active && walkStage.pathViews.indexOf(viewId) < 0
    opacity: walkDimmed ? 0.22 : 1
    Behavior on opacity { NumberAnimation { duration: 220 } }

    // -- move / resize gesture state (canvas px while dragging) --
    property bool dragging: false
    property rect dragRect: Qt.rect(0, 0, 0, 0)
    readonly property real pad: theme.metrics.pad
    // the card's rect in canvas px (pan and pad undone)
    function canvasRect() {
        var p = controller.pan
        return Qt.rect(x - pad + p.x, y - pad + p.y, width, height)
    }
    function beginGesture(ma, mouse) {
        ma.start = ma.mapToItem(null, mouse.x, mouse.y)
        ma.startRect = root.canvasRect()
        controller.focusView(root.viewId)
        root.forceActiveFocus()  // the prompt field lets go (the bar may hide)
    }
    function deltaOf(ma, mouse) {
        var p = ma.mapToItem(null, mouse.x, mouse.y)
        return Qt.point(p.x - ma.start.x, p.y - ma.start.y)
    }
    function follow(snapped) {
        if (snapped === undefined || snapped.x === undefined) return
        root.dragRect = Qt.rect(snapped.x, snapped.y, snapped.w, snapped.h)
        root.dragging = true
    }
    function endGesture() {
        root.dragging = false
        controller.cancelDrag(root.viewId)  // the curves settle (a no-op after a drop)
    }

    // a selected view's soft halo (#120, design #123: a glow of the ink, 8 px blur and
    // 3 px spread, at 22 %), so the selection reads apart from every chain colour. The
    // glow's source is the card's own shape, which the card paints over.
    Item {
        id: halo
        objectName: "halo:" + root.viewId
        readonly property real spread: 3
        property color ring: theme.colors.selected_halo
        visible: root.selected
        anchors.fill: card
        anchors.margins: -spread
        opacity: card.opacity
        Rectangle {
            id: haloShape
            anchors.fill: parent
            anchors.margins: halo.spread
            radius: card.radius
            color: theme.colors.surface
            visible: false
        }
        MultiEffect {
            source: haloShape
            anchors.fill: haloShape
            autoPaddingEnabled: true
            shadowEnabled: true
            shadowColor: halo.ring
            shadowOpacity: 1
            shadowBlur: 0.25
            shadowScale: 1 + 2 * halo.spread / Math.max(1, Math.min(haloShape.width, haloShape.height))
            shadowHorizontalOffset: 0
            shadowVerticalOffset: 0
        }
    }

    // the chain-coloured frame around the whole view (ViewFrame.qml): header and body
    // sit inside its stroke, a transparent gutter keeps snapped views 4 px apart. A
    // selected or focused view's frame is the ink outline (theme `selected`, #120) at
    // the same 1.5 px as any other (#126, #201): the floor's footprint carries the
    // selection, not a thicker border.
    ViewFrame {
        id: card
        viewId: root.viewId
        strokeWidth: 1.5
        tone: root.focused || root.selected ? theme.colors.selected : root.chainTone
        elevated: root.floating
        // under the wiring layer (#82) the card dims; the port circles below stay clear
        opacity: controller.wiring ? 0.4 : 1
        Behavior on opacity { NumberAnimation { duration: theme.metrics.anim_ms } }

        Item {
            id: header
            objectName: "header:" + root.viewId
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: theme.metrics.header_h

            // drag the header to move the card; the controller snaps every step. A click
            // selects the view (Shift or Ctrl toggles it in the selection); a
            // double-click maximises (and restores).
            MouseArea {
                id: moveArea
                objectName: "move:" + root.viewId
                anchors.fill: parent
                property point start
                property rect startRect
                property bool toggle: false
                cursorShape: pressed && !root.maximized ? Qt.SizeAllCursor : Qt.ArrowCursor
                onPressed: function (mouse) {
                    // the press point first: selecting (like focusing) widens the frame
                    // stroke, which moves this item under the already-computed mouse.x/y
                    root.beginGesture(moveArea, mouse)
                    toggle = (mouse.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)) !== 0
                    controller.pressView(root.viewId, toggle)
                }
                onDoubleClicked: controller.maximize(root.viewId)
                onPositionChanged: function (mouse) {
                    if (!pressed || root.maximized) return
                    var d = root.deltaOf(moveArea, mouse)
                    root.follow(controller.dragMove(root.viewId, startRect.x + d.x, startRect.y + d.y))
                }
                onReleased: function (mouse) {
                    // the gesture ends before the submit: whatever the drop does, the
                    // card never stays stuck on its drag rect
                    var was = root.dragging, r = root.dragRect
                    root.endGesture()
                    if (was) controller.endMove(root.viewId, r.x, r.y)
                    else if (!toggle) controller.selectView(root.viewId, false)  // a click
                }
                onCanceled: root.endGesture()
            }

            // the title and the controls (ViewHeader.qml, #126), always visible (#158)
            ViewHeader {
                anchors.fill: parent
                viewId: root.viewId
                info: root.info
                collapsed: root.collapsed
                maximized: root.maximized
                titleInset: root.barPorts
            }
        }

        // the header/body separator: the port circles' centres sit on it (#121)
        Rectangle {
            objectName: "separator:" + root.viewId
            visible: !root.collapsed
            anchors.top: header.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: theme.colors.border
        }

        // the host's item is parented here by hosts.attach
        Item {
            id: content
            objectName: "slot:" + root.viewId
            visible: !root.collapsed
            anchors.top: header.bottom
            anchors.topMargin: 1
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            clip: true
            Component.onCompleted: if (root.viewId) hosts.attach(root.viewId, content)
            Component.onDestruction: if (root.viewId) hosts.detach(root.viewId, content)
        }

        // fly-to pulse
        Rectangle {
            id: flashRect
            anchors.fill: parent
            radius: card.radius
            color: theme.colors.accent
            opacity: 0
            z: 40
        }
        SequentialAnimation {
            id: flashAnim
            NumberAnimation { target: flashRect; property: "opacity"; to: 0.35; duration: theme.metrics.anim_ms }
            NumberAnimation { target: flashRect; property: "opacity"; to: 0; duration: theme.metrics.flash_ms }
        }
        Connections {
            target: controller
            function onFlash(vid) { if (vid === root.viewId) flashAnim.restart() }
        }

        // the agent changed this view: a brief outline says where to look (#44)
        Rectangle {
            id: outlineRect
            anchors.fill: parent
            anchors.margins: -3
            radius: card.radius + 3
            color: "transparent"
            border.width: 3
            border.color: theme.colors.agent
            opacity: 0
            z: 41
        }
        SequentialAnimation {
            id: outlineAnim
            NumberAnimation { target: outlineRect; property: "opacity"; to: 1; duration: theme.metrics.anim_ms }
            PauseAnimation { duration: 1600 }
            NumberAnimation { target: outlineRect; property: "opacity"; to: 0; duration: theme.metrics.flash_ms }
        }
        Connections {
            target: promptBar
            function onTouched(ids) { if (ids.indexOf(root.viewId) >= 0) outlineAnim.restart() }
        }
    }

    // -- ports (#81): one input circle per linked input stacked down the frame's left
    // edge, the first centred on the header/body separator (#121; one hollow circle
    // when nothing is linked), each showing the upstream view's identicon; the output
    // circle on the right edge, on the same line, shows the view's own (half outside
    // the frame); hover shows the filter and the data type.
    // Collapsed, they sit on the title bar's middle, extra inputs rightwards (chains.py) --
    readonly property real barY: root.height / 2
    readonly property real portStep: theme.metrics.port_px + theme.metrics.port_stack
    // how far the extra input circles reach into a collapsed title bar
    readonly property real barPorts: root.collapsed ? Math.max(0, root.inPorts.length - 1) * portStep : 0
    readonly property var inPorts: root.info.inPorts || []
    Repeater {
        model: Math.max(1, root.inPorts.length)
        delegate: PortCircle {
            readonly property var port: root.inPorts[model.index] || ({})
            viewId: root.viewId
            direction: "in"
            index: model.index
            below: root.collapsed ? 0 : root.inPorts.length - 1 - model.index
            tone: root.chain ? root.chainTone : theme.colors.chain_none
            linked: !!port.link
            icon: port.icon || ""
            tip: port.tip || root.info.inTip || ""
            readonly property real step: size + theme.metrics.port_stack
            x: card.gutter - size / 2 + (root.collapsed ? index * step : 0)
            y: root.collapsed ? root.barY - size / 2
                              : card.gutter + theme.metrics.port_inset - size / 2 + index * step
            visible: !root.maximized
            z: 35
        }
    }
    PortCircle {
        viewId: root.viewId
        direction: "out"
        tone: root.chain ? root.chainTone : theme.colors.chain_none
        linked: root.info.outLinked === true
        icon: root.info.icon || ""
        tip: root.info.outTip || ""
        x: root.width - card.gutter - size / 2
        y: root.collapsed ? root.barY - size / 2 : card.gutter + theme.metrics.port_inset - size / 2
        visible: !root.maximized
        z: 35
    }

    // -- resize handles: the borders and corners of the slot (gutter included), `handle_px` wide --
    component ResizeHandle: MouseArea {
        id: handle
        property var edges: []
        property point start
        property rect startRect
        readonly property real hp: theme.metrics.handle_px
        readonly property bool horiz: edges.indexOf("left") >= 0 || edges.indexOf("right") >= 0
        readonly property bool vert: edges.indexOf("top") >= 0 || edges.indexOf("bottom") >= 0
        objectName: "resize:" + root.viewId + ":" + edges.join("-")
        // a collapsed view resizes its width only: its height waits for the expand
        visible: !root.maximized && !(root.collapsed && vert)
        z: 30
        cursorShape: horiz && vert
                     ? ((edges.indexOf("left") >= 0) === (edges.indexOf("top") >= 0) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor)
                     : (horiz ? Qt.SizeHorCursor : Qt.SizeVerCursor)
        onPressed: function (mouse) { root.beginGesture(handle, mouse) }
        onPositionChanged: function (mouse) {
            if (!pressed) return
            var d = root.deltaOf(handle, mouse)
            var r = startRect
            var left = r.x, top = r.y, right = r.x + r.width, bottom = r.y + r.height
            if (edges.indexOf("left") >= 0) left += d.x
            if (edges.indexOf("right") >= 0) right += d.x
            if (edges.indexOf("top") >= 0) top += d.y
            if (edges.indexOf("bottom") >= 0) bottom += d.y
            root.follow(controller.dragResize(root.viewId, left, top, right - left, bottom - top, edges))
        }
        onReleased: function (mouse) {
            var was = root.dragging, dr = root.dragRect
            root.endGesture()
            if (was) controller.endResize(root.viewId, dr.x, dr.y, dr.width, dr.height, edges)
        }
        onCanceled: root.endGesture()
    }
    ResizeHandle { edges: ["left"]; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: hp; anchors.bottomMargin: hp; width: hp }
    ResizeHandle { edges: ["right"]; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: hp; anchors.bottomMargin: hp; width: hp }
    ResizeHandle { edges: ["top"]; anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: hp; anchors.rightMargin: hp; height: hp }
    ResizeHandle { edges: ["bottom"]; anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: hp; anchors.rightMargin: hp; height: hp }
    ResizeHandle { edges: ["top", "left"]; anchors.top: parent.top; anchors.left: parent.left; width: hp * 2; height: hp * 2 }
    ResizeHandle { edges: ["top", "right"]; anchors.top: parent.top; anchors.right: parent.right; width: hp * 2; height: hp * 2 }
    ResizeHandle { edges: ["bottom", "left"]; anchors.bottom: parent.bottom; anchors.left: parent.left; width: hp * 2; height: hp * 2 }
    ResizeHandle { edges: ["bottom", "right"]; anchors.bottom: parent.bottom; anchors.right: parent.right; width: hp * 2; height: hp * 2 }
}
