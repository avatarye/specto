import QtQuick

// The instance name (#187) first, in the agent colour: the address fleet agents message
// this window's agent by, beside the agent it addresses. Undo position, view count and
// what is resolving: quiet text; then the hints of the
// hovered view, else the selected one (empty state, health, gestures:
// `controller.statusHint`, #80). Developer data (endpoint, workers) sits behind the
// details button on the right (DetailsPopover); the file tree's details toggle lives in
// each tree's footer (#122). The bar is its own surface under a divider, apart from the
// canvas (#120). Its text starts clear of the agent icon, which floats over the bar's
// left end (#162, reading A: 76 px in, text-muted; #170: the icon's 12 px gap, its
// 52 px, then 12 px).
Item {
    id: root
    implicitHeight: 26
    height: implicitHeight
    signal detailsRequested(var anchorItem)

    Rectangle {
        objectName: "statusSurface"
        anchors.fill: parent
        color: theme.colors.bg_tint
    }
    Rectangle {
        objectName: "statusDivider"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: theme.colors.border_strong
    }

    Row {
        id: left
        anchors.left: parent.left
        anchors.leftMargin: 76
        anchors.verticalCenter: parent.verticalCenter
        spacing: 16
        Text {
            objectName: "statusInstance"
            visible: text !== ""
            text: app.instanceName
            color: theme.colors.agent
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            font.bold: true
            HoverHandler { id: instanceHover }
            Tip {
                visible: instanceHover.hovered
                text: "this Specto's instance name: fleet agents reach its agent with "
                      + "`specto message " + app.instanceName + " <text>`"
            }
        }
        Text {
            id: undoText
            objectName: "statusUndo"
            text: "undo " + controller.undoCursor + "/" + controller.undoDepth
                  + (controller.lastTxnLabel ? "  last: " + controller.lastTxnLabel : "")
            color: theme.colors.text_muted
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
        }
        Text {
            objectName: "statusViews"
            text: controller.viewCount + " view" + (controller.viewCount === 1 ? "" : "s")
                  + (controller.pausedCount ? "  ·  " + controller.pausedCount + " paused" : "")
            color: theme.colors.text_muted
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
        }
        // the links resolving into views right now (was the top bar's, #120)
        Text {
            objectName: "resolvingCount"
            visible: controller.resolvingCount > 0
            text: controller.resolvingCount + " resolving"
            color: theme.colors.loading
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
        }
    }
    Text {
        objectName: "statusHint"
        anchors.left: left.right
        anchors.leftMargin: 24
        anchors.right: detailsButton.left
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        // a walk-through (#174) names itself and its keys
        text: walkStage.active ? walkStage.statusLine : controller.statusHint
        elide: Text.ElideRight
        color: theme.colors.text_muted
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
    }
    IconButton {
        id: detailsButton
        objectName: "detailsButton"
        anchors.right: parent.right
        anchors.rightMargin: theme.metrics.pad_tight
        anchors.verticalCenter: parent.verticalCenter
        name: "info"
        size: 22
        iconSize: 14
        tip: "details: endpoint, workers"
        onClicked: root.detailsRequested(detailsButton)
    }
}
