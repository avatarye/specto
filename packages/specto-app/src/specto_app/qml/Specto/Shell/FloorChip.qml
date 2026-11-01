import QtQuick

// The far cue's chip (#191, design #188 frame 08): a new view beyond the maximum pulse
// distance gets three pulses at the window edge (the tile floor) and this chip there:
// its name, how far it is and an arrow towards it. It stays 4 s (`tileFloor.chip`);
// a click pans to the view.
Rectangle {
    id: root
    objectName: "floorChip"
    readonly property var chip: tileFloor.chip
    readonly property real pad: theme.metrics.pad
    readonly property bool shown: chip.show === true
    // the last chip, kept while it fades out
    property var last: ({})
    onChipChanged: if (chip.show === true) last = chip  // not `shown`: it may not have updated yet
    visible: opacity > 0
    opacity: shown ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 160 } }
    width: row.implicitWidth + 2 * theme.metrics.pad_tight + 8
    height: 26
    radius: height / 2
    x: Math.max(pad, Math.min(parent.width - width - pad, (last.x || 0) - width / 2))
    y: Math.max(pad, Math.min(parent.height - height - pad, (last.y || 0) - height / 2))
    color: hover.hovered ? theme.colors.surface_hover : theme.colors.surface
    border.width: 1.5
    border.color: theme.colors[last.chain || "accent"] || theme.colors.accent

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Text {
            text: root.last.view || ""
            color: theme.colors.text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: root.last.text || ""
            color: theme.colors.text_muted
            font.family: theme.fonts.mono
            font.pixelSize: theme.metrics.font_px - 1
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            objectName: "floorChipArrow"
            text: "→"
            color: theme.colors.text
            font.pixelSize: theme.metrics.font_px
            rotation: (root.last.angle || 0) * 180 / Math.PI
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: tileFloor.chipClicked() }
}
