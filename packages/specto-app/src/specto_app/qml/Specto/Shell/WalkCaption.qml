import QtQuick
import QtQuick.Controls

// The walk-through's caption card (#174, design: caption card): fixed under the hop,
// never beside it, 620 px wide (less on a narrow window) and as tall as its text up to
// `maxHeight`, past which it scrolls (#183). Long paths: the narration and the note wrap
// (at a '/' where they can), the kind chip is one line with the middle elided and the
// whole label in its tip. A hop: "HOP 2 OF 7", the link
// kind chip, from -> to (identicons and names: a name frames that view and pauses), the
// narration (15/22, names and numbers Semi Bold), the flow meter (in -> out, the out side
// in the chain colour) with Open link and Ask about this, and the muted note line. The
// establishing shot and the summary: the walk's intro or summary over the whole path's
// meter. Preparing: the planned hops as rows that tick off as their captions arrive.
Rectangle {
    id: root
    objectName: "walkCaption"
    readonly property var hop: walkStage.hop
    readonly property bool isHop: hop.link !== undefined
    readonly property bool preparing: walkStage.preparing
    readonly property bool summary: walkStage.atSummary
    readonly property color walkTone: theme.colors[(walkStage.faces[walkStage.target] || {}).chain]
                                      || theme.colors.chain_0
    readonly property int links: walkStage.hopCount
    property real maxHeight: 360
    width: 620
    height: Math.min(col.implicitHeight + 28, maxHeight)
    radius: theme.metrics.radius_card
    color: theme.colors.surface
    border.width: 1
    border.color: theme.colors.border
    signal openLink(string linkId)
    SoftShadow { radius: root.radius }

    component Faint: Text {
        color: theme.colors.text_faint
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
    }
    component Muted: Text {
        color: theme.colors.text_muted
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
    }
    component Action: Text {
        signal activated()
        color: theme.colors.accent
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
        font.underline: actHover.hovered
        HoverHandler { id: actHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: parent.activated() }
    }
    component Ends: Row {
        property string from: ""
        property string to: ""
        spacing: 6
        Repeater {
            model: [parent.from, parent.to]
            delegate: Row {
                required property var modelData
                required property int index
                spacing: 6
                Text {
                    visible: index === 1
                    text: "→"
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: 12
                    anchors.verticalCenter: parent.verticalCenter
                }
                WalkChip { view: modelData; state_: "past"; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    objectName: "walkName:" + modelData
                    text: modelData
                    color: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.underline: nameHover.hovered
                    anchors.verticalCenter: parent.verticalCenter
                    HoverHandler { id: nameHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: walkStage.frameView(modelData) }
                }
            }
        }
    }
    component KindChip: Rectangle {
        id: chip
        property string text: ""
        property real maxWidth: 260
        readonly property string tip: text
        width: Math.min(chipLabel.implicitWidth, maxWidth - 16) + 16
        height: chipLabel.implicitHeight + 6
        radius: height / 2
        color: theme.colors.tip_port_bg
        border.width: 1
        border.color: theme.colors.tip_port_border
        Text {
            id: chipLabel
            objectName: "walkCardChipText"
            anchors.centerIn: parent
            width: Math.min(implicitWidth, chip.maxWidth - 16)
            elide: Text.ElideMiddle
            text: parent.text
            color: theme.colors.tip_port_text
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            font.weight: Font.DemiBold
        }
        HoverHandler { id: chipHover }
        Tip {
            visible: chipHover.hovered && chipLabel.truncated
            text: chip.tip
            tag: "walkCardChip"
        }
    }

    // a new step shows from its top
    readonly property int step: walkStage.step
    onStepChanged: scroll.contentY = 0

    Flickable {
        id: scroll
        objectName: "walkCardScroll"
        x: 18
        y: 14
        width: root.width - 36
        height: root.height - 28
        contentWidth: width
        contentHeight: col.implicitHeight
        clip: true
        interactive: contentHeight > height + 0.5
        boundsBehavior: Flickable.StopAtBounds
        ScrollIndicator.vertical: ScrollIndicator {}

        Column {
            id: col
            width: scroll.width
            spacing: 10

            // the hop row: what this is, the kind chip, from -> to
            Item {
                width: parent.width
                height: 21
                Row {
                    spacing: 8
                    anchors.verticalCenter: parent.verticalCenter
                    Faint {
                        id: hopLabel
                        objectName: "walkHopLabel"
                        text: root.preparing ? "PREPARING THE WALK"
                            : root.isHop ? "HOP " + walkStage.step + " OF " + root.links
                            : root.summary ? "SUMMARY" : "WALK-THROUGH"
                        font.weight: Font.DemiBold
                        font.letterSpacing: 0.66
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    KindChip {
                        objectName: "walkCardChip"
                        text: root.isHop ? (root.hop.kind_label || root.hop.kind || "")
                                         : root.links + " link" + (root.links === 1 ? "" : "s")
                        // what the label and the ends leave of the row
                        maxWidth: Math.max(40, Math.min(260, col.width - hopLabel.implicitWidth
                                                             - ends.implicitWidth - 24))
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Ends {
                    id: ends
                    objectName: "walkCardEnds"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    from: root.isHop ? root.hop.from : "project"
                    to: root.isHop ? root.hop.to : walkStage.target
                }
            }

            // preparing: one row per planned hop, ticked off as its caption arrives
            Column {
                visible: root.preparing
                width: parent.width
                spacing: 8
                Repeater {
                    model: root.preparing ? walkStage.hops : []
                    delegate: Row {
                        required property var modelData
                        required property int index
                        objectName: "walkPrepRow:" + (index + 1)
                        spacing: 10
                        height: 18
                        Muted { text: index + 1; width: 10; anchors.verticalCenter: parent.verticalCenter }
                        Text {
                            width: 120
                            text: modelData.from + " → " + modelData.to
                            color: modelData.ready ? theme.colors.text : theme.colors.text_muted
                            font.family: theme.fonts.ui
                            font.pixelSize: theme.metrics.font_px
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            objectName: "walkPrepMark:" + (index + 1)
                            width: 12
                            text: modelData.ready ? "✓" : "…"
                            color: modelData.ready ? theme.colors.accent : theme.colors.chain_1
                            font.family: theme.fonts.ui
                            font.pixelSize: theme.metrics.font_px
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            visible: modelData.ready
                            width: col.width - 170
                            text: modelData.html
                            textFormat: Text.StyledText
                            elide: Text.ElideRight
                            color: theme.colors.text
                            font.family: theme.fonts.ui
                            font.pixelSize: theme.metrics.font_px
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Rectangle {
                            visible: !modelData.ready
                            width: 220 + (index % 3) * 30
                            height: 8
                            radius: 4
                            color: theme.colors.bg_tint
                            anchors.verticalCenter: parent.verticalCenter
                            SequentialAnimation on opacity {
                                running: root.preparing && !walkStage.reducedMotion
                                loops: Animation.Infinite
                                NumberAnimation { to: 0.45; duration: 600 }
                                NumberAnimation { to: 1; duration: 600 }
                            }
                        }
                    }
                }
            }

            // the narration
            Text {
                objectName: "walkNarration"
                visible: !root.preparing
                width: parent.width
                text: root.isHop ? (root.hop.html || "") : walkStage.captionHtml
                textFormat: Text.StyledText
                wrapMode: Text.Wrap
                lineHeight: 22
                lineHeightMode: Text.FixedHeight
                color: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: 15
            }

            // the flow meter and the actions
            Item {
                visible: !root.preparing
                width: parent.width
                height: 18
                readonly property var m: root.isHop
                    ? ({ inValue: root.hop.inValue, inUnit: root.hop.inUnit,
                         outValue: root.hop.outValue, outUnit: root.hop.outUnit })
                    : walkStage.meter
                Row {
                    objectName: "walkMeter"
                    spacing: 8
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        text: parent.parent.m.inValue || ""
                        visible: text !== ""
                        color: theme.colors.text
                        font.family: theme.fonts.mono
                        font.pixelSize: 13
                        anchors.baseline: unitIn.baseline
                    }
                    Muted { id: unitIn; text: parent.parent.m.inUnit || ""; visible: text !== "" }
                    Text {
                        visible: (parent.parent.m.outValue || "") !== ""
                        text: "→"
                        color: root.walkTone
                        font.family: theme.fonts.ui
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        anchors.baseline: unitIn.baseline
                    }
                    Text {
                        objectName: "walkMeterOut"
                        text: parent.parent.m.outValue || ""
                        visible: text !== ""
                        color: root.walkTone
                        font.family: theme.fonts.mono
                        font.pixelSize: 13
                        anchors.baseline: unitIn.baseline
                    }
                    Muted { text: parent.parent.m.outUnit || ""; visible: text !== "" }
                }
                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    Action {
                        objectName: "walkOpenLink"
                        visible: root.isHop
                        text: "Open link"
                        onActivated: { walkStage.pause(); root.openLink(root.hop.link) }
                    }
                    Faint { visible: root.isHop; text: "·" }
                    Action {
                        objectName: "walkAsk"
                        text: root.summary ? "Ask a follow-up" : "Ask about this"
                        onActivated: { walkStage.pause(); promptBar.prefill(walkStage.askText) }
                    }
                }
            }

            // the note line (optional), the summary's last word, preparing's footnote
            Muted {
                objectName: "walkNote"
                width: parent.width
                visible: text !== ""
                wrapMode: Text.Wrap
                lineHeight: 16
                lineHeightMode: Text.FixedHeight
                text: root.preparing
                    ? "One pass over the canvas document, the link summaries and the resolved counts; "
                      + "every caption is written before the walk starts. Esc cancels."
                    : root.isHop ? (root.hop.noteText || "")
                    : root.summary ? "Replay plays the same script again. Done or Esc puts the camera back "
                                     + "where you were; nothing on the canvas changed."
                    : ""
            }
        }
    }
}
