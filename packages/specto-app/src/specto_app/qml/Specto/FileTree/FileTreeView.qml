// The file tree view's item: a list of the visible rows of the tree (the host's
// FileTreeModel), indented by depth, with a chevron on folders and a selection
// highlight. Clicks go to the host by row: plain click selects, Ctrl toggles, Shift
// ranges; the chevron (or a double-click) expands and collapses a folder (a folder is
// listed when it is first opened, #231); a double-click on a file opens it in a preview
// that follows the picks (story 35, #106, #122); a right-click opens the shell's context
// menu for the row, or the selection it is in (#154); a left drag drags the row (or the
// selection it is in) out to another view or app (#185). Hovering an image row shows
// its thumbnail beside the row (story 34). A long name is cut in the middle (the start
// and the extension stay) and shows in full in the hover tip. Details (#115, the user's
// `viewSettings.treeDetails`, toggled in the footer) add a second, smaller line: the
// modified time coloured by age, and the size; the ages are refreshed once a minute
// while details show. The details button sits in the tree's own footer (#122, the
// shared ViewFooter since #126).
// Type-to-search (#232, SPEC-38): the box at the top (typing in the focused tree starts
// it). While it has text the same list shows the search tree (the host's searchModel):
// the hits of the latest keystroke inside their folders, every folder above a hit open,
// rows like the tree's own (every gesture goes to the host by row, and it acts on the tree
// the list shows); a row's reveal button goes back to the tree at it; Enter opens the best
// hit; Escape clears the search, and the tree comes back where it was scrolled.
// Styled from the shell's `theme` context object when present.
import QtQuick
import QtQuick.Controls.Basic
import Specto.Shell as Shell

Item {
    id: root
    property string viewId: ""
    property string caption: ""
    property bool empty: false           // scanned and found no files (the host sets it)
    property QtObject bridge: null
    property var treeModel: null
    // type-to-search (#232, SPEC-38): the search tree, the box's text, the query's state
    // ("", "searching", "done") and the footer's line about the hits (the host sets them)
    property var searchModel: null
    property string searchText: ""
    property string searchState: ""
    property string searchNote: ""
    // the host's rule too (TreeSearch.active): blank text is no search
    readonly property bool searching: searchText.trim() !== ""
    // the list shows the search tree while searching; the tree's own scroll is kept for
    // when it comes back. The model is assigned here, not bound: a binding would swap it
    // before this handler could read the tree's contentY (a model swap scrolls a
    // ListView to the top), and the restore must follow the swap
    property real treeY: 0
    onSearchingChanged: {
        if (searching) {
            treeY = list.contentY
            list.model = root.searchModel
        } else {
            list.model = root.treeModel
            list.forceLayout()
            list.contentY = Math.max(0, Math.min(treeY, list.contentHeight - list.height))
        }
    }
    // the host cleared the search (reveal, new data): the box follows
    onSearchTextChanged: if (searchText === "" && searchField.text !== "") searchField.text = ""
    // details: the user's view setting (the app's, never the canvas's)
    readonly property bool details: typeof viewSettings !== "undefined" && viewSettings !== null
                                    && viewSettings.treeDetails
    // theme tokens only (ADR 0008); standing alone (no shell), the system palette stands in
    readonly property bool hasTheme: typeof theme !== "undefined" && theme !== null
    SystemPalette { id: sys }
    function tok(key) {
        if (hasTheme) return theme.colors[key]
        if (key.indexOf("text") >= 0 || key === "lock" || key.indexOf("age_") === 0) return sys.text
        if (key.indexOf("accent") >= 0 || key === "added" || key === "focus") return sys.highlight
        if (key.indexOf("border") >= 0) return sys.mid
        if (key.indexOf("surface") >= 0) return sys.base
        return sys.window
    }
    function font(kind) { return hasTheme ? theme.fonts[kind] : Qt.application.font.family }
    function metric(key, fallback) { return hasTheme ? theme.metrics[key] : fallback }
    // a selection received from a link (#168): the first row it names comes into view,
    // at the top when it was off screen; a row already shown does not move the list
    function reveal(row) {
        if (row < 0)
            return
        Qt.callLater(function () {
            var y = row * root.rowH
            if (y < list.contentY || y + root.rowH > list.contentY + list.height)
                list.positionViewAtIndex(row, ListView.Beginning)
        })
    }
    // the search box takes the keys: a printable key in the focused tree starts a search
    function startSearch(text) {
        searchField.forceActiveFocus()
        searchField.text = searchField.text + text
        searchField.cursorPosition = searchField.text.length
    }
    // the #126 design's row: 6 px, a 10 px chevron, 4 px, the 16 px type icon, 4 px, the
    // name; 10 px of indent per level
    readonly property int nameH: 20
    readonly property int rowH: details ? 34 : nameH
    readonly property int leftMargin: 6
    readonly property int indent: 10
    readonly property int chevronPx: 10
    readonly property int iconPx: 16
    readonly property int gap: 4
    readonly property int nameInset: chevronPx + gap + iconPx + gap
    // a deep row in a narrow tree gives up its indent before the name falls below this
    readonly property real nameMinW: Math.ceil(nameMinMetrics.advanceWidth)
    readonly property real rightMargin: 8
    readonly property int searchH: 26
    // the age colours, by the model's age (0: under 10 min .. 3: older; -1 unknown)
    readonly property var ageTokens: ["age_minutes", "age_hour", "age_day", "age_old"]
    TextMetrics {
        id: nameMinMetrics
        text: "abcd…wxyz"
        font.family: root.font("ui")
        font.pixelSize: root.metric("font_px", 13)
    }
    // the ages move on with the clock: once a minute, while the details show
    Timer {
        interval: 60000
        repeat: true
        running: root.details && root.visible && root.bridge !== null
        onTriggered: root.bridge.refreshAges()
    }
    onDetailsChanged: if (details && bridge) bridge.refreshAges()
    readonly property int thumbPx: 160
    // the hovered image row's thumbnail (empty url: none) and which row it belongs to
    property url hoverThumb: ""
    property string hoverPath: ""
    property real hoverY: 0

    clip: true
    // typing in the focused tree starts a search (#232); Escape in the tree clears one,
    // before the shell's window-wide Escape sees it
    Keys.onShortcutOverride: function (event) {
        if (event.key === Qt.Key_Escape && root.searching)
            event.accepted = true
    }
    Keys.onEscapePressed: function (event) {
        if (!root.searching) {
            event.accepted = false
            return
        }
        searchField.text = ""
    }
    Keys.onPressed: function (event) {
        if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            return
        if (event.text.length === 1 && event.text >= " " && event.key !== Qt.Key_Space) {
            root.startSearch(event.text)
            event.accepted = true
        }
    }

    Rectangle {
        anchors.fill: parent
        color: root.tok("surface")
    }

    // the search box (#232)
    Rectangle {
        id: searchBox
        objectName: "treeSearchBox:" + root.viewId
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 4
        anchors.bottomMargin: 0
        height: root.searchH
        radius: root.metric("radius_inner", 8)
        color: root.tok("surface_alt")
        border.color: searchField.activeFocus ? root.tok("focus") : root.tok("border")
        Shell.Icon {
            id: searchIcon
            name: "search"
            size: 14
            color: root.tok("text_faint")
            anchors.left: parent.left
            anchors.leftMargin: 7
            anchors.verticalCenter: parent.verticalCenter
        }
        TextInput {
            id: searchField
            objectName: "treeSearch:" + root.viewId
            anchors.left: searchIcon.right
            anchors.leftMargin: 6
            anchors.right: clearButton.visible ? clearButton.left : parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            clip: true
            selectByMouse: true
            color: root.tok("text")
            selectionColor: root.tok("accent_soft")
            selectedTextColor: root.tok("text")
            font.family: root.font("ui")
            font.pixelSize: root.metric("font_px", 13)
            Text {
                anchors.fill: parent
                visible: !searchField.text && !searchField.activeFocus
                text: "Search files"
                color: root.tok("text_faint")
                font: searchField.font
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            onTextChanged: {
                root.searchText = text
                if (root.bridge) root.bridge.search(text)
            }
            // Escape clears the search before the shell's window-wide Escape sees it
            Keys.onShortcutOverride: function (event) {
                if (event.key === Qt.Key_Escape && (text !== "" || activeFocus))
                    event.accepted = true
            }
            Keys.onEscapePressed: {
                text = ""
                root.forceActiveFocus()
            }
            Keys.onReturnPressed: function (event) {
                if (root.bridge) root.bridge.openFirstHit(Number(event.modifiers))
            }
            // Down: into the search tree, at its first hit (Up from the top row comes back)
            Keys.onDownPressed: {
                if (!root.searching || list.count === 0) return
                var first = root.bridge ? root.bridge.firstHitRow() : 0
                list.currentIndex = first >= 0 ? first : 0
                list.forceActiveFocus()
            }
        }
        Shell.Icon {
            id: clearButton
            objectName: "treeSearchClear:" + root.viewId
            visible: searchField.text !== ""
            name: "close"
            size: 12
            color: clearHover.hovered ? root.tok("text") : root.tok("text_faint")
            anchors.right: parent.right
            anchors.rightMargin: 7
            anchors.verticalCenter: parent.verticalCenter
            HoverHandler { id: clearHover }
            TapHandler {
                onTapped: {
                    searchField.text = ""
                    root.forceActiveFocus()
                }
            }
        }
    }

    ListView {
        id: list
        objectName: "fileTreeList:" + root.viewId
        anchors.top: searchBox.bottom
        anchors.topMargin: 2
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: footer.top
        clip: true
        // set once here, then swapped by root.onSearchingChanged
        model: root.treeModel
        boundsBehavior: Flickable.StopAtBounds
        // the keys, once Down from the search box gave the list the focus: Up and Down
        // move the current row (Up from the top row goes back to the box), Return opens a
        // file or toggles a folder, Escape clears the search
        Keys.onUpPressed: {
            if (currentIndex <= 0) searchField.forceActiveFocus()
            else decrementCurrentIndex()
        }
        Keys.onReturnPressed: function (event) {
            if (!root.bridge || currentIndex < 0 || currentItem === null) return
            if (currentItem.isDir) root.bridge.toggle(currentIndex)
            else root.bridge.open(currentIndex, Number(event.modifiers))
        }
        Keys.onShortcutOverride: function (event) {
            if (event.key === Qt.Key_Escape && root.searching)
                event.accepted = true
        }
        Keys.onEscapePressed: {
            searchField.text = ""
            root.forceActiveFocus()
        }
        delegate: Rectangle {
            id: row
            required property int index
            required property string path
            required property string name
            required property int depth
            required property bool isDir
            required property bool expanded
            required property bool selected
            required property int count
            required property string sizeText
            required property string stamp
            required property int age
            required property string icon
            objectName: "treeRow:" + root.viewId + ":" + path
            width: list.width
            height: root.rowH
            // the keys' current row reads as hovered while the list has the focus
            readonly property bool keyed: list.activeFocus && ListView.isCurrentItem
            color: selected ? root.tok("accent_soft")
                 : (rowHover.hovered || keyed ? root.tok("surface_hover") : "transparent")
            HoverHandler {
                id: rowHover
                // leaving one row and entering the next arrive in either order: a leave
                // only clears the thumbnail of the row it belongs to
                onHoveredChanged: {
                    if (hovered) {
                        root.hoverPath = row.path
                        root.hoverThumb = (root.bridge && !row.isDir) ? root.bridge.thumbUrl(row.path) : ""
                        root.hoverY = row.mapToItem(root, 0, 0).y
                    } else if (root.hoverPath === row.path) {
                        root.hoverPath = ""
                        root.hoverThumb = ""
                    }
                }
            }
            // the indent, given up on a row so deep the name would fall below its minimum
            readonly property real countW: countText.visible ? countText.implicitWidth + 4 : 0
            readonly property real nameLeft: Math.max(root.leftMargin, Math.min(root.leftMargin + depth * root.indent,
                width - root.rightMargin - root.nameMinW - countW - root.nameInset))
            // the chevron's span toggles a folder; anywhere else selects (Ctrl, Shift)
            readonly property real chevronLeft: nameLeft
            TapHandler {
                id: tap
                acceptedButtons: Qt.LeftButton
                // the modifiers live on the handler's point (an eventPoint has none), and
                // the point has let go of them by doubleTapped: the latest tap keeps them
                property int mods: 0
                onTapped: function (ep) {
                    if (!root.bridge) return
                    root.forceActiveFocus()  // the tree takes the keys (the Menu key, #154)
                    tap.mods = Number(tap.point.modifiers)
                    var x = ep.position.x
                    if (row.isDir && x >= row.chevronLeft - root.gap
                            && x <= row.chevronLeft + root.chevronPx + root.gap)
                        root.bridge.toggle(row.index)
                    else
                        root.bridge.click(row.index, tap.mods)
                }
                // a double-click opens a file's dedicated view, Ctrl+double-click the
                // contextual preview (SPEC-32); the gesture makes sure the file ends up
                // selected even though the Ctrl taps before it toggled the row
                onDoubleTapped: {
                    if (!root.bridge) return
                    if (row.isDir) root.bridge.toggle(row.index)
                    else root.bridge.open(row.index, tap.mods)
                }
            }
            // a left drag past the platform's drag distance drags the row out, or the
            // selection it is in (#185); a click still picks (the tap gives up only once
            // the drag starts), and the handler's grab keeps the list from flicking
            DragHandler {
                id: rowDrag
                target: null
                acceptedButtons: Qt.LeftButton
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad | PointerDevice.Stylus
                dragThreshold: Qt.styleHints.startDragDistance
                // never from an item: the canvas's hand (Space or middle drag) keeps its pan
                grabPermissions: PointerHandler.CanTakeOverFromHandlersOfDifferentType
                                 | PointerHandler.ApprovesTakeOverByAnything
                onActiveChanged: if (active && root.bridge) root.bridge.drag(row.index)
            }
            // right-click: the shell's context menu for this row (#154), at the pointer
            TapHandler {
                acceptedButtons: Qt.RightButton
                onTapped: function (ep) {
                    if (root.bridge) root.bridge.contextMenu(row.index, ep.scenePosition.x, ep.scenePosition.y)
                }
            }
            // while searching, back to the tree at this row: its folders listed, it shown
            Shell.ChromeButton {
                id: revealButton
                objectName: "treeRowReveal:" + root.viewId + ":" + row.path
                visible: root.searching && root.hasTheme && rowHover.hovered
                z: 2
                anchors.right: parent.right
                anchors.rightMargin: 6
                anchors.verticalCenter: nameLine.verticalCenter
                name: "target"
                tip: "show in the tree"
                onClicked: if (root.bridge) root.bridge.revealHit(row.index)
            }
            // the name's line: the top of the row (all of it without details)
            Item {
                id: nameLine
                width: parent.width
                height: root.nameH
            }
            Row {
                id: nameRow
                anchors.left: parent.left
                anchors.leftMargin: row.nameLeft
                anchors.right: parent.right
                anchors.rightMargin: root.rightMargin
                anchors.verticalCenter: nameLine.verticalCenter
                anchors.verticalCenterOffset: root.details ? 1 : 0
                spacing: root.gap
                // a folder's chevron; a file keeps the slot empty, so names line up
                Item {
                    width: root.chevronPx
                    height: root.chevronPx
                    anchors.verticalCenter: parent.verticalCenter
                    Loader {
                        active: row.isDir
                        anchors.fill: parent
                        sourceComponent: Shell.Icon {
                            objectName: "treeChevron:" + root.viewId + ":" + row.path
                            name: row.expanded ? "chevron-down" : "chevron-right"
                            size: root.chevronPx
                            color: root.tok("text_faint")
                        }
                    }
                }
                // the file or folder type's icon (#122): a 16 px design, rasterised at the
                // screen's pixel ratio from the SVG
                Image {
                    objectName: "treeIcon:" + root.viewId + ":" + row.path
                    width: root.iconPx
                    height: root.iconPx
                    anchors.verticalCenter: parent.verticalCenter
                    source: row.icon ? "icons/" + row.icon + ".svg" : ""
                    sourceSize.width: root.iconPx
                    sourceSize.height: root.iconPx
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    cache: true
                }
                Text {
                    id: nameText
                    objectName: "treeName:" + root.viewId + ":" + row.path
                    text: row.name
                    elide: Text.ElideMiddle
                    width: Math.min(implicitWidth, Math.max(0, parent.width - root.nameInset - row.countW))
                    color: row.selected ? root.tok("accent") : root.tok("text")
                    font.family: root.font("ui")
                    font.pixelSize: root.metric("font_px", 13)
                    font.bold: row.isDir
                    anchors.verticalCenter: parent.verticalCenter
                    // the full name, when it is cut short
                    ToolTip {
                        id: nameTip
                        visible: rowHover.hovered && nameText.truncated
                        text: row.name
                        delay: 0
                        timeout: -1
                        margins: 4
                        closePolicy: Popup.NoAutoClose
                        x: 0
                        y: parent.height + 4
                        padding: 4
                        leftPadding: 7
                        rightPadding: 7
                        background: Rectangle {
                            radius: root.metric("radius_inner", 8)
                            color: root.tok("text")
                        }
                        contentItem: Text {
                            objectName: "treeTip:" + root.viewId + ":" + row.path
                            text: nameTip.text
                            color: root.tok("surface")
                            font.family: root.font("ui")
                            font.pixelSize: root.metric("font_px_small", 11)
                        }
                    }
                }
                Text {
                    id: countText
                    visible: row.isDir && !row.expanded && row.count > 0
                    text: row.count
                    color: root.tok("text_faint")
                    font.family: root.font("mono")
                    font.pixelSize: root.metric("font_px_small", 11)
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            // details: the time (coloured by age) and the size, under the name; a folder's
            // own time and its item count once listed (#38; the model's sizeText carries
            // the tail for both, so no row's line is empty); empty texts while details
            // are off, so a row costs what it did before
            Row {
                objectName: "treeDetails:" + root.viewId + ":" + row.path
                visible: root.details
                x: row.nameLeft + root.nameInset
                // 2 px into the name's line box, whatever the font's line height (Segoe UI
                // at 13 px is a pixel taller under FreeType than under DirectWrite)
                y: Math.round(root.nameH / 2 + 1 + nameRow.height / 2) - 2
                width: Math.max(0, row.width - x - root.rightMargin)
                spacing: 4
                clip: true
                Text {
                    objectName: "treeStamp:" + root.viewId + ":" + row.path
                    text: root.details ? row.stamp : ""
                    color: row.age >= 0 ? root.tok(root.ageTokens[row.age]) : root.tok("text_faint")
                    font.family: root.font("ui")
                    font.pixelSize: root.metric("font_px_small", 11)
                }
                Text {
                    objectName: "treeDetailSize:" + root.viewId + ":" + row.path
                    text: root.details && row.sizeText ? (row.stamp ? "· " : "") + row.sizeText : ""
                    color: root.tok("text_faint")
                    font.family: root.font("ui")
                    font.pixelSize: root.metric("font_px_small", 11)
                }
            }
        }
    }

    // the empty state: a scanned folder with nothing in it (the tree keeps watching)
    Column {
        visible: root.empty && !root.searching
        anchors.centerIn: list
        width: Math.max(40, list.width - 32)
        spacing: 4
        Text {
            objectName: "fileTreeEmpty:" + root.viewId
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "This folder has no files yet"
            color: root.tok("text_muted")
            font.family: root.font("ui")
            font.pixelSize: root.metric("font_px", 13)
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Files you add to it appear here as they arrive."
            color: root.tok("text_faint")
            font.family: root.font("ui")
            font.pixelSize: root.metric("font_px_small", 11)
        }
    }
    // a search with no hits says which backend looked, so a miss can be understood (#232)
    Text {
        objectName: "fileTreeSearchEmpty:" + root.viewId
        visible: root.searching && root.searchState === "done" && list.count === 0
        anchors.centerIn: list
        width: Math.max(40, list.width - 32)
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: root.searchNote
        color: root.tok("text_muted")
        font.family: root.font("ui")
        font.pixelSize: root.metric("font_px", 13)
    }

    // the hover thumbnail: decoded scaled, off the GUI thread, beside the hovered row. A
    // popup, so it draws in the window's overlay: outside the tree's card when the window
    // has room to the right (`margins` keeps it inside the window), above every other card
    Popup {
        id: thumbPopup
        visible: root.hoverThumb !== "" && thumb.status === Image.Ready
        modal: false
        dim: false
        focus: false
        closePolicy: Popup.NoAutoClose
        margins: 4
        padding: 0
        x: root.width + 6
        y: Math.max(4, Math.min(root.hoverY - height / 2 + root.rowH / 2, list.height - height - 4))
        width: thumb.paintedWidth + 8
        height: thumb.paintedHeight + 8
        background: Rectangle {
            objectName: "fileTreeThumb:" + root.viewId
            radius: root.metric("radius_inner", 8)
            color: root.tok("surface")
            border.color: root.tok("border_strong")
        }
        contentItem: Item {
            Image {
                id: thumb
                anchors.centerIn: parent
                source: root.hoverThumb
                asynchronous: true
                cache: true
                sourceSize.width: root.thumbPx
                sourceSize.height: root.thumbPx
                fillMode: Image.PreserveAspectFit
                width: Math.min(root.thumbPx, implicitWidth || root.thumbPx)
                height: Math.min(root.thumbPx, implicitHeight || root.thumbPx)
            }
        }
    }

    // the footer (#126, the shared one): how many files, how many selected (the hits
    // while searching); the details toggle on the right
    Shell.ViewFooter {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        viewId: root.viewId
        Shell.FooterLabel {
            objectName: "fileTreeCaption:" + root.viewId
            width: Math.min(implicitWidth, footer.room)
            text: root.searching ? (root.searchState === "searching" && !root.searchNote ? "searching…" : root.searchNote)
                                 : root.caption
            elide: Text.ElideRight
            tone: "text_faint"
        }
        // the details (#115): a second line under each name, time and size; the user's
        // own view setting (viewSettings, every tree), not the canvas's
        trailing: [
            Shell.ChromeButton {
                id: detailsToggle
                objectName: "treeDetailsButton:" + root.viewId
                readonly property bool settable: typeof viewSettings !== "undefined" && viewSettings !== null
                visible: settable && root.hasTheme
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                name: "details"
                on: root.details
                tip: root.details ? "hide times and sizes" : "show times and sizes"
                onClicked: if (settable) viewSettings.toggleTreeDetails()
            }
        ]
    }
}
