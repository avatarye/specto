// The video footer's volume slider (#216): a short horizontal track beside the speaker
// button, the part up to the thumb in accent, dimmed while the view is muted. A press or
// drag sets the volume live (bridge.pickVolume, final on release: remembered as
// navigation); the wheel steps it 5 % (bridge.stepVolume). Hovering names the volume
// ("80 %"). `view` lends the theme lookups.
// objectNames: "<prefix>:volumeSlider", "<prefix>:volumeThumb".
import QtQuick
import Specto.Shell as Shell

Item {
    id: root
    property Item view: null
    property QtObject bridge: null
    property string objectPrefix: "video"
    readonly property real fraction: bridge ? bridge.volume : 1
    readonly property bool muted: bridge ? bridge.muted : true
    readonly property real trackW: width - thumb.width
    function xOf(f) { return thumb.width / 2 + f * trackW }

    objectName: objectPrefix + ":volumeSlider"
    width: 56
    height: 22
    opacity: muted ? 0.45 : 1
    Behavior on opacity { NumberAnimation { duration: 120 } }

    Rectangle {   // track
        x: thumb.width / 2
        anchors.verticalCenter: parent.verticalCenter
        width: root.trackW
        height: 3
        radius: 1.5
        color: root.view.tok("border")
    }
    Rectangle {   // the part up to the thumb
        x: thumb.width / 2
        anchors.verticalCenter: parent.verticalCenter
        width: root.fraction * root.trackW
        height: 3
        radius: 1.5
        color: root.view.tok("accent")
    }
    Rectangle {   // thumb
        id: thumb
        objectName: root.objectPrefix + ":volumeThumb"
        width: 9
        height: 9
        radius: 4.5
        x: root.xOf(root.fraction) - width / 2
        anchors.verticalCenter: parent.verticalCenter
        color: root.view.tok("surface")
        border.color: root.view.tok("accent")
        border.width: 1.5
    }
    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        function pick(x, final) {
            if (root.bridge)
                root.bridge.pickVolume(Math.max(0, Math.min(1, (x - thumb.width / 2) / root.trackW)), final)
        }
        onPressed: (m) => pick(m.x, false)
        onPositionChanged: (m) => { if (pressed) pick(m.x, false) }
        onReleased: (m) => pick(m.x, true)
        onWheel: (wheel) => { if (root.bridge) root.bridge.stepVolume(wheel.angleDelta.y > 0 ? 1 : -1) }
    }
    Shell.Tip {
        visible: area.containsMouse && !area.pressed
        text: "volume " + (root.bridge ? root.bridge.volumeLabel : "")
        tag: root.objectName
    }
}
