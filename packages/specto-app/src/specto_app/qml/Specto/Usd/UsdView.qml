// The usd view's item (#139; design usd.md rev 2): the native FrameItem (Specto.Native)
// showing the frames of the view's source process, between the VIEW panel (left: camera
// speed, World origin, Frame selected, the Free / Orbit camera mode (#39), the renderer
// readout) and the PRIMS outliner (right: the stage's
// prims, read-only, lazy, with a search), over the shared footer (#126: previous / next
// stage, the frame target (the selection, else all), the stage's name; play / pause, the time readout and the
// frame's cost on the right). A stage with time samples (#141) also gets the video's
// scrubber (Video/VideoProgress.qml) above the footer, driven by `tl` (the host's
// TimelineController); a static stage shows neither the scrubber nor play. Both panels are the side panel standard (Shell/SidePanel.qml), their state
// in the view param `panels`. The host owns every value; `bridge` (UsdBridge) shows them
// and takes the user's intent back.
//
// Input over the picture: a click picks the prim under it (Ctrl adds or takes out, #140);
// the camera is Unreal Editor's (#151, the table in usd/nav.py): drag turns and walks on
// the ground, right-drag looks (held: W A S D fly, Q / E down / up, Shift faster, the
// wheel sets the speed), middle- or left+right-drag pans, the wheel dollies; with Alt a
// left-drag orbits the selection or the point under the press, a right-drag dollies, a
// middle-drag pans; in Orbit mode (#39) a left-drag orbits the selection's centre (else
// the point under the press), a middle-drag pans, a right-drag and the wheel dolly toward
// it; F (and the panel's Frame selected) frames the selection, each prim's whole
// subtree, everything when nothing is; PageUp / PageDown (and the footer's chevrons) step
// through the picked stages, Home / End to the first / last; an animated stage: Space
// plays / pauses, Left / Right step one timecode. In the outliner a click on a row selects
// it (Ctrl adds), its chevron opens it, a double-click frames the prim; a selection made
// anywhere shows on its row, scrolled into view.
// objectNames: frame, viewport:<viewId>, panel:<viewId>:view|prims (SidePanel's parts),
// phase:<viewId> (its words: phaseText:<viewId>), speed:<viewId>, origin:<viewId>,
// frameSelected:<viewId>, cameraMode:<viewId> (its halves cameraMode:<viewId>:free|orbit),
// renderer:<viewId>, rendererNote:<viewId>, primsFilter:<viewId>, prims:<viewId>,
// prim:<viewId>:<path>, toggle:<viewId>:<path>, more:<viewId>:<path>,
// frameAll:<viewId>, stage:<viewId>, play:<viewId>, time:<viewId>, cost:<viewId>,
// footer:<viewId>, usd:<viewId>:track|cursor|tip (the scrubber's); a renderer that died
// (#155): failure:<viewId>, failureReason:<viewId>, retry:<viewId>, storm:<viewId>; the
// loading label (#156): cover:<viewId>, coverFile:<viewId>, coverPhase:<viewId>;
// viewMode:<viewId> (the footer's view mode pill, #153; its menu's rows
// menu:mode-<mode>), viewModeNote:<viewId>.
import QtQuick
import QtQuick.Window
import Specto.Native
import "../Shell"
import "../Video" as Video

Item {
    id: root
    property string viewId: ""
    property QtObject bridge: null
    property QtObject tl: null
    readonly property bool animated: bridge !== null && bridge.animated
    // the scrubber's theme lookups (VideoProgress asks its `view`)
    function tok(key) { return theme.colors[key] }
    function font(kind) { return theme.fonts[kind] }
    function metric(key, fallback) { return theme.metrics[key] !== undefined ? theme.metrics[key] : fallback }
    // where the view is in its collection (hosts.stepping sets them)
    property int stepIndex: -1
    property int stepCount: 0
    property var stepTips: []
    readonly property var panels: bridge ? bridge.panels : ({})
    function panel(name, key, fallback) {
        var p = panels[name]
        return p && p[key] !== undefined ? p[key] : fallback
    }
    readonly property int rowH: 20
    readonly property var selected: bridge ? bridge.selected : []

    clip: true
    focus: true
    onActiveFocusChanged: if (!activeFocus && bridge) bridge.focusLost()

    Rectangle {
        anchors.fill: parent
        color: theme.colors.surface_alt
    }

    // -- the VIEW panel (left) ------------------------------------------------------------
    SidePanel {
        id: viewPanel
        name: "panel:" + root.viewId + ":view"
        title: "View"
        side: "left"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: scrubber.top
        collapsed: root.panel("view", "collapsed", false)
        panelWidth: root.panel("view", "width", 200)
        minWidth: 140
        onCollapseRequested: (c) => root.bridge.setPanelCollapsed("view", c)
        onResized: (w) => root.bridge.setPanelWidth("view", w)

        Column {
            width: parent.width
            spacing: 4
            Item {
                width: parent.width
                height: speedCol.height + 10
                Column {
                    id: speedCol
                    x: 8
                    y: 6
                    width: parent.width - 16
                    spacing: 4
                    Text {
                        text: "Camera speed"
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                        color: theme.colors.text
                    }
                    Row {
                        width: parent.width
                        spacing: 6
                        SpeedSlider {
                            id: speed
                            objectName: "speed:" + root.viewId
                            width: parent.width - speedValue.width - 6
                            value: root.bridge ? root.bridge.speedLog2 : 0
                            onMoved: (v, final) => root.bridge.setSpeedLog2(v, final)
                        }
                        FooterLabel {
                            id: speedValue
                            width: 34
                            height: 14
                            horizontalAlignment: Text.AlignRight
                            mono: true
                            text: {
                                var s = Math.pow(2, speed.shown)
                                return s.toFixed(s < 1 ? 2 : 1) + "x"
                            }
                        }
                    }
                }
            }
            PanelButton {
                objectName: "origin:" + root.viewId
                icon: "home"
                text: "World origin"
                tip: "back to the first look: the world origin, everything in sight"
                onClicked: root.bridge.worldOrigin()
            }
            PanelButton {
                // F's button (#39): the selected prims and everything under them, else all
                objectName: "frameSelected:" + root.viewId
                icon: "target"
                text: "Frame selected"
                tip: "frame the selected prims and everything under them, else all (F)"
                onClicked: { root.forceActiveFocus(); root.bridge.frame() }
            }
            Item { width: 1; height: 6 }
            Column {
                // the camera mode (#39): Free, Unreal Editor's, or Orbit, around the selection
                x: 8
                width: parent.width - 16
                spacing: 4
                Text {
                    text: "Camera"
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    color: theme.colors.text
                }
                Rectangle {
                    id: modeSwitch
                    objectName: "cameraMode:" + root.viewId
                    readonly property string mode: root.bridge ? root.bridge.cameraMode : "free"
                    width: segments.width + 2
                    height: 22
                    radius: height / 2
                    color: theme.colors.surface
                    border.color: theme.colors.border_strong
                    border.width: 1
                    Row {
                        id: segments
                        anchors.centerIn: parent
                        spacing: 0
                        Repeater {
                            model: [
                                { mode: "free", label: "Free",
                                  tip: "free camera: drag turns and walks, right-drag looks and flies, Alt+drag orbits" },
                                { mode: "orbit", label: "Orbit",
                                  tip: "orbit camera: drag turns around the selection (else the point under the press), right-drag and wheel dolly toward it" }
                            ]
                            Rectangle {
                                id: segment
                                required property var modelData
                                readonly property bool on: modeSwitch.mode === modelData.mode
                                objectName: "cameraMode:" + root.viewId + ":" + modelData.mode
                                width: segmentLabel.implicitWidth + 20
                                height: 20
                                radius: height / 2
                                color: on ? theme.colors.accent_soft
                                     : segmentHover.hovered ? theme.colors.bg : "transparent"
                                Text {
                                    id: segmentLabel
                                    anchors.centerIn: parent
                                    text: segment.modelData.label
                                    font.family: theme.fonts.ui
                                    font.pixelSize: theme.metrics.font_px_small
                                    color: segment.on ? theme.colors.accent : theme.colors.text
                                }
                                HoverHandler { id: segmentHover }
                                TapHandler {
                                    onTapped: { root.forceActiveFocus(); root.bridge.setCameraMode(segment.modelData.mode) }
                                }
                                Tip {
                                    visible: segmentHover.hovered
                                    text: segment.modelData.tip
                                    tag: segment.objectName
                                }
                            }
                        }
                    }
                }
            }
            Item { width: 1; height: 6 }
            Column {
                objectName: "renderer:" + root.viewId
                x: 8
                width: parent.width - 16
                spacing: 3
                Text {
                    text: "Renderer"
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    color: theme.colors.text
                }
                FooterLabel {
                    width: parent.width
                    mono: true
                    tone: "text"
                    text: root.bridge && root.bridge.renderer ? root.bridge.renderer : "starting"
                }
                Text {
                    // what the renderer cannot show (Storm: MDL materials grey, no sky)
                    objectName: "rendererNote:" + root.viewId
                    width: parent.width
                    visible: text !== ""
                    text: root.bridge ? root.bridge.rendererNote : ""
                    wrapMode: Text.WordWrap
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    color: theme.colors.text_muted
                }
                FooterLabel {
                    width: parent.width
                    visible: root.bridge && root.bridge.upAxis !== ""
                    mono: true
                    text: root.bridge ? root.bridge.upAxis + " up · " + root.bridge.primCount + " prims" : ""
                }
            }
        }
    }

    // -- the PRIMS outliner (right) ---------------------------------------------------------
    SidePanel {
        id: primsPanel
        name: "panel:" + root.viewId + ":prims"
        title: "Prims"
        side: "right"
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: scrubber.top
        collapsed: root.panel("prims", "collapsed", false)
        panelWidth: root.panel("prims", "width", 240)
        minWidth: 140
        onCollapseRequested: (c) => root.bridge.setPanelCollapsed("prims", c)
        onResized: (w) => root.bridge.setPanelWidth("prims", w)

        Rectangle {
            id: search
            x: 8
            width: parent.width - 16
            height: 20
            radius: 4
            color: theme.colors.surface
            border.color: filterInput.activeFocus ? theme.colors.accent : theme.colors.border
            border.width: 1
            Icon {
                id: searchIcon
                x: 5
                anchors.verticalCenter: parent.verticalCenter
                name: "filter"
                size: 11
                color: theme.colors.text_faint
            }
            TextInput {
                id: filterInput
                objectName: "primsFilter:" + root.viewId
                anchors.left: searchIcon.right
                anchors.leftMargin: 5
                anchors.right: parent.right
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                clip: true
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
                color: theme.colors.text
                selectByMouse: true
                onTextEdited: filterDelay.restart()
                Keys.onEscapePressed: { text = ""; filterDelay.restart(); root.forceActiveFocus() }
                Text {
                    visible: !filterInput.text && !filterInput.activeFocus
                    text: "Search prims"
                    font: filterInput.font
                    color: theme.colors.text_faint
                }
            }
            Timer {
                id: filterDelay
                interval: 200
                onTriggered: root.bridge.setFilter(filterInput.text)
            }
        }
        ListView {
            id: prims
            objectName: "prims:" + root.viewId
            anchors.top: search.bottom
            anchors.topMargin: 6
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: root.bridge ? root.bridge.rows : []
            delegate: Item {
                id: row
                required property var modelData
                readonly property bool prim: modelData.kind === "prim"
                readonly property bool chosen: prim && root.selected.indexOf(modelData.path) >= 0
                objectName: (prim ? "prim:" : "more:") + root.viewId + ":" + modelData.path
                width: ListView.view.width
                height: root.rowH
                Rectangle {
                    anchors.fill: parent
                    color: row.chosen ? theme.colors.accent_soft
                         : rowHover.hovered ? theme.colors.surface_hover : "transparent"
                }
                Rectangle {
                    visible: row.chosen
                    width: 2
                    height: parent.height
                    color: theme.colors.accent
                }
                Item {
                    id: disclosure
                    objectName: "toggle:" + root.viewId + ":" + row.modelData.path
                    x: 6 + row.modelData.depth * 12
                    width: 12
                    height: parent.height
                    visible: row.prim && row.modelData.count > 0
                    Icon {
                        anchors.centerIn: parent
                        name: row.modelData.expanded ? "chevron-down" : "chevron-right"
                        size: 10
                        color: theme.colors.text_muted
                    }
                }
                FooterLabel {
                    id: primName
                    visible: row.prim
                    anchors.left: disclosure.right
                    anchors.leftMargin: 3
                    anchors.right: primType.left
                    anchors.rightMargin: 6
                    height: parent.height
                    mono: true
                    tone: "text"
                    elide: Text.ElideRight
                    text: row.modelData.name || ""
                }
                FooterLabel {
                    id: primType
                    visible: row.prim
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    height: parent.height
                    tone: "text_faint"
                    font.pixelSize: 9
                    text: row.modelData.type || ""
                }
                FooterLabel {
                    visible: !row.prim
                    x: disclosure.x + 15
                    height: parent.height
                    tone: "accent"
                    text: (row.modelData.count || 0) + " more"
                    font.underline: rowHover.hovered
                }
                HoverHandler { id: rowHover }
                TapHandler {
                    id: rowTap
                    // the chevron opens or closes the row; elsewhere a click selects it
                    // (Ctrl: adds) and a double-click frames it; "n more": the next page
                    onTapped: (point) => {
                        var path = row.modelData.path
                        if (!row.prim) root.bridge.moreRows(path)
                        else if (disclosure.visible && point.position.x < disclosure.x + disclosure.width + 2)
                            root.bridge.toggleRow(path)
                        else if (rowTap.tapCount === 2) root.bridge.frameRow(path)
                        else root.bridge.selectRow(path, (rowTap.point.modifiers & Qt.ControlModifier) !== 0)
                    }
                }
            }
            Connections {
                target: root.bridge
                function onRevealRequested(index) {
                    Qt.callLater(function () { prims.positionViewAtIndex(index, ListView.Contain) })
                }
            }
        }
        FooterLabel {
            anchors.centerIn: prims
            visible: prims.count === 0
            tone: "text_faint"
            text: filterInput.text ? "no prims match" : (root.bridge && root.bridge.stageName ? "loading prims" : "no stage")
        }
    }

    // -- the picture ------------------------------------------------------------------------
    Item {
        id: viewport
        objectName: "viewport:" + root.viewId
        anchors.left: viewPanel.right
        anchors.right: primsPanel.left
        anchors.top: parent.top
        anchors.bottom: scrubber.top
        clip: true
        onWidthChanged: resizeDelay.restart()
        onHeightChanged: resizeDelay.restart()
        Timer {
            id: resizeDelay
            interval: 60
            onTriggered: if (root.bridge && viewport.width > 0 && viewport.height > 0)
                root.bridge.viewportResized(viewport.width, viewport.height, Screen.devicePixelRatio)
        }
        Component.onCompleted: resizeDelay.restart()

        FrameItem {
            id: frame
            objectName: "frame"
            anchors.fill: parent
            // the loading label lifts on the new stage's first complete frame (#156)
            onFrameChanged: if (root.bridge) root.bridge.frameShown(frameSeq)
            onBackendChanged: if (root.bridge && backend === "none") root.bridge.framesUnavailable()
        }
        // the loading label (#156): the stage's name and the phase, over the previous
        // stage's picture and the new one's first partial frames
        Rectangle {
            id: cover
            objectName: "cover:" + root.viewId
            anchors.fill: parent
            z: 1
            visible: root.bridge !== null && root.bridge.covered
            color: theme.colors.surface_alt
            Column {
                anchors.centerIn: parent
                width: Math.min(parent.width - 32, 480)
                spacing: 6
                Text {
                    objectName: "coverFile:" + root.viewId
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideMiddle
                    text: root.bridge ? root.bridge.coverFile : ""
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px
                    color: theme.colors.text
                }
                Text {
                    objectName: "coverPhase:" + root.viewId
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: root.bridge ? root.bridge.coverPhase : ""
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    color: theme.colors.text_muted
                }
            }
        }
        // the start phase ("composing world.usda"), a failure, or no stage yet
        Rectangle {
            objectName: "phase:" + root.viewId
            readonly property string status: root.bridge ? root.bridge.healthStatus : ""
            readonly property bool hasStage: root.bridge !== null && root.bridge.stageName !== ""
            anchors.centerIn: parent
            visible: !failure.visible && !cover.visible && (status === "loading" || status === "error" || frame.frames === 0)
            width: phase.implicitWidth + 24
            height: phase.implicitHeight + 12
            radius: height / 2
            color: theme.colors.surface
            border.color: theme.colors.border
            border.width: 1
            Text {
                id: phase
                objectName: "phaseText:" + root.viewId
                anchors.centerIn: parent
                text: parent.status === "loading" || parent.status === "error"
                      ? root.bridge.healthMessage
                      : parent.hasStage ? "starting renderer" : "no stage"
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
                color: parent.status === "error" ? theme.colors.removed : theme.colors.text_muted
            }
        }
        // the renderer died (#155): why, its last error lines, Retry / Open with Storm,
        // over the dead picture
        Rectangle {
            id: failure
            objectName: "failure:" + root.viewId
            anchors.fill: parent
            z: 2  // over the picture's input: its buttons take the clicks
            visible: root.bridge !== null && root.bridge.failed
            color: theme.colors.surface_alt
            Column {
                anchors.centerIn: parent
                width: Math.min(parent.width - 32, 520)
                spacing: 8
                Text {
                    objectName: "failureReason:" + root.viewId
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: root.bridge ? root.bridge.failureReason : ""
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                    color: theme.colors.removed
                }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    visible: text !== ""
                    text: root.bridge ? root.bridge.failureCause : ""
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    color: theme.colors.text
                }
                Repeater {
                    model: root.bridge ? root.bridge.failureErrors : []
                    Text {
                        required property string modelData
                        width: parent.width
                        wrapMode: Text.WrapAnywhere
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        text: modelData
                        font.family: theme.fonts.mono
                        font.pixelSize: theme.metrics.font_px_small
                        color: theme.colors.text_muted
                    }
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    wrapMode: Text.WrapAnywhere
                    text: root.bridge && root.bridge.failureLog ? "log: " + root.bridge.failureLog : ""
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px_small
                    color: theme.colors.text_faint
                }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 8
                    ActionButton {
                        objectName: "retry:" + root.viewId
                        text: "Retry"
                        onClicked: root.bridge.retry()
                    }
                    ActionButton {
                        objectName: "storm:" + root.viewId
                        visible: root.bridge !== null && root.bridge.canStorm
                        text: "Open with Storm"
                        tip: "render this stage with Hydra Storm: lighter on the GPU; MDL materials show grey"
                        onClicked: root.bridge.openWithStorm()
                    }
                }
            }
        }
        // every button goes down and up on its own (UE's chords: LMB+RMB pans); the host's
        // camera reads the held set, and Alt as the first button went down (#151)
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            property real lastX: 0
            property real lastY: 0
            property real pressX: 0
            property real pressY: 0
            // a lone, unmodified left press: it picks if it comes up within the slop
            property bool clickable: false
            readonly property real clickSlop: 4  // usd/nav.py CLICK_SLOP
            onPressed: (m) => {
                root.forceActiveFocus()
                var alone = m.buttons === m.button
                if (alone) {
                    lastX = pressX = m.x
                    lastY = pressY = m.y
                }
                clickable = alone && m.button === Qt.LeftButton && !(m.modifiers & Qt.AltModifier)
                if (!root.bridge || width <= 0 || height <= 0) return
                root.bridge.press(m.button, (m.modifiers & Qt.AltModifier) !== 0,
                                  2 * m.x / width - 1, 1 - 2 * m.y / height)  // NDC, +y up
            }
            onPositionChanged: (m) => {
                if (!pressed || !root.bridge) return
                // a click's jitter does not move the camera; past the slop the drag takes it all
                if (clickable && Math.hypot(m.x - pressX, m.y - pressY) <= clickSlop) return
                clickable = false
                root.bridge.drag(m.x - lastX, m.y - lastY)
                lastX = m.x
                lastY = m.y
            }
            // a left click that did not drag picks (#140)
            onReleased: (m) => {
                if (!root.bridge) return
                root.bridge.release(m.button)
                if (m.button !== Qt.LeftButton || !clickable || width <= 0 || height <= 0) return
                clickable = false
                root.bridge.pick(2 * m.x / width - 1, 1 - 2 * m.y / height,  // NDC, +y up
                                 (m.modifiers & Qt.ControlModifier) !== 0)
            }
            // a grab taken away (a popup, the window lost) lets go of every button
            onCanceled: if (root.bridge) root.bridge.focusLost()
            onWheel: (w) => {
                if (root.bridge && w.angleDelta.y !== 0) root.bridge.wheel(w.angleDelta.y)
                w.accepted = true
            }
        }
    }

    // the view mode menu: the modes this stage offers (collision only with colliders), the
    // one shown bold; rows are menu:mode-<mode>
    PopMenu { id: modeMenu; heading: "View mode"; minWidth: 200 }
    readonly property string viewMode: bridge ? bridge.viewMode : "lit"
    function openModeMenu() {
        if (!root.bridge) return
        modeMenu.entries = root.bridge.viewModes.map(function (m) {
            return {
                text: m.label,
                name: "mode-" + m.mode,
                bold: m.mode === root.viewMode,
                action: function () { root.forceActiveFocus(); root.bridge.setViewMode(m.mode) }
            }
        })
        modeMenu.openAt(modeChip, 0, -(modeMenu.entries.length * 28 + 40) - 4)
    }

    // fly keys go down and up; the rest act once
    readonly property var flyKeys: ({ [Qt.Key_W]: "w", [Qt.Key_A]: "a", [Qt.Key_S]: "s",
                                      [Qt.Key_D]: "d", [Qt.Key_Q]: "q", [Qt.Key_E]: "e",
                                      [Qt.Key_Shift]: "shift" })
    Keys.onPressed: (event) => {
        if (!root.bridge) return
        var fly = root.flyKeys[event.key]
        if (fly !== undefined) {
            if (!event.isAutoRepeat) root.bridge.key(fly, true)
        } else switch (event.key) {
        case Qt.Key_F: root.bridge.frame(); break
        case Qt.Key_PageUp: root.bridge.step(-1); break
        case Qt.Key_PageDown: root.bridge.step(1); break
        case Qt.Key_Home: root.bridge.step(-1000000000); break
        case Qt.Key_End: root.bridge.step(1000000000); break
        case Qt.Key_Space: if (!root.animated) return; root.tl.togglePlay(); break
        case Qt.Key_Left: if (!root.animated) return; root.tl.step(-1); break
        case Qt.Key_Right: if (!root.animated) return; root.tl.step(1); break
        default: return
        }
        event.accepted = true
    }
    Keys.onReleased: (event) => {
        var fly = root.flyKeys[event.key]
        if (fly === undefined || event.isAutoRepeat || !root.bridge) return
        root.bridge.key(fly, false)
        event.accepted = true
    }

    // -- the scrubber (#141): an animated stage's time codes, in seconds ------------------
    Video.VideoProgress {
        id: scrubber
        objectPrefix: "usd:" + root.viewId
        tl: root.tl
        view: root
        tipLayer: root
        visible: root.animated
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: footer.top
        height: visible ? implicitHeight : 0
    }

    // -- the footer (#126) ------------------------------------------------------------------
    ViewFooter {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        viewId: root.viewId
        stepIndex: root.stepIndex
        stepCount: root.stepCount
        stepTips: root.stepTips
        onStepped: (delta) => root.bridge.step(delta)
        ChromeButton {
            objectName: "frameAll:" + root.viewId
            anchors.verticalCenter: parent.verticalCenter
            name: "target"
            tip: "frame the selection, else all (F)"
            onClicked: root.bridge.frame()
        }
        FooterLabel {
            objectName: "stage:" + root.viewId
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(implicitWidth, footer.room - 26)
            text: root.bridge ? root.bridge.stageName : ""
            mono: true
            tone: "text"
        }
        trailing: [
            FooterLabel {
                // what the view mode shows (collision: the colliders drawn and how)
                objectName: "viewModeNote:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: modeChip.on && text !== ""
                text: root.bridge ? root.bridge.viewModeNote : ""
            },
            Rectangle {
                // the view mode (#153): always shown (#158), the accent when not lit; a
                // click opens the menu
                id: modeChip
                objectName: "viewMode:" + root.viewId
                readonly property bool on: root.viewMode !== "lit"
                readonly property string text: root.bridge ? root.bridge.viewModeLabel : "Lit"
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                width: modeRow.width + 14
                height: 18
                radius: height / 2
                color: on ? theme.colors.accent_soft
                          : modeArea.containsMouse ? theme.colors.surface_hover : "transparent"
                border.color: on ? theme.colors.accent : theme.colors.border_strong
                border.width: 1
                Row {
                    id: modeRow
                    anchors.centerIn: parent
                    spacing: 3
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: modeChip.text
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                        color: modeChip.on ? theme.colors.accent : theme.colors.text
                    }
                    Icon {
                        anchors.verticalCenter: parent.verticalCenter
                        name: "chevron-down"
                        size: 9
                        color: modeChip.on ? theme.colors.accent : theme.colors.text_muted
                    }
                }
                MouseArea {
                    id: modeArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: root.openModeMenu()
                }
                Tip {
                    visible: modeArea.containsMouse && !modeMenu.opened
                    text: "view mode: lit, unlit, wireframe or collision"
                    tag: modeChip.objectName
                }
            },
            ChromeButton {
                objectName: "play:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: root.animated
                name: root.tl && root.tl.playing ? "pause" : "play"
                tip: root.tl && root.tl.playing ? "pause (Space)" : "play the stage's time (Space)"
                onClicked: { root.forceActiveFocus(); root.tl.togglePlay() }
            },
            FooterLabel {
                // the stage's time (#141): "timecode / start-end @ tc/s"
                objectName: "time:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: root.animated
                mono: true
                tone: "text"
                text: root.tl ? root.tl.label : ""
            },
            FooterLabel {
                // the source's frame time and VRAM (VRAM from NVML: #137)
                objectName: "cost:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                mono: true
                text: (frame.frames > 0 ? frame.renderMs.toFixed(1) : "–") + " ms · – GB"
            }
        ]
    }

    // the VIEW panel's buttons: a pill with an icon and words (World origin, Frame selected)
    component PanelButton: Rectangle {
        id: button
        property string icon: ""
        property string text: ""
        property string tip: ""
        signal clicked()
        x: 8
        width: buttonRow.width + 20
        height: 22
        radius: height / 2
        color: buttonHover.hovered ? theme.colors.bg : theme.colors.surface
        border.color: theme.colors.border_strong
        border.width: 1
        Row {
            id: buttonRow
            anchors.centerIn: parent
            spacing: 6
            Icon {
                anchors.verticalCenter: parent.verticalCenter
                name: button.icon
                size: 12
                color: theme.colors.text
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: button.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
                color: theme.colors.text
            }
        }
        HoverHandler { id: buttonHover }
        TapHandler { onTapped: button.clicked() }
        Tip {
            visible: buttonHover.hovered && button.tip !== ""
            text: button.tip
            tag: button.objectName
        }
    }

    // the failure's buttons (#155): a pill like World origin's
    component ActionButton: Rectangle {
        id: action
        property string text: ""
        property string tip: ""
        signal clicked()
        width: actionLabel.implicitWidth + 24
        height: 24
        radius: height / 2
        color: actionHover.hovered ? theme.colors.bg : theme.colors.surface
        border.color: theme.colors.border_strong
        border.width: 1
        Text {
            id: actionLabel
            anchors.centerIn: parent
            text: action.text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            color: theme.colors.text
        }
        HoverHandler { id: actionHover }
        TapHandler { onTapped: action.clicked() }
        Tip {
            visible: actionHover.hovered && action.tip !== ""
            text: action.tip
            tag: action.objectName
        }
    }

    // the VIEW panel's slider: log2 of the camera speed, -3 .. 3 in quarter steps; live
    // while dragged (`moved(v, false)`), final on release (`moved(v, true)`)
    component SpeedSlider: Item {
        id: slider
        property real value: 0
        property real from: -3
        property real to: 3
        property real stepSize: 0.25
        property real dragValue: NaN
        readonly property real shown: isNaN(dragValue) ? value : dragValue
        signal moved(real value, bool final)
        height: 14
        function at(x) {
            var f = Math.max(0, Math.min(1, (x - knob.width / 2) / (width - knob.width)))
            return Math.round((from + f * (to - from)) / stepSize) * stepSize
        }
        readonly property real fraction: (shown - from) / (to - from)
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            height: 4
            radius: 2
            color: theme.colors.border_strong
            Rectangle {
                width: knob.x + knob.width / 2
                height: parent.height
                radius: 2
                color: theme.colors.accent
            }
        }
        Rectangle {
            id: knob
            width: 12
            height: 12
            radius: 6
            anchors.verticalCenter: parent.verticalCenter
            x: slider.fraction * (slider.width - width)
            color: theme.colors.accent
        }
        MouseArea {
            anchors.fill: parent
            onPressed: (m) => { slider.dragValue = slider.at(m.x); slider.moved(slider.dragValue, false) }
            onPositionChanged: (m) => {
                if (!pressed) return
                var v = slider.at(m.x)
                if (v !== slider.dragValue) { slider.dragValue = v; slider.moved(v, false) }
            }
            onReleased: {
                var v = slider.dragValue
                slider.dragValue = NaN
                slider.moved(v, true)
            }
        }
        Tip {
            visible: sliderHover.hovered
            text: "how fast the camera flies, moves, pans and dollies (right-drag + wheel sets it too)"
            tag: slider.objectName
        }
        HoverHandler { id: sliderHover }
    }
}
