import QtQuick

// A quiet square icon button (Calm Canvas): no border until hover, a filled tone when
// ``filled``. ``tip`` shows on hover. Every colour is a theme token.
Rectangle {
    id: root
    property string name: ""
    property string tip: ""
    property string tone: "neutral"   // neutral | accent | agent | lock | removed
    property bool filled: false
    property real size: 28
    property real iconSize: 16
    signal clicked()

    readonly property color toneColor: tone === "neutral" ? theme.colors.text_muted : theme.colors[tone]
    width: size
    height: size
    radius: theme.metrics.radius_inner
    color: filled ? toneColor : (hover.hovered ? theme.colors.surface_hover : "transparent")
    border.width: filled || hover.hovered ? 1 : 0
    border.color: filled ? toneColor : theme.colors.border
    opacity: enabled ? 1.0 : 0.4

    Icon {
        anchors.centerIn: parent
        name: root.name
        size: root.iconSize
        color: root.filled ? theme.colors.surface : (hover.hovered && root.tone === "neutral" ? theme.colors.text : root.toneColor)
    }
    HoverHandler { id: hover }
    TapHandler {
        enabled: root.enabled
        onTapped: root.clicked()
    }
    Tip {
        visible: hover.hovered && root.tip !== ""
        text: root.tip
        tag: root.objectName
    }
}
