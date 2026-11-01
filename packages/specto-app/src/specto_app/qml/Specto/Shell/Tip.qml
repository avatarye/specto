import QtQuick
import QtQuick.Controls

// A hover tip below its parent, drawn in the window's overlay so no card, neighbour or
// clip covers it (a tip inside the card was hidden by the focused card next to it).
// `tag` names the item for locate/tests: objectName "tip:<tag>" on the background.
// `fill`, `ink` and `edge` recolour it (a port circle's tip, #111). Showing, it takes
// the one tip slot (`TipState`), so a port tip waiting for the pointer closes (#127).
ToolTip {
    id: tip
    property string tag: ""
    property color fill: theme.colors.text
    property color ink: theme.colors.surface
    property color edge: "transparent"
    onVisibleChanged: {
        if (visible) {
            TipState.owner = "tip:" + tag
            TipState.open = true
        } else if (TipState.owner === "tip:" + tag) {
            TipState.open = false
        }
    }
    delay: 0
    timeout: -1
    margins: 4                      // kept inside the window
    closePolicy: Popup.NoAutoClose
    x: parent ? (parent.width - width) / 2 : 0
    y: parent ? parent.height + 4 : 0
    padding: 4
    leftPadding: 7
    rightPadding: 7
    background: Rectangle {
        objectName: "tip:" + tip.tag
        radius: theme.metrics.radius_inner
        color: tip.fill
        border.color: tip.edge
        border.width: 1
    }
    contentItem: Text {
        text: tip.text
        color: tip.ink
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
    }
}
