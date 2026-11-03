import QtQuick

// The text view (SPEC-32): one short text the document carries, centred, wrapped; the
// "Not supported: <name> (<ext>)" view. Theme tokens only (ADR 0008); standing alone
// (no shell), the system palette stands in.
Item {
    id: root
    property string viewId: ""
    property string text: ""
    readonly property bool hasTheme: typeof theme !== "undefined" && theme !== null
    SystemPalette { id: sys }
    function tok(key) {
        if (hasTheme) return theme.colors[key]
        if (key.indexOf("text") >= 0) return sys.text
        return sys.base
    }
    function font(kind) { return hasTheme ? theme.fonts[kind] : Qt.application.font.family }
    function metric(key, fallback) { return hasTheme ? theme.metrics[key] : fallback }

    clip: true

    Rectangle {
        anchors.fill: parent
        color: root.tok("bg_tint")
    }

    Text {
        objectName: "text:" + root.viewId + ":body"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * root.metric("pad", 12), 480)
        wrapMode: Text.Wrap
        horizontalAlignment: Text.AlignHCenter
        text: root.text
        color: root.tok("text_muted")
        font.family: root.font("ui")
        font.pixelSize: root.metric("font_px", 13)
    }
}
