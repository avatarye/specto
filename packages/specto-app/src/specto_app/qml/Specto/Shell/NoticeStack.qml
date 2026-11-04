import QtQuick

// Fix-it notices (HLD principle 4): message plus fix. Errors stay until dismissed,
// info fades after a few seconds.
Column {
    id: root
    spacing: 6
    Repeater {
        model: notices.items
        delegate: Rectangle {
            id: card
            required property var modelData
            objectName: "notice:" + modelData.level  // the flows' notice:warn (#192)
            width: root.width
            height: body.implicitHeight + 2 * theme.metrics.pad_tight + 4
            radius: theme.metrics.radius_inner
            color: theme.colors.surface
            border.color: modelData.level === "error" ? theme.colors.error
                        : modelData.level === "warn" ? theme.colors.changed : theme.colors.accent
            Rectangle {
                width: 4
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.margins: 1
                radius: 2
                color: card.border.color
            }
            Column {
                id: body
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: theme.metrics.pad
                anchors.rightMargin: 30
                spacing: 2
                Text {
                    objectName: "noticeText:" + card.modelData.level  // the flows' assert_text
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: (card.modelData.level === "error" ? "[ERROR] " : card.modelData.level === "warn" ? "[WARN] " : "")
                          + card.modelData.message
                    color: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                }
                Text {
                    visible: !!card.modelData.fix
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "fix: " + card.modelData.fix
                    color: theme.colors.text_muted
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px_small
                }
            }
            Pill {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 4
                text: "x"
                onClicked: notices.dismiss(card.modelData.id)
            }
            Timer {
                running: card.modelData.level === "info"
                interval: 6000
                onTriggered: notices.dismiss(card.modelData.id)
            }
        }
    }
}
