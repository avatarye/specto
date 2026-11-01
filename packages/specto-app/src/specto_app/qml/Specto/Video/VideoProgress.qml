// The video footer's progress bar (#128): the footer's full length, a 3 px track with
// the played part in accent, a 9 px playhead at the cursor and the agent's A-B loop as a
// soft span. Press / drag scrubs (scrubStart / scrubTo / scrubEnd on the
// TimelineController `tl`); hovering asks the owner for the frame under the pointer and
// shows it in a dark tip above the bar: clip time, then SMPTE timecode
// (tl.hoverClip / tl.hoverTimecode). `view` lends the theme lookups (tok, font) of the
// view that holds it. objectNames: "<prefix>:track", "<prefix>:cursor", "<prefix>:tip".
import QtQuick

Item {
    id: root
    property QtObject tl: null          // TimelineController
    property Item view: null
    property string objectPrefix: "video"
    // the tip's parent: an item above the picture, so the tip may rise over it
    property Item tipLayer: parent
    implicitHeight: 10

    readonly property real tMin: tl ? tl.tMin : 0
    readonly property real span: Math.max(1e-9, (tl ? tl.tMax : 1) - tMin)
    function fracOf(t) { return Math.max(0, Math.min(1, (t - tMin) / span)) }
    function xOf(t) { return fracOf(t) * width }
    function tAt(x) { return tMin + Math.max(0, Math.min(1, x / Math.max(1, width))) * span }

    Rectangle {
        anchors.fill: parent
        color: root.view.tok("surface")
    }
    Item {
        id: track
        objectName: root.objectPrefix + ":track"
        anchors.fill: parent
        Rectangle {   // track
            y: 4
            width: parent.width
            height: 3
            color: root.view.tok("border")
        }
        Rectangle {   // the agent's A-B loop
            readonly property bool on: root.tl !== null && root.tl.loopA >= 0 && root.tl.loopB >= 0
            visible: on
            x: on ? root.xOf(root.tl.loopA) : 0
            width: on ? Math.max(2, root.xOf(root.tl.loopB) - x) : 0
            y: 3
            height: 5
            radius: 1
            color: root.view.tok("accent_soft")
        }
        Rectangle {   // played
            y: 4
            width: root.tl ? root.xOf(root.tl.t) : 0
            height: 3
            color: root.view.tok("accent")
        }
        Rectangle {   // playhead
            objectName: root.objectPrefix + ":cursor"
            width: 9
            height: 9
            radius: 4.5
            x: Math.max(0, Math.min(root.width - width, (root.tl ? root.xOf(root.tl.t) : 0) - width / 2))
            y: 0.5
            color: root.view.tok("surface")
            border.color: root.view.tok("accent")
            border.width: 1.5
        }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        anchors.topMargin: -4      // a 10 px bar is a thin target: reach a little above it
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onPressed: (m) => { if (root.view) root.view.forceActiveFocus(); if (root.tl) root.tl.scrubStart(root.tAt(m.x)) }
        onPositionChanged: (m) => {
            if (!root.tl) return
            if (pressed) root.tl.scrubTo(root.tAt(m.x))
            root.tl.hover(root.tAt(m.x))
        }
        onReleased: (m) => { if (root.tl) root.tl.scrubEnd(root.tAt(m.x)) }
        onExited: if (root.tl && !pressed) root.tl.hoverLeave()
    }

    // -- hover tip: clip time and SMPTE timecode, above the bar at the pointer -------------
    Rectangle {
        id: tip
        objectName: root.objectPrefix + ":tip"
        parent: root.tipLayer
        visible: root.tl !== null && root.tl.hovering && root.tl.hoverClip !== ""
        z: 10
        readonly property point at: root.mapToItem(parent, mouse.mouseX, 0)
        x: Math.max(4, Math.min(parent.width - width - 4, at.x - width / 2))
        y: at.y - height - 4
        width: tipRow.implicitWidth + 16
        height: tipRow.implicitHeight + 8
        radius: root.view.metric("radius_inner", 8)
        color: root.view.tok("text")
        Row {
            id: tipRow
            anchors.centerIn: parent
            spacing: 8
            Repeater {
                model: root.tl ? [root.tl.hoverClip, root.tl.hoverTimecode] : []
                Text {
                    required property string modelData
                    text: modelData
                    color: root.view.tok("surface")
                    font.family: root.view.font("mono")
                    font.pixelSize: root.view.metric("font_px_small", 11)
                }
            }
        }
    }
}
