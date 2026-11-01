import QtQuick

// Undo, redo and the one global "..." menu, floating over the canvas's top-right corner
// (#120: there is no top bar; the window title names the project). One pill (design #123
// section 1): 22 px buttons, a divider before the menu button, which shows "on" while its
// menu is open; a disabled redo stays in place, faint. The menu holds what the project
// menu held, open folder (a project opens, a plain folder becomes one, #217), the recent
// projects (a submenu to its left, the open project in bold) and quit (#218: snap and
// details left it; details sits behind the status bar's info button, snap stays the
// agent's and the endpoint's).
Rectangle {
    id: root
    signal openProject()
    readonly property real inset: 4
    width: buttons.implicitWidth + 2 * (inset + border.width)
    height: buttons.implicitHeight + 2 * (inset + border.width)
    radius: height / 2
    color: theme.colors.surface
    border.width: 1
    border.color: theme.colors.border
    SoftShadow { radius: root.radius; blur: 0.4; strength: 0.12 }

    PopMenu {
        id: moreMenu
        objectName: "moreMenu"
        minWidth: 242
        onRowHovered: function (entry, row) {
            if (entry.name === "recent") root.openRecent(row)
            else recentMenu.close()
        }
        onClosed: recentMenu.close()
    }
    PopMenu {
        id: recentMenu
        objectName: "recentMenu"
        minWidth: 222
    }
    // rows are menu:openFolder, menu:recent, menu:quit; a recent project's row is
    // menu:recent:<its name>
    function openMoreMenu() {
        moreMenu.entries = [
            { text: "Open folder...", name: "openFolder", detail: "Ctrl+O", action: function () { root.openProject() } },
            { text: "Recent projects", name: "recent", detail: "›", keepOpen: true,
              enabled: app.recents.length > 0, action: function (row) { root.openRecent(row) } },
            { separator: true },
            { text: "Quit", name: "quit", detail: "Ctrl+Q", action: function () { app.quit() } },
        ]
        // right-aligned under the button: the menu stays inside the window
        moreMenu.openAt(moreButton, moreButton.width + root.inset + root.border.width,
                        moreButton.height + root.inset + root.border.width + 6, true)
    }
    function openRecent(row) {
        if (recentMenu.opened) return
        var entries = []
        var recents = app.recents
        for (var i = 0; i < recents.length; i++) {
            var e = recents[i]
            ;(function (e_) {
                entries.push({ text: e_.name, name: "recent:" + e_.name, detail: e_.short, mono: true,
                               bold: e_.current === true, enabled: !e_.missing,
                               action: function () {
                                   moreMenu.close()
                                   if (!e_.current) app.openProject(e_.root, false)
                               } })
            })(e)
        }
        if (!entries.length) return
        recentMenu.entries = entries
        // to the left of the menu, its first row level with this one
        recentMenu.openAt(row, -moreMenu.leftPadding - 2, -recentMenu.topPadding, true)
    }

    Row {
        id: buttons
        anchors.centerIn: parent
        spacing: 2
        IconButton {
            objectName: "undoButton"
            name: "undo"
            size: 22
            iconSize: 14
            radius: width / 2
            enabled: controller.canUndo
            tip: controller.lastTxnLabel ? "undo " + controller.lastTxnLabel + " (Ctrl+Z)" : "nothing to undo"
            onClicked: controller.undo()
        }
        IconButton {
            objectName: "redoButton"
            name: "redo"
            size: 22
            iconSize: 14
            radius: width / 2
            enabled: controller.canRedo
            tip: controller.nextRedoLabel ? "redo " + controller.nextRedoLabel + " (Ctrl+Y)" : "nothing to redo"
            onClicked: controller.redo()
        }
        Rectangle {
            objectName: "controlsDivider"
            width: 1
            height: 14
            anchors.verticalCenter: parent.verticalCenter
            color: theme.colors.border
        }
        Item {
            width: 22
            height: 22
            // "on" while the menu is open: accent_soft under an accent icon
            Rectangle {
                anchors.fill: parent
                radius: width / 2
                visible: moreMenu.opened
                color: theme.colors.accent_soft
            }
            IconButton {
                id: moreButton
                objectName: "moreButton"
                name: "more"
                size: 22
                iconSize: 14
                radius: width / 2
                tone: moreMenu.opened ? "accent" : "neutral"
                tip: moreMenu.opened ? "" : "open, recent projects, quit"
                onClicked: root.openMoreMenu()
            }
        }
    }
}
