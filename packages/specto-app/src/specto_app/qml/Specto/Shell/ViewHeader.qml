import QtQuick

// A view's header line, the same for every view type (#126, design 05 section 2): the
// auto-update switch (the eye) at the left end (#158), the title (the name bold, the
// agent's label muted, `<id>: <label>`, #80), the health dot only when the view is not
// ok, Approve while a Python link waits (#43), then the controls in a fixed order on the
// right: refresh · lock · float · collapse · maximise · close, each a 22 px round
// ChromeButton. View controls are always visible (the user's decision of 2026-10-24,
// #158): a control that is on wears the accent fill (auto-update on, locked, floating,
// collapsed, maximised). Refresh is for a view whose auto-update is off; its slot stays
// reserved, empty, while auto-update is on, so nothing moves. A view type without a
// control has no slot: auto-update and refresh only on a view an intaker feeds
// (`info.autoUpdate` a bool). Each click is the controller's (one user submit each,
// refresh a rescan).
Item {
    id: root
    property string viewId: ""
    property var info: ({})
    property bool collapsed: false
    property bool maximized: false
    // the extra input circles on a collapsed title bar (#114): the title starts after them
    property real titleInset: 0
    readonly property bool locked: info.locked === true
    readonly property bool floating: info.floating === true
    readonly property string health: info.health || "ok"
    readonly property bool fedByIntaker: info.autoUpdate === true || info.autoUpdate === false
    readonly property bool autoUpdate: info.autoUpdate === true
    // the contextual view (SPEC-32): the preview on a selection says so after its title
    readonly property bool contextual: info.contextual === true

    // auto-update (#102): the eye says "this view follows its files"; one undo step. It
    // leads the header (#158); on a collapsed bar it starts after the extra input circles
    ChromeButton {
        id: eye
        objectName: "autoUpdate:" + root.viewId
        readonly property bool checked: root.autoUpdate
        visible: root.fedByIntaker
        anchors.left: parent.left
        anchors.leftMargin: theme.metrics.pad + root.titleInset
        anchors.verticalCenter: parent.verticalCenter
        name: "eye"
        on: root.autoUpdate
        tip: on ? "auto-update on · click to stop" : "auto-update off · click to follow new files"
        onClicked: controller.toggleAutoUpdate(root.viewId)
    }

    Item {
        // the title row is clipped before the buttons on narrow cards; on a collapsed bar
        // it starts after the extra input circles (#114) and the eye
        anchors.left: parent.left
        anchors.right: buttons.left
        anchors.leftMargin: theme.metrics.pad + root.titleInset
                            + (eye.visible ? eye.width + theme.metrics.pad_tight : 0)
        anchors.rightMargin: theme.metrics.pad_tight
        height: parent.height
        clip: true
        Row {
            id: titleRow
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            spacing: 6
            // the name is the handle the user says to the agent; the label its few-word
            // summary, quieter, cut short first (#80: nothing else belongs here)
            Item {
                id: title
                objectName: "title:" + root.viewId
                readonly property string label: root.info.label && root.info.label !== root.viewId
                                                ? root.info.label : ""
                readonly property string text: root.viewId + (label ? ": " + label : "")
                width: Math.min(nameText.implicitWidth + labelText.implicitWidth,
                                titleRow.width - (dot.visible ? dot.width + titleRow.spacing : 0)
                                - (approve.visible ? approve.width + titleRow.spacing : 0)
                                - (contextualTag.visible ? contextualTag.width + follows.width
                                                           + 2 * titleRow.spacing : 0))
                height: nameText.implicitHeight
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    id: nameText
                    text: root.viewId
                    color: theme.colors.text
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                    font.bold: true
                }
                Text {
                    id: labelText
                    objectName: "label:" + root.viewId
                    x: nameText.implicitWidth
                    width: Math.max(0, title.width - x)
                    text: title.label ? ": " + title.label : ""
                    elide: Text.ElideRight
                    color: theme.colors.text_muted
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                }
            }
            // the contextual tag (SPEC-32): "Contextual" and "follows selection", so the
            // preview on the picks is never taken for a view dedicated to one file
            Pill {
                id: contextualTag
                objectName: "contextual:" + root.viewId
                visible: root.contextual
                anchors.verticalCenter: parent.verticalCenter
                text: "Contextual"
                tone: "accent"
                tip: "this view follows the selection; double-click a file for a view of its own"
            }
            Text {
                id: follows
                objectName: "follows:" + root.viewId
                visible: root.contextual
                anchors.verticalCenter: parent.verticalCenter
                text: "follows selection"
                color: theme.colors.text_muted
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
            }
            // health: a dot after the title only when not ok (a pause glyph while paused);
            // its message is the status bar's while the pointer is on the view (#80). A
            // collapsed view, paused by its collapse, shows none (#114)
            Item {
                id: dot
                objectName: "health:" + root.viewId
                visible: root.health !== "ok" && !(root.collapsed && root.health === "paused")
                width: 14
                height: 14
                anchors.verticalCenter: parent.verticalCenter
                Icon {
                    visible: root.health === "paused"
                    name: "pause"
                    size: 11
                    color: theme.colors.paused
                    anchors.centerIn: parent
                }
                Rectangle {
                    visible: root.health !== "paused"
                    width: 7; height: 7; radius: 4
                    color: theme.colors[root.health] || theme.colors.text_faint
                    anchors.centerIn: parent
                }
            }
            // a Python link into this view awaits the user's approval (#43, ADR 0006)
            Pill {
                id: approve
                objectName: "approve:" + root.viewId
                visible: !!root.info.approval
                anchors.verticalCenter: parent.verticalCenter
                text: "Approve"
                tone: "approval"
                filled: true
                tip: "run " + (root.info.approval || "") + " as it is now; a changed file asks again"
                onClicked: controller.approveView(root.viewId)
            }
        }
    }

    Row {
        id: buttons
        anchors.right: parent.right
        anchors.rightMargin: theme.metrics.pad
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        // refresh: rescan once, only while auto-update is off; faint while a rescan runs
        ChromeButton {
            objectName: "refresh:" + root.viewId
            visible: root.fedByIntaker
            name: "refresh"
            disabled: root.health === "loading"
            opacity: root.autoUpdate ? 0 : 1
            enabled: !root.autoUpdate
            tip: disabled ? "rescanning" : "refresh · rescan now"
            onClicked: controller.refreshView(root.viewId)
        }
        // the lock is the user's (story 74): it binds the agent; one undo step
        ChromeButton {
            objectName: "lock:" + root.viewId
            name: root.locked ? "lock" : "unlock"
            on: root.locked
            tip: on ? "locked · the agent asks first" : "lock against the agent"
            onClicked: controller.toggleLock(root.viewId)
        }
        // the float layer (#112): fixed in the window, or dropped back on the canvas here
        ChromeButton {
            objectName: "float:" + root.viewId
            name: "float"
            on: root.floating
            tip: on ? "floating · click to drop here" : "float above the canvas"
            onClicked: controller.toggleFloat(root.viewId)
        }
        // collapse to the title bar (a bar, arrow up), expand back (arrow down) (#114)
        ChromeButton {
            objectName: "collapse:" + root.viewId
            name: root.collapsed ? "expand" : "collapse"
            on: root.collapsed
            tip: on ? "expand" : "collapse to the title bar"
            onClicked: controller.toggleCollapse(root.viewId)
        }
        // maximise: corners out; maximised: corners in (navigation, no submit)
        ChromeButton {
            objectName: "maximize:" + root.viewId
            name: root.maximized ? "restore" : "maximize"
            on: root.maximized
            tip: on ? "restore (Esc)" : "maximise (double-click the header)"
            onClicked: controller.maximize(root.viewId)
        }
        ChromeButton {
            objectName: "close:" + root.viewId
            name: "close"
            danger: true
            tip: "close (undoable)"
            onClicked: controller.removeView(root.viewId)
        }
    }
}
