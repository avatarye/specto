import QtQuick

// A view footer's text (#126): 11 px, the ui face or the mono one (names, keys,
// readouts), coloured by a theme token (`tone`, muted by default).
Text {
    property bool mono: false
    property string tone: "text_muted"
    color: theme.colors[tone]
    font.family: mono ? theme.fonts.mono : theme.fonts.ui
    font.pixelSize: theme.metrics.font_px_small
    elide: Text.ElideMiddle
    verticalAlignment: Text.AlignVCenter
}
