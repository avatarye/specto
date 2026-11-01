import QtQuick

// The agent prompt (#162; it replaces the prompt bar of #44): the agent icon floating at
// the window's bottom left above everything, the status bar included, and the agent
// panel that opens above it. The icon (AgentIcon.qml, swappable art) shows the agent's
// state; resting the pointer on it opens the panel (`promptBar.panel` owns the open, pin
// and size rules: hover, text, focus, a selection drag and the pin hold it open, Esc
// closes it and keeps the draft). Nothing opens by itself: a turn that ends while the
// panel is closed changes the icon and the status bar only. Ctrl+K (Main.qml) opens it
// and focuses the input; `promptBar.prefill(text)` ("Ask agent to change", #45) too.
Item {
    id: root
    objectName: "promptBar"
    readonly property var input: card.input
    readonly property real iconSize: 52
    // the icon is as far from the window's left as from its bottom (#170)
    readonly property real iconGap: theme.metrics.pad

    onWidthChanged: promptBar.panel.setWindowSize(width, height)
    onHeightChanged: promptBar.panel.setWindowSize(width, height)
    Component.onCompleted: promptBar.panel.setWindowSize(width, height)

    function focusInput() {
        promptBar.panel.summon()
        card.input.forceActiveFocus()
    }
    function prefill(text) {
        card.input.text = text
        card.input.cursorPosition = card.input.length
        focusInput()
    }
    function closePanel() {
        card.input.focus = false
        promptBar.panel.escape()
    }
    // Esc (Main.qml's shortcut, or the input's own key): the slash list first, then the panel
    function handleEscape() {
        if (card.slashOpen) { card.closeSlash(); return true }
        if (promptBar.panel.open) { closePanel(); return true }
        return false
    }
    // Tab completes a slash command, and never toggles the overworld while typing
    function handleTab() {
        if (card.slashOpen) { card.pickSlash(); return true }
        return card.input.activeFocus
    }
    Connections {
        target: promptBar
        function onPrefillRequested(text) { root.prefill(text) }
    }
    Connections {
        target: promptBar.icon
        function onShake() { art.shake() }
    }

    // what holds the panel open besides the pointer: text, focus, a selection drag
    readonly property var holds: [card.input.length > 0, card.input.activeFocus, press.active]
    onHoldsChanged: promptBar.panel.setHolds(holds[0], holds[1], holds[2])

    // -- the icon ----------------------------------------------------------------------------
    Item {
        id: iconBox
        objectName: "agentIcon"
        x: root.iconGap
        y: root.height - root.iconGap - root.iconSize
        width: root.iconSize
        height: root.iconSize
        z: 2
        AgentIcon {
            id: art
            state: promptBar.icon.state
            asks: promptBar.icon.asks
            hovered: iconHover.hovered
            gaze: iconHover.hovered
                  ? Qt.vector2d((iconHover.point.position.x - root.iconSize / 2) / (root.iconSize / 2),
                                (iconHover.point.position.y - root.iconSize / 2) / (root.iconSize / 2))
                  : (promptBar.panel.open ? Qt.vector2d(0.5, -0.8) : Qt.vector2d(0, 0))
        }
        HoverHandler {
            id: iconHover
            cursorShape: Qt.PointingHandCursor
            onHoveredChanged: promptBar.panel.hoverIcon(hovered)
        }
        TapHandler { onTapped: root.focusInput() }
        Tip {
            visible: iconHover.hovered && promptBar.icon.state === "error" && !promptBar.panel.open
            text: "the agent is unavailable: " + promptBar.icon.errorText
            tag: "agentIcon"
            y: -height - 4
        }
    }
    // a finished turn, for the GUI flows' wait_until
    Item {
        objectName: "agentDone"
        visible: promptBar.hasReply && !promptBar.busy
        width: 1
        height: 1
    }

    // -- the panel, and the 8 px gap above the icon that counts as inside -------------------
    Item {
        id: panelBox
        x: root.iconGap
        y: iconBox.y - height
        width: card.width
        height: card.height + 8
        visible: card.opacity > 0
        HoverHandler {
            id: panelHover
            onHoveredChanged: promptBar.panel.hoverPanel(hovered)
        }
        PointHandler {
            id: press
            acceptedButtons: Qt.LeftButton
        }
        AgentPanel {
            id: card
            y: 0
            readonly property bool shown: promptBar.panel.open
            opacity: shown ? 1 : 0
            scale: shown ? 1 : 0.96
            transformOrigin: Item.BottomLeft
            transform: Translate { y: card.shown ? 0 : 6 }
            Behavior on opacity { NumberAnimation { duration: card.shown ? 180 : 160; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        }
    }
}
