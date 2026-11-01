import QtQuick

// The walked link's pulse (#174, motion notes "pulse"): dots running along the link's
// curve, out port to in port, at a constant screen speed and size (220 px/s, 10 px, one
// every 180 ms, at most 12 on the wire), arc-length parameterised so they keep an even
// spacing round the bends. Nothing travels inside views: no item is followed. At the link
// node (the curve's middle) an intaker drops dots in the real ratio (they turn ghost,
// drift 8 px down and fade in 180 ms); a time map or a Python link rings as each dot
// passes (1 -> 1.6, fading, 240 ms); the in port rings once per dot (120 ms). `wire` is
// a controller.wireMap entry; `toX`/`toY` map canvas px to this item's px (1:1 here).
// Reduced motion: no dots run; a static gradient of tokens lies along the path.
Item {
    id: root
    objectName: "walkPulse"
    property var wire: ({})
    property color tone: theme.colors.chain_0
    property string kind: ""
    property real ratio: -1
    property bool running: false
    property bool still: false
    property var toX: function (cx) { return cx }
    property var toY: function (cy) { return cy }
    readonly property real speed: 0.22       // px per ms
    readonly property real dotPx: 10
    readonly property int maxDots: 12
    readonly property real every: Math.max(180, length / speed / maxDots)
    readonly property bool rings: kind === "time_map" || kind === "python"
    readonly property bool drops: kind === "intaker" && ratio >= 0
    property real elapsed: 0
    // a long wire starts already flowing: its first dot 400 px before the in port, so a
    // hop the camera framed at its target shows the dots arrive within its dwell
    readonly property real preroll: Math.max(0, (length - 400) / speed)
    function restart() { elapsed = preroll }
    // the arc-length table in canvas px: [x, y, s] every 1/48 of the curve (the pulse
    // runs at 1:1, so canvas and screen lengths agree; points map through toX/toY when
    // drawn, so the dots follow the camera)
    property var table: []
    property real length: 0
    // the link node: the curve's middle by length
    readonly property point node: at(length / 2)

    function rebuild() {
        var w = root.wire
        if (w === undefined || w.x1 === undefined) { table = []; length = 0; return }
        var x1 = w.x1, y1 = w.y1, x2 = w.x2, y2 = w.y2
        var bend = Math.max(24, Math.abs(x2 - x1) / 3)
        var c1x = x1 + bend, c1y = y1, c2x = x2 - bend, c2y = y2
        var out = [], s = 0, px = x1, py = y1
        for (var i = 0; i <= 48; i++) {
            var t = i / 48, u = 1 - t
            var x = u * u * u * x1 + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t * t * t * x2
            var y = u * u * u * y1 + 3 * u * u * t * c1y + 3 * u * t * t * c2y + t * t * t * y2
            s += Math.sqrt((x - px) * (x - px) + (y - py) * (y - py))
            out.push([x, y, s])
            px = x; py = y
        }
        table = out
        length = s
    }
    function at(s) {
        var tb = root.table
        if (tb.length === 0) return Qt.point(0, 0)
        if (s <= 0) return Qt.point(toX(tb[0][0]), toY(tb[0][1]))
        for (var i = 1; i < tb.length; i++) {
            if (tb[i][2] >= s) {
                var a = tb[i - 1], b = tb[i]
                var f = (s - a[2]) / Math.max(1e-6, b[2] - a[2])
                return Qt.point(toX(a[0] + (b[0] - a[0]) * f), toY(a[1] + (b[1] - a[1]) * f))
            }
        }
        var last = tb[tb.length - 1]
        return Qt.point(toX(last[0]), toY(last[1]))
    }
    // whether dot k is dropped at the intaker: walk_camera.dropped, the same rule
    function dropped(k) {
        if (!root.drops) return false
        var r = Math.min(1, root.ratio)
        if (k === 0) return r <= 0
        return Math.floor((k + 1) * r) === Math.floor(k * r)
    }
    onWireChanged: rebuild()
    Component.onCompleted: rebuild()
    onRunningChanged: restart()

    FrameAnimation {
        running: root.running && !root.still && root.length > 0
        onTriggered: root.elapsed += frameTime * 1000
    }

    // the dots: slot j shows the latest dot k = j + 12 n emitted so far; a slot's dot is
    // gone before its next one leaves (every * 12 >= the travel time)
    Repeater {
        model: root.still ? 0 : root.maxDots
        delegate: Rectangle {
            id: dot
            required property int index
            readonly property int k: {
                var n = Math.floor((root.elapsed / root.every - index) / root.maxDots)
                return n < 0 ? -1 : index + root.maxDots * n
            }
            readonly property real s: k < 0 ? -1 : (root.elapsed - k * root.every) * root.speed
            readonly property real half: root.length / 2
            readonly property bool ghost: s > half && root.dropped(k)
            // a ghost drifts and fades for 180 ms from the node, then is gone
            readonly property real ghostT: ghost ? (s - half) / root.speed / 180 : 0
            readonly property point p: ghost ? root.node : root.at(s)
            objectName: "walkDot:" + index
            visible: root.running && k >= 0 && s >= 0 && s <= root.length && ghostT < 1
            width: root.dotPx
            height: root.dotPx
            radius: width / 2
            x: p.x - width / 2
            y: p.y - height / 2 + (ghost ? 8 * ghostT : 0)
            color: ghost ? theme.colors.surface : root.tone
            border.width: 2
            border.color: ghost ? Qt.alpha(root.tone, 0.5) : theme.colors.surface
            opacity: ghost ? 0.6 * (1 - ghostT) : 1
        }
    }

    // reduced motion: a static gradient of tokens along the path
    Repeater {
        model: root.still ? 9 : 0
        delegate: Rectangle {
            required property int index
            readonly property point p: root.at(root.length * (index + 1) / 10)
            width: 8
            height: 8
            radius: 4
            x: p.x - 4
            y: p.y - 4
            color: root.tone
            opacity: 0.25 + 0.75 * (index + 1) / 9
        }
    }

    // the link node: a ring the dots pass through; a time map or a Python link rings
    Item {
        id: nodeMark
        objectName: "walkNode"
        x: root.node.x
        y: root.node.y
        visible: root.length > 0
        // ms since the last dot passed the node (the latest k with s >= half)
        readonly property real since: {
            var travel = root.length / 2 / root.speed
            var k = Math.floor((root.elapsed - travel) / root.every)
            return k < 0 ? 1e9 : root.elapsed - travel - k * root.every
        }
        Rectangle {
            width: 14
            height: 14
            radius: 7
            x: -7
            y: -7
            color: theme.colors.surface
            border.width: 2
            border.color: root.tone
            Rectangle { anchors.centerIn: parent; width: 6; height: 6; radius: 3; color: root.tone }
        }
        Rectangle {
            objectName: "walkNodeRing"
            readonly property real t: Math.min(1, nodeMark.since / 240)
            visible: root.rings && root.running && t < 1
            width: 14
            height: 14
            radius: 7
            x: -7
            y: -7
            scale: 1 + 0.6 * t
            color: "transparent"
            border.width: 2
            border.color: root.tone
            opacity: 1 - t
        }
    }

    // the in port rings once per dot (120 ms, ease-out)
    Rectangle {
        readonly property real travel: root.length / root.speed
        // the latest dot that reached the in port; a dropped one never arrives
        readonly property int arrived: Math.floor((root.elapsed - travel) / root.every)
        readonly property real since: arrived < 0 ? 1e9 : root.elapsed - travel - arrived * root.every
        readonly property real t: Math.min(1, since / 120)
        readonly property point end: root.at(root.length)
        visible: root.running && !root.still && t < 1 && !root.dropped(arrived)
        width: 18
        height: 18
        radius: 9
        x: end.x - 9
        y: end.y - 9
        scale: 1 + 0.5 * (1 - (1 - t) * (1 - t))
        color: "transparent"
        border.width: 2
        border.color: root.tone
        opacity: 1 - t
    }
}
