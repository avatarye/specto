import QtQuick

// An agent reply in the thread (#162): its markdown blocks (`thread.py` split_markdown)
// one under another. Prose renders through Qt's markdown (lists, bold, inline code,
// links); a fenced code block is mono on a tinted card with its language and Copy. All
// of it is selectable and copies with Ctrl+C.
Column {
    id: root
    property var blocks: []
    property color ink: theme.colors.text
    spacing: 8

    Repeater {
        model: root.blocks
        delegate: Item {
            id: block
            required property var modelData
            required property int index
            width: root.width
            height: modelData.kind === "code" ? code.height : prose.height

            TextEdit {
                id: prose
                objectName: visible ? "replyMarkdown" : ""
                visible: block.modelData.kind !== "code"
                width: parent.width
                height: visible ? implicitHeight : 0
                text: visible ? block.modelData.text : ""
                textFormat: TextEdit.MarkdownText
                readOnly: true
                selectByMouse: true
                persistentSelection: false
                wrapMode: TextEdit.Wrap
                color: root.ink
                selectionColor: theme.colors.accent_soft
                selectedTextColor: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
                onLinkActivated: function (link) { Qt.openUrlExternally(link) }
                HoverHandler {
                    enabled: prose.hoveredLink !== ""
                    cursorShape: Qt.PointingHandCursor
                }
            }

            Rectangle {
                id: code
                objectName: visible ? "replyCode" : ""
                visible: block.modelData.kind === "code"
                width: parent.width
                height: visible ? codeColumn.implicitHeight + 16 : 0
                radius: theme.metrics.radius_inner
                color: theme.colors.mono_bg
                border.width: 1
                border.color: theme.colors.border
                Column {
                    id: codeColumn
                    x: 10
                    y: 6
                    width: parent.width - 20
                    spacing: 2
                    Item {
                        width: parent.width
                        height: 22
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: block.modelData.lang || ""
                            color: theme.colors.text_faint
                            font.family: theme.fonts.ui
                            font.pixelSize: theme.metrics.font_px_small
                        }
                        Pill {
                            id: copyButton
                            objectName: code.visible ? "replyCodeCopy" : ""
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            implicitHeight: 20
                            text: copied.running ? "Copied" : "Copy"
                            tip: "copy the code"
                            onClicked: { promptBar.copyText(block.modelData.text); copied.restart() }
                            Timer { id: copied; interval: 1200 }
                        }
                    }
                    TextEdit {
                        objectName: code.visible ? "replyCodeText" : ""
                        width: parent.width
                        text: code.visible ? block.modelData.text : ""
                        textFormat: TextEdit.PlainText
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.WrapAnywhere
                        color: theme.colors.text
                        selectionColor: theme.colors.accent_soft
                        selectedTextColor: theme.colors.text
                        font.family: theme.fonts.mono
                        font.pixelSize: theme.metrics.font_px_small + 1
                    }
                }
            }
        }
    }
}
