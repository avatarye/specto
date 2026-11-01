import QtQuick

// A view footer's text button (#126): a 17 px pill with 7 px of padding whose label
// reads its state ("66%" for a zoom, "name ↓" for a sort); quiet at rest, the
// surface-hover fill under the pointer. The one button a footer gives a cycle or a menu.
Rectangle {
    id: root
    property string text: ""
    property string tip: ""
    property bool mono: false
    // on: the soft accent fill under the accent label (its menu or popover is open, #128)
    property bool on: false
    signal clicked()

    width: label.implicitWidth + 14
    height: 17
    radius: height / 2
    color: on ? theme.colors.accent_soft : (hover.hovered ? theme.colors.surface_hover : "transparent")
    FooterLabel {
        id: label
        anchors.centerIn: parent
        text: root.text
        mono: root.mono
        tone: root.on ? "accent" : (hover.hovered ? "text" : "text_muted")
        elide: Text.ElideNone
    }
    HoverHandler { id: hover }
    TapHandler { onTapped: root.clicked() }
    Tip {
        visible: hover.hovered && root.tip !== ""
        text: root.tip
        tag: root.objectName
    }
}
