import QtQuick

// One row of the agent thread (#162), by kind:
//   user     the request, a bubble on the right (markdown)
//   peer     a request from a fleet agent (#187), on the left: "from <sender>", the text,
//            and Run / Dismiss while it waits for the user (Peer requests: ask);
//            "queued" or "dismissed" after; "refused: Peer requests is off" (#204: the
//            first line only, in the error tone). Its turn row names the sender too.
//   approval a link.approve request from the endpoint (#203), on the left: "<requester>
//            asks you to approve", the file and link, and Approve / Open / Dismiss while
//            it waits for the user; "approved", "dismissed" or "changed since asked"
//            after. Only the user's Approve here (or in the view's header) approves.
//   agent    the reply (ReplyBlocks); "thinking..." until the first words stream in;
//            a failed turn in the error tone
//   turn     the turn's submit label "t<n> . label", the views and links it touched,
//            Undo (only while it is the newest step; "undone" after), diff on request
//   skill    the project skills the turn saved or deleted (#161): Open, Undo
//   code     the link code files the turn wrote (#168), "needs your approval": Open
//            (the code), Undo; the skill row's look
//   fleet    one message the agent sent to a nimitz fleet member (#187): "to FLEET/SQUAD",
//            the message; a refused one says why in the error tone
//   launch   a program the agent launched (#186), "launched Blender 4.2 on a.blend",
//            or was refused (#192, status "failed", in the error tone, with the reason);
//            the skill row's look, nothing to open or undo
//   scratch  the scratch pad changes the turn made (#184): Undo (while they are the pad's
//            newest step); the skill row's look, no Open
//   divider  a session began
// The latest turn's turn, skill, code and scratch rows (no request after them) carry the
// names the flows and tests drive (replyUndo, replyDiff, replySkill, replySkillOpen,
// replySkillUndo, replyCode, replyCodeOpen, replyCodeUndo, replyScratch, replyScratchUndo,
// replyFleet, replyLaunch, replyLaunchNote).
Item {
    id: root
    required property int index
    required property int entryId
    required property string kind
    required property string text
    required property var blocks
    required property string status
    required property string label
    required property string counts
    required property string diff
    required property int number
    required property string dividerText
    required property bool canUndo
    required property bool undone
    required property string path
    required property bool latest
    required property string sender
    required property bool verified
    required property bool canRun
    signal skillOpen(Item anchor, int entryId)

    property bool showDiff: false
    readonly property real gap: 10
    implicitHeight: content.implicitHeight + gap
    height: implicitHeight

    Item {
        id: content
        width: parent.width
        implicitHeight: {
            switch (root.kind) {
            case "user": return bubble.height
            case "peer": return peerCard.height
            case "approval": return approvalCard.height
            case "agent": return reply.height
            case "turn": return turnRow.height + (diffText.visible ? diffText.height + 6 : 0)
            case "skill": return skillRow.height
            case "command": return skillRow.height
            case "data": return skillRow.height
            case "code": return skillRow.height
            case "fleet": return fleetRow.height
            case "launch": return skillRow.height
            case "scratch": return skillRow.height
            default: return divider.height
            }
        }

        // -- user ----------------------------------------------------------------------------
        Rectangle {
            id: bubble
            visible: root.kind === "user"
            anchors.right: parent.right
            width: userText.width + 24
            height: visible ? userText.height + 16 : 0
            radius: 12
            color: theme.colors.bg_tint
            // the request's unwrapped width: a short one hugs, a long one wraps at 82 %
            Text {
                id: measure
                visible: false
                text: userText.text
                textFormat: Text.MarkdownText
                font: userText.font
            }
            TextEdit {
                id: userText
                objectName: bubble.visible ? "userText" : ""
                x: 12
                y: 8
                width: Math.max(8, Math.min(root.width * 0.82 - 24, measure.implicitWidth + 2))
                text: bubble.visible ? root.text : ""
                textFormat: TextEdit.MarkdownText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                color: theme.colors.text
                selectionColor: theme.colors.accent_soft
                selectedTextColor: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
            }
        }

        // -- peer (#187) ---------------------------------------------------------------------
        Rectangle {
            id: peerCard
            objectName: visible ? "peerRow:" + root.entryId : ""
            visible: root.kind === "peer"
            width: Math.min(parent.width, Math.max(peerHead.implicitWidth, peerText.width) + 24)
            height: visible ? peerCol.height + 16 : 0
            radius: 12
            color: theme.colors.surface
            border.width: 1
            border.color: theme.colors.agent_soft
            Column {
                id: peerCol
                x: 12
                y: 8
                spacing: 4
                Row {
                    id: peerHead
                    spacing: 8
                    Text {
                        objectName: peerCard.visible ? "peerFrom:" + root.entryId : ""
                        // #190: verified from the caller's nimitz labels, else only claimed
                        text: root.verified ? "from " + root.sender
                            : "says it is \"" + root.sender + "\", unverified"
                        color: theme.colors.agent
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                        font.bold: true
                    }
                    Text {
                        // #204: a request turned away (Peer requests: off) stays as a
                        // refused row, in the error tone, with why
                        readonly property bool refused: root.status === "refused"
                        objectName: peerCard.visible && refused ? "peerRefused" : ""
                        visible: root.status === "queued" || root.status === "dismissed" || refused
                        text: refused && root.label ? root.status + ": " + root.label : root.status
                        color: refused ? theme.colors.error : theme.colors.text_faint
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                    }
                }
                Text {
                    id: peerMeasure
                    visible: false
                    text: peerText.text
                    textFormat: Text.MarkdownText
                    font: peerText.font
                }
                TextEdit {
                    id: peerText
                    objectName: peerCard.visible ? "peerText:" + root.entryId : ""
                    width: Math.max(8, Math.min(root.width * 0.82 - 24, peerMeasure.implicitWidth + 2))
                    text: peerCard.visible ? root.text : ""
                    textFormat: TextEdit.MarkdownText
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    color: root.status === "dismissed" || root.status === "refused" ? theme.colors.text_muted : theme.colors.text
                    selectionColor: theme.colors.accent_soft
                    selectedTextColor: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                }
                Row {
                    visible: root.canRun
                    height: visible ? 24 : 0
                    spacing: 6
                    Pill {
                        objectName: root.canRun ? "peerRun" : ""
                        implicitHeight: 20
                        text: "Run"
                        tone: "agent"
                        tip: "let the agent work on this request (a turn you can undo)"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: promptBar.runPeer(root.entryId)
                    }
                    Pill {
                        objectName: root.canRun ? "peerDismiss" : ""
                        implicitHeight: 20
                        text: "Dismiss"
                        tone: "neutral"
                        tip: "leave it: the agent does not act on it"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: promptBar.dismissPeer(root.entryId)
                    }
                }
            }
        }

        // -- approval (#203) -----------------------------------------------------------------
        Rectangle {
            id: approvalCard
            objectName: visible ? "approvalRow:" + root.entryId : ""
            visible: root.kind === "approval"
            width: Math.min(parent.width, Math.max(approvalHead.implicitWidth, approvalText.implicitWidth) + 24)
            height: visible ? approvalCol.height + 16 : 0
            radius: 12
            color: theme.colors.surface
            border.width: 1
            border.color: root.canRun ? theme.colors.approval : theme.colors.approval_soft
            Column {
                id: approvalCol
                x: 12
                y: 8
                spacing: 4
                Row {
                    id: approvalHead
                    spacing: 8
                    Text {
                        objectName: approvalCard.visible ? "approvalFrom:" + root.entryId : ""
                        // the requester, verified from the caller's nimitz labels or only claimed
                        text: (root.verified ? root.sender : "\"" + root.sender + "\" (unverified)")
                              + " asks you to approve"
                        color: theme.colors.approval
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                        font.bold: true
                    }
                    Text {
                        objectName: approvalCard.visible ? "approvalStatus:" + root.entryId : ""
                        visible: !root.canRun
                        text: root.status === "changed" ? "changed since asked" : root.status
                        color: theme.colors.text_faint
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                    }
                }
                Text {
                    id: approvalText
                    objectName: approvalCard.visible ? "approvalText:" + root.entryId : ""
                    width: Math.max(8, Math.min(root.width * 0.82 - 24, implicitWidth))
                    text: approvalCard.visible ? root.text : ""
                    wrapMode: Text.WrapAnywhere
                    color: root.canRun ? theme.colors.text : theme.colors.text_muted
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px_small
                }
                Row {
                    visible: root.canRun
                    height: visible ? 24 : 0
                    spacing: 6
                    Pill {
                        objectName: root.canRun ? "approvalApprove" : ""
                        implicitHeight: 20
                        text: "Approve"
                        tone: "approval"
                        tip: "run this file as it is now; a changed file asks again"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: promptBar.approveLink(root.entryId)
                    }
                    Pill {
                        id: approvalOpen
                        objectName: root.canRun ? "approvalOpen" : ""
                        implicitHeight: 20
                        text: "Open"
                        tip: "the file as it is now"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: root.skillOpen(approvalOpen, root.entryId)
                    }
                    Pill {
                        objectName: root.canRun ? "approvalDismiss" : ""
                        implicitHeight: 20
                        text: "Dismiss"
                        tone: "neutral"
                        tip: "leave it: the code does not run"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: promptBar.dismissApproval(root.entryId)
                    }
                }
            }
        }

        // -- agent ---------------------------------------------------------------------------
        Item {
            id: reply
            visible: root.kind === "agent"
            width: parent.width
            height: visible ? (blocksView.visible ? blocksView.height : waiting.height) : 0
            Text {
                id: waiting
                objectName: visible ? "replyThinking" : ""
                visible: root.status === "working" && root.text === ""
                text: "thinking..."
                color: theme.colors.text_faint
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
                font.italic: true
            }
            ReplyBlocks {
                id: blocksView
                objectName: root.latest || root.status === "working" ? "replyText" : ""
                visible: !waiting.visible
                width: parent.width
                blocks: reply.visible ? root.blocks : []
                ink: root.status === "failed" ? theme.colors.error : theme.colors.text
            }
        }

        // -- turn ----------------------------------------------------------------------------
        Rectangle {
            id: turnRow
            objectName: root.kind === "turn" && root.latest ? "replyTurn" : ""
            visible: root.kind === "turn"
            width: Math.min(parent.width, turnLine.implicitWidth + 20)
            height: visible ? 26 : 0
            radius: 6
            color: theme.colors.agent_soft
            Row {
                id: turnLine
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Rectangle {
                    width: 6
                    height: 6
                    radius: 3
                    color: theme.colors.agent
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: "t" + root.number + " · " + root.label
                    color: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small + 1
                    font.bold: true
                    elide: Text.ElideRight
                    // the label gives way: the counts, Undo and diff always show
                    width: Math.max(48, Math.min(implicitWidth, content.width - 20 - 6
                        - turnCounts.width - (turnUndo.visible ? turnUndo.width : turnUndoText.width)
                        - turnDiff.width - (turnSender.visible ? turnSender.width : 0)
                        - 6 * turnLine.spacing))
                    anchors.verticalCenter: parent.verticalCenter
                }
                // a peer's turn (#187) is labelled with its sender
                Text {
                    id: turnSender
                    objectName: visible ? "turnSender:" + root.entryId : ""
                    visible: root.kind === "turn" && root.sender !== ""
                    text: root.verified ? "from " + root.sender
                        : "from \"" + root.sender + "\" (unverified)"
                    color: theme.colors.agent
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    id: turnCounts
                    visible: root.counts !== ""
                    text: root.counts
                    color: theme.colors.text_muted
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    anchors.verticalCenter: parent.verticalCenter
                }
                Pill {
                    id: turnUndo
                    objectName: root.canUndo && root.latest ? "replyUndo" : "turnUndo:" + root.entryId
                    visible: root.canUndo
                    implicitHeight: 20
                    text: "Undo"
                    tone: "neutral"
                    tip: "revert the agent's whole turn (one step)"
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: promptBar.undoTurnAt(root.entryId)
                }
                Text {
                    id: turnUndoText
                    visible: !root.canUndo
                    text: root.undone ? "undone" : "Undo"
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    id: turnDiff
                    objectName: root.latest ? "replyDiff" : "turnDiff:" + root.entryId
                    visible: root.diff !== ""
                    text: root.showDiff ? "hide diff" : "diff"
                    color: theme.colors.accent
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    anchors.verticalCenter: parent.verticalCenter
                    TapHandler { onTapped: root.showDiff = !root.showDiff }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
            }
        }
        // the detailed diff: one line per change (+ added, - removed, ~ changed), mono
        TextEdit {
            id: diffText
            objectName: root.latest ? "replyDiffText" : ""
            visible: root.kind === "turn" && root.showDiff && root.diff !== ""
            anchors.top: turnRow.bottom
            anchors.topMargin: 6
            x: 16
            width: parent.width - 16
            text: visible ? root.diff : ""
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.WrapAnywhere
            color: theme.colors.text_muted
            selectionColor: theme.colors.accent_soft
            font.family: theme.fonts.mono
            font.pixelSize: theme.metrics.font_px_small
        }

        // -- skill (#161), and code (#168), scratch (#184), command and data (#219) in the
        // same look -----------------------------------------------------------------------------
        Rectangle {
            id: skillRow
            readonly property bool code: root.kind === "code"
            readonly property bool launch: root.kind === "launch"
            readonly property bool scratch: root.kind === "scratch"
            readonly property bool command: root.kind === "command"
            //: (data is every Item's children property: not a name for this flag)
            readonly property bool dataFile: root.kind === "data"
            //: the flows' names: replySkill* for a skill row, replyCode* for a code row,
            //: replyLaunch* for a launch row, replyScratch* for a scratch row, replyCommand*
            //: and replyData* for a command and a data row
            readonly property string tag: code ? "Code" : launch ? "Launch" : scratch ? "Scratch"
                : command ? "Command" : dataFile ? "Data" : "Skill"
            //: the row's undo slot name's stem: skillUndo:<id>, commandUndo:<id>, ...
            readonly property string stem: code ? "code" : scratch ? "scratch"
                : command ? "command" : dataFile ? "data" : "skill"
            objectName: visible && root.latest ? "reply" + tag : ""
            visible: root.kind === "skill" || code || launch || scratch || command || dataFile
            width: Math.min(parent.width, skillLine.implicitWidth + 20)
            height: visible ? 26 : 0
            radius: 6
            color: theme.colors.agent_soft
            Row {
                id: skillLine
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Text {
                    text: theme.glyphs.agent
                    color: theme.colors.agent
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px_small
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    objectName: root.latest ? "reply" + skillRow.tag + "Note" : ""
                    text: root.label
                    color: skillRow.launch && root.status === "failed" ? theme.colors.error
                                                                         : theme.colors.text_muted
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    elide: Text.ElideRight
                    width: Math.max(48, Math.min(implicitWidth, content.width - 20 - 14
                        - (skillOpen.visible ? skillOpen.width : 0) - (skillUndo.visible ? skillUndo.width : 0)
                        - (skillUndone.visible ? skillUndone.width : 0) - 4 * skillLine.spacing))
                    anchors.verticalCenter: parent.verticalCenter
                }
                Pill {
                    id: skillOpen
                    visible: !skillRow.launch && !skillRow.scratch
                    objectName: root.latest ? "reply" + skillRow.tag + "Open"
                        : skillRow.stem + "Open:" + root.entryId
                    implicitHeight: 20
                    text: "Open"
                    tip: skillRow.code ? "the link code the agent wrote"
                        : skillRow.command ? "the command file, rendered"
                        : skillRow.dataFile ? "the data file the agent wrote"
                        : "the skill file, rendered"
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: root.skillOpen(skillOpen, root.entryId)
                }
                Pill {
                    id: skillUndo
                    objectName: root.latest ? "reply" + skillRow.tag + "Undo"
                        : skillRow.stem + "Undo:" + root.entryId
                    visible: root.canUndo
                    implicitHeight: 20
                    icon: "undo"
                    text: "Undo"
                    tone: "agent"
                    tip: skillRow.code ? "put the link code back as it was before this turn"
                        : skillRow.scratch ? "put the scratch pad back as it was before this turn"
                        : skillRow.command ? "put the command back as it was before this turn"
                        : skillRow.dataFile ? "put the data file back as it was before this turn"
                        : "put the skill back as it was before this turn"
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: skillRow.code ? promptBar.undoCodeAt(root.entryId)
                        : skillRow.scratch ? promptBar.undoScratchAt(root.entryId)
                        : skillRow.command ? promptBar.undoCommandsAt(root.entryId)
                        : skillRow.dataFile ? promptBar.undoDataAt(root.entryId)
                        : promptBar.undoSkillsAt(root.entryId)
                }
                Text {
                    id: skillUndone
                    visible: root.undone
                    text: "undone"
                    color: theme.colors.text_faint
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        // -- fleet (#187) -----------------------------------------------------------------------
        Rectangle {
            id: fleetRow
            readonly property bool failed: root.status === "failed"
            objectName: visible && root.latest ? "replyFleet" : ""
            visible: root.kind === "fleet"
            width: Math.min(parent.width, fleetLine.implicitWidth + 20)
            height: visible ? 26 : 0
            radius: 6
            color: theme.colors.agent_soft
            Row {
                id: fleetLine
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Text {
                    text: theme.glyphs.agent
                    color: fleetRow.failed ? theme.colors.error : theme.colors.agent
                    font.family: theme.fonts.mono
                    font.pixelSize: theme.metrics.font_px_small
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    id: fleetTo
                    text: root.label
                    color: fleetRow.failed ? theme.colors.error : theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    font.bold: true
                    elide: Text.ElideRight
                    width: Math.min(implicitWidth, (content.width - 34) * 0.6)
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: root.text
                    color: theme.colors.text_muted
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px_small
                    elide: Text.ElideRight
                    width: Math.max(48, Math.min(implicitWidth,
                        content.width - 20 - 14 - fleetTo.width - 2 * fleetLine.spacing))
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        // -- divider -------------------------------------------------------------------------
        Item {
            id: divider
            objectName: visible ? "threadDivider" : ""
            visible: root.kind === "divider"
            width: parent.width
            height: visible ? 28 : 0
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.right: dividerLabel.left
                anchors.rightMargin: 10
                height: 1
                color: theme.colors.border
            }
            Text {
                id: dividerLabel
                anchors.centerIn: parent
                text: root.dividerText
                color: theme.colors.text_faint
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
            }
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: dividerLabel.right
                anchors.leftMargin: 10
                anchors.right: parent.right
                height: 1
                color: theme.colors.border
            }
        }
    }
}
