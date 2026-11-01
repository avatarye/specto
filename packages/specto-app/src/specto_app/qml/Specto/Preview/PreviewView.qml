// The preview view's item: a frame the host parents the renderer of the shown item's
// kind into (the image, pdf, video, markdown, json, html or 3D view's own item, filling
// it), or, for an item no renderer shows, a quiet card with the file's name and size and
// a hint to open it in a view of its own. The host sets `card` (null while a renderer
// shows) and `caption`. A renderer carries the previous / next controls and keys
// (#107) in its footer; the card has a footer of its own (#126: step, position, the
// kind's icon and the name), and Left/Right, PageUp/PageDown, Home/End while the preview
// has the focus.
import QtQuick
import "../Shell"

Item {
    id: root
    property string viewId: ""
    // {name, size, hint} for an item no renderer shows; null (or undefined: a None set
    // from Python) otherwise
    property var card: null
    property string caption: ""
    property QtObject bridge: null
    property int stepIndex: -1
    property int stepCount: 0
    property var stepTips: []
    function step(delta) { if (root.bridge) root.bridge.step(delta) }
    // theme tokens only (ADR 0008); standing alone (no shell), the system palette stands in
    readonly property bool hasTheme: typeof theme !== "undefined" && theme !== null
    SystemPalette { id: sys }
    function tok(key) {
        if (hasTheme) return theme.colors[key]
        if (key.indexOf("text") >= 0) return sys.text
        if (key.indexOf("border") >= 0) return sys.mid
        if (key.indexOf("surface") >= 0) return sys.base
        return sys.window
    }
    function font(kind) { return hasTheme ? theme.fonts[kind] : Qt.application.font.family }
    function metric(key, fallback) { return hasTheme ? theme.metrics[key] : fallback }

    clip: true

    HoverHandler { id: hover }
    Keys.onPressed: (event) => {
        if (!root.card) return
        switch (event.key) {
        case Qt.Key_Left: case Qt.Key_PageUp: root.step(-1); break
        case Qt.Key_Right: case Qt.Key_PageDown: root.step(1); break
        case Qt.Key_Home: root.step(-1000000000); break
        case Qt.Key_End: root.step(1000000000); break
        default: return
        }
        event.accepted = true
    }

    Rectangle {
        anchors.fill: parent
        color: root.tok("bg_tint")
    }

    // the renderer's item goes here (the host sizes it to this item)
    Item {
        id: slot
        objectName: "preview:" + root.viewId + ":slot"
        anchors.fill: parent
        visible: !root.card
    }

    Rectangle {
        id: cardBox
        objectName: "preview:" + root.viewId + ":card"
        visible: !!root.card
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * root.metric("pad", 12), 360)
        height: cardColumn.implicitHeight + 2 * root.metric("pad", 12)
        radius: root.metric("radius_inner", 8)
        color: root.tok("surface")
        border.color: root.tok("border")
        border.width: 1
        TapHandler { onTapped: root.forceActiveFocus() }

        Column {
            id: cardColumn
            anchors.centerIn: parent
            width: parent.width - 2 * root.metric("pad", 12)
            spacing: 4
            Text {
                width: parent.width
                elide: Text.ElideMiddle
                horizontalAlignment: Text.AlignHCenter
                text: root.card ? root.card.name : ""
                color: root.tok("text")
                font.family: root.font("ui")
                font.pixelSize: root.metric("font_px", 13)
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: root.card ? root.card.size : ""
                color: root.tok("text_muted")
                font.family: root.font("mono")
                font.pixelSize: root.metric("font_px_small", 11)
            }
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                text: root.card ? root.card.hint : ""
                color: root.tok("text_muted")
                font.family: root.font("ui")
                font.pixelSize: root.metric("font_px_small", 11)
            }
        }
    }

    // the card's footer: step:<view>:card:prev, the renderer's own keeps step:<view>:prev
    ViewFooter {
        id: cardFooter
        visible: !!root.card
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        viewId: root.viewId + ":card"
        stepIndex: root.stepIndex
        stepCount: root.stepCount
        stepTips: root.stepTips
        kindIcon: root.card && root.card.icon ? root.card.icon : ""
        onStepped: (delta) => root.step(delta)
        FooterLabel {
            width: Math.min(implicitWidth, cardFooter.room)
            text: root.card ? root.card.name : ""
            mono: true
            tone: "text"
        }
    }
}
