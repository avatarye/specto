import QtQuick

// The normal view of the canvas at 1:1: one card per view at its document position,
// the canvas point `controller.pan` at the area's padded top-left corner. Floating views
// (#112, ADR 0021) sit above them on the float layer, fixed in the window: the pan moves
// the canvas under them and never hides them. Off-screen
// views hide (their hosts pause). The normal view draws no link curves (#81): links show
// as identicons on the port circles; the wiring layer (#82) and the overworld
// (Overworld.qml, which replaces this item while it shows) draw the wires.
Item {
    id: root
    objectName: "canvasArea"
    clip: true
    readonly property var pan: controller.pan
    readonly property real pad: theme.metrics.pad
    // a maximised view starts this far down, under the floating controls (#120), so they
    // never cover its header buttons
    property real maximizedTop: pad
    //: the notice cards' height over the area's bottom right corner (Main's NoticeStack):
    //: the empty canvas hint sits above them, never under them (#192)
    property real noticeInset: 0

    // the controller maps the pointer onto the views (#80): it needs the area's place too
    function report() {
        controller.setViewportSize(width, height)
        var o = mapToItem(null, 0, 0)
        controller.setViewportOrigin(o.x, o.y)
    }
    Component.onCompleted: report()
    onWidthChanged: report()
    onHeightChanged: report()
    onXChanged: report()
    onYChanged: report()

    // canvas px -> area px
    function ax(cx) { return pad + cx - pan.x }
    function ay(cy) { return pad + cy - pan.y }

    // the pointer in canvas px: the prompt bar sends it with every request (#44)
    HoverHandler {
        onPointChanged: promptBar.setMouse(point.position.x - root.pad + root.pan.x,
                                           point.position.y - root.pad + root.pan.y)
        onHoveredChanged: if (!hovered) promptBar.clearMouse()
    }

    // the empty canvas: a left click drops the focus and the selection (Shift or Ctrl
    // keep it), a left drag is the selection marquee over the views it touches. It never
    // pans, and neither does the wheel (no handler here: a view under the wheel scrolls
    // itself, the canvas does nothing), so a scrolled file tree cannot drag the canvas
    // along. The right button is taken and left alone: reserved for a context menu.
    MouseArea {
        id: bgArea
        objectName: "panArea"
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        property point start
        property var base: []  // the selection a Shift/Ctrl marquee adds to
        onPressed: function (mouse) {
            if (mouse.button !== Qt.LeftButton) return
            controller.focusView("")
            root.forceActiveFocus()  // the prompt field lets go (the bar may hide)
            start = Qt.point(mouse.x, mouse.y)
            var additive = (mouse.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)) !== 0
            base = additive ? controller.selectedViews : []
            if (!additive) controller.clearSelection()
        }
        onPositionChanged: function (mouse) {
            if (!(pressedButtons & Qt.LeftButton)) return
            var r = Qt.rect(Math.min(start.x, mouse.x), Math.min(start.y, mouse.y),
                            Math.abs(mouse.x - start.x), Math.abs(mouse.y - start.y))
            if (!marquee.visible && r.width + r.height < 4) return  // a click, not yet a drag
            marquee.x = r.x; marquee.y = r.y; marquee.width = r.width; marquee.height = r.height
            marquee.visible = true
            controller.selectInRect(r.x - root.pad + root.pan.x, r.y - root.pad + root.pan.y,
                                    r.width, r.height, base)
        }
        onReleased: marquee.visible = false
        onCanceled: marquee.visible = false
    }

    // the tile floor (#191, #197, #201): square tiles under everything, fixed to the
    // screen, lit by the agent's status, new views and the selection's footprint; the
    // far chip above the views
    TileFloor {
        anchors.fill: parent
        z: -1
        originX: root.pad - root.pan.x
        originY: root.pad - root.pan.y
    }
    FloorChip { z: 12 }

    // the walk-through (#174): a 6 % scrim darkens the canvas under the views (the
    // path's stay lit, the rest dim to 22 %: ViewSlot)
    Rectangle {
        objectName: "walkScrim"
        anchors.fill: parent
        z: 0.5
        color: "#000000"
        opacity: walkStage.active ? 0.06 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 220 } }
    }

    // one card per view, for as long as the view exists (a row model, not an array:
    // a move must not rebuild the card whose release handler is running)
    Repeater {
        model: controller.viewList
        delegate: ViewSlot {
            required property string vid
            viewId: vid
            // while a gesture runs the card follows its snapped drag rect; a card of the
            // selection another header's drag carries along (#84) follows the rect the
            // controller holds for it; the maximised card fills the area inside its
            // padding (story 72). A floating view (#112) sits on the float layer at its
            // window position (info.fx, fy), whatever the pan, above every canvas view.
            readonly property var groupRect: controller.dragRects[vid]
            readonly property bool held: dragging || !!groupRect
            readonly property var live: dragging
                ? ({x: dragRect.x, y: dragRect.y, w: dragRect.width, h: dragRect.height})
                : (groupRect || info)
            x: maximized ? root.pad
                : (floating && !held ? root.pad + (info.fx || 0) : root.pad + (live.x || 0) - root.pan.x)
            y: maximized ? root.maximizedTop
                : (floating && !held ? root.pad + (info.fy || 0) : root.pad + (live.y || 0) - root.pan.y)
            width: maximized ? root.width - 2 * root.pad : (live.w || 0)
            height: maximized ? root.height - root.maximizedTop - root.pad : (live.h || 0)
            z: maximized ? 11 : (floating ? 8 : 0) + (controller.focusedView === viewId ? 2 : 1)
        }
    }

    // the project source (#83): the fixed node at the canvas origin, above the cards so
    // a canvas whose file tree still sits at the origin cannot hide it; it pans with the
    // canvas and hides while a view is maximised
    ProjectNode {
        objectName: "projectNode"
        x: root.ax(controller.projectNode.x || 0)
        y: root.ay(controller.projectNode.y || 0)
        visible: !controller.maximized
        z: 4
    }

    // snap lines: the borders the gesture in progress pulled flush to
    Repeater {
        model: controller.snapGuides
        delegate: Rectangle {
            required property var modelData
            readonly property bool vertical: modelData.axis === "x"
            x: vertical ? root.ax(modelData.value) - 0.5 : 0
            y: vertical ? 0 : root.ay(modelData.value) - 0.5
            width: vertical ? 1 : root.width
            height: vertical ? root.height : 1
            color: theme.colors.accent
            opacity: 0.7
            z: 12
        }
    }

    // pan: a middle drag, or Space held plus a left drag, anywhere over the area, views
    // included (Figma). The hand lies above the cards and takes only those buttons, so
    // every other press falls through to the view under it; a left drag already under
    // way keeps the hand when Space goes first. The pan settles into the window frame's
    // origin (controller.setPan). It lies above the float layer too, so a pan started
    // over a floating view pans the canvas under it.
    MouseArea {
        id: handArea
        objectName: "panHand"
        anchors.fill: parent
        z: 13
        acceptedButtons: Qt.MiddleButton | ((controller.spaceHeld || pressed) ? Qt.LeftButton : 0)
        property point start
        property point startPan
        onPressed: function (mouse) {
            start = Qt.point(mouse.x, mouse.y)
            startPan = Qt.point(root.pan.x, root.pan.y)
        }
        onPositionChanged: function (mouse) {
            if (pressed)
                controller.setPan(startPan.x - (mouse.x - start.x), startPan.y - (mouse.y - start.y))
        }
    }
    // the hand cursor, only while the hand is armed or dragging: a cursor-only layer, so
    // the views' own cursors (resize arrows, text beams) show the rest of the time
    MouseArea {
        anchors.fill: parent
        z: 14
        visible: controller.spaceHeld || handArea.pressed
        acceptedButtons: Qt.NoButton
        cursorShape: handArea.pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
    }

    // the selection marquee: the accent at low alpha over the views it touches
    Rectangle {
        id: marquee
        objectName: "marquee"
        visible: false
        z: 7
        color: Qt.alpha(theme.colors.accent, 0.12)
        border.width: 1
        border.color: theme.colors.accent
    }

    // a canvas with nothing but its file tree says what comes next
    Rectangle {
        visible: controller.viewCount <= 1
        objectName: "emptyCanvasHintCard"
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: root.pad * 2
        anchors.bottomMargin: Math.max(root.pad * 2, root.noticeInset)
        width: Math.min(420, root.width - 4 * root.pad)
        height: hint.implicitHeight + 2 * root.pad
        radius: theme.metrics.radius_card
        color: theme.colors.surface
        border.color: theme.colors.border
        opacity: 0.92
        // the agent is the S icon at the bottom left (#162, #170): hover opens its thread
        Text {
            id: hint
            objectName: "emptyCanvasHint"
            anchors.centerIn: parent
            width: parent.width - 2 * root.pad
            wrapMode: Text.Wrap
            text: "Your files are on the left. Ask the project's agent for what you want to see "
                  + "next to them: rest the pointer on the S at the bottom left, or press Ctrl+K. "
                  + "It writes the canvas document, and every turn is one undo step."
            color: theme.colors.text_muted
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
        }
    }
}
