import QtQuick

// A small rounded button or chip in the Calm Canvas style. Every colour is a theme token.
// ``icon`` draws a line icon before the text; ``glyph`` a short mono string (a key hint).
Rectangle {
    id: root
    property string text: ""
    property string glyph: ""     // a change/agent glyph before the text, in the tone colour
    property string hint: ""      // a key hint after the text (``Tab``, ``Ctrl+K``), faint mono
    property string icon: ""
    property string tone: "neutral"   // neutral | accent | agent | added | changed | removed | ghost
    property bool filled: false
    property string tip: ""
    signal clicked()

    readonly property color toneColor: tone === "neutral" ? theme.colors.text_muted : theme.colors[tone]
    readonly property color softColor: tone === "neutral" ? theme.colors.surface_alt : theme.colors[tone + "_soft"]
    readonly property color fg: filled ? theme.colors.surface : (tone === "neutral" ? theme.colors.text : toneColor)

    implicitWidth: row.implicitWidth + 2 * theme.metrics.pad_tight + 8
    implicitHeight: 24
    radius: theme.metrics.radius_pill
    color: filled ? toneColor : (hover.hovered ? theme.colors.surface_hover : softColor)
    border.color: filled ? toneColor : (tone === "neutral" ? theme.colors.border : toneColor)
    border.width: 1
    opacity: enabled ? 1.0 : 0.45

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 5
        Icon {
            visible: root.icon !== ""
            name: root.icon
            size: 13
            color: root.filled ? theme.colors.surface : root.toneColor
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            visible: root.glyph !== ""
            text: root.glyph
            color: root.filled ? theme.colors.surface : root.toneColor
            font.family: theme.fonts.mono
            font.pixelSize: theme.metrics.font_px_small
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: root.text
            color: root.fg
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            visible: root.hint !== ""
            text: root.hint
            color: root.filled ? theme.colors.surface : theme.colors.text_faint
            font.family: theme.fonts.mono
            font.pixelSize: theme.metrics.font_px_small
            anchors.verticalCenter: parent.verticalCenter
        }
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
