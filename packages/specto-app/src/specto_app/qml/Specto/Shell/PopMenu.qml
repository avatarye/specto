import QtQuick
import QtQuick.Controls

// A small context/dropdown menu in the Calm Canvas style (design #123 section 1: 28 px
// rows, the detail right-aligned and faint, a hovered row in surface_hover). ``entries`` is
// a list of { text, detail?, enabled?, separator?, name?, icon?, bold?, mono?, keepOpen?, action }
// objects (name: objectName override, rows are "menu:<name>"; icon: a shell icon name, a
// 14 px column every row lines up with when any row has one; mono: the detail in the
// monospace font; keepOpen: a tap leaves the menu open, for a submenu row). ``action``
// gets the row item. ``rowHovered(entry, row)`` fires as the pointer enters a row.
// ``openAt(item, x, y[, rightEdge])`` places it at a point of ``item`` (window coordinates through
// the overlay). Opened with ``keyboard`` set (the Menu key, #154) it takes the focus: Up and
// Down move the current row (the first enabled one to start), Enter runs it, Esc closes;
// Right runs a submenu row (keepOpen), Left closes a menu that is a submenu (``sub``).
Popup {
    id: root
    property var entries: []
    property string heading: ""
    property real minWidth: 180
    property bool keyboard: false        // opened from the keyboard: the menu takes the keys
    property bool sub: false             // a submenu: Left closes it
    property int current: -1             // the keyboard's row (-1: none)
    readonly property real iconInset: hasIcons ? 14 + 8 : 0
    readonly property bool hasIcons: {
        for (var i = 0; i < entries.length; i++) if (entries[i].icon) return true
        return false
    }
    signal rowHovered(var entry, var row)
    parent: Overlay.overlay
    modal: false
    dim: false
    topPadding: 6
    bottomPadding: 6
    leftPadding: 1
    rightPadding: 1
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    focus: keyboard
    // the opener sets ``keyboard`` on every open (the menus that never do stay pointer-only)
    onAboutToShow: current = keyboard ? next(-1, 1) : -1

    // the next enabled row from ``from`` in direction ``d`` (``from`` itself when none)
    function next(from, d) {
        for (var i = from + d; i >= 0 && i < entries.length; i += d) {
            var e = entries[i]
            if (e.separator !== true && e.enabled !== false) return i
        }
        return from
    }
    function run(i) {
        var e = entries[i]
        if (!e || e.separator === true || e.enabled === false) return
        if (e.keepOpen !== true) root.close()
        if (e.action) e.action(rows.itemAt(i))
    }
    width: Math.max(minWidth, col.implicitWidth + leftPadding + rightPadding)
    height: col.implicitHeight + topPadding + bottomPadding

    // ``rightEdge``: (px, py) is where the menu's top-right corner goes; the menu's width
    // is only known once its rows are laid out, so the place stays bound to it
    function openAt(item, px, py, rightEdge) {
        var p = item.mapToItem(Overlay.overlay, px, py)
        root.x = Qt.binding(function () {
            return Math.max(4, Math.min(rightEdge ? p.x - root.width : p.x,
                                        Overlay.overlay.width - root.width - 4))
        })
        root.y = Qt.binding(function () {
            return Math.max(4, Math.min(p.y, Overlay.overlay.height - root.height - 4))
        })
        root.open()
    }
    function openBelow(item) { openAt(item, 0, item.height + 4) }

    background: Rectangle {
        radius: theme.metrics.radius_inner
        color: theme.colors.surface
        border.color: theme.colors.border
        SoftShadow { radius: parent.radius; blur: 0.6; strength: 0.16 }
    }

    contentItem: Column {
        id: col
        focus: true
        Keys.onUpPressed: root.current = root.next(root.current < 0 ? root.entries.length : root.current, -1)
        Keys.onDownPressed: root.current = root.next(root.current, 1)
        Keys.onReturnPressed: root.run(root.current)
        Keys.onEnterPressed: root.run(root.current)
        Keys.onRightPressed: {
            var e = root.entries[root.current]
            if (e && e.keepOpen === true) root.run(root.current)
        }
        Keys.onLeftPressed: if (root.sub) root.close()
        Text {
            visible: root.heading !== ""
            text: root.heading
            color: theme.colors.text_faint
            font.family: theme.fonts.ui
            font.pixelSize: theme.metrics.font_px_small
            leftPadding: 12
            topPadding: 2
            bottomPadding: 4
        }
        Repeater {
            id: rows
            model: root.entries
            delegate: Item {
                id: row
                required property var modelData
                required property int index
                readonly property bool separator: modelData.separator === true
                readonly property bool on: modelData.enabled !== false
                readonly property string key: modelData.name || modelData.text || ""
                objectName: separator ? "" : "menu:" + key
                // the widest row sets the menu's width; every row then spans it (`band`)
                width: Math.max(root.minWidth - root.leftPadding - root.rightPadding,
                                separator ? 0 : label.implicitWidth + detail.width + 24 + 24 + root.iconInset)
                height: separator ? 11 : 28
                Rectangle {
                    visible: row.separator
                    anchors.verticalCenter: parent.verticalCenter
                    width: col.width
                    height: 1
                    color: theme.colors.border
                }
                Rectangle {
                    id: band
                    visible: !row.separator
                    width: col.width
                    height: parent.height
                    color: (rowHover.hovered || root.current === row.index) && row.on
                           ? theme.colors.surface_hover : "transparent"
                    opacity: row.on ? 1 : 0.45
                    HoverHandler {
                        id: rowHover
                        onHoveredChanged: if (hovered) {
                            if (root.keyboard && row.on) root.current = row.index
                            root.rowHovered(row.modelData, row)
                        }
                    }
                    TapHandler {
                        enabled: row.on
                        // the press is the menu's alone (an exclusive grab): a control
                        // under the menu never sees it, nor taps on the release (#126)
                        gesturePolicy: TapHandler.WithinBounds
                        onTapped: {
                            if (row.modelData.keepOpen !== true) root.close()
                            if (row.modelData.action) row.modelData.action(row)
                        }
                    }
                    Icon {
                        visible: !!row.modelData.icon
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        name: row.modelData.icon || ""
                        size: 14
                        color: theme.colors.text_muted
                    }
                    Text {
                        id: label
                        objectName: "menuText:" + row.key
                        anchors.left: parent.left
                        anchors.leftMargin: 12 + root.iconInset
                        anchors.right: detail.left
                        anchors.rightMargin: 24
                        anchors.verticalCenter: parent.verticalCenter
                        elide: Text.ElideRight
                        text: row.modelData.text || ""
                        color: theme.colors.text
                        font.family: theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px
                        font.bold: row.modelData.bold === true
                    }
                    Text {
                        id: detail
                        objectName: "menuDetail:" + row.key
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.min(implicitWidth, 280)  // a long path elides in the middle
                        elide: Text.ElideMiddle
                        visible: !!row.modelData.detail
                        text: row.modelData.detail || ""
                        color: theme.colors.text_faint
                        font.family: row.modelData.mono === true ? theme.fonts.mono : theme.fonts.ui
                        font.pixelSize: theme.metrics.font_px_small
                    }
                }
            }
        }
    }
}
