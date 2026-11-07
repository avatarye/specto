// The image view's item: one full-resolution image (QML Image, decoded by Qt's image
// loader thread off the GUI thread) with wheel zoom around the cursor, drag pan, fit /
// 1:1, next / previous, and the shared footer (#126). The host sets `source` and `caption` and
// hears back through `bridge` (step, status, zoom and centre). A restored view arrives
// as navZoom / navCx / navCy / navFit plus a bumped navSerial, applied once the image
// is ready.
//
// Input: wheel zooms around the cursor; drag pans; double-click toggles fit and 1:1 at
// the cursor; Ctrl+drag drags the image out to another view or app, Shift+drag marks a
// crop (in image pixels, it follows pan and zoom) and a drag that starts inside the crop
// drags the crop out as a picture; a click outside it or Esc clears it (#185); Left/Right and PageUp/PageDown (and the footer's step buttons) step the
// item (#107); Space steps forward; 0 or F fits; 1 shows 1:1; Home/End jump to the
// first/last item; R, G, B, A (L) solo a channel, the same key again shows them all
// (#171, only while the view has the focus).
//
// Channels (#171): the host sets `channels` (views/channels.py: offered, shown, mode and
// the shader's uniforms). The footer offers a toggle per channel the image has; while
// the view shows anything but its plain colours (a solo, a mix, transparency over the
// checkerboard) a ShaderEffect samples the decoded image's own texture in its place, at
// the same place and zoom: no re-decode, panning moves one quad.
import QtQuick
import "../Shell"

Item {
    id: root
    property string viewId: ""
    property QtObject bridge: null
    property url source: ""
    // paused (SPEC-3): the Image lets go of its picture and its texture; zoom, centre,
    // crop and the reported status stay until it is shown again and decodes anew
    property bool released: false
    onReleasedChanged: {
        if (released) {
            // the view as it was comes back through the restore path once ready again
            if (img.status === Image.Ready) {
                navZoom = zoom; navCx = centerX(); navCy = centerY(); navFit = fit
                navPending = true
            }
            img.source = ""
        } else {
            img.source = Qt.binding(function () { return root.source })
        }
    }
    property string caption: ""
    // where the view is in its collection (hosts.stepping sets them)
    property int stepIndex: -1
    property int stepCount: 0
    property var stepTips: []
    // the shown file's kind, while a preview renders it here (#126)
    property string kindIcon: ""
    property bool fit: true
    property real zoom: 1.0
    property real fitZoom: 1.0
    // navigation state from the document (state.zoom / center / fit), applied on demand
    property real navZoom: 1.0
    property real navCx: 0.5
    property real navCy: 0.5
    property bool navFit: true
    property int navSerial: 0
    property bool navPending: false
    // the crop (#185): a rectangle of the image in its own pixels; empty: none
    property rect crop: Qt.rect(0, 0, 0, 0)
    readonly property bool hasCrop: crop.width >= 1 && crop.height >= 1
    onSourceChanged: {
        crop = Qt.rect(0, 0, 0, 0)
        if (released) navPending = false  // another picture: it opens as a new one would
    }
    // a viewport point in image pixels (unclamped)
    function imageX(vx) { return (vx - img.x) / root.zoom }
    function imageY(vy) { return (vy - img.y) / root.zoom }
    function inCrop(vx, vy) {
        var x = imageX(vx), y = imageY(vy)
        return hasCrop && x >= crop.x && x <= crop.x + crop.width && y >= crop.y && y <= crop.y + crop.height
    }
    // the crop from two viewport points, clamped to the image and snapped to whole pixels
    function cropFrom(ax, ay, bx, by) {
        var w = img.implicitWidth, h = img.implicitHeight
        var x0 = Math.max(0, Math.min(w, Math.floor(Math.min(imageX(ax), imageX(bx)))))
        var y0 = Math.max(0, Math.min(h, Math.floor(Math.min(imageY(ay), imageY(by)))))
        var x1 = Math.max(0, Math.min(w, Math.ceil(Math.max(imageX(ax), imageX(bx)))))
        var y1 = Math.max(0, Math.min(h, Math.ceil(Math.max(imageY(ay), imageY(by)))))
        return Qt.rect(x0, y0, x1 - x0, y1 - y0)
    }
    onNavSerialChanged: { navPending = true; applyNav() }
    // the channel view (host-set, #171)
    property var channels: ({ offered: "", shown: "", mode: "normal", active: false,
                              mask: [1, 1, 1, 1], pick: [0, 0, 0, 0], grey: 0, checker: 0 })
    // the shader draws only on a GPU backend (the tests' software one draws the image)
    readonly property bool gpu: GraphicsInfo.api !== GraphicsInfo.Software
    readonly property bool channelFx: gpu && !!channels && channels.active === true
                                      && img.status === Image.Ready
    function channelClicked(c, mix) {
        if (root.bridge && root.channels.offered.indexOf(c) >= 0) root.bridge.channelClicked(c, mix)
    }
    readonly property int imageWidth: img.implicitWidth
    readonly property int imageHeight: img.implicitHeight
    readonly property string status: statusName(img.status)
    function statusName(s) {
        return s === Image.Ready ? "ready" : s === Image.Loading ? "loading"
             : s === Image.Error ? "error" : "empty"
    }
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
    focus: true
    HoverHandler { id: hover }

    Rectangle {
        anchors.fill: parent
        color: root.tok("bg_tint")
    }

    // -- the picture ------------------------------------------------------------------
    function fitImage() {
        if (img.implicitWidth <= 0 || img.implicitHeight <= 0) return
        var z = Math.min(viewport.width / img.implicitWidth, viewport.height / img.implicitHeight)
        root.fitZoom = z
        root.zoom = z
        img.x = (viewport.width - img.implicitWidth * z) / 2
        img.y = (viewport.height - img.implicitHeight * z) / 2
        root.fit = true
        report()
    }
    function oneToOne(cx, cy) {
        zoomAt(1.0, cx === undefined ? viewport.width / 2 : cx, cy === undefined ? viewport.height / 2 : cy)
        root.fit = false
    }
    function zoomAt(z, cx, cy) {
        z = Math.max(Math.min(root.fitZoom, 1.0) * 0.25, Math.min(z, 32))
        var f = z / root.zoom
        img.x = cx - (cx - img.x) * f
        img.y = cy - (cy - img.y) * f
        root.zoom = z
        root.fit = false
        clampPan()
        report()
    }
    function clampPan() {
        var w = img.implicitWidth * root.zoom, h = img.implicitHeight * root.zoom
        if (w <= viewport.width) img.x = (viewport.width - w) / 2
        else img.x = Math.min(0, Math.max(viewport.width - w, img.x))
        if (h <= viewport.height) img.y = (viewport.height - h) / 2
        else img.y = Math.min(0, Math.max(viewport.height - h, img.y))
    }
    // the image point (0..1 of its width and height) under the viewport centre
    function centerX() {
        var w = img.implicitWidth * root.zoom
        return w > 0 ? (viewport.width / 2 - img.x) / w : 0.5
    }
    function centerY() {
        var h = img.implicitHeight * root.zoom
        return h > 0 ? (viewport.height / 2 - img.y) / h : 0.5
    }
    function applyNav() {
        if (img.status !== Image.Ready || viewport.width <= 0 || viewport.height <= 0) return
        navPending = false
        if (navFit) { fitImage(); return }
        root.zoom = Math.max(0.01, Math.min(navZoom, 32))
        img.x = viewport.width / 2 - navCx * img.implicitWidth * root.zoom
        img.y = viewport.height / 2 - navCy * img.implicitHeight * root.zoom
        root.fit = false
        clampPan()
        report()
    }
    function report() {
        if (root.bridge) root.bridge.viewChanged(root.zoom, root.fit, centerX(), centerY())
    }
    function step(delta) {
        if (root.bridge) root.bridge.step(delta)
    }
    function vec4(a) {
        return a && a.length === 4 ? Qt.vector4d(a[0], a[1], a[2], a[3]) : Qt.vector4d(0, 0, 0, 0)
    }

    Item {
        id: viewport
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        clip: true
        onWidthChanged: if (root.fit) root.fitImage(); else root.clampPan()
        onHeightChanged: if (root.fit) root.fitImage(); else root.clampPan()

        Image {
            id: img
            objectName: "image"
            source: root.source
            asynchronous: true
            cache: false
            autoTransform: true
            smooth: true
            mipmap: true
            fillMode: Image.Pad
            transformOrigin: Item.TopLeft
            scale: root.zoom
            // the shader draws it while channels show (its texture stays the source)
            opacity: root.channelFx ? 0 : 1
            onStatusChanged: {
                if (root.released) return  // let go while paused: nothing to report
                if (status === Image.Ready) {
                    if (root.navPending) root.applyNav()
                    else if (root.fit) root.fitImage()
                    else root.clampPan()
                }
                if (root.bridge) root.bridge.imageStatus(root.statusName(status), implicitWidth, implicitHeight)
            }
        }

        // the channel display (#171): the image's texture through shaders/channels.frag
        ShaderEffect {
            id: channelEffect
            objectName: "channels:" + root.viewId
            x: img.x
            y: img.y
            width: img.implicitWidth * root.zoom
            height: img.implicitHeight * root.zoom
            visible: root.channelFx
            property variant source: img
            property vector4d mask: root.vec4(root.channels.mask)
            property vector4d pick: root.vec4(root.channels.pick)
            property real grey: root.channels.grey || 0
            property real checker: root.channels.checker || 0
            readonly property real cell: 8
            property vector2d cells: Qt.vector2d(width / cell, height / cell)
            property color dark: root.tok("checker_dark")
            property color light: root.tok("checker_light")
            fragmentShader: "shaders/channels.frag.qsb"
        }

        // the crop (#185), where it is on the image now
        Rectangle {
            objectName: "crop:" + root.viewId
            visible: root.hasCrop
            x: img.x + root.crop.x * root.zoom
            y: img.y + root.crop.y * root.zoom
            width: root.crop.width * root.zoom
            height: root.crop.height * root.zoom
            color: Qt.alpha(root.tok("accent"), 0.12)
            border.color: root.tok("accent")
            border.width: 1
        }

        // empty / loading / error hint
        Text {
            anchors.centerIn: parent
            visible: img.status !== Image.Ready
            text: img.status === Image.Loading ? "loading" : img.status === Image.Error ? "cannot load image" : "no image"
            font.pixelSize: root.metric("font_px", 13)
            font.family: root.font("ui")
            color: root.tok("text_faint")
        }

        MouseArea {
            id: mouse
            anchors.fill: parent
            property real lastX: 0
            property real lastY: 0
            property bool dragging: false
            // what this press does: pan, crop (Shift), drag (Ctrl: the image out), dragCrop
            // (a press inside the crop: the crop out); the drags start past the platform's
            // drag distance
            property string mode: ""
            property real pressX: 0
            property real pressY: 0
            cursorShape: mode === "crop" ? Qt.CrossCursor : dragging ? Qt.ClosedHandCursor : Qt.ArrowCursor
            acceptedButtons: Qt.LeftButton
            onPressed: (m) => {
                root.forceActiveFocus(); lastX = m.x; lastY = m.y; dragging = false
                pressX = m.x; pressY = m.y
                if (img.status !== Image.Ready) mode = "pan"
                else if (m.modifiers & Qt.ShiftModifier) mode = "crop"
                else if (m.modifiers & Qt.ControlModifier) mode = "drag"
                else if (root.inCrop(m.x, m.y)) mode = "dragCrop"
                else mode = "pan"
            }
            onPositionChanged: (m) => {
                if (!(m.buttons & Qt.LeftButton)) return
                if (mode === "crop") {
                    root.crop = root.cropFrom(pressX, pressY, m.x, m.y)
                    return
                }
                if (mode === "drag" || mode === "dragCrop") {
                    if (Math.abs(m.x - pressX) + Math.abs(m.y - pressY) < Qt.styleHints.startDragDistance) return
                    var what = mode
                    mode = ""
                    if (!root.bridge) return
                    if (what === "drag") root.bridge.dragImage()
                    else root.bridge.dragCrop(root.crop.x, root.crop.y, root.crop.width, root.crop.height)
                    return
                }
                if (mode !== "pan") return
                var dx = m.x - lastX, dy = m.y - lastY
                if (!dragging && Math.abs(dx) < 3 && Math.abs(dy) < 3) return
                dragging = true
                img.x += dx
                img.y += dy
                lastX = m.x
                lastY = m.y
                root.clampPan()
            }
            onReleased: {
                if (mode === "pan" && !dragging && root.hasCrop) root.crop = Qt.rect(0, 0, 0, 0)
                mode = ""
                if (dragging) { dragging = false; root.report() }
            }
            onDoubleClicked: (m) => { if (root.fit || Math.abs(root.zoom - 1.0) > 1e-3) root.oneToOne(m.x, m.y); else root.fitImage() }
            onWheel: (w) => {
                var d = w.angleDelta.y !== 0 ? w.angleDelta.y : w.angleDelta.x
                if (d === 0) return
                root.zoomAt(root.zoom * Math.pow(1.2, d / 120), w.x, w.y)
                w.accepted = true
            }
        }
    }

    readonly property var channelKeys: ({ [Qt.Key_R]: "r", [Qt.Key_G]: "g", [Qt.Key_B]: "b",
                                          [Qt.Key_A]: "a", [Qt.Key_L]: "l" })
    // the shell's window-wide Escape shortcut would take the key first: claim it while a
    // crop shows, so Esc clears the crop (#185)
    Keys.onShortcutOverride: (event) => {
        if (event.key === Qt.Key_Escape && root.hasCrop) event.accepted = true
    }
    Keys.onPressed: (event) => {
        var ch = root.channelKeys[event.key]
        if (ch !== undefined) {
            if (event.modifiers !== Qt.NoModifier || root.channels.offered.indexOf(ch) < 0) return
            root.channelClicked(ch, false)
            event.accepted = true
            return
        }
        switch (event.key) {
        case Qt.Key_Left: case Qt.Key_Up: case Qt.Key_PageUp: root.step(-1); break
        case Qt.Key_Right: case Qt.Key_Down: case Qt.Key_PageDown: case Qt.Key_Space: root.step(1); break
        case Qt.Key_Home: root.step(-1000000000); break
        case Qt.Key_End: root.step(1000000000); break
        case Qt.Key_0: case Qt.Key_F: root.fitImage(); break
        case Qt.Key_1: root.oneToOne(); break
        case Qt.Key_Plus: case Qt.Key_Equal: root.zoomAt(root.zoom * 1.25, viewport.width / 2, viewport.height / 2); break
        case Qt.Key_Minus: root.zoomAt(root.zoom / 1.25, viewport.width / 2, viewport.height / 2); break
        case Qt.Key_Escape: if (!root.hasCrop) return; root.crop = Qt.rect(0, 0, 0, 0); break
        default: return
        }
        event.accepted = true
    }

    // -- the footer (#126): step, position, file name; the resolution and one zoom button
    //    (fit <-> 1:1; the wheel zooms) on the right
    ViewFooter {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        viewId: root.viewId
        stepIndex: root.stepIndex
        stepCount: root.stepCount
        stepTips: root.stepTips
        kindIcon: root.kindIcon
        onStepped: (delta) => root.step(delta)
        FooterLabel {
            objectName: "image:" + root.viewId + ":caption"  // scripted flows read it
            width: Math.min(implicitWidth, footer.room)
            text: root.caption
            mono: true
            tone: "text"
        }
        trailing: [
            Row {
                id: channelRow
                objectName: "channelToggles:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                spacing: 1
                visible: img.status === Image.Ready && root.channels.offered.length > 0
                Repeater {
                    model: root.channels.offered.split("")
                    ChannelToggle {
                        required property string modelData
                        objectName: "channel:" + root.viewId + ":" + modelData
                        channel: modelData
                        on: root.channels.mode !== "normal" && root.channels.shown.indexOf(modelData) >= 0
                        off: root.channels.shown.indexOf(modelData) < 0
                        tip: modelData.toUpperCase() + ": solo (" + modelData.toUpperCase()
                             + "); Ctrl+click: in the mix; right-click: menu"
                        onClicked: (mix) => { root.forceActiveFocus(); root.channelClicked(modelData, mix) }
                        onMenu: root.openChannelMenu(this, modelData)
                    }
                }
            },
            FooterLabel {
                objectName: "resolution:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: img.status === Image.Ready
                text: img.implicitWidth + "×" + img.implicitHeight
                mono: true
            },
            ChromeTextButton {
                objectName: "zoom:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: img.status === Image.Ready
                text: Math.round(root.zoom * 100) + "%"
                tip: root.fit ? "actual pixels (1)" : "fit (0)"
                onClicked: root.fit ? root.oneToOne() : root.fitImage()
            }
        ]
    }

    // a toggle's menu (#171): solo it, toggle it in the mix, or every channel again;
    // rows are menu:channel-solo-<c>, menu:channel-mix-<c>, menu:channel-all
    PopMenu { id: channelMenu; objectName: "channelMenu:" + root.viewId; minWidth: 170 }
    function openChannelMenu(button, c) {
        var C = c.toUpperCase()
        var shown = root.channels.shown.indexOf(c) >= 0
        var mixed = root.channels.mode !== "normal"
        channelMenu.entries = [
            { text: "Solo " + C, detail: C, name: "channel-solo-" + c, mono: true,
              action: function () { root.forceActiveFocus(); root.channelClicked(c, false) } },
            { text: (shown ? "Remove " + C + " from" : "Add " + C + " to") + " the mix",
              detail: "Ctrl+click", name: "channel-mix-" + c,
              action: function () { root.forceActiveFocus(); root.channelClicked(c, true) } },
            { separator: true },
            { text: "All channels", name: "channel-all", enabled: mixed,
              action: function () { root.forceActiveFocus(); if (root.bridge) root.bridge.channelReset() } }
        ]
        channelMenu.openAt(button, button.width, -(3 * 28 + 12 + 12) - 4, true)
    }
}
