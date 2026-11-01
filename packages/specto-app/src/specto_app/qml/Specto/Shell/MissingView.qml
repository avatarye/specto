import QtQuick

// Shown when a view's type has no host in this process: why, and what to do.
Rectangle {
    id: root
    property string viewId: ""
    property string viewType: ""
    property string reason: ""
    property string fix: ""
    color: theme.colors.removed_soft

    Column {
        anchors.centerIn: parent
        width: parent.width - 2 * theme.metrics.pad
        spacing: 6
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: theme.glyphs.error + " no host for view type \"" + root.viewType + "\""
            color: theme.colors.error
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.reason
            color: theme.colors.text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: "fix: " + root.fix
            color: theme.colors.text_muted
            font.family: theme.fonts.mono
            font.pixelSize: theme.metrics.font_px_small
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
