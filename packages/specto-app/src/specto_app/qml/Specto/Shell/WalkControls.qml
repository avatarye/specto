import QtQuick

// The walk-through's control bar (#174, design: controls): a pill fixed at the bottom
// centre. Step back / play-pause / step forward, the hop track, where the walk is, exit
// (x and Esc). Playing is the default (auto-advance on): the pause button shows its on
// state with a thin ring that fills over the hop's dwell. Preparing: steps and play are
// disabled and the track is hollow. The establishing shot keeps step back, disabled, so
// the pill keeps its shape. The summary trades the steps for Replay and exit for Done.
// The hop track: the stops in walk order, identicons inside, a branch after its fork as
// ↩ plus the identicon of the view it starts from; past filled, the current hop's ends
// ringed, future hollow; a click jumps to that hop, hovering names it. The walk speed
// (#183: slow, normal, fast, the user's setting) cycles with a click on its word.
Rectangle {
    id: root
    objectName: "walkControls"
    readonly property bool preparing: walkStage.preparing
    readonly property bool summary: walkStage.atSummary
    readonly property color walkTone: theme.colors[(walkStage.faces[walkStage.target] || {}).chain]
                                      || theme.colors.chain_0
    width: bar.implicitWidth + 18
    height: 44
    radius: height / 2
    color: theme.colors.surface
    border.width: 1
    border.color: theme.colors.border
    SoftShadow { radius: root.radius }

    component Divider: Rectangle {
        width: 1
        height: 18
        color: theme.colors.border
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    }
    component Words: Text {
        color: theme.colors.text_muted
        font.family: theme.fonts.mono
        font.pixelSize: theme.metrics.font_px_small
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    }

    Row {
        id: bar
        anchors.verticalCenter: parent.verticalCenter
        x: 8
        spacing: 4

        // the summary: Replay instead of the steps
        Text {
            objectName: "walkReplay"
            visible: root.summary
            text: "Replay"
            leftPadding: 10
            rightPadding: 6
            color: replayHover.hovered ? theme.colors.text : theme.colors.text_muted
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
            anchors.verticalCenter: parent.verticalCenter
            HoverHandler { id: replayHover }
            TapHandler { onTapped: walkStage.replay() }
        }
        ChromeButton {
            objectName: "walkBack"
            visible: !root.summary
            name: "chevron-left"
            tip: "step back (←)"
            disabled: root.preparing || walkStage.step <= 0
            anchors.verticalCenter: parent.verticalCenter
            onClicked: walkStage.stepBack()
        }
        ChromeButton {
            id: playBtn
            objectName: "walkPlay"
            visible: !root.summary
            name: walkStage.playing || root.preparing ? "pause" : "play"
            tip: walkStage.playing ? "pause (space)" : "play (space)"
            on: walkStage.playing
            disabled: root.preparing
            anchors.verticalCenter: parent.verticalCenter
            onClicked: walkStage.togglePlay()
            // the dwell: a thin ring that fills until auto-advance moves on
            property real progress: 0
            readonly property bool playing: walkStage.playing
            function restart() {
                dwellAnim.stop()
                progress = 0
                if (playing && walkStage.dwellMs > 0) {
                    dwellAnim.duration = walkStage.dwellMs
                    dwellAnim.start()
                }
            }
            // the dwell runs from the camera's landing (walkthrough.settled), as the ring
            onPlayingChanged: restart()
            // a new walk speed starts the step's dwell over (WalkController.setWalkSpeed)
            readonly property string speed: walkStage.walkSpeed
            onSpeedChanged: restart()
            Connections {
                target: walkStage
                function onSettledOn(step) { playBtn.restart() }
            }
            NumberAnimation { id: dwellAnim; target: playBtn; property: "progress"; from: 0; to: 1 }
            Canvas {
                objectName: "walkDwell"
                anchors.fill: parent
                visible: playBtn.playing
                readonly property real p: playBtn.progress
                onPChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = theme.colors.accent
                    ctx.lineWidth = 1.5
                    ctx.beginPath()
                    ctx.arc(width / 2, height / 2, width / 2 - 1, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * p)
                    ctx.stroke()
                }
            }
        }
        ChromeButton {
            objectName: "walkForward"
            visible: !root.summary
            name: "chevron-right"
            tip: "step forward (→)"
            disabled: root.preparing
            anchors.verticalCenter: parent.verticalCenter
            onClicked: walkStage.stepForward()
        }
        Item { width: 4; height: 1 }
        Divider {}

        // the hop track
        Row {
            objectName: "walkTrack"
            leftPadding: 6
            rightPadding: 6
            spacing: 0
            anchors.verticalCenter: parent.verticalCenter
            Repeater {
                model: walkStage.track
                delegate: Item {
                    id: entry
                    required property var modelData
                    required property int index
                    readonly property var hop: walkStage.hops[modelData.hop - 1] || ({})
                    readonly property bool lit: modelData.state !== "future"
                    objectName: "walkTrack:" + index
                    width: shape.width
                    height: 18
                    Item {
                        id: shape
                        width: modelData.kind === "stop" ? 18
                             : modelData.kind === "link" ? 12
                             : modelData.kind === "branch" ? branchRow.implicitWidth + 6
                             : moreText.implicitWidth + 8
                        height: 18
                        WalkChip {
                            objectName: "walkStop:" + entry.modelData.view
                            visible: entry.modelData.kind === "stop"
                            view: entry.modelData.view
                            state_: entry.modelData.state
                        }
                        Rectangle {
                            visible: entry.modelData.kind === "link"
                            anchors.verticalCenter: parent.verticalCenter
                            width: 12
                            height: 2
                            radius: 1
                            color: entry.lit ? root.walkTone : theme.colors.border
                        }
                        Row {
                            id: branchRow
                            visible: entry.modelData.kind === "branch"
                            x: 3
                            spacing: 1
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                objectName: "walkBranch:" + entry.modelData.hop
                                text: "↩"
                                color: entry.lit ? root.walkTone : theme.colors.text_faint
                                font.family: theme.fonts.ui
                                font.pixelSize: 12
                                font.bold: entry.lit
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            WalkChip {
                                size: 14
                                view: entry.modelData.view
                                state_: entry.modelData.state
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        Text {
                            id: moreText
                            visible: entry.modelData.kind === "more"
                            anchors.centerIn: parent
                            text: "· · ·"
                            color: theme.colors.text_faint
                            font.family: theme.fonts.ui
                            font.pixelSize: 12
                        }
                    }
                    HoverHandler { id: entryHover; enabled: !root.preparing }
                    TapHandler {
                        enabled: !root.preparing
                        onTapped: walkStage.goTo(entry.modelData.step)
                    }
                    Tip {
                        tag: "walkTrack:" + entry.index
                        visible: entryHover.hovered && entry.modelData.hop > 0
                        text: "hop " + entry.modelData.hop + " · " + (entry.hop.from || "") + " → "
                              + (entry.hop.to || "") + (entry.hop.branch ? ", another input" : "")
                    }
                }
            }
        }
        Words {
            objectName: "walkSpeed"
            visible: !root.preparing
            text: walkStage.walkSpeed
            rightPadding: 4
            color: speedHover.hovered ? theme.colors.text : theme.colors.text_muted
            HoverHandler { id: speedHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: walkStage.cycleWalkSpeed() }
            Tip {
                visible: speedHover.hovered
                text: "walk speed: slow / normal / fast (click to change)"
                tag: "walkSpeed"
            }
        }
        Words {
            objectName: "walkCounter"
            text: walkStage.counter
            rightPadding: 4
        }
        Divider { visible: !root.summary }
        ChromeButton {
            objectName: "walkExit"
            visible: !root.summary
            name: "close"
            tip: root.preparing ? "cancel (Esc)" : "exit (Esc)"
            anchors.verticalCenter: parent.verticalCenter
            onClicked: walkStage.exit()
        }
        Text {
            visible: !root.summary
            text: "Esc"
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            anchors.verticalCenter: parent.verticalCenter
        }
        // the summary: Done
        Rectangle {
            objectName: "walkDone"
            visible: root.summary
            width: doneText.implicitWidth + 24
            height: 28
            radius: 14
            color: doneHover.hovered ? Qt.darker(theme.colors.accent, 1.1) : theme.colors.accent
            anchors.verticalCenter: parent.verticalCenter
            Text {
                id: doneText
                anchors.centerIn: parent
                text: "Done"
                color: theme.colors.surface
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
                font.weight: Font.DemiBold
            }
            HoverHandler { id: doneHover }
            TapHandler { onTapped: walkStage.exit() }
        }
    }
}
