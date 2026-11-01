import QtQuick

// A theme-tinted line icon from the shell's SVG set (icons/*.svg, rendered by the
// image://icons provider). ``Icon { name: "link"; color: theme.colors.text_muted }``.
Image {
    id: root
    property string name: ""
    property color color: theme.colors.text_muted
    property real size: 16
    width: size
    height: size
    sourceSize: Qt.size(Math.round(size * 2), Math.round(size * 2))
    source: name !== "" ? "image://icons/" + name + "/" + String(color).replace("#", "") : ""
    fillMode: Image.PreserveAspectFit
    smooth: true
    asynchronous: false
}
