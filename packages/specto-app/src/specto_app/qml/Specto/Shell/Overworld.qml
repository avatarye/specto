import QtQuick

// The overworld (#45, glossary): the whole canvas, paused, zoomed out on a quiet
// whiteboard. Every view is a card with its last snapshot, framed in its link chain's
// colour; links are clear chain-coloured wires with a link node each. No guide marks
// the app window (#120). Cards move and resize freely while the pointer is down and
// resolve snapped into the normal view on release (one user submit). A link node, a dot
// (#121), opens the link view (Delete, Ask agent to change). Tab toggles. Floating
// views (#112) are not on the canvas: a "floating" strip along the top shows them, and
// a faint outline marks where each floats over the canvas at the current pan.
Item {
    id: root
    objectName: "overworld"
    property real zoom: 0.4
    property real panX: 0
    property real panY: 0
    readonly property real ui: 1 / Math.max(0.05, zoom)   // canvas px per screen px
    signal askAgent(string text)

    function toCanvas(px, py) { return { x: (px - panX) / zoom, y: (py - panY) / zoom } }
    function fit() {
        var b = overworld.bounds
        if (!b || !(b.w > 0) || width < 10 || height < 10) return
        var margin = 48, top = 64 + (floatStrip.visible ? floatStrip.height + 8 : 0)
        var zx = (width - 2 * margin) / Math.max(1, b.w)
        var zy = (height - top - margin) / Math.max(1, b.h)
        zoom = Math.max(0.05, Math.min(1.0, Math.min(zx, zy)))
        panX = (width - b.w * zoom) / 2 - b.x * zoom
        panY = top + (height - top - margin - b.h * zoom) / 2 - b.y * zoom
    }
    function zoomAt(px, py, factor) {
        var c = toCanvas(px, py)
        zoom = Math.max(0.05, Math.min(2, zoom * factor))
        panX = px - c.x * zoom
        panY = py - c.y * zoom
    }
    function closePopups() { linkView.close() }
    // a walk-through (#174) holds the camera for its establishing shot and summary
    function follow() {
        zoom = walkStage.overZoom
        panX = walkStage.overX
        panY = walkStage.overY
    }

    Connections {
        target: overworld
        function onActiveChanged() {
            if (!overworld.active) root.closePopups()
            else if (walkStage.over) root.follow()
            else root.fit()
        }
        function onAskAgentRequested(text) { root.askAgent(text) }
    }
    Connections {
        target: walkStage
        function onCameraChanged() {
            if (walkStage.over) root.follow()
            else if (overworld.active) root.fit()  // the walk let go: the user's own view
        }
    }
    onWidthChanged: if (overworld.active && !walkStage.over) fit()
    onHeightChanged: if (overworld.active && !walkStage.over) fit()
    onUiChanged: overworld.setScreenScale(ui)
    Component.onCompleted: overworld.setScreenScale(ui)

    // the quiet whiteboard is the tile floor (#191), fixed to the screen at any zoom (#197)
    Rectangle { anchors.fill: parent; color: theme.colors.bg }
    TileFloor {
        objectName: "overTileFloor"
        anchors.fill: parent
        originX: root.panX
        originY: root.panY
        zoom: root.zoom
    }

    // pan with a drag on the background, zoom with the wheel about the pointer; a plain
    // click clears the link selection
    MouseArea {
        id: bgArea
        objectName: "overPanArea"
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        property point start
        property point startPan
        property bool moved: false
        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.ArrowCursor
        onPressed: function (m) { start = Qt.point(m.x, m.y); startPan = Qt.point(root.panX, root.panY); moved = false }
        onPositionChanged: function (m) {
            if (!pressed) return
            if (Math.abs(m.x - start.x) + Math.abs(m.y - start.y) > 3) moved = true
            root.panX = startPan.x + (m.x - start.x)
            root.panY = startPan.y + (m.y - start.y)
        }
        onReleased: function (m) { if (!moved) { overworld.selectLink(""); root.closePopups() } }
        onWheel: function (w) {
            root.zoomAt(w.x, w.y, w.angleDelta.y > 0 ? 1.15 : 1 / 1.15)
            w.accepted = true
        }
    }

    Item {
        id: content
        x: root.panX
        y: root.panY
        scale: root.zoom
        transformOrigin: Item.TopLeft

        // wires under the cards, clear; one item per link id so a dragged card's wires
        // follow it live
        Repeater {
            model: overworld.wireIds
            delegate: LinkCurve {
                required property var modelData
                objectName: "overWire:" + modelData
                wire: overworld.wireMap[modelData] || ({})
                faint: false
                selected: overworld.selectedLink === modelData
                strokeWidth: 3 * root.ui
                // a walk-through (#174) dims the wires off its path
                opacity: walkStage.active && walkStage.pathLinks.indexOf(modelData) < 0 ? 0.22 : 1
                z: 1
            }
        }

        // cards: one per view id (a row model: a card lives as long as its view, so a
        // drag in progress survives a move, health and selection changes); a floating
        // view has none (it is in the strip), only a faint outline where it floats
        Repeater {
            model: controller.viewList
            delegate: OverCard {
                required property string vid
                card: overworld.cardMap[vid] || ({})
                visible: card.floating !== true
                ui: root.ui
                canvasItem: content
                z: 2
            }
        }
        Repeater {
            model: overworld.floatingCards
            delegate: Rectangle {
                required property var modelData
                objectName: "overFloatGhost:" + modelData.id
                x: modelData.rect.x + theme.metrics.gutter
                y: modelData.rect.y + theme.metrics.gutter
                width: modelData.rect.w - 2 * theme.metrics.gutter
                height: modelData.rect.h - 2 * theme.metrics.gutter
                radius: 18
                color: Qt.alpha(theme.colors.accent, 0.05)
                border.width: 1.5 * root.ui
                border.color: Qt.alpha(theme.colors.accent, 0.5)
                z: 2
                Text {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.margins: 10 * root.ui
                    text: modelData.id + " floats here"
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: 12 * root.ui
                }
            }
        }

        // the project source (#83): the same fixed node as in the normal view, above the
        // link nodes (the wire to the file tree is short; its dot must not hide the node)
        ProjectNode {
            objectName: "overProjectNode"
            refreshable: false
            x: controller.projectNode.x || 0
            y: controller.projectNode.y || 0
            ui: root.ui
            z: 4
        }

        // link nodes above the cards: always clickable, and never on a card's header
        // (#90): the controller places each dot from its reported size
        Repeater {
            model: overworld.wireIds
            delegate: LinkNode {
                id: node
                required property var modelData
                readonly property var place: overworld.nodePlaces[modelData]
                wire: overworld.wireMap[modelData] || ({})
                ui: root.ui
                opacity: walkStage.active && walkStage.pathLinks.indexOf(modelData) < 0 ? 0.22 : 1
                x: (place ? place.x : (wire.nx || 0)) - width / 2
                y: (place ? place.y : (wire.ny || 0)) - height / 2
                z: 3
                function report() { overworld.setNodeSize(modelData, width, height) }
                onWidthChanged: report()
                onHeightChanged: report()
                Component.onCompleted: report()
                onOpenLink: linkView.openFor(modelData)
            }
        }
    }

    LinkView { id: linkView }

    // header: what this is, fit and zoom, the way back (not during a walk-through)
    Row {
        visible: !walkStage.active
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: theme.metrics.pad
        spacing: 8
        Rectangle {
            objectName: "overHeaderPill"
            width: hdrRow.implicitWidth + 20
            height: 30
            radius: theme.metrics.radius_pill
            color: theme.colors.text
            Row {
                id: hdrRow
                anchors.centerIn: parent
                spacing: 8
                Icon { name: "overworld"; size: 14; color: theme.colors.surface; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Overworld · paused · " + overworld.cards.length + " view" + (overworld.cards.length === 1 ? "" : "s")
                          + " · " + overworld.wires.length + " link" + (overworld.wires.length === 1 ? "" : "s")
                    color: theme.colors.surface
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
        Pill { objectName: "overFit"; text: "fit"; icon: "fit"; implicitHeight: 30; onClicked: root.fit() }
        Pill { text: Math.round(root.zoom * 100) + "%"; implicitHeight: 30; onClicked: root.fit() }
        Pill {
            objectName: "overBack"
            text: "normal view"
            hint: "Tab"
            tone: "accent"
            implicitHeight: 30
            onClicked: overworld.hide()
        }
        Text {
            text: "drag a card to move it, its border to resize · click a link dot to open the link · wheel zooms · drag the background to pan"
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    // the floating views (#112): a strip along the top, under the header; a chip goes
    // back to the normal view with that view flashing
    Rectangle {
        id: floatStrip
        objectName: "overFloatStrip"
        visible: overworld.floatingCards.length > 0
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.topMargin: theme.metrics.pad + 30 + 8
        anchors.leftMargin: theme.metrics.pad
        width: Math.min(parent.width - 2 * theme.metrics.pad, stripRow.implicitWidth + 2 * theme.metrics.pad)
        height: 96
        radius: theme.metrics.radius_card
        color: theme.colors.surface
        border.width: 1
        border.color: theme.colors.border
        clip: true
        SoftShadow { radius: parent.radius }
        Row {
            id: stripRow
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: theme.metrics.pad
            spacing: 10
            Text {
                text: "floating"
                color: theme.colors.text_muted
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }
            Repeater {
                model: overworld.floatingCards
                delegate: Rectangle {
                    id: chip
                    required property var modelData
                    readonly property color tone: theme.colors[modelData.chain] || theme.colors.border_strong
                    objectName: "overFloat:" + modelData.id
                    width: 150
                    height: 80
                    radius: theme.metrics.radius_inner
                    color: theme.colors.surface
                    border.width: chipHover.hovered ? 2 : 1.5
                    border.color: chipHover.hovered ? theme.colors.accent : tone
                    clip: true
                    Image {
                        anchors.fill: parent
                        anchors.margins: 2
                        anchors.topMargin: 22
                        source: chip.modelData.hasSnapshot ? chip.modelData.snapshot : ""
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                    }
                    Text {
                        objectName: "overFloatTitle:" + chip.modelData.id
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 5
                        elide: Text.ElideRight
                        text: chip.modelData.id + (chip.modelData.label && chip.modelData.label !== chip.modelData.id
                                                   ? ": " + chip.modelData.label : "")
                        color: theme.colors.text
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                        font.bold: true
                    }
                    HoverHandler { id: chipHover }
                    TapHandler { onTapped: overworld.flyTo(chip.modelData.id) }
                }
            }
        }
    }

    Keys.onPressed: function (ev) {
        if (ev.key === Qt.Key_F) { root.fit(); ev.accepted = true }
    }
}
