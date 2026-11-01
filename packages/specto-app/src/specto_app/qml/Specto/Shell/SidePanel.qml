import QtQuick

// The side panel standard (#130) for native views (#139), the QML twin of the web views'
// sidepanel.js: expanded, a 24 px header (TITLE, All / None when the panel lists items,
// the collapse button), the view's rows below it (the children) and a grip on the inner
// edge (a 2 px bar, 30 px tall, over a 6 px band) that sets the width; collapsed, a 24 px
// strip with the expand button and the title on its side.
//
// The panel shows state, it does not keep it: the view keeps {collapsed, width} in its
// `panels` param and binds `collapsed` / `panelWidth` here; the user's changes come out
// as `collapseRequested(collapsed)` and, once at the end of a grip drag, `resized(width)`.
// objectNames under `name` (e.g. "panel:kobi:prims"): <name>, <name>:toggle, <name>:grip,
// <name>:all, <name>:none.
Rectangle {
    id: root
    property string name: ""
    property string title: ""
    property string side: "left"
    property bool collapsed: false
    property real panelWidth: 180
    property real minWidth: 120
    property real maxWidth: 480
    property bool allNone: false
    default property alias content: body.data
    readonly property real strip: theme.metrics.panel_bar
    readonly property bool atLeft: side === "left"
    // the width while the grip is dragged (the param changes on release)
    property real dragWidth: -1
    signal collapseRequested(bool collapsed)
    signal resized(real width)
    signal all()
    signal none()

    objectName: name
    width: collapsed ? strip : (dragWidth >= 0 ? dragWidth : panelWidth)
    color: theme.colors.surface
    clip: true

    Rectangle {
        // the border on the inner edge
        x: root.atLeft ? root.width - 1 : 0
        width: 1
        height: parent.height
        color: theme.colors.border
    }

    // -- expanded -------------------------------------------------------------------------
    Item {
        id: head
        visible: !root.collapsed
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.strip
        Text {
            anchors.left: parent.left
            anchors.leftMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: root.title.toUpperCase()
            font.family: theme.fonts.ui
            font.pixelSize: 9
            font.weight: Font.DemiBold
            font.letterSpacing: 0.72
            color: theme.colors.text_faint
        }
        Row {
            anchors.right: parent.right
            anchors.rightMargin: root.atLeft ? 3 : 2
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            PanelLink {
                visible: root.allNone
                objectName: root.name + ":all"
                text: "All"
                tip: "check every row"
                onClicked: root.all()
            }
            PanelLink {
                visible: root.allNone
                objectName: root.name + ":none"
                text: "None"
                tip: "uncheck every row"
                onClicked: root.none()
            }
            ChromeButton {
                objectName: root.name + ":toggle"
                name: root.atLeft ? "sidebar-left" : "sidebar"
                tip: "collapse the " + root.title.toLowerCase() + " panel"
                onClicked: root.collapseRequested(true)
            }
        }
    }
    Item {
        id: body
        visible: !root.collapsed
        anchors.top: head.bottom
        anchors.topMargin: 4
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.rightMargin: root.atLeft ? 1 : 0
        anchors.leftMargin: root.atLeft ? 0 : 1
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 6
    }
    Item {
        id: grip
        objectName: root.name + ":grip"
        visible: !root.collapsed
        width: 6
        height: parent.height
        x: root.atLeft ? root.width - 5 : -1
        z: 2
        Rectangle {
            anchors.centerIn: parent
            width: 2
            height: 30
            radius: 1
            color: theme.colors.border_strong
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.SizeHorCursor
            property real startX: 0
            property real startW: 0
            onPressed: (m) => {
                startX = mapToItem(null, m.x, m.y).x
                startW = root.panelWidth
                root.dragWidth = startW
            }
            onPositionChanged: (m) => {
                if (!pressed) return
                var dx = mapToItem(null, m.x, m.y).x - startX
                var w = startW + (root.atLeft ? dx : -dx)
                root.dragWidth = Math.round(Math.max(root.minWidth, Math.min(root.maxWidth, w)))
            }
            onReleased: {
                var w = root.dragWidth
                root.dragWidth = -1
                if (w >= 0 && w !== root.panelWidth) root.resized(w)
            }
        }
    }

    // -- collapsed: the strip ---------------------------------------------------------------
    Column {
        visible: root.collapsed
        anchors.horizontalCenter: parent.horizontalCenter
        y: 1
        spacing: 18
        ChromeButton {
            objectName: root.name + ":expand"
            anchors.horizontalCenter: parent.horizontalCenter
            name: root.atLeft ? "sidebar-left" : "sidebar"
            on: true
            tip: "show the " + root.title.toLowerCase() + " panel"
            onClicked: root.collapseRequested(false)
        }
        Item {
            anchors.horizontalCenter: parent.horizontalCenter
            width: stripTitle.implicitHeight
            height: stripTitle.implicitWidth
            Text {
                id: stripTitle
                anchors.centerIn: parent
                rotation: 90
                text: root.title.toUpperCase()
                font.family: theme.fonts.ui
                font.pixelSize: 9
                font.weight: Font.DemiBold
                font.letterSpacing: 0.72
                color: theme.colors.text_faint
            }
        }
    }

    component PanelLink: Text {
        id: link
        property string tip: ""
        signal clicked()
        text: ""
        font.family: theme.fonts.ui
        font.pixelSize: theme.metrics.font_px_small
        font.underline: linkHover.hovered
        color: theme.colors.accent
        HoverHandler { id: linkHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: link.clicked() }
        Tip {
            visible: linkHover.hovered && link.tip !== ""
            text: link.tip
            tag: link.objectName
        }
    }
}
