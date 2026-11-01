// The pdf view's item: Qt's PdfMultiPageView (QtQuick.Pdf; pages rendered by QtPdf off
// the GUI thread) over a footer with the page counter and the controls. The host sets
// `source` and `caption` and hears back through `bridge` (page, zoom and fit).
//
// Input: the wheel scrolls through the pages; Ctrl+wheel zooms; PageDown/PageUp, Space,
// Backspace turn a page; Left/Right (and the step buttons at the footer's left) step to
// the previous / next pdf of the collection (#107: the pages keep
// the page keys every pdf reader uses); Home/End jump to the first/last page; +/- zoom;
// F or 0 fits the page width; P fits the whole page; 1 shows 1:1 (72 dpi points as
// pixels).
import QtQuick
import QtQuick.Pdf
import "../Shell"

Item {
    id: root
    property string viewId: ""
    property QtObject bridge: null
    property url source: ""
    property string caption: ""
    // where the view is in its collection (hosts.stepping sets them)
    property int stepIndex: -1
    property int stepCount: 0
    property var stepTips: []
    // the shown file's kind, while a preview renders it here (#126)
    property string kindIcon: ""
    // "width" | "page" | "none": what the zoom follows when the view is resized
    property string fit: "width"
    readonly property int pageCount: doc.pageCount
    readonly property int currentPage: pages.currentPage
    readonly property real zoom: pages.renderScale
    readonly property string status: doc.status === PdfDocument.Ready ? "ready"
        : doc.status === PdfDocument.Loading ? "loading"
        : doc.status === PdfDocument.Error ? "error" : "empty"
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

    PdfDocument {
        id: doc
        source: root.source
        // a turn later: PdfMultiPageView resets its scale to 1 when the source changes,
        // which can land after a Ready emitted while the source is being set
        onStatusChanged: if (status === PdfDocument.Ready) Qt.callLater(root.applyFit)
    }

    // -- zoom and fit -------------------------------------------------------------------
    function applyFit() {
        if (doc.status !== PdfDocument.Ready || viewport.width <= 0 || viewport.height <= 0) return
        // from the first page's point size directly: PdfMultiPageView's own scaleToWidth
        // reads a cached size that is still 1x1 pt in the Ready signal's own turn. The
        // table keeps a 2 px left margin and its vertical scroll bar inside the width.
        var ps = doc.pagePointSize(0)
        if (ps.width <= 0 || ps.height <= 0) return
        var w = viewport.width - 18, h = viewport.height - 8
        // about the top centre: the line at the top of the view stays there
        if (root.fit === "width") scaleAbout(viewport.width / 2, 0, w / ps.width)
        else if (root.fit === "page") scaleAbout(viewport.width / 2, 0, Math.min(w / ps.width, h / ps.height))
        report()
    }
    function fitWidth() { root.fit = "width"; applyFit() }
    function fitPage() { root.fit = "page"; applyFit() }
    // zoom about a point of the view (the pointer for Ctrl+wheel, the centre for keys
    // and buttons): the document point under it stays put
    function zoomAt(px, py, z) {
        root.fit = "none"
        scaleAbout(px, py, z)
        report()
    }
    function zoomTo(z) { zoomAt(viewport.width / 2, viewport.height / 2, z) }

    // PdfMultiPageView's table: one column as wide as the widest page (at least the
    // view), each page centred in its row. Its own zoom paths either keep the current
    // page's top (the navigator) or keep the content position (a bare renderScale), so
    // every page grows about its own centre (#94); here the content position is set so
    // the document point at (px, py) stays at (px, py). The table lays rows out from
    // the loaded ones (positions above them are estimates), so the page under the point
    // is measured on its delegate before and after the relayout.
    function table() {
        for (var i = 0; i < pages.children.length; ++i)
            if (pages.children[i].rowSpacing !== undefined) return pages.children[i]
        return null
    }
    function scaleAbout(px, py, z) {
        z = Math.max(0.1, Math.min(z, 10))
        var t = table()
        var s0 = pages.renderScale
        if (t === null) {
            pages.renderScale = z
            return
        }
        var tx = px - t.x, ty = py - t.y
        var cx = t.contentX + tx, cy = t.contentY + ty  // content item coordinates
        var row = -1, holder = null
        for (var r = t.topRow; r >= 0 && r <= t.bottomRow; ++r) {
            var it = t.itemAtCell(Qt.point(0, r))
            if (it !== null && (holder === null || it.y <= cy)) { holder = it; row = r }
        }
        if (holder === null || Math.abs(z - s0) < 1e-6) {
            pages.renderScale = z
            return
        }
        // the point under (px, py) on its page, in points (the page is centred in its row)
        var ps = doc.pagePointSize(row)
        var u = (cx - holder.x - (holder.width - ps.width * s0) / 2) / s0
        var v = (cy - holder.y) / s0
        t.cancelFlick()
        pages.renderScale = z
        t.forceLayout()
        holder = t.itemAtCell(Qt.point(0, row))
        if (holder === null) return
        t.contentX = holder.x + (holder.width - ps.width * z) / 2 + u * z - tx
        t.contentY = holder.y + v * z - ty
        t.returnToBounds()
    }
    function goToPage(p) {
        if (doc.pageCount <= 0) return
        pages.goToPage(Math.max(0, Math.min(doc.pageCount - 1, p)))
    }
    function step(delta) {
        if (root.bridge) root.bridge.step(delta)
    }
    function report() {
        if (root.bridge) root.bridge.viewChanged(pages.currentPage, pages.renderScale, root.fit)
    }

    Item {
        id: viewport
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        clip: true
        onWidthChanged: root.applyFit()
        onHeightChanged: if (root.fit === "page") root.applyFit()

        PdfMultiPageView {
            id: pages
            objectName: "pages"
            anchors.fill: parent
            document: doc
            onCurrentPageChanged: root.report()
        }

        // empty / loading / error hint
        Text {
            anchors.centerIn: parent
            visible: doc.status !== PdfDocument.Ready
            text: doc.status === PdfDocument.Loading ? "loading"
                : doc.status === PdfDocument.Error ? "cannot open pdf" : "no pdf"
            font.pixelSize: root.metric("font_px", 13)
            font.family: root.font("ui")
            color: root.tok("text_faint")
        }

        // above the pages: a press takes the keyboard focus, Ctrl+wheel zooms; plain
        // wheels and the pages' own handlers (text selection, links) pass underneath
        Item {
            anchors.fill: parent
            z: 1
            TapHandler {
                acceptedButtons: Qt.LeftButton
                gesturePolicy: TapHandler.WithinBounds
                onPressedChanged: if (pressed) root.forceActiveFocus()
            }
            WheelHandler {
                acceptedModifiers: Qt.ControlModifier
                onWheel: (w) => {
                    var d = w.angleDelta.y !== 0 ? w.angleDelta.y : w.angleDelta.x
                    if (d !== 0) root.zoomAt(w.x, w.y, pages.renderScale * Math.pow(1.2, d / 120))
                }
            }
        }
    }

    Keys.onPressed: (event) => {
        switch (event.key) {
        case Qt.Key_PageDown: case Qt.Key_Space: root.goToPage(pages.currentPage + 1); break
        case Qt.Key_PageUp: case Qt.Key_Backspace: root.goToPage(pages.currentPage - 1); break
        case Qt.Key_Left: root.step(-1); break
        case Qt.Key_Right: root.step(1); break
        case Qt.Key_Home: root.goToPage(0); break
        case Qt.Key_End: root.goToPage(doc.pageCount - 1); break
        case Qt.Key_0: case Qt.Key_F: root.fitWidth(); break
        case Qt.Key_P: root.fitPage(); break
        case Qt.Key_1: root.zoomTo(1.0); break
        case Qt.Key_Plus: case Qt.Key_Equal: root.zoomTo(pages.renderScale * 1.25); break
        case Qt.Key_Minus: root.zoomTo(pages.renderScale / 1.25); break
        default: return
        }
        event.accepted = true
    }

    // -- the footer (#126): step, position, the pdf's name; the page readout and one
    //    zoom button (fit width <-> whole page; Ctrl+wheel zooms) on the right
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
            objectName: "pdf:" + root.viewId + ":caption"
            width: Math.min(implicitWidth, footer.room)
            text: root.caption
            mono: true
            tone: "text"
        }
        trailing: [
            FooterLabel {
                objectName: "pdf:" + root.viewId + ":page"
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: doc.status === PdfDocument.Ready
                text: "p " + (pages.currentPage + 1) + " / " + doc.pageCount
                mono: true
            },
            ChromeTextButton {
                objectName: "zoom:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: doc.status === PdfDocument.Ready
                text: Math.round(pages.renderScale * 100) + "%"
                tip: root.fit === "width" ? "fit the whole page (P)" : "fit the width (F)"
                onClicked: root.fit === "width" ? root.fitPage() : root.fitWidth()
            }
        ]
    }
}
