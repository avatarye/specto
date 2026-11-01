import QtQuick
import QtQuick.Controls

// A skill row's Open (#161, in the agent thread since #162): the project skill's SKILL.md
// rendered as markdown (Qt's own renderer), its path below; read-only, Escape or a click
// outside closes it. Opens above the row.
Popup {
    id: root
    objectName: "skillPopover"
    parent: Overlay.overlay
    modal: false
    dim: false
    padding: theme.metrics.pad
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    width: Math.min(560, Overlay.overlay.width - 2 * theme.metrics.pad)
    height: Math.min(column.implicitHeight + 2 * padding, Overlay.overlay.height * 0.6)
    property string markdown: ""
    property string path: ""
    property real anchorX: 0
    property real anchorTop: 0
    x: Math.max(4, Math.min(anchorX, Overlay.overlay.width - width - 4))
    y: Math.max(4, anchorTop - height - 6)

    function openAbove(item) {
        var p = item.mapToItem(Overlay.overlay, 0, 0)
        root.anchorX = p.x
        root.anchorTop = p.y
        root.open()
    }

    background: Rectangle {
        radius: theme.metrics.radius_card
        color: theme.colors.surface
        border.color: theme.colors.border_strong
        SoftShadow { radius: parent.radius }
    }
    contentItem: Flickable {
        clip: true
        contentWidth: width
        contentHeight: column.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        Column {
            id: column
            width: parent.width
            spacing: 8
            Text {
                objectName: "skillMarkdown"
                width: parent.width
                text: root.markdown
                textFormat: Text.MarkdownText
                wrapMode: Text.Wrap
                color: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
            }
            Text {
                width: parent.width
                text: root.path
                color: theme.colors.text_faint
                font.family: theme.fonts.mono
                font.pixelSize: theme.metrics.font_px_small
                elide: Text.ElideMiddle
            }
        }
    }
}
