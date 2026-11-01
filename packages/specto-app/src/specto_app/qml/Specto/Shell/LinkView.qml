import QtQuick
import QtQuick.Controls

// The link view (glossary): what a link node opens (#121). The link's summary, kind,
// ends, parameters as JSON, Python reference and code when it has one, and what the
// runtime made of it, all read-only; Delete (one user submit) and Ask agent to change
// (pre-fills the prompt bar) are the two things the user does to a link.
Popup {
    id: root
    property string linkId: ""
    property var info: ({})
    parent: Overlay.overlay
    modal: true
    dim: true
    focus: true
    padding: theme.metrics.pad
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    width: Math.min(680, Overlay.overlay.width - 4 * theme.metrics.pad)
    height: Math.min(col.implicitHeight + 2 * padding, Overlay.overlay.height - 4 * theme.metrics.pad)
    x: (Overlay.overlay.width - width) / 2
    y: (Overlay.overlay.height - height) / 2
    readonly property color tone: theme.colors[info.chain] || theme.colors.chain_none
    readonly property real innerWidth: width - 2 * padding

    function openFor(id) {
        root.linkId = id
        root.info = overworld.linkDetails(id)
        root.open()
    }

    Overlay.modal: Rectangle { color: theme.colors.scrim }
    background: Rectangle {
        radius: theme.metrics.radius_card
        color: theme.colors.surface
        border.color: root.tone
        border.width: 1.5
        SoftShadow { radius: parent.radius }
    }

    component Key: Text {
        color: theme.colors.text_faint
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
        width: 80
    }
    component Value: Text {
        color: theme.colors.text
        font.family: theme.fonts.mono
        font.pixelSize: theme.metrics.font_px_small
        width: root.innerWidth - 90
        wrapMode: Text.Wrap
    }
    component Code: Rectangle {
        property alias text: codeText.text
        width: root.innerWidth
        height: Math.min(codeText.implicitHeight + 16, 260)
        radius: theme.metrics.radius_inner
        color: theme.colors.mono_bg
        border.color: theme.colors.border
        clip: true
        Flickable {
            anchors.fill: parent
            anchors.margins: 8
            contentHeight: codeText.implicitHeight
            Text {
                id: codeText
                width: parent.width
                wrapMode: Text.WrapAnywhere
                color: theme.colors.text
                font.family: theme.fonts.mono
                font.pixelSize: theme.metrics.font_px_small
                textFormat: Text.PlainText
            }
        }
    }

    contentItem: Flickable {
        objectName: "linkView"
        clip: true
        contentHeight: col.implicitHeight
        Column {
            id: col
            width: root.innerWidth
            spacing: 10
            Row {
                width: parent.width
                spacing: 8
                Rectangle { width: 10; height: 10; radius: 5; color: root.tone; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Link " + root.linkId + (root.info.label ? " · " + root.info.label : "")
                    color: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_title
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
                Pill { text: root.info.kind || ""; implicitHeight: 20; anchors.verticalCenter: parent.verticalCenter }
            }
            Text {
                objectName: "linkSentence"
                width: parent.width
                wrapMode: Text.Wrap
                text: root.info.sentence || ""
                color: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
            }
            Grid {
                columns: 2
                columnSpacing: 10
                rowSpacing: 5
                Key { text: "source" }
                Value { text: root.info.source || "" }
                Key { text: "target" }
                Value { text: root.info.target || "" }
                Key { text: "direction" }
                Value { text: root.info.twoWay ? "two-way" : "one-way"; font.family: theme.fonts.ui }
                Key { text: "status" }
                Value {
                    text: (root.info.status || "unknown") + (root.info.statusMessage ? "  " + root.info.statusMessage : "")
                    font.family: theme.fonts.ui
                    color: root.info.status === "failed" ? theme.colors.error : theme.colors.text
                }
                Key { visible: !!root.info.python; text: "python" }
                Value { visible: !!root.info.python; text: root.info.python || "" }
                Key { visible: !!root.info.locked; text: "lock" }
                Value { visible: !!root.info.locked; text: "locked: the agent must ask before changing it"; font.family: theme.fonts.ui }
            }
            Text { text: "parameters"; color: theme.colors.text_faint; font.family: theme.fonts.ui; font.pixelSize: theme.metrics.font_px_small }
            Code { text: root.info.params || "{}" }
            Text { visible: !!root.info.code; text: "code"; color: theme.colors.text_faint; font.family: theme.fonts.ui; font.pixelSize: theme.metrics.font_px_small }
            Code { visible: !!root.info.code; text: root.info.code || "" }
            Row {
                spacing: 8
                Pill {
                    objectName: "linkDelete"
                    text: "Delete"
                    icon: "unlink"
                    tone: "removed"
                    enabled: !root.info.locked
                    tip: root.info.locked ? "this link is locked" : "delete the link (undoable)"
                    onClicked: { root.close(); overworld.deleteLink(root.linkId) }
                }
                Pill {
                    objectName: "linkAskAgent"
                    text: "Ask agent to change"
                    icon: "agent"
                    tone: "agent"
                    onClicked: { root.close(); overworld.askAgent(root.linkId) }
                }
                Pill { objectName: "linkClose"; text: "Close"; hint: "Esc"; onClicked: root.close() }
            }
        }
    }
}
