import QtQuick

// The port capsules (#160) over the normal view: one PortCapsule per port pair
// (`controller.capsuleIds`, so an item survives a drag and animates its join and split),
// drawn where the pair's circles are now (`controller.capsuleMap`, the dragged rects
// included). Above the views and the wiring layer's curves (#82), whose link a capsule
// stands for there: a click on it opens the link (`openLink`). Hidden while the
// overworld shows or a view is maximised.
Item {
    id: root
    objectName: "portCapsules"
    signal openLink(string linkId)
    readonly property var pan: controller.pan
    readonly property real pad: theme.metrics.pad
    visible: !overworld.active && !controller.maximized
    clip: true

    // canvas px -> area px (the layer is anchored to the canvas area)
    function ax(cx) { return pad + cx - pan.x }
    function ay(cy) { return pad + cy - pan.y }

    Repeater {
        model: controller.capsuleIds
        delegate: PortCapsule {
            required property var modelData
            pair: controller.capsuleMap[modelData] || ({})
            toX: function (cx) { return root.ax(cx) }
            toY: function (cy) { return root.ay(cy) }
            openable: controller.wiring
            onOpenLink: root.openLink(pair.link)
        }
    }
}
