import QtQuick

// The one view footer (#126, design 05 section 4): a 24 px strip under a view's body,
// the same on every view. With two or more items it starts with the step controls
// (previous / next, 22 px ChromeButtons, faint at an end: stepping stops there, no
// wrap) and the position "n / N"; then the view's own leading items (the children:
// a name, a picked key; a preview's renderer puts the kind's icon first), and its
// `trailing` items on the right (a readout, one
// button). Footer controls are always visible: they act on the content, not the view.
// `room` is what is left for the leading items, so a long name can elide in it.
// objectNames: footer:<viewId>, step:<viewId>:prev / :next, position:<viewId>.
Rectangle {
    id: root
    property string viewId: ""
    property int stepIndex: -1
    property int stepCount: 0
    // the step buttons' tips, naming the view's own step keys (hosts.stepping.step_tips)
    property var stepTips: []
    // the shown file's kind, a file-type icon's name (a preview's renderer, #126)
    property string kindIcon: ""
    default property alias content: lead.data
    property alias trailing: trail.data
    readonly property bool stepping: stepCount > 1
    readonly property real room: Math.max(0, trail.x - lead.x - 2 * spacing)
    readonly property real spacing: 4
    signal stepped(int delta)

    objectName: "footer:" + viewId
    height: 24
    color: theme.colors.surface
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: theme.colors.border
    }

    Row {
        id: steps
        x: 6
        anchors.verticalCenter: parent.verticalCenter
        spacing: root.spacing
        visible: root.stepping
        ChromeButton {
            objectName: "step:" + root.viewId + ":prev"
            name: "chevron-left"
            disabled: root.stepIndex <= 0
            tip: root.stepTips[0] || "previous"
            onClicked: root.stepped(-1)
        }
        ChromeButton {
            objectName: "step:" + root.viewId + ":next"
            name: "chevron-right"
            disabled: root.stepIndex >= root.stepCount - 1
            tip: root.stepTips[1] || "next"
            onClicked: root.stepped(1)
        }
        FooterLabel {
            // named only while it shows: a browser lays out its own position
            objectName: root.stepping ? "position:" + root.viewId : ""
            anchors.verticalCenter: parent.verticalCenter
            text: (root.stepIndex + 1) + " / " + root.stepCount
        }
    }
    Image {
        id: kind
        objectName: "kindIcon:" + root.viewId
        visible: root.kindIcon !== ""
        x: root.stepping ? steps.x + steps.width + root.spacing : 6
        width: visible ? 16 : 0
        height: 16
        anchors.verticalCenter: parent.verticalCenter
        source: root.kindIcon ? "../FileTree/icons/" + root.kindIcon + ".svg" : ""
        sourceSize.width: 16
        sourceSize.height: 16
        fillMode: Image.PreserveAspectFit
        smooth: true
    }
    Row {
        id: lead
        x: kind.visible ? kind.x + kind.width + root.spacing : kind.x
        anchors.verticalCenter: parent.verticalCenter
        spacing: root.spacing
    }
    Row {
        id: trail
        anchors.right: parent.right
        anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        spacing: root.spacing
    }
}
