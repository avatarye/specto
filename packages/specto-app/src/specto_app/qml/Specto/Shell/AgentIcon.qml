import QtQuick
import QtQuick.Shapes

// The agent icon's art (#162, #170): the S character on a 52 px tile at the bottom left,
// showing the agent's state. SWAPPABLE: AgentPrompt.qml only relies on `state` (idle |
// thinking | working | needs_you | error), `gaze` (where the agent looks, x and y in
// -1..1: the pointer while it hovers, the panel while that is open), `asks` (unread
// answers, the needs-you badge), `hovered` (the tile lifts) and `shake()`.
// The S (SMark.qml) never moves; its two eyes do, together, inside the upper bowl, never
// past the art's travel from rest. The art is data (`logoArt`: specto_app/logo.py reading
// the designer's assets/logo/*.svg), and so are the states' poses (prompt/icon_pose.py,
// from the design's README, Figma page 04 "Logo final: S character"):
//   idle       eyes at rest, following the gaze; a blink every 4 to 7 s at random
//              (close 80 ms, open 120 ms, ease-out)
//   thinking   eyes up-right, narrowed to 85 %; a look-around: hold 1.2 s, up-left,
//              hold, back (a 4 s loop, ease in-out)
//   working    eyes scan the travel from the left end to the right and back, 1.2 s a sweep
//   needs_you  wide eyes following the gaze within their own smaller travel (held at
//              rest if the art gives none), the S and the tile in agent colours, the badge;
//              `shake()` (every 4 s from promptBar.icon): +-2 px three times in 400 ms
//   error      X eyes drawn in 160 ms, the S at 45 %
// Motion runs only while the window shows (not hidden, not minimised).
Item {
    id: root
    objectName: "agentIconArt"
    property vector2d gaze: Qt.vector2d(0, 0)
    property int asks: 0
    property bool hovered: false
    property real shakeX: 0
    implicitWidth: 52
    implicitHeight: 52
    width: implicitWidth
    height: implicitHeight
    state: "idle"
    states: [
        State { name: "idle" },
        State { name: "thinking" },
        State { name: "working" },
        State { name: "needs_you" },
        State { name: "error" }
    ]

    // -- the pose (prompt/icon_pose.py) ---------------------------------------------------------
    readonly property bool needsYou: state === "needs_you"
    readonly property bool errored: state === "error"
    readonly property var pose: logoArt.poses[state] || logoArt.poses.idle
    readonly property string eyeStyle: pose.eyes
    readonly property string motion: pose.motion
    readonly property bool followsGaze: pose.gaze
    readonly property real bodyOpacity: pose.bodyOpacity
    readonly property real squash: pose.squash

    // the window shows: motion runs only then
    readonly property bool live: Window.window !== null && Window.visibility !== Window.Hidden
                                 && Window.visibility !== Window.Minimized
    readonly property bool motionRunning: lookAnim.running || scanAnim.running || blinkTimer.running

    // the mark's 100-unit grid on the tile (the art's placement, 76 % of it, centred)
    readonly property real k: width / 52
    readonly property real unit: logoArt.unit * width
    readonly property real origin: logoArt.origin * width

    // where both eyes are, in grid units from rest: the gaze (eased 250 ms) in the states
    // without an eye motion of their own, else the state's loop
    function gazeOffset(g) {
        if (!isFinite(g.x) || !isFinite(g.y)) return Qt.vector2d(0, 0)
        var len = Math.sqrt(g.x * g.x + g.y * g.y)
        var s = len > 1 ? 1 / len : 1
        var t = root.eyeStyle === "wide" ? logoArt.wideTravel : logoArt.travel
        return Qt.vector2d(g.x * s * t[0], g.y * s * t[1])
    }
    readonly property vector2d gazeTo: gazeOffset(gaze)
    property real gazeDx: gazeTo.x
    property real gazeDy: gazeTo.y
    Behavior on gazeDx { enabled: root.live; NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } }
    Behavior on gazeDy { enabled: root.live; NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } }
    property real loopDx: 0
    property real loopDy: 0
    readonly property real eyeDx: followsGaze ? gazeDx : loopDx
    readonly property real eyeDy: followsGaze ? gazeDy : loopDy
    onStateChanged: {
        loopDx = pose.dx
        loopDy = pose.dy
    }
    readonly property var lookAt: logoArt.poses.thinking
    readonly property real scanFrom: logoArt.poses.working.dx

    // -- motion -------------------------------------------------------------------------------
    // the eyelid: 1 open, shut at the blink's key pose (its line over the eye's diameter)
    property real lid: 1
    function blink() { blinkAnim.restart() }
    function nextBlink() { return Math.round(4000 + Math.random() * 3000) }
    Timer {
        id: blinkTimer
        objectName: "blinkTimer"
        interval: root.nextBlink()
        repeat: true
        running: root.live && root.motion === "blink"
        onTriggered: {
            root.blink()
            interval = root.nextBlink()
        }
    }
    SequentialAnimation {
        id: blinkAnim
        NumberAnimation { target: root; property: "lid"; to: logoArt.blinkLid; duration: 80; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "lid"; to: 1; duration: 120; easing.type: Easing.OutCubic }
    }
    SequentialAnimation {
        id: lookAnim
        objectName: "lookAnim"
        running: root.live && root.motion === "look_around"
        loops: Animation.Infinite
        PropertyAction { target: root; property: "loopDx"; value: root.lookAt.dx }
        PropertyAction { target: root; property: "loopDy"; value: root.lookAt.dy }
        PauseAnimation { duration: 1200 }
        NumberAnimation { target: root; property: "loopDx"; to: -root.lookAt.dx; duration: 800; easing.type: Easing.InOutQuad }
        PauseAnimation { duration: 1200 }
        NumberAnimation { target: root; property: "loopDx"; to: root.lookAt.dx; duration: 800; easing.type: Easing.InOutQuad }
    }
    SequentialAnimation {
        id: scanAnim
        objectName: "scanAnim"
        running: root.live && root.motion === "scan"
        loops: Animation.Infinite
        PropertyAction { target: root; property: "loopDx"; value: root.scanFrom }
        PropertyAction { target: root; property: "loopDy"; value: 0 }
        NumberAnimation { target: root; property: "loopDx"; to: -root.scanFrom; duration: 1200; easing.type: Easing.InOutQuad }
        NumberAnimation { target: root; property: "loopDx"; to: root.scanFrom; duration: 1200; easing.type: Easing.InOutQuad }
    }
    function shake() { if (live) shakeAnim.restart() }
    SequentialAnimation {
        id: shakeAnim
        loops: 3
        NumberAnimation { target: root; property: "shakeX"; to: 2 * root.k; duration: 33 }
        NumberAnimation { target: root; property: "shakeX"; to: -2 * root.k; duration: 67 }
        NumberAnimation { target: root; property: "shakeX"; to: 0; duration: 33 }
    }
    // the X eyes draw in (0 -> 1)
    property real xDraw: errored ? 1 : 0
    Behavior on xDraw { enabled: root.live; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

    // an eye: a dot that moves as a whole and narrows by a vertical scale (thinking, the
    // blink), so its curve is never rebuilt while it moves
    component Eye: Shape {
        id: eye
        property real r: 1
        property color fill
        property real yScale: 1
        width: 2 * r
        height: 2 * r
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        transform: Scale { origin.x: eye.r; origin.y: eye.r; yScale: eye.yScale }
        ShapePath {
            fillColor: eye.fill
            strokeColor: "transparent"
            PathAngleArc { centerX: eye.r; centerY: eye.r; radiusX: eye.r; radiusY: eye.r; sweepAngle: 360 }
        }
    }

    // -- the art ------------------------------------------------------------------------------
    Item {
        id: body
        x: root.shakeX
        width: root.width
        height: root.height

        SoftShadow {
            radius: tile.radius
            strength: root.hovered ? 0.34 : 0.22
            blur: root.hovered ? 0.75 : 0.55
        }
        Rectangle {
            id: tile
            anchors.fill: parent
            radius: 14 * root.k
            color: root.needsYou ? theme.colors.agent_soft : theme.colors.surface
            border.width: root.needsYou ? 1.5 : 1
            border.color: root.needsYou ? theme.colors.agent
                          : (root.hovered ? theme.colors.text_faint : theme.colors.border_strong)
        }
        SMark {
            objectName: "agentIconS"
            x: root.origin
            y: root.origin
            width: 100 * root.unit
            height: width
            eyes: false
            ink: root.needsYou ? theme.colors.agent : theme.colors.text
            opacity: root.bodyOpacity
        }
        // the eyes look where the agent looks
        Item {
            objectName: "agentIconEyes"
            x: root.origin
            y: root.origin
            visible: root.eyeStyle !== "x"
            Repeater {
                model: root.eyeStyle === "wide" ? logoArt.wide : logoArt.open
                Eye {
                    required property var modelData
                    r: modelData.r * root.unit
                    yScale: root.squash * root.lid
                    x: (modelData.cx + root.eyeDx) * root.unit - r
                    y: (modelData.cy + root.eyeDy) * root.unit - r
                    fill: theme.colors.agent
                }
            }
        }
        // error: X eyes, one stroked path per eye, growing from its centre as they draw in
        Item {
            objectName: "agentIconCross"
            x: root.origin
            y: root.origin
            width: 100
            height: 100
            scale: root.unit
            transformOrigin: Item.TopLeft
            visible: root.errored
            Repeater {
                model: logoArt.x
                Shape {
                    required property var modelData
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    antialiasing: true
                    transform: Scale {
                        origin.x: modelData.cx
                        origin.y: modelData.cy
                        xScale: Math.max(0.01, root.xDraw)
                        yScale: Math.max(0.01, root.xDraw)
                    }
                    ShapePath {
                        strokeColor: theme.colors.removed
                        strokeWidth: modelData.width
                        fillColor: "transparent"
                        capStyle: modelData.cap === "round" ? ShapePath.RoundCap : ShapePath.FlatCap
                        PathSvg { path: modelData.path }
                    }
                }
            }
        }
        // needs you: unread answers
        Rectangle {
            objectName: "agentIconBadge"
            visible: root.needsYou && root.asks > 0
            width: Math.max(18, badgeText.implicitWidth + 8)
            height: 18
            radius: 9
            x: parent.width - width / 2 - 4
            y: -height / 2 + 4
            color: theme.colors.agent
            border.width: 2
            border.color: theme.colors.surface
            Text {
                id: badgeText
                anchors.centerIn: parent
                text: root.asks
                color: theme.colors.surface
                font.family: theme.fonts.ui
                font.pixelSize: theme.metrics.font_px_small
                font.bold: true
            }
        }
    }
}
