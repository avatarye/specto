import QtQuick
import QtQuick.Controls

// The agent panel (#162): the conversation thread with the window's agent, above the
// agent icon. Header: the agent, the project, new session, pin. The thread: the whole
// session (ThreadRow), newest at the bottom; scrolling back to the top pages older turns
// in (skeleton rows meanwhile), "Latest" returns to the newest. The input: multi-line
// markdown (Enter sends, Shift+Enter a new line; a formatted paste arrives as markdown,
// Ctrl+Shift+V pastes plain text), `/` completes the project's commands and skills, the
// model chip picks the next turn's model. Resize from the top edge, the right edge or
// the top-right corner; the bottom-left stays on the icon (`promptBar.panel` clamps and
// remembers the size per project).
Rectangle {
    id: root
    objectName: "promptPanel"
    property alias input: input
    readonly property bool slashOpen: slash.active
    readonly property real headerH: 44
    readonly property real footerH: inputBox.height + 44
    radius: theme.metrics.radius_card
    color: theme.colors.surface
    border.width: 1
    border.color: theme.colors.border_strong
    width: promptBar.panel.shownWidth
    height: promptBar.panel.heightFor(headerH + list.contentHeight + 24 + footerH)
    SoftShadow { radius: root.radius }

    function send() {
        if (promptBar.busy) return  // the input keeps its text and focus for a follow-up
        if (promptBar.submit(input.text)) {
            input.text = ""
            list.stick = true
        }
    }
    function closeSlash() { slash.dismissed = input.text }
    function pickSlash() { slash.pick() }
    function complete(name) {
        input.text = "/" + name + " "
        input.cursorPosition = input.text.length
        slash.dismissed = input.text
    }
    function paste(plain) {
        var hadFormatting = !plain && promptBar.clipboardHasFormatting()
        var text = promptBar.clipboardMarkdown(plain)
        if (text === "") return
        if (input.selectedText !== "") input.remove(input.selectionStart, input.selectionEnd)
        input.insert(input.cursorPosition, text)
        pasteHint.visible = hadFormatting
        pasteHintTimer.restart()
    }

    // -- header ------------------------------------------------------------------------------
    Item {
        id: header
        width: parent.width
        height: root.headerH
        // the S character (#170), in its 16 px drawing
        SMark {
            id: mark
            objectName: "panelMark"
            x: 15
            anchors.verticalCenter: parent.verticalCenter
            width: 16
            height: 16
            small: true
        }
        Text {
            id: title
            anchors.left: mark.right
            anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            objectName: "panelTitle"
            // the instance name (#187): the address fleet agents message this agent by
            text: "Specto agent" + (app.instanceName ? " · " + app.instanceName : "")
            color: theme.colors.text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
            font.bold: true
        }
        Text {
            anchors.left: title.right
            anchors.leftMargin: 6
            anchors.right: buttons.left
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: app.projectName()
            elide: Text.ElideRight
            color: theme.colors.text_muted
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small + 1
        }
        Row {
            id: buttons
            anchors.right: parent.right
            anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            IconButton {
                objectName: "panelNewSession"
                name: "new-session"
                size: 26
                iconSize: 15
                enabled: !promptBar.busy
                tip: "new session: the agent starts fresh; this thread stays above a divider"
                onClicked: promptBar.newSession()
            }
            IconButton {
                objectName: "panelPin"
                name: "pin"
                size: 26
                iconSize: 15
                tone: promptBar.panel.pinned ? "accent" : "neutral"
                color: promptBar.panel.pinned ? theme.colors.accent_soft : "transparent"
                tip: promptBar.panel.pinned ? "unpin: the panel closes when you leave it"
                                            : "pin: keep the panel open"
                onClicked: promptBar.panel.togglePin()
            }
        }
        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: theme.colors.border
        }
    }

    // -- the thread --------------------------------------------------------------------------
    ListView {
        id: list
        objectName: "threadList"
        anchors.top: header.bottom
        anchors.bottom: footer.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: 12
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        clip: true
        model: promptBar.thread
        spacing: 4
        boundsBehavior: Flickable.StopAtBounds
        cacheBuffer: 400
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        //: follow the newest row (a streaming reply, a new turn) unless the user scrolled up
        property bool stick: true

        delegate: ThreadRow {
            width: list.width - 8
            onSkillOpen: function (anchor, entryId) {
                skillPopover.markdown = promptBar.skillMarkdownAt(entryId)
                skillPopover.path = path
                skillPopover.openAbove(anchor)
            }
        }
        header: Column {
            width: list.width - 8
            visible: promptBar.thread.loading
            height: visible ? implicitHeight + 8 : 0
            spacing: 8
            Text {
                objectName: "threadLoading"
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Loading earlier turns..."
                color: theme.colors.text_faint
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
            }
            Repeater {
                model: [[0.45, true], [0.8, false], [0.6, false]]
                Rectangle {
                    required property var modelData
                    objectName: "threadSkeleton"
                    width: parent.width * modelData[0]
                    x: modelData[1] ? parent.width - width : 0
                    height: modelData[1] ? 28 : 10
                    radius: modelData[1] ? 12 : 5
                    color: theme.colors.bg_tint
                }
            }
        }
        Text {
            objectName: "threadEmpty"
            visible: list.count === 0
            anchors.centerIn: parent
            width: parent.width - 32
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Ask for what you want to see: a view of your files, two runs side by side, "
                  + "a link that keeps them in step."
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
        }

        function toEnd() {
            stick = true
            positionViewAtEnd()
        }
        onContentHeightChanged: if (stick) Qt.callLater(positionViewAtEnd)
        onCountChanged: if (stick) Qt.callLater(positionViewAtEnd)
        onHeightChanged: if (stick) Qt.callLater(positionViewAtEnd)
        onMovementEnded: stick = atYEnd
        onContentYChanged: {
            if (moving && !atYEnd) stick = false
            if (contentY - originY < 40 && promptBar.thread.hasOlder && !promptBar.thread.loading
                    && (moving || dragging))
                promptBar.thread.loadOlder()
        }
        Connections {
            target: promptBar.thread
            function onOlderLoaded(n) { list.positionViewAtIndex(n, ListView.Beginning) }
        }
        Connections {
            target: promptBar.panel
            function onOpened() { list.toEnd() }
        }
        Component.onCompleted: positionViewAtEnd()
    }
    Pill {
        id: latest
        objectName: "latestPill"
        visible: !list.stick && !list.atYEnd && list.count > 0
        anchors.horizontalCenter: list.horizontalCenter
        anchors.bottom: list.bottom
        anchors.bottomMargin: 8
        icon: "chevron-down"
        text: "Latest"
        color: theme.colors.surface
        onClicked: list.toEnd()
    }

    SlashList {
        id: slash
        //: the input text the list was closed for (Esc, a completion): it stays closed until
        //: the text changes
        property string dismissed: ""
        readonly property string query: /^\/\S*$/.test(input.text) ? input.text : ""
        rows: query !== "" ? promptBar.slashCommands(query) : []
        visible: query !== "" && query !== dismissed && rows.length > 0 && input.activeFocus
        onRowsChanged: current = 0
        anchors.left: inputBox.left
        anchors.right: inputBox.right
        anchors.bottom: inputBox.top
        anchors.bottomMargin: 6
        z: 3
        onPicked: function (name) { root.complete(name) }
    }

    // -- the input ---------------------------------------------------------------------------
    Item {
        id: footer
        anchors.bottom: parent.bottom
        width: parent.width
        height: root.footerH
        Rectangle {
            anchors.top: parent.top
            width: parent.width
            height: 1
            color: theme.colors.border
        }
    }
    Rectangle {
        id: inputBox
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: controls.top
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.bottomMargin: 4
        height: Math.min(input.contentHeight, 140) + 20
        radius: theme.metrics.radius_inner + 2
        color: theme.colors.surface
        border.width: input.activeFocus ? 2 : 1
        border.color: input.activeFocus ? theme.colors.focus : theme.colors.border_strong
        Flickable {
            id: inputScroll
            anchors.fill: parent
            anchors.margins: 10
            contentWidth: width
            contentHeight: input.contentHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            function follow(r) {
                if (contentY >= r.y) contentY = r.y
                else if (contentY + height <= r.y + r.height) contentY = r.y + r.height - height
            }
            TextEdit {
                id: input
                objectName: "promptInput"
                width: inputScroll.width
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.Wrap
                selectByMouse: true
                color: theme.colors.text
                selectionColor: theme.colors.accent_soft
                selectedTextColor: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px + 1
                onCursorRectangleChanged: inputScroll.follow(cursorRectangle)
                onTextChanged: promptBar.setDraft(text)
                Component.onCompleted: promptBar.highlight(input.textDocument)
                Keys.onPressed: function (event) {
                    if (event.matches(StandardKey.Paste)) {
                        root.paste(false)
                        event.accepted = true
                    } else if (event.key === Qt.Key_V
                               && (event.modifiers & Qt.ControlModifier)
                               && (event.modifiers & Qt.ShiftModifier)) {
                        root.paste(true)
                        event.accepted = true
                    } else if (slash.active && event.key === Qt.Key_Up) {
                        slash.move(-1)
                        event.accepted = true
                    } else if (slash.active && event.key === Qt.Key_Down) {
                        slash.move(1)
                        event.accepted = true
                    } else if (slash.active && event.key === Qt.Key_Tab) {
                        slash.pick()
                        event.accepted = true
                    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        if (event.modifiers & Qt.ShiftModifier) {
                            input.insert(input.cursorPosition, "\n")
                        } else if (slash.active) {
                            slash.pick()
                        } else {
                            root.send()
                        }
                        event.accepted = true
                    }
                }
                Text {
                    anchors.fill: parent
                    visible: input.length === 0
                    text: promptBar.busy ? "working..." : "Ask for what you want to see"
                    color: theme.colors.text_faint
                    font: input.font
                }
            }
        }
    }
    Item {
        id: controls
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        height: 40
        Pill {
            id: modelChip
            objectName: "modelChip"
            anchors.verticalCenter: parent.verticalCenter
            text: promptBar.modelLabel + " ▾"
            tone: "neutral"
            tip: "model for the next turn (" + promptBar.model + "); click to switch"
            onClicked: {
                modelMenu.entries = promptBar.models.map(function (m) {
                    return {
                        text: m.label,
                        detail: m.name === promptBar.model ? "current" : m.detail,
                        name: "model-" + m.name,
                        action: function () { promptBar.setModel(m.name) }
                    }
                })
                // above the chip; a closed Popup has no laid-out height yet: size it from the rows
                var menuHeight = modelMenu.entries.length * 29 + 40
                modelMenu.openAt(modelChip, 0, -menuHeight - 6)
            }
        }
        // the user's Peer requests setting (#187): what a fleet agent's message does
        Pill {
            id: peerChip
            objectName: "peerChip"
            anchors.left: modelChip.right
            anchors.leftMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            text: "peers: " + promptBar.peerRequests + " ▾"
            tone: "neutral"
            tip: "Peer requests from fleet agents: " + promptBar.peerRequests
                 + "; click to switch"
            onClicked: {
                peerMenu.entries = promptBar.peerModes.map(function (m) {
                    return {
                        text: m.name,
                        detail: m.name === promptBar.peerRequests ? "current" : m.detail,
                        name: "peers-" + m.name,
                        action: function () { promptBar.setPeerRequests(m.name) }
                    }
                })
                var menuHeight = peerMenu.entries.length * 29 + 40
                peerMenu.openAt(peerChip, 0, -menuHeight - 6)
            }
        }
        Text {
            id: pasteHint
            objectName: "pasteHint"
            visible: false
            anchors.left: peerChip.right
            anchors.leftMargin: 10
            anchors.right: sendButton.left
            anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: "Pasted with its formatting as markdown · Ctrl+Shift+V pastes plain text"
            elide: Text.ElideRight
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            Timer { id: pasteHintTimer; interval: 4000; onTriggered: pasteHint.visible = false }
        }
        Text {
            visible: !pasteHint.visible
            anchors.left: peerChip.right
            anchors.leftMargin: 10
            anchors.right: sendButton.left
            anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: promptBar.busy ? "the agent is working · Esc closes the panel"
                  : (input.length > 0 ? "Enter sends · Shift+Enter new line"
                                      : "/ commands · Shift+Enter new line")
            elide: Text.ElideRight
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
        }
        Rectangle {
            id: sendButton
            objectName: promptBar.busy ? "promptStop" : "promptSend"
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 28
            height: 28
            radius: 14
            readonly property bool ready: promptBar.busy || input.length > 0
            color: ready ? theme.colors.agent : theme.colors.bg_tint
            Icon {
                anchors.centerIn: parent
                name: promptBar.busy ? "stop" : "send"
                size: promptBar.busy ? 12 : 15
                color: sendButton.ready ? theme.colors.surface : theme.colors.text_faint
            }
            TapHandler {
                onTapped: promptBar.busy ? promptBar.interrupt() : root.send()
            }
            HoverHandler { id: sendHover; cursorShape: Qt.PointingHandCursor }
            Tip {
                visible: sendHover.hovered
                text: promptBar.busy ? "stop the agent's turn" : "send (Enter)"
                tag: "promptSend"
            }
        }
    }

    // -- resizing: the top edge, the right edge, the top-right corner ------------------------
    component Grip: MouseArea {
        required property Item card
        property bool horizontal: false
        property bool vertical: false
        property real startW: 0
        property real startH: 0
        property point start
        hoverEnabled: true
        preventStealing: true
        z: 4
        onPressed: function (mouse) {
            start = mapToItem(null, mouse.x, mouse.y)
            startW = card.width
            startH = card.height
        }
        onPositionChanged: function (mouse) {
            if (!pressed) return
            var p = mapToItem(null, mouse.x, mouse.y)
            var w = horizontal ? startW + (p.x - start.x) : promptBar.panel.width
            var h = vertical ? startH - (p.y - start.y) : promptBar.panel.storedHeight
            promptBar.panel.resizeTo(w, h)
        }
        onReleased: promptBar.panel.commit()
    }
    Grip {
        card: root
        objectName: "panelResize:top"
        vertical: true
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.topMargin: -3
        height: 7
        cursorShape: Qt.SizeVerCursor
    }
    Grip {
        card: root
        objectName: "panelResize:right"
        horizontal: true
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.topMargin: 12
        anchors.rightMargin: -3
        width: 7
        cursorShape: Qt.SizeHorCursor
    }
    Grip {
        id: corner
        card: root
        objectName: "panelResize:corner"
        horizontal: true
        vertical: true
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: -4
        anchors.rightMargin: -4
        width: 16
        height: 16
        cursorShape: Qt.SizeBDiagCursor
    }
    // the size while a grip is held
    Rectangle {
        objectName: "panelSizeTip"
        visible: corner.pressed
        x: parent.width + 8
        y: -4
        width: sizeText.implicitWidth + 14
        height: 22
        radius: theme.metrics.radius_inner
        color: theme.colors.text
        Text {
            id: sizeText
            anchors.centerIn: parent
            text: Math.round(root.width) + " x " + Math.round(root.height)
            color: theme.colors.surface
            font.family: theme.fonts.mono
            font.pixelSize: theme.metrics.font_px_small
        }
    }

    PopMenu { id: modelMenu; heading: "Model for the next turn" }
    PopMenu { id: peerMenu; heading: "Peer requests" }
    SkillPopover { id: skillPopover }
}
