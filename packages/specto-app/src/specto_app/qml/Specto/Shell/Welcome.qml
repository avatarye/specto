import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Dialogs

// specto-app with no project: a calm card (style B) with recent projects and one "Open
// folder..." (#217: a folder with a .specto/ project opens, any other folder becomes one
// named after itself). Everything goes through the welcome controller, which opens or
// initialises the project and hands it to the app in this process.
Window {
    id: win
    width: 820
    height: 560
    minimumWidth: 640
    minimumHeight: 420
    // never takes the focus under the test driver (#237); plain when loaded on its own
    flags: typeof shellWindowFlags !== "undefined" ? shellWindowFlags : Qt.Window
    visible: true
    color: theme.colors.bg
    title: "Specto"

    FolderDialog {
        id: openDialog
        title: "Open a folder as a Specto project"
        onAccepted: welcome.openPath(selectedFolder.toString(), true)
    }

    Rectangle {
        id: card
        objectName: "welcomeCard"
        anchors.centerIn: parent
        width: Math.min(720, parent.width - 4 * theme.metrics.pad)
        height: Math.min(440, parent.height - 4 * theme.metrics.pad)
        radius: theme.metrics.radius_card
        color: theme.colors.surface
        border.color: theme.colors.border
        Rectangle {
            anchors.fill: parent
            anchors.topMargin: theme.metrics.shadow_y
            radius: parent.radius
            color: theme.colors.shadow
            z: -1
        }

        // left: brand, actions, a path field
        Column {
            id: left
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.margins: theme.metrics.pad * 2
            width: 300
            spacing: 12
            Row {
                spacing: 10
                // the S character (#170) on the agent's tile
                Rectangle {
                    objectName: "welcomeMark"
                    width: 36; height: 36; radius: 10
                    color: theme.colors.surface
                    border.width: 1
                    border.color: theme.colors.border_strong
                    SMark {
                        anchors.centerIn: parent
                        width: 27.4
                        height: 27.4
                    }
                }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        text: "Specto"
                        color: theme.colors.text
                        font.family: theme.fonts.ui
                        font.pixelSize: 20
                        font.bold: true
                    }
                    Text {
                        text: "a viewer you and your agent compose together"
                        color: theme.colors.text_muted
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                    }
                }
            }
            Item { width: 1; height: 6 }
            Pill {
                objectName: "openFolder"
                text: "Open folder..."
                tone: "accent"
                filled: true
                implicitHeight: 30
                width: left.width
                tip: "a folder with a .specto/ project opens; any other folder becomes one"
                onClicked: openDialog.open()
            }
            Pill {
                visible: welcome.hereProject !== ""
                text: "Open the project of this folder"
                tone: "neutral"
                implicitHeight: 30
                width: left.width
                tip: welcome.hereProject
                onClicked: welcome.openPath(welcome.hereProject, false)
            }
            Item { width: 1; height: 6 }
            Text {
                text: "or type a folder"
                color: theme.colors.text_faint
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
            }
            Row {
                spacing: 6
                TextField {
                    id: pathField
                    objectName: "pathField"
                    width: left.width - openBtn.width - 6
                    height: 30
                    placeholderText: "C:/data/my-project"
                    font.family: theme.fonts.ui
                    font.pixelSize: theme.metrics.font_px
                    color: theme.colors.text
                    placeholderTextColor: theme.colors.text_faint
                    background: Rectangle {
                        radius: theme.metrics.radius_inner
                        color: theme.colors.surface_alt
                        border.color: pathField.activeFocus ? theme.colors.focus : theme.colors.border
                    }
                    Keys.onReturnPressed: welcome.openPath(text, true)
                }
                Pill {
                    id: openBtn
                    objectName: "openPath"
                    text: "Open"
                    tone: "accent"
                    implicitHeight: 30
                    anchors.verticalCenter: parent.verticalCenter
                    tip: "open the folder as a project (a plain folder becomes one)"
                    onClicked: welcome.openPath(pathField.text, true)
                }
            }
            Text {
                objectName: "welcomeError"
                visible: welcome.error !== ""
                width: left.width
                wrapMode: Text.Wrap
                text: welcome.error
                color: theme.colors.error
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
            }
        }

        Rectangle {
            id: divider
            anchors.left: left.right
            anchors.leftMargin: theme.metrics.pad * 1.5
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.margins: theme.metrics.pad * 2
            width: 1
            color: theme.colors.border
        }

        // right: recent projects
        Column {
            anchors.left: divider.right
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.margins: theme.metrics.pad * 2
            anchors.leftMargin: theme.metrics.pad * 1.5
            spacing: 8
            Text {
                text: "Recent projects"
                color: theme.colors.text
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_title
                font.bold: true
            }
            Text {
                visible: welcome.recents.length === 0
                text: "None yet. Open a folder of runs, images or JSON; it becomes a project."
                color: theme.colors.text_muted
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px
                width: parent.width
                wrapMode: Text.Wrap
            }
            ListView {
                id: recentList
                objectName: "recentList"
                width: parent.width
                height: parent.height - 40
                clip: true
                spacing: 4
                model: welcome.recents
                delegate: Rectangle {
                    id: row
                    required property var modelData
                    required property int index
                    width: recentList.width
                    height: 46
                    radius: theme.metrics.radius_inner
                    color: rowHover.hovered ? theme.colors.surface_hover : "transparent"
                    opacity: modelData.missing ? 0.5 : 1
                    HoverHandler { id: rowHover }
                    TapHandler {
                        onTapped: if (!row.modelData.missing) welcome.openPath(row.modelData.root, false)
                    }
                    Column {
                        anchors.left: parent.left
                        anchors.leftMargin: theme.metrics.pad_tight
                        anchors.right: forgetBtn.left
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            text: row.modelData.name + (row.modelData.missing ? "  (folder missing)" : "")
                            color: theme.colors.text
                            font.family: theme.fonts.ui
                            font.pixelSize: theme.metrics.font_px
                            font.bold: true
                            elide: Text.ElideRight
                            width: parent.width
                        }
                        Text {
                            text: welcome.shortPath(row.modelData.root)
                            color: theme.colors.text_faint
                            font.family: theme.fonts.mono
                            font.pixelSize: theme.metrics.font_px_small
                            elide: Text.ElideMiddle
                            width: parent.width
                        }
                    }
                    Pill {
                        id: forgetBtn
                        anchors.right: parent.right
                        anchors.rightMargin: theme.metrics.pad_tight
                        anchors.verticalCenter: parent.verticalCenter
                        visible: rowHover.hovered
                        text: "x"
                        tip: "forget"
                        onClicked: welcome.forget(row.modelData.root)
                    }
                }
            }
        }
    }

    Text {
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.margins: theme.metrics.pad
        text: "a project is a folder with a .specto/ inside; your data stays where it is"
        color: theme.colors.text_faint
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
    }
}
