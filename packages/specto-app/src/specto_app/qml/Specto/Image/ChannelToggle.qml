import QtQuick
import "../Shell"

// One channel toggle of the image view's footer (#171), a ChromeTextButton's pill: the
// channel's letter, "on" (the soft accent fill) while it shows in a solo or a mix, faint
// while it is off. A click solos it, Ctrl+click or Alt+click toggles it in the mix (#182),
// a right-click opens the view's channel menu for it.
Rectangle {
    id: root
    property string channel: ""      // r g b a l
    property bool on: false
    property bool off: false
    property string tip: ""
    signal clicked(bool mix)
    signal menu()

    width: label.implicitWidth + 12
    height: 17
    radius: height / 2
    color: on ? theme.colors.accent_soft : (area.containsMouse ? theme.colors.surface_hover : "transparent")
    FooterLabel {
        id: label
        anchors.centerIn: parent
        text: root.channel.toUpperCase()
        mono: true
        tone: root.on ? "accent" : root.off ? "text_faint" : (area.containsMouse ? "text" : "text_muted")
        elide: Text.ElideNone
    }
    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: (m) => {
            if (m.button === Qt.RightButton) root.menu()
            else root.clicked((m.modifiers & (Qt.ControlModifier | Qt.AltModifier)) !== 0)
        }
    }
    Tip {
        visible: area.containsMouse && root.tip !== ""
        text: root.tip
        tag: root.objectName
    }
}
