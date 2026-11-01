// The video footer's speed popover (#128): a vertical slider, 4x at the top to 0.25x at
// the foot, its five stops evenly spaced (a log2 scale, VideoBridge.speedStops). A press
// or drag on the slider, or a click on a stop's label, picks the nearest stop
// (bridge.pickSpeed): live, never a submit. The track fills from the foot to the thumb.
// It opens above its parent (the footer's speed button), right edges aligned, and
// closes on Escape or a press outside. `view` lends the theme lookups.
// objectNames: "<prefix>:speedSlider", "<prefix>:speed:<speed>" per stop.
import QtQuick
import QtQuick.Controls.Basic

Popup {
    id: pop
    property Item view: null
    property QtObject bridge: null
    property string objectPrefix: "video"
    readonly property real fraction: bridge ? bridge.speedFraction : 0.5
    readonly property real trackTop: 16
    readonly property real trackH: 116
    readonly property real trackX: 43
    function yOf(f) { return trackTop + (1 - f) * trackH }

    width: 60
    height: 150
    padding: 0
    focus: true                 // Escape reaches it
    x: parent ? parent.width - width : 0
    y: parent ? -height - 4 : 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
    background: Rectangle {
        objectName: pop.objectPrefix + ":speedPopover"
        color: pop.view.tok("surface")
        border.color: pop.view.tok("border")
        border.width: 1
        radius: pop.view.metric("radius_inner", 8)
    }
    contentItem: Item {
        objectName: pop.objectPrefix + ":speedSlider"
        Rectangle {   // track
            x: pop.trackX - 1.5
            y: pop.trackTop
            width: 3
            height: pop.trackH
            radius: 1.5
            color: pop.view.tok("border")
        }
        Rectangle {   // value fill, from the foot up to the thumb
            x: pop.trackX - 1.5
            y: pop.yOf(pop.fraction)
            width: 3
            height: pop.trackTop + pop.trackH - y
            color: pop.view.tok("accent")
        }
        Repeater {
            model: pop.bridge ? pop.bridge.speedStops : []
            Item {
                id: stop
                required property var modelData
                readonly property bool current: Math.abs(pop.fraction - modelData.fraction) < 1e-6
                objectName: pop.objectPrefix + ":speed:" + modelData.speed
                x: 0
                y: pop.yOf(modelData.fraction) - height / 2
                width: pop.width
                height: 18
                Text {
                    x: pop.trackX - 8 - width
                    anchors.verticalCenter: parent.verticalCenter
                    text: stop.modelData.label
                    color: stop.current ? pop.view.tok("accent") : pop.view.tok("text_muted")
                    font.family: pop.view.font("mono")
                    font.pixelSize: pop.view.metric("font_px_small", 11)
                }
                Rectangle {   // the stop's tick across the track
                    x: pop.trackX - 3.5
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7
                    height: 1
                    color: pop.view.tok("border_strong")
                }
            }
        }
        Rectangle {   // thumb
            objectName: pop.objectPrefix + ":speedThumb"
            width: 11
            height: 11
            radius: 5.5
            x: pop.trackX - width / 2
            y: pop.yOf(pop.fraction) - height / 2
            color: pop.view.tok("surface")
            border.color: pop.view.tok("accent")
            border.width: 1.5
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            function pick(y) {
                if (pop.bridge) pop.bridge.pickSpeed(1 - (y - pop.trackTop) / pop.trackH)
            }
            onPressed: (m) => pick(m.y)
            onPositionChanged: (m) => { if (pressed) pick(m.y) }
        }
    }
}
