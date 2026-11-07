import QtQuick
import QtQuick.Window
import QtQuick.Dialogs

// The Specto shell: one OS window showing the project's one canvas (ADR 0012).
// Style B "Calm Canvas": warm background, white rounded cards, chrome only when needed.
// Everything below reacts to the controller's models, which react to document patches.
// What a human does here is a submit with origin user: close a view, undo, redo; an
// agent composes the rest through submit_canvas, asked in the agent panel (#44, #162).
Window {
    id: win
    width: 1280
    height: 800
    minimumWidth: 640
    minimumHeight: 400
    // a plain window; under the test driver off the offscreen platform one that never
    // takes the focus (#237)
    flags: typeof shellWindowFlags !== "undefined" ? shellWindowFlags : Qt.Window
    visible: true
    color: theme.colors.bg
    // the instance name (#187) is the address fleet agents message this window by
    title: "Specto - " + app.projectName() + (app.instanceName ? " · " + app.instanceName : "")
    // the keys' rule: a text field has the keyboard (a TextInput or TextEdit, or a web
    // view: Monaco), so the canvas's keys stand down. Decided here, not in Python: the
    // focus item inside a web view is Chromium's render delegate, and wrapping that item
    // for Python segfaults (Linux)
    readonly property bool editingText: {
        var item = activeFocusItem
        if (!item) return false
        if (item.cursorPosition !== undefined && item.selectByMouse !== undefined) return true
        for (var p = item; p; p = p.parent)
            if (typeof p.runJavaScript === "function") return true
        return false
    }
    // the index in ``items`` of the one holding the keyboard focus (it or an item inside
    // it), else -1; Python asks with the view hosts' items, never touching the focus item
    function focusedIndexOf(items) {
        for (var p = activeFocusItem; p; p = p.parent) {
            var i = items.indexOf(p)
            if (i >= 0) return i
        }
        return -1
    }

    Shortcut { sequence: "Ctrl+Z"; context: Qt.ApplicationShortcut; onActivated: controller.undo() }
    Shortcut { sequence: "Ctrl+Y"; context: Qt.ApplicationShortcut; onActivated: controller.redo() }
    Shortcut { sequence: "Ctrl+Shift+Z"; context: Qt.ApplicationShortcut; onActivated: controller.redo() }
    Shortcut { sequence: "Ctrl+O"; context: Qt.ApplicationShortcut; onActivated: openDialog.open() }
    Shortcut { sequence: "Ctrl+Q"; context: Qt.ApplicationShortcut; onActivated: app.quit() }
    // Esc returns from a maximised view (story 72); otherwise it drops the focus and the
    // selection. While the wiring layer shows the controller's event filter takes Esc
    // before this shortcut (#82, WiringLayer.qml).
    Shortcut {
        sequence: "Escape"
        onActivated: {
            if (prompt.handleEscape()) return  // the agent panel first (#162)
            if (controller.maximized) controller.maximize("")
            else { controller.focusView(""); controller.clearSelection() }
        }
    }
    // Home is no shortcut: the controller's event filter hands it to the focused view first
    // and pans the canvas home when no view takes it (#113)
    // Tab toggles the overworld (#45)
    Shortcut {
        sequence: "Tab"
        context: Qt.ApplicationShortcut
        onActivated: if (!prompt.handleTab()) overworld.toggle()
    }
    Shortcut { sequence: "Ctrl+K"; context: Qt.ApplicationShortcut; onActivated: prompt.focusInput() }

    // the native folder dialog of the project menu (#217: a folder with a .specto/
    // project opens, any other folder becomes one)
    FolderDialog {
        id: openDialog
        title: "Open a folder as a Specto project"
        onAccepted: app.openProject(selectedFolder.toString(), true)
    }
    Connections {
        target: app
        function onOpenProjectRequested() { openDialog.open() }
    }

    // The hidden holder item: detached host items wait here, invisible, until a slot
    // takes them again (hosts.set_holder).
    Item { id: holder; objectName: "holder"; visible: false; width: 0; height: 0 }

    DetailsPopover { id: details }

    StatusBar {
        id: statusBar
        objectName: "statusBar"
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        onDetailsRequested: function (item) { details.openAbove(item) }
    }

    CanvasArea {
        id: canvasArea
        maximizedTop: canvasControls.y + canvasControls.height + theme.metrics.pad_tight
        // the hint card stays above the notices (#192)
        noticeInset: noticeStack.height > 0 ? noticeStack.height + 2 * theme.metrics.pad : 0
        visible: !overworld.active
        anchors.top: parent.top
        anchors.bottom: statusBar.top
        anchors.left: parent.left
        anchors.right: parent.right
    }
    // the overworld takes the canvas area's place while it shows (#45)
    Overworld {
        id: overworldArea
        visible: overworld.active
        anchors.fill: canvasArea
        // "Ask agent to change" pre-fills the prompt bar (#44) when one is mounted
        onAskAgent: function (text) {
            if (typeof promptBar !== "undefined" && promptBar.prefill) promptBar.prefill(text)
            else overworld.notify("info", "ask the agent: " + text)
        }
    }

    // the wiring layer (#82): every link as a curve with its node, above the dimmed views
    WiringLayer {
        id: wiringLayer
        anchors.fill: canvasArea
        z: 5
    }
    // the port capsules (#160): above the views and the wiring layer's curves; in the
    // layer a capsule opens its link like the dot it stands for
    PortCapsules {
        anchors.fill: canvasArea
        z: 6
        onOpenLink: function (linkId) { wiringLayer.openLink(linkId) }
    }

    // the walk-through (#174): its rings, wires and pulse over the canvas area, in the
    // normal view and the overworld; its caption card and control bar above them
    WalkOverlay {
        anchors.fill: canvasArea
        z: 7
    }
    WalkChrome {
        anchors.fill: canvasArea
        z: 25
    }

    // no top bar (#120): undo, redo and the global menu float over the canvas's
    // top-right corner, in the normal view and the overworld alike
    CanvasControls {
        id: canvasControls
        objectName: "canvasControls"
        anchors.top: canvasArea.top
        anchors.right: canvasArea.right
        anchors.margins: theme.metrics.pad_tight
        z: 11
        onOpenProject: openDialog.open()
    }

    // the one context menu (#154): a view asks `contextMenus` (its bridge, or a web page's
    // `contextmenu`), Python answers the rows for what is under the cursor and this menu
    // opens there; a picked row runs back in Python (`contextMenus.trigger`). The Menu
    // key and Shift+F10 open it for the focused view's selection, keyboard-driven. A row
    // with children (the launchers' "Open with", #186) opens them in `contextSubMenu`
    // beside it: on hover, a click, Enter or Right; Left or Esc closes it again.
    PopMenu {
        id: contextMenu
        objectName: "contextMenu"
        minWidth: 200
        // opened by a right-click too it takes the keys, so Esc closes it (Popup's
        // CloseOnEscape needs the focus) and Down picks a row; closing hands them back to
        // the view it was opened on (#165)
        focus: true
        onRowHovered: function (entry, row) {
            if (entry.children) win.openContextSub(entry, row, false)
            else contextSubMenu.close()
        }
        onClosed: contextSubMenu.close()
    }
    PopMenu {
        id: contextSubMenu
        objectName: "contextSubMenu"
        minWidth: 200
        sub: true
        onClosed: if (contextMenu.opened) contextMenu.contentItem.forceActiveFocus()
    }
    function contextEntries(rows) {
        var entries = []
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i]
            if (r.separator) { entries.push({ separator: true }); continue }
            ;(function (r_) {
                if (r_.children) {
                    entries.push({ text: r_.text, name: r_.name, icon: r_.icon, detail: "›",
                                   enabled: r_.enabled, keepOpen: true, children: r_.children,
                                   action: function (row) { win.openContextSub(r_, row, true) } })
                    return
                }
                entries.push({ text: r_.text, name: r_.name, detail: r_.detail, icon: r_.icon,
                               enabled: r_.enabled,
                               action: function () {
                                   contextMenu.close()
                                   contextMenus.trigger(r_.name)
                               } })
            })(r)
        }
        return entries
    }
    // the submenu of `entry` beside its row: to the right, or to the left when the window
    // has no room there; `keyboard`: it takes the keys
    function openContextSub(entry, row, keyboard) {
        if (!row) return
        if (contextSubMenu.opened && contextSubMenu.entries.length
                && (contextSubMenu.entries[0].name || "").indexOf(entry.name + "/") === 0) {
            if (keyboard) contextSubMenu.contentItem.forceActiveFocus()
            return
        }
        contextSubMenu.close()
        contextSubMenu.entries = win.contextEntries(entry.children)
        contextSubMenu.keyboard = keyboard
        var right = row.mapToItem(win.contentItem, row.width, 0).x
        if (right + contextSubMenu.minWidth + 8 <= win.width)
            contextSubMenu.openAt(row, row.width + contextMenu.rightPadding + 2, -contextSubMenu.topPadding)
        else
            contextSubMenu.openAt(row, -contextMenu.leftPadding - 2, -contextSubMenu.topPadding, true)
    }
    Connections {
        target: contextMenus
        function onOpenRequested(rows, x, y, keyboard) {
            contextMenu.close()
            contextMenu.entries = win.contextEntries(rows)
            contextMenu.keyboard = keyboard
            contextMenu.openAt(win.contentItem, x, y)
        }
    }

    NoticeStack {
        id: noticeStack
        objectName: "noticeStack"
        anchors.right: parent.right
        anchors.bottom: statusBar.top
        anchors.margins: theme.metrics.pad
        width: 380
    }

    // the S icon's centre, where the tile floor's status cues start (#191), in the canvas
    // area's px
    Item {
        visible: false
        readonly property point centre: Qt.point(
            prompt.iconGap + prompt.iconSize / 2 - canvasArea.x,
            prompt.height - prompt.iconGap - prompt.iconSize / 2 - canvasArea.y)
        onCentreChanged: tileFloor.setAgentAnchor(centre.x, centre.y)
        Component.onCompleted: tileFloor.setAgentAnchor(centre.x, centre.y)
    }

    // the agent icon and its panel float above everything at the bottom left, the status
    // bar included (#162). `promptBar` stays the Python object everywhere:
    // `promptBar.prefill(text)` ("Ask agent to change", #45) is its slot, which the
    // panel's input follows.
    AgentPrompt {
        id: prompt
        anchors.fill: parent
        z: 30
    }
}
