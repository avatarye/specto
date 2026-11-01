import QtQuick
import QtQuick.Controls

// Developer details behind one status-bar button (style B keeps them off the chrome):
// the instance (#187: its name, project and endpoint, with Copy), the fleet recipients
// the agent may message (#204: a list of fleet paths with add and remove), worker pool,
// ticks, the window-frame guide.
Popup {
    id: root
    objectName: "detailsPopover"
    parent: Overlay.overlay
    modal: false
    dim: false
    padding: theme.metrics.pad
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    width: Math.min(480, Overlay.overlay.width - 2 * theme.metrics.pad)
    property var status: ({})
    readonly property real valueWidth: width - 2 * padding - 90
    property real anchorRight: 0
    property real anchorTop: 0
    // bound, not assigned: the height is only known once the content is laid out
    x: Math.max(4, Math.min(anchorRight - width, Overlay.overlay.width - width - 4))
    y: Math.max(4, anchorTop - height - 6)

    // above the status bar's info button, its only opener (#218)
    function openAbove(item) {
        root.status = app.status()
        var p = item.mapToItem(Overlay.overlay, 0, 0)
        root.anchorRight = p.x + item.width
        root.anchorTop = p.y
        root.open()
    }

    component Key: Text {
        color: theme.colors.text_faint
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
        width: 76
    }
    component Value: Text {
        color: theme.colors.text
        font.family: theme.fonts.mono
        font.pixelSize: theme.metrics.font_px_small
        width: root.valueWidth
        elide: Text.ElideMiddle
    }

    background: Rectangle {
        radius: theme.metrics.radius_card
        color: theme.colors.surface
        border.color: theme.colors.border_strong
        SoftShadow { radius: parent.radius }
    }
    contentItem: Column {
        spacing: 8
        Text {
            text: "Details"
            color: theme.colors.text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px
            font.bold: true
        }
        Grid {
            columns: 2
            columnSpacing: 10
            rowSpacing: 5
            Key { text: "instance" }
            Value {
                objectName: "detailsInstance"
                text: app.instanceName
                color: theme.colors.agent
                font.bold: true
            }
            Key { text: "recipients" }
            // #204: the allowed-recipients setting (prompt/bar.py): the nimitz paths the
            // agent's fleet_send may message, with add and remove; none means every fleet
            // nimitz reports. A path that is no fleet path (or //...) is refused here.
            Column {
                id: recipients
                objectName: "detailsRecipientsEditor"
                width: root.valueWidth
                spacing: 4
                readonly property var bar: typeof promptBar !== "undefined" ? promptBar : null
                readonly property var paths: bar ? bar.fleetRecipients : []
                property bool refused: false
                function add(text) {
                    var path = String(text || "").trim()
                    if (!path || !bar) return false
                    var next = paths.slice()
                    if (next.indexOf(path) < 0) next.push(path)
                    var ok = bar.setFleetRecipients(next)
                    refused = !ok
                    if (ok) recipientInput.text = ""
                    return ok
                }
                function remove(index) {
                    if (!bar) return false
                    var next = paths.slice()
                    next.splice(index, 1)
                    refused = false
                    return bar.setFleetRecipients(next)
                }
                Text {
                    objectName: "detailsRecipients"
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: recipients.paths.length
                          ? recipients.paths.length + " allowed (fleet_send reaches these alone)"
                          : "every fleet nimitz reports (add a path to narrow it)"
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                }
                Repeater {
                    model: recipients.paths
                    Row {
                        spacing: 8
                        Text {
                            objectName: "recipientRow:" + index
                            text: modelData
                            color: theme.colors.text
                            font.family: theme.fonts.mono
                            font.pixelSize: theme.metrics.font_px_small
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        ChromeTextButton {
                            objectName: "recipientRemove:" + index
                            text: "remove"
                            tip: "stop fleet_send from messaging " + modelData
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: recipients.remove(index)
                        }
                    }
                }
                Row {
                    spacing: 6
                    TextField {
                        id: recipientInput
                        objectName: "recipientInput"
                        width: recipients.width - recipientAdd.width - 6
                        height: 24
                        placeholderText: "FLEET, FLEET/SQUAD, FLEET/SQUAD/SHIP or FLEET/..."
                        font.family: theme.fonts.mono
                        font.pixelSize: theme.metrics.font_px_small
                        color: theme.colors.text
                        placeholderTextColor: theme.colors.text_faint
                        leftPadding: 6
                        rightPadding: 6
                        background: Rectangle {
                            radius: theme.metrics.radius_inner
                            color: theme.colors.surface_alt
                            border.color: recipientInput.activeFocus ? theme.colors.accent : theme.colors.border
                        }
                        onAccepted: recipients.add(text)
                        onTextEdited: recipients.refused = false
                    }
                    ChromeTextButton {
                        id: recipientAdd
                        objectName: "recipientAdd"
                        text: "Add"
                        tip: "allow fleet_send to message this fleet path"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: recipients.add(recipientInput.text)
                    }
                }
                Text {
                    objectName: "recipientRefused"
                    visible: recipients.refused
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "not a fleet path: F, F/SQ, F/SQ/SHIP or F/SQ/... (never //...)"
                    color: theme.colors.error
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                }
            }
            Key { text: "project" }
            Value { text: app.projectRoot() }
            Key { text: "endpoint" }
            Value { objectName: "statusEndpoint"; text: app.endpointAddress || "none" }
            Key { text: "workers" }
            Value { text: (root.status.workers || 0) + " process(es), " + (root.status.jobs_active || 0) + " job(s) active"; font.family: theme.fonts.ui }
            Key { text: "ticks" }
            Value { text: (root.status.ticks || 0) + (root.status.web ? "  web views on" : "  web views off"); font.family: theme.fonts.ui }
            Key { text: "window frame" }
            Value { text: Math.round(controller.windowFrame.w) + "x" + Math.round(controller.windowFrame.h) + " @ " + Math.round(controller.windowFrame.x) + "," + Math.round(controller.windowFrame.y); font.family: theme.fonts.ui }
        }
        ChromeTextButton {
            objectName: "detailsCopy"
            text: "Copy"
            tip: "copy the instance name, project and endpoint"
            onClicked: app.copyText("instance: " + app.instanceName + "\nproject: "
                                    + app.projectRoot() + "\nendpoint: "
                                    + (app.endpointAddress || "none"))
        }
        Text {
            width: root.width - 2 * root.padding
            wrapMode: Text.Wrap
            text: "An agent reaches this canvas over MCP or the specto CLI through .specto/endpoint.json; "
                  + "a fleet agent messages its agent with `specto message " + app.instanceName + " <text>`."
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
        }
    }
}
