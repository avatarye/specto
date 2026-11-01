import QtQuick

// The `/` completion above the agent panel's input (#162): the project's own skills and
// commands (`promptBar.slashCommands`), then Claude Code's built-ins greyed, which Specto
// refuses. Arrow keys choose, Tab or Enter completes, Esc closes the list (AgentPanel
// routes the keys here).
Rectangle {
    id: root
    objectName: "slashList"
    property var rows: []
    property int current: 0
    signal picked(string name)
    readonly property bool active: visible && rows.length > 0
    height: column.implicitHeight + 8
    radius: theme.metrics.radius_inner
    color: theme.colors.surface
    border.width: 1
    border.color: theme.colors.border_strong
    SoftShadow { radius: root.radius }

    function move(step) {
        if (rows.length) current = (current + step + rows.length) % rows.length
    }
    function pick() {
        if (rows.length) picked(rows[Math.max(0, Math.min(current, rows.length - 1))].name)
    }

    Column {
        id: column
        x: 4
        y: 4
        width: parent.width - 8
        Repeater {
            model: root.rows
            delegate: Rectangle {
                id: row
                required property var modelData
                required property int index
                objectName: "slash:" + modelData.name
                width: column.width
                height: 28
                radius: 6
                color: index === root.current ? theme.colors.accent_soft
                       : (rowHover.hovered ? theme.colors.surface_hover : "transparent")
                Text {
                    id: nameText
                    x: 10
                    width: Math.min(implicitWidth, parent.width * 0.4)
                    anchors.verticalCenter: parent.verticalCenter
                    text: "/" + row.modelData.name
                    elide: Text.ElideRight
                    color: row.modelData.available ? theme.colors.text : theme.colors.text_faint
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px_small + 1
                }
                Text {
                    anchors.left: nameText.right
                    anchors.leftMargin: 14
                    anchors.right: kindText.left
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.modelData.description
                    elide: Text.ElideRight
                    color: row.modelData.available ? theme.colors.text_muted : theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                }
                Text {
                    id: kindText
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.modelData.kind
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                }
                HoverHandler { id: rowHover }
                TapHandler {
                    onTapped: { root.current = row.index; root.pick() }
                }
            }
        }
        Text {
            x: 10
            height: 22
            verticalAlignment: Text.AlignVCenter
            text: "↑↓ choose · Tab or Enter completes · Esc closes the list"
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
        }
    }
}
