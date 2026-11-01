import QtQuick

// A view's identicon in a small circle (#174: the hop track, the caption card): `past`
// filled with the chain's soft tint, `current` ringed on white, `future` hollow and
// faint. `view` is a view id or "project"; its face comes from walkStage.faces.
Rectangle {
    id: root
    property string view: ""
    property string state_: "past"
    property real size: 18
    readonly property var face: walkStage.faces[view] || ({})
    readonly property color tone: theme.colors[face.chain] || theme.colors.chain_0
    readonly property real ring: state_ === "current" ? 2 : 1.5
    width: size
    height: size
    radius: size / 2
    color: state_ === "past" ? theme.colors[(face.chain || "chain_0") + "_soft"] || theme.colors.chain_0_soft
                             : theme.colors.surface
    border.width: ring
    border.color: state_ === "future" ? theme.colors.border_strong : tone
    Image {
        anchors.fill: parent
        anchors.margins: root.ring
        source: root.state_ === "future" ? (root.face.plain || "") : (root.face.icon || "")
        sourceSize: Qt.size(Math.round(width * Screen.devicePixelRatio), Math.round(height * Screen.devicePixelRatio))
        smooth: false
        opacity: root.state_ === "future" ? 0.55 : 1
    }
}
