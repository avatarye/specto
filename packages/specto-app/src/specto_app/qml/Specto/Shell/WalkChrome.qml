import QtQuick

// The walk-through's float layer (#174): the caption card and the control bar, fixed at
// the bottom centre of the canvas area (the bar 16 px above the bottom bar, the card 12 px
// above the bar), in both camera levels. Leaving, they drop 8 px and fade in 160 ms.
// "Open link" opens the link view, as a link node does.
Item {
    id: root
    objectName: "walkChrome"
    readonly property bool on: walkStage.active
    visible: on || leave.running
    opacity: on ? 1 : 0
    Behavior on opacity { NumberAnimation { id: leave; duration: 160 } }
    // the walk takes the keys from a text field (the prompt it was asked in): the arrows,
    // Space, Home, End and Esc are the walk's while it shows (WalkStage's event filter)
    onOnChanged: if (on) Qt.callLater(keySink.forceActiveFocus)
    Item { id: keySink; objectName: "walkKeys" }

    Item {
        id: band
        anchors.fill: parent
        anchors.topMargin: root.on ? 0 : 8
        anchors.bottomMargin: root.on ? 0 : -8
        Behavior on anchors.topMargin { NumberAnimation { duration: 160 } }
        Behavior on anchors.bottomMargin { NumberAnimation { duration: 160 } }

        WalkControls {
            id: controls
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 16
        }
        WalkCaption {
            id: card
            // never wider than the area, never taller than what is left above the bar
            width: Math.min(620, root.width - 32)
            maxHeight: Math.max(96, Math.min(360, root.height - controls.height - 16 - 12 - 16))
            // the band the camera keeps clear: the card's top to the area's bottom
            readonly property real keepClear: root.height - y + 12
            onKeepClearChanged: if (root.on && height > 0) walkStage.setBand(keepClear)
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: controls.top
            anchors.bottomMargin: 12
            onOpenLink: function (linkId) { linkView.openFor(linkId) }
        }
    }

    LinkView { id: linkView }
}
