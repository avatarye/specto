import QtQuick

// The wiring layer (#82, glossary): a window-level layer over the canvas area that the
// backtick/tilde key toggles (`controller.wiring`; Esc leaves it too). While it shows the
// views dim (ViewSlot lowers its card's opacity, the port circles stay clear) and every
// link is drawn: the curve in the chain's colour from the source's output circle to the
// input circle the link occupies, a link node (a dot, #121) on each that opens the link
// view (Delete, Ask agent to change, the overworld's). The layer takes no mouse
// events itself, so pan, select and the views' own gestures keep working under it, and
// the curves follow a view while it is dragged (controller.wireMap). The overworld
// draws its own wires and hides the layer while it shows.
Item {
    id: root
    objectName: "wiringLayer"
    readonly property var pan: controller.pan
    readonly property real pad: theme.metrics.pad
    readonly property bool on: controller.wiring && !overworld.active
    visible: on
    // the layer goes (the key, Esc, the overworld): its popovers and selection go with it
    onOnChanged: if (!on) { linkView.close(); overworld.selectLink("") }
    // Esc (the controller's event filter hands it over while the layer shows): the
    // link view closes first, the layer leaves on the next press
    Connections {
        target: controller
        function onWiringEscape() {
            if (linkView.opened) linkView.close()
            else controller.setWiring(false)
        }
    }

    // canvas px -> area px (the layer is anchored to the canvas area)
    function ax(cx) { return pad + cx - pan.x }
    function ay(cy) { return pad + cy - pan.y }
    // a port capsule stands for a joined link here (#160): a click on it opens the link
    function openLink(id) { linkView.openFor(id) }

    // one curve and one node per link while the layer shows (nothing of it exists
    // otherwise: the normal view draws no curves, #81); the items survive a drag
    Repeater {
        model: root.on ? controller.wireIds : []
        delegate: Item {
            id: entry
            required property var modelData
            readonly property var wire: controller.wireMap[modelData] || ({})
            // a port capsule joins the link's two circles (#160): it is the wire
            readonly property var capsule: controller.capsuleMap[modelData]
            readonly property bool joined: !!capsule && capsule.joined === true
            anchors.fill: parent

            LinkCurve {
                objectName: "wire:" + entry.modelData
                visible: !entry.joined
                wire: entry.wire
                faint: false
                strokeWidth: 2.5
                selected: overworld.selectedLink === entry.modelData
                toX: function (cx) { return root.ax(cx) }
                toY: function (cy) { return root.ay(cy) }
            }
            // the node never covers a view's header or a port circle (#90, the
            // overworld's rule): the controller places it from the dot's size
            LinkNode {
                readonly property var place: controller.nodePlaces[entry.modelData]
                objectName: "wireNode:" + entry.modelData
                visible: !entry.joined
                wire: entry.wire
                ui: 1
                x: root.ax(place ? place.x : (entry.wire.nx || 0)) - width / 2
                y: root.ay(place ? place.y : (entry.wire.ny || 0)) - height / 2
                function report() { controller.setNodeSize(entry.modelData, width, height) }
                onWidthChanged: report()
                onHeightChanged: report()
                Component.onCompleted: report()
                onOpenLink: linkView.openFor(entry.modelData)
            }
        }
    }

    LinkView { id: linkView }
}
