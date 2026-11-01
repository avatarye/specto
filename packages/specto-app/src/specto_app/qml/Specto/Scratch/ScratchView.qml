// The scratch pad's item (#184): a tab strip of the pad's named pages over the web page
// that shows the active page as markdown (the host parents that page's item into
// `scratchSlot:<view>`). The host sets `tabs` [{id, name, count}] and `page` (the shown
// page's id). In the strip a click shows a page, a double-click renames it, a drag moves
// it, x closes it and + adds one. A drop anywhere over the page (from a view, Explorer,
// a browser, text) is handed to the host per mime type and lands on the shown page; a
// drop on a tab lands on that page. Ctrl+V while the strip has the focus pastes; inside
// the page the page itself asks the host to paste. Every change is an undo step (the
// host's store), the page switch is navigation.
import QtQuick
import QtQuick.Controls.Basic
import Specto.Shell as Shell

Item {
    id: root
    objectName: "scratch:" + viewId
    property string viewId: ""
    property QtObject bridge: null
    // [{id, name, count}] in order, and the shown page's id
    property var tabs: []
    property string page: ""
    // the tab being dragged ("" when none) and the slot it would land in
    property string tabDragging: ""
    property int tabInsertAt: -1
    // a drop is over the page (the page shows a frame)
    property bool dropping: false
    // theme tokens only (ADR 0008); standing alone (no shell), the system palette stands in
    readonly property bool hasTheme: typeof theme !== "undefined" && theme !== null
    SystemPalette { id: sys }
    function tok(key) {
        if (hasTheme) return theme.colors[key]
        if (key.indexOf("text") >= 0) return sys.text
        if (key.indexOf("accent") >= 0) return sys.highlight
        if (key.indexOf("border") >= 0) return sys.mid
        if (key.indexOf("surface") >= 0) return sys.base
        return sys.window
    }
    function font(kind) { return hasTheme ? theme.fonts[kind] : Qt.application.font.family }
    function metric(key, fallback) { return hasTheme ? theme.metrics[key] : fallback }

    // the slot among the tabs under a point of `from` (before the tab under it, after
    // it in its right half)
    function tabSlotAt(from, x, y) {
        var p = tabRow.mapFromItem(from, x, y)
        for (var i = 0; i < tabRepeater.count; ++i) {
            var t = tabRepeater.itemAt(i)
            if (t && p.x < t.x + t.width / 2) return i
        }
        return tabs.length
    }
    // hand a drop's data to the host per mime type: on the shown page, or on `page`
    function dropOn(drop, page) {
        if (!bridge) return
        var data = {}
        for (var i = 0; i < drop.formats.length; ++i) {
            var f = drop.formats[i]
            if (f === "text/plain") data[f] = drop.text
            else if (f === "text/uri-list") data[f] = drop.urls.map(String).join("\r\n")
            else if (f === "application/x-specto-item+json" || f.indexOf("image/") === 0
                     || f.indexOf("FileContents") >= 0)
                data[f] = drop.getDataAsArrayBuffer(f)
        }
        if (drop.hasUrls && !("text/uri-list" in data)) data["text/uri-list"] = drop.urls.map(String).join("\r\n")
        if (drop.hasText && !("text/plain" in data)) data["text/plain"] = drop.text
        bridge.drop(data, page)
        drop.accept(Qt.CopyAction)
    }

    clip: true

    Keys.onPressed: (event) => {
        if (root.bridge && event.matches(StandardKey.Paste)) { root.bridge.paste(); event.accepted = true }
    }

    Rectangle {
        anchors.fill: parent
        color: root.tok("surface")
    }

    // the tab strip: one tab per page, then +
    Rectangle {
        id: strip
        objectName: "scratchTabs:" + root.viewId
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 30
        color: root.tok("surface_alt")
        TapHandler { onTapped: root.forceActiveFocus() }
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: root.tok("border")
        }
        Flickable {
            id: tabFlick
            anchors.fill: parent
            anchors.leftMargin: 6
            anchors.rightMargin: 6
            contentWidth: tabRow.width
            contentHeight: height
            clip: true
            interactive: contentWidth > width && root.tabDragging === ""
            boundsBehavior: Flickable.StopAtBounds
            Row {
                id: tabRow
                height: tabFlick.height
                spacing: 2
                Repeater {
                    id: tabRepeater
                    model: root.tabs
                    delegate: Rectangle {
                        id: tab
                        required property var modelData
                        required property int index
                        readonly property bool shown: modelData.id === root.page
                        property bool naming: false
                        objectName: "scratchTab:" + root.viewId + ":" + modelData.id
                        y: 4
                        height: tabRow.height - 4
                        width: Math.min(180, tabLine.implicitWidth + 16)
                        radius: 6
                        color: tabDrop.containsDrag ? root.tok("accent_soft")
                             : shown ? root.tok("surface")
                             : (tabHover.hovered ? root.tok("surface_hover") : "transparent")
                        border.color: shown ? root.tok("border") : "transparent"
                        opacity: root.tabDragging === modelData.id ? 0.5 : 1
                        HoverHandler { id: tabHover }
                        Row {
                            id: tabLine
                            x: 8
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 6
                            Text {
                                objectName: "scratchTabName:" + root.viewId + ":" + tab.modelData.id
                                visible: !tab.naming
                                text: tab.modelData.name
                                width: Math.min(implicitWidth, 120)
                                elide: Text.ElideRight
                                color: tab.shown ? root.tok("text") : root.tok("text_muted")
                                font.family: root.font("ui")
                                font.pixelSize: root.metric("font_px_small", 11)
                                font.bold: tab.shown
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            TextField {
                                id: tabNamer
                                objectName: "scratchTabNamer:" + root.viewId + ":" + tab.modelData.id
                                visible: tab.naming
                                width: 110
                                height: visible ? 20 : 0
                                padding: 2
                                font.family: root.font("ui")
                                font.pixelSize: root.metric("font_px_small", 11)
                                anchors.verticalCenter: parent.verticalCenter
                                function done() {
                                    if (!tab.naming) return
                                    tab.naming = false
                                    if (root.bridge && text.trim() !== "")
                                        root.bridge.renamePage(tab.modelData.id, text)
                                    root.forceActiveFocus()
                                }
                                onAccepted: done()
                                Keys.onEscapePressed: done()
                                onActiveFocusChanged: if (!activeFocus) done()
                            }
                            // close (an undo step; the host's notice says how to bring a
                            // non-empty page back)
                            Item {
                                objectName: "scratchTabClose:" + root.viewId + ":" + tab.modelData.id
                                visible: root.tabs.length > 1 && (tabHover.hovered || tab.shown) && !tab.naming
                                width: visible ? 12 : 0
                                height: 12
                                anchors.verticalCenter: parent.verticalCenter
                                Shell.Icon {
                                    visible: root.hasTheme
                                    anchors.centerIn: parent
                                    name: "close"
                                    size: 9
                                }
                                TapHandler { onTapped: if (root.bridge) root.bridge.closePage(tab.modelData.id) }
                            }
                        }
                        // click shows, double-click renames, a drag moves the tab
                        MouseArea {
                            anchors.fill: parent
                            anchors.rightMargin: root.tabs.length > 1 ? 18 : 0
                            enabled: !tab.naming
                            property real pressX: 0
                            onPressed: (mouse) => { pressX = mouse.x; root.forceActiveFocus() }
                            onPositionChanged: (mouse) => {
                                if (root.tabDragging === "" && Math.abs(mouse.x - pressX) > 6)
                                    root.tabDragging = tab.modelData.id
                                if (root.tabDragging !== "")
                                    root.tabInsertAt = root.tabSlotAt(this, mouse.x, mouse.y)
                            }
                            onReleased: {
                                var dragged = root.tabDragging !== ""
                                var to = root.tabInsertAt
                                root.tabDragging = ""
                                root.tabInsertAt = -1
                                if (!root.bridge) return
                                if (!dragged) { root.bridge.showPage(tab.modelData.id); return }
                                if (to > tab.index) to -= 1   // the slot in the list without it
                                if (to >= 0 && to !== tab.index) root.bridge.movePage(tab.modelData.id, to)
                            }
                            onCanceled: { root.tabDragging = ""; root.tabInsertAt = -1 }
                            onDoubleClicked: {
                                tabNamer.text = tab.modelData.name
                                tab.naming = true
                                tabNamer.forceActiveFocus()
                                tabNamer.selectAll()
                            }
                        }
                        // a drop on the tab lands on its page
                        DropArea {
                            id: tabDrop
                            objectName: "scratchTabDrop:" + root.viewId + ":" + tab.modelData.id
                            anchors.fill: parent
                            onEntered: (drag) => { drag.accept(Qt.CopyAction) }
                            onDropped: (drop) => { root.dropOn(drop, tab.modelData.id) }
                        }
                    }
                }
                // add a page
                Rectangle {
                    objectName: "scratchTabAdd:" + root.viewId
                    y: 4
                    width: 22
                    height: tabRow.height - 8
                    radius: 6
                    color: addHover.hovered ? root.tok("surface_hover") : "transparent"
                    Text {
                        anchors.centerIn: parent
                        text: "+"
                        color: root.tok("text_muted")
                        font.family: root.font("ui")
                        font.pixelSize: root.metric("font_px", 13)
                    }
                    HoverHandler { id: addHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: if (root.bridge) root.bridge.addPage() }
                }
            }
        }
        // where a dragged tab lands
        Rectangle {
            visible: root.tabInsertAt >= 0
            width: 2
            height: parent.height - 8
            y: 4
            color: root.tok("accent")
            x: {
                if (root.tabInsertAt < 0) return 0
                var last = root.tabInsertAt >= tabRepeater.count
                var t = tabRepeater.itemAt(Math.min(root.tabInsertAt, tabRepeater.count - 1))
                if (!t) return 6
                return tabFlick.x + t.x - tabFlick.contentX + (last ? t.width + 1 : -2)
            }
        }
    }

    // the page (the host parents the web view's item here)
    Item {
        id: slot
        objectName: "scratchSlot:" + root.viewId
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: strip.bottom
        anchors.bottom: parent.bottom
    }

    // drops from views (the drag and drop contract) and from other apps, over the page:
    // they land on the shown page (at the editor's caret while the source shows)
    DropArea {
        id: drops
        objectName: "scratchDrop:" + root.viewId
        anchors.fill: slot
        onEntered: (drag) => { drag.accept(Qt.CopyAction); root.dropping = true }
        onExited: root.dropping = false
        onDropped: (drop) => { root.dropping = false; root.dropOn(drop, "") }
    }
    Rectangle {
        objectName: "scratchDropFrame:" + root.viewId
        visible: root.dropping
        anchors.fill: slot
        anchors.margins: 2
        color: "transparent"
        radius: root.metric("radius_inner", 8)
        border.color: root.tok("accent")
        border.width: 2
    }
}
