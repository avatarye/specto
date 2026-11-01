import QtQuick
import QtQuick.Effects

// A soft drop shadow under a rounded card (style B). The effect's source is a flat
// rectangle in the card colour; the card itself paints over it, so only the blurred
// shadow around the edges shows. Place it as the card's first child with z below.
Item {
    id: root
    property real radius: theme.metrics.radius_card
    property real blur: 0.55
    property real strength: 0.22
    property color fill: theme.colors.surface
    anchors.fill: parent
    z: -1
    Rectangle {
        id: src
        anchors.fill: parent
        radius: root.radius
        color: root.fill
        visible: false
    }
    MultiEffect {
        source: src
        anchors.fill: src
        shadowEnabled: true
        shadowBlur: root.blur
        shadowOpacity: root.strength
        shadowVerticalOffset: theme.metrics.shadow_y
        shadowHorizontalOffset: 0
        shadowColor: theme.colors.text
        autoPaddingEnabled: true
    }
}
