// The gallery view's item: the native GalleryItem (Specto.Native) filling the slot, a
// thin overlay scroll bar, the hover preview popup, the quick-look overlay (Space) and
// the shared footer (#126), styled from the shell's `theme` context object
// when present (B Calm Canvas: warm background, white rounded surfaces, 13 px text).
//
// Everything per frame happens inside GalleryItem; this file only reacts to its signals.
import QtQuick
import QtQuick.Controls.Basic
import Specto.Native
import "../Shell"

Item {
    id: root
    property string viewId: ""
    property string caption: ""
    property QtObject bridge: null
    property alias gallery: gallery
    property int quickIndex: -1          // quick look (Space) target, -1 = closed
    property int newCount: 0             // arrivals not yet seen (watch mode); the host sets it
    property int newFirst: -1            // index of the first of them
    property string sortBy: ""           // params.sort without its sign: "", key, name, time
    property bool sortDesc: false
    // theme tokens only (ADR 0008); standing alone (no shell), the system palette stands in
    readonly property bool hasTheme: typeof theme !== "undefined" && theme !== null
    SystemPalette { id: sys }
    function tok(key) {
        if (hasTheme) return theme.colors[key]
        if (key.indexOf("text") >= 0 || key === "lock") return sys.text
        if (key.indexOf("accent") >= 0 || key === "added" || key === "focus") return sys.highlight
        if (key.indexOf("border") >= 0) return sys.mid
        if (key.indexOf("surface") >= 0) return sys.base
        return sys.window
    }
    function font(kind) { return hasTheme ? theme.fonts[kind] : Qt.application.font.family }
    function metric(key, fallback) { return hasTheme ? theme.metrics[key] : fallback }

    clip: true

    // thumbnails sit on the white card, as in the style B mockup
    Rectangle {
        anchors.fill: parent
        color: root.tok("surface")
    }

    GalleryItem {
        id: gallery
        objectName: "gallery"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        focus: true
        selectionColor: root.tok("accent")
        hoverColor: Qt.alpha(root.tok("accent"), 0.55)
        placeholderColor: root.tok("bg_tint")
        failedColor: root.tok("removed_soft")
        captionColor: root.tok("text_muted")
        captionFont.family: root.font("mono")
        captionFont.pixelSize: root.metric("font_px_small", 11)
        radius: root.metric("radius_inner", 8)
        borderWidth: 3
        gap: 8
        margin: 8

        onHoverIndexChanged: preview.onHover()
        onDraggingChanged: if (dragging) preview.hide()
        onScrollingChanged: if (scrolling) preview.hide()
        onLayoutChanged: preview.hide()
        onQuickLook: (index) => root.toggleQuickLook(index)
        onActivated: (index) => root.closeQuickLook()
        onSelectedIndexChanged: if (root.quickIndex >= 0 && gallery.selectedIndex >= 0) root.showQuickLook(gallery.selectedIndex)
        onUserSelected: preview.hide()
        onClicked: (index, button, modifiers) => {
            // right-click: the shell's context menu for the tile (#154), at the pointer
            if (button !== Qt.RightButton || index < 0 || !root.bridge) return
            preview.hide()
            var p = gallery.mapToItem(null, gallery.hoverPos.x, gallery.hoverPos.y)
            root.bridge.contextMenu(index, p.x, p.y)
        }
    }

    // the context menu's Quick look (#154): what Space does, on the tile it was opened for
    Connections {
        target: root.bridge
        function onQuickLookRequested(index) { root.showQuickLook(index) }
    }

    // -- the scroll bar (#126): a thin overlay pill at the body's right edge, there while
    //    the tiles overflow; under the pointer it widens in the accent; drag it to jump
    Item {
        id: indicator
        readonly property real pad: 6
        readonly property bool needed: gallery.contentHeight > gallery.height + 1
        readonly property real track: gallery.height - 2 * pad
        readonly property real thumbH: needed ? Math.max(28, track * gallery.height / gallery.contentHeight) : track
        readonly property real thumbY: needed && gallery.maxScroll > 0
            ? pad + (track - thumbH) * gallery.scrollY / gallery.maxScroll : pad
        anchors.right: gallery.right
        anchors.top: gallery.top
        width: 14
        height: gallery.height
        visible: needed
        Rectangle {
            objectName: "scrollbar:" + root.viewId
            readonly property bool hot: hoverTrack.hovered || dragTrack.active
            x: indicator.width - width - 2
            y: indicator.thumbY
            width: hot ? 6 : 4
            height: indicator.thumbH
            radius: width / 2
            color: hot ? root.tok("accent") : root.tok("border_strong")
            Behavior on width { NumberAnimation { duration: 120 } }
        }
        HoverHandler { id: hoverTrack }
        MouseArea {
            id: dragTrack
            property bool active: false
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            function jump(y) {
                var t = indicator.track - indicator.thumbH
                if (t <= 0) return
                var f = (y - indicator.pad - indicator.thumbH / 2) / t
                gallery.scrollTo(Math.max(0, Math.min(1, f)) * gallery.maxScroll, false)
            }
            onPressed: (mouse) => { active = true; jump(mouse.y) }
            onPositionChanged: (mouse) => { if (active) jump(mouse.y) }
            onReleased: active = false
        }
    }

    // -- "+N new": items that arrived while watching. One pill per burst: it appears with
    //    the first arrival, its count grows with the next ones, and it retires 3 s after
    //    the burst went quiet once the new items are in view (a click scrolls to them).
    Rectangle {
        id: newPill
        objectName: "newPill"
        readonly property bool inView: root.newFirst >= 0 && gallery.lastVisible >= root.newFirst
        visible: root.newCount > 0
        anchors.top: gallery.top
        anchors.horizontalCenter: gallery.horizontalCenter
        anchors.topMargin: 10
        z: 12
        width: newLabel.implicitWidth + 24
        height: 26
        radius: 13
        color: root.tok("added")
        opacity: visible ? 0.95 : 0.0
        Behavior on opacity { NumberAnimation { duration: 180 } }
        Text {
            id: newLabel
            anchors.centerIn: parent
            text: "+" + root.newCount + " new"
            color: root.tok("surface")
            font.pixelSize: root.metric("font_px_small", 11)
            font.family: root.font("ui")
            font.bold: true
        }
        TapHandler {
            onTapped: {
                gallery.revealIndex(root.newFirst, true)
                retireTimer.restart()
            }
        }
        Timer {
            // the burst went quiet: retire once the new items are on screen (else wait
            // for the scroll or the click that brings them there)
            id: retireTimer
            interval: 3000
            onTriggered: if (newPill.inView && root.bridge) root.bridge.ackArrivals()
        }
        onInViewChanged: if (inView && root.newCount > 0) retireTimer.restart()
        Connections {
            target: root
            function onNewCountChanged() { if (root.newCount > 0) retireTimer.restart() }
        }
    }

    // -- hover preview: after 400 ms over a tile, a larger decode beside that tile (in
    //    the direction with room, never over the tile itself), clamped to the view; not
    //    shown at all when the tile is already as big as the preview would be. A popup:
    //    it draws in the window's overlay, above the neighbouring cards
    Popup {
        id: preview
        modal: false
        dim: false
        focus: false
        closePolicy: Popup.NoAutoClose
        margins: 4
        padding: 0
        property int index: -1
        property point lastPos: Qt.point(-1, -1)
        readonly property real maxW: Math.min(420, root.width * 0.6)
        readonly property real maxH: Math.min(420, gallery.height * 0.8)
        readonly property real gapPx: 10
        function onHover() {
            if (gallery.hoverIndex < 0 || gallery.hoverIndex === index) {
                if (gallery.hoverIndex < 0) hide()
                return
            }
            hide()
            if (!worthIt(gallery.hoverRect)) return   // tiles already large: nothing to gain
            // only a real pointer move arms the preview: a reflow under a resting pointer
            // changes hoverIndex too, and a popup nobody asked for is a distraction
            var p = gallery.hoverPos
            if (Math.abs(p.x - lastPos.x) < 2 && Math.abs(p.y - lastPos.y) < 2) return
            lastPos = p
            hoverTimer.restart()
        }
        function fitSize(a) {
            // the picture box for aspect `a` inside maxW x maxH
            var w = maxW, h = w / a
            if (h > maxH) { h = maxH; w = h * a }
            return Qt.size(w, h)
        }
        function worthIt(r) {
            // a popup that covers neighbours must show clearly more than the tile does
            if (!r || r.width <= 0) return false
            var s = fitSize(gallery.cellAspect)
            return s.width >= r.width * 1.5 && s.height >= r.height * 1.5
        }
        function show(i) {
            if (!root.bridge || i < 0 || root.quickIndex >= 0) return
            // a tile that is not an image: its thumbnail is already its best picture here;
            // the preview (double-click, Space) shows the file itself (#106)
            if (!root.bridge.isImage(i)) return
            index = i
            img.source = root.bridge.previewUrl(i)
            label.text = root.bridge.captionOf(i)
            place()
            visible = true
        }
        function hide() {
            hoverTimer.stop()
            visible = false
            index = -1
            img.source = ""
        }
        function place() {
            var r = index >= 0 ? gallery.rectOf(index) : gallery.hoverRect
            var a = img.status === Image.Ready && img.implicitHeight > 0
                ? img.implicitWidth / img.implicitHeight : gallery.cellAspect
            var s = fitSize(a)
            width = s.width + 2 * pad
            height = s.height + 2 * pad + label.height + 4
            var W = gallery.width, H = gallery.height, g = gapPx, m = 4
            if (!r || r.width <= 0) {   // no tile rect (should not happen): near the cursor
                var p = gallery.hoverPos
                x = Math.max(m, Math.min(W - width - m, p.x + 18))
                y = Math.max(m, Math.min(H - height - m, p.y + 18))
                return
            }
            // candidates beside the tile, in order of preference; the first that fits wins,
            // else the side with the most room, clamped so the popup stays in the view
            var sides = [
                { x: r.x + r.width + g, y: r.y, room: W - (r.x + r.width + g) },
                { x: r.x - g - width, y: r.y, room: r.x - g },
                { x: r.x, y: r.y + r.height + g, room: H - (r.y + r.height + g) },
                { x: r.x, y: r.y - g - height, room: r.y - g }
            ]
            var pick = null
            for (var i = 0; i < sides.length; i++) {
                var c = sides[i]
                var need = i < 2 ? width : height
                if (c.room >= need) { pick = c; break }
            }
            if (!pick) {
                pick = sides[0]
                for (var j = 1; j < sides.length; j++)
                    if (sides[j].room > pick.room) pick = sides[j]
            }
            x = Math.max(m, Math.min(W - width - m, pick.x))
            y = Math.max(m, Math.min(H - height - m, pick.y))
        }
        readonly property real pad: 6
        visible: false
        background: Rectangle {
            objectName: "galleryPreview:" + root.viewId
            radius: root.metric("radius_inner", 8)
            color: root.tok("surface")
            border.color: root.tok("border")
            border.width: 1
        }
        Timer {
            id: hoverTimer
            interval: 400
            onTriggered: if (gallery.hoverIndex >= 0 && !gallery.dragging && !gallery.scrolling) preview.show(gallery.hoverIndex)
        }
        Image {
            id: img
            x: preview.pad
            y: preview.pad
            width: preview.width - 2 * preview.pad
            height: preview.height - 2 * preview.pad - label.height - 4
            asynchronous: true
            cache: false
            autoTransform: true
            smooth: true
            fillMode: Image.PreserveAspectFit
            sourceSize.width: 640
            onStatusChanged: if (status === Image.Ready && preview.visible) preview.place()
        }
        Text {
            id: label
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: preview.pad
            anchors.bottomMargin: 4
            elide: Text.ElideMiddle
            font.pixelSize: root.metric("font_px_small", 11)
            font.family: root.font("mono")
            color: root.tok("text_muted")
        }
    }

    // -- quick look (Space): the selected image fitted into the view, over a scrim
    function toggleQuickLook(index) {
        if (quickIndex >= 0) closeQuickLook()
        else showQuickLook(index)
    }
    function showQuickLook(index) {
        if (!bridge || index < 0) return
        preview.hide()
        // a tile that is not an image looks in the gallery's preview, not as an upscaled
        // thumbnail (#106)
        if (!bridge.isImage(index)) { bridge.openPreview(index); return }
        quickIndex = index
        quickImg.source = bridge.previewUrl(index)
        quickLabel.text = bridge.captionOf(index)
    }
    function closeQuickLook() {
        quickIndex = -1
        quickImg.source = ""
    }
    Rectangle {
        id: quick
        objectName: "quickLook:" + root.viewId
        anchors.fill: gallery
        visible: root.quickIndex >= 0
        z: 20
        color: Qt.alpha(root.tok("bg"), 0.92)
        MouseArea {
            anchors.fill: parent
            onClicked: root.closeQuickLook()
            onWheel: (wheel) => wheel.accepted = true
        }
        Rectangle {
            id: quickCard
            // the card hugs the picture: fit the image's size into the view minus margins
            readonly property real availW: quick.width - 48 - 16
            readonly property real availH: quick.height - 48 - quickLabel.height - 28
            readonly property real iw: quickImg.implicitWidth > 0 ? quickImg.implicitWidth : 640
            readonly property real ih: quickImg.implicitHeight > 0 ? quickImg.implicitHeight : 480
            readonly property real s: Math.min(availW / iw, availH / ih)
            anchors.centerIn: parent
            width: iw * s + 16
            height: ih * s + quickLabel.height + 28
            radius: root.metric("radius_card", 14)
            color: root.tok("surface")
            border.color: root.tok("border")
            border.width: 1
            Image {
                id: quickImg
                x: 8
                y: 8
                width: quickCard.iw * quickCard.s
                height: quickCard.ih * quickCard.s
                asynchronous: true
                cache: false
                autoTransform: true
                smooth: true
                fillMode: Image.PreserveAspectFit
                sourceSize.width: 2048
            }
            Text {
                id: quickLabel
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 8
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideMiddle
                font.pixelSize: root.metric("font_px", 13)
                font.family: root.font("mono")
                color: root.tok("text")
            }
        }
    }
    // the shell's window-wide Escape shortcut would take the key first: claim it while
    // there is an overlay of ours to close
    Keys.onShortcutOverride: (event) => {
        if (event.key === Qt.Key_Escape && (root.quickIndex >= 0 || preview.visible)) event.accepted = true
    }
    Keys.onEscapePressed: (event) => {
        if (root.quickIndex >= 0) { root.closeQuickLook(); event.accepted = true }
        else if (preview.visible) { preview.hide(); event.accepted = true }
    }

    // -- the footer (#126): the picked item and where it is ("4 / 12"; "– / 12" with
    //    nothing picked); one sort button on the right, its menu key / name / time (the
    //    active sort again flips the order)
    ViewFooter {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        viewId: root.viewId
        FooterLabel {
            objectName: "picked:" + root.viewId
            width: Math.min(implicitWidth, footer.room - position.width - footer.spacing)
            visible: text !== ""
            text: root.caption
            mono: true
            tone: "text"
        }
        FooterLabel {
            id: position
            objectName: "position:" + root.viewId
            text: (gallery.selectedIndex >= 0 ? gallery.selectedIndex + 1 : "–") + " / " + gallery.count
        }
        trailing: [
            ChromeTextButton {
                objectName: "sort:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: gallery.count > 0
                text: root.sortBy ? root.sortBy + (root.sortDesc ? " ↑" : " ↓") : "sort"
                tip: "sort by key, name or time"
                onClicked: root.openSortMenu(this)
            }
        ]
    }
    PopMenu {
        id: sortMenu
        objectName: "sortMenu:" + root.viewId
        minWidth: 120
    }
    function openSortMenu(button) {
        var rows = []
        var bys = ["key", "name", "time"]
        for (var i = 0; i < bys.length; i++) {
            var by = bys[i]
            rows.push({ text: by, name: "sort:" + root.viewId + ":" + by,
                        detail: root.sortBy === by ? (root.sortDesc ? "↑" : "↓") : "",
                        action: (function (b) { return function () { if (root.bridge) root.bridge.setSort(b) } })(by) })
        }
        sortMenu.entries = rows
        sortMenu.openAt(button, button.width, button.height + 4, true)
    }
}
