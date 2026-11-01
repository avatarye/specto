// The video view's item: the native MpvItem (Specto.Native) filling the slot above the
// player's footer (#128): a progress bar the footer's full length (hover tip: clip time
// and SMPTE timecode), the transport row (play / pause, "12 / 240 @ 32.48 fps", the
// speaker button and volume slider (#216), the speed button that pops up a vertical
// slider) and the viewer row (previous / next video, "n / N", the file's name, its
// resolution); both rows are the shared ViewFooter (#126). The host sets `caption` (the
// file's name), drives `tl` (a TimelineController) and hears user intent through
// `bridge` (play / pause, frame step, speed, mute, volume, item step). The A-B loop and
// hardware decoding are agent parameters of the view (no buttons); the loop shows on
// the bar.
//
// Input: click the picture or Space: play / pause; Left / Right: one frame; wheel over
// the picture: frames; Home / End: first / last frame; - and +: speed down / up; M:
// mute / unmute; PgUp / PgDn (and the viewer row's chevrons, #107): previous / next
// video item.
import QtQuick
import Specto.Native
import Specto.Shell as Shell

Item {
    id: root
    property string viewId: ""
    property QtObject bridge: null
    property QtObject tl: null
    property string caption: ""
    // where the view is in its collection (hosts.stepping sets them)
    property int stepIndex: -1
    property int stepCount: 0
    property alias mpv: mpv
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
    readonly property string prefix: "video:" + root.viewId

    clip: true
    focus: true

    Rectangle {
        anchors.fill: parent
        color: root.tok("video_bg")
    }

    MpvItem {
        id: mpv
        objectName: "mpv"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: progress.top
    }

    // letterbox hint while nothing is loaded, or the error
    Text {
        anchors.centerIn: mpv
        width: Math.min(mpv.width - 40, implicitWidth)
        wrapMode: Text.Wrap
        horizontalAlignment: Text.AlignHCenter
        visible: !mpv.fileLoaded
        text: mpv.error !== "" ? mpv.error : (mpv.source === "" ? "no video" : "loading " + mpv.source)
        color: mpv.error !== "" ? root.tok("removed_soft") : root.tok("text_faint")
        font.family: root.font("ui")
        font.pixelSize: root.metric("font_px", 13)
    }

    // paused: a quiet play glyph in the middle of the picture, on a soft disc of the
    // letterbox colour so it reads over any frame
    Rectangle {
        objectName: root.prefix + ":pausedGlyph"
        visible: root.hasTheme && mpv.fileLoaded && root.tl !== null && !root.tl.playing
        anchors.centerIn: mpv
        width: 44
        height: 44
        radius: 22
        color: Qt.alpha(root.tok("video_bg"), 0.45)
        Shell.Icon {
            anchors.centerIn: parent
            anchors.horizontalCenterOffset: 2   // the triangle's optical centre
            name: "play"
            size: 30
            color: root.tok("surface")
            opacity: 0.85
        }
    }

    MouseArea {
        anchors.fill: mpv
        acceptedButtons: Qt.LeftButton
        onClicked: { root.forceActiveFocus(); if (root.bridge) root.bridge.togglePlay() }
        onWheel: (wheel) => { if (root.bridge) root.bridge.step(wheel.angleDelta.y > 0 ? -1 : 1) }
    }

    VideoProgress {
        id: progress
        objectName: "videoBar"
        objectPrefix: root.prefix
        tl: root.tl
        view: root
        tipLayer: root
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: transport.top
        height: implicitHeight
    }

    // -- transport row (a ViewFooter without steps): play / pause, the frame readout, the
    //    speed button and its slider
    Shell.ViewFooter {
        id: transport
        objectName: root.prefix + ":transport"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: viewer.top
        viewId: root.viewId
        Shell.ChromeButton {
            objectName: root.prefix + ":play"
            name: root.tl && root.tl.playing ? "pause" : "play"
            tip: root.tl && root.tl.playing ? "pause (Space)" : "play (Space)"
            onClicked: { root.forceActiveFocus(); if (root.tl) root.tl.togglePlay() }
        }
        Shell.FooterLabel {
            objectName: root.prefix + ":readout"
            anchors.verticalCenter: parent ? parent.verticalCenter : undefined
            width: Math.min(implicitWidth, Math.max(0, transport.room - 26))
            elide: Text.ElideRight
            text: root.tl ? root.tl.label : ""
            mono: true
            tone: "text"
        }
        trailing: [
            // sound (#216): the speaker toggles mute (accent while this view has sound;
            // faint without an audio track), the short slider beside it is the volume
            Shell.ChromeButton {
                objectName: root.prefix + ":mute"
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                name: root.bridge && !root.bridge.muted ? "volume" : "volume-off"
                on: root.bridge ? !root.bridge.muted : false
                disabled: root.bridge ? !root.bridge.hasAudio : true
                tip: root.bridge && !root.bridge.hasAudio ? "no audio track"
                   : root.bridge && !root.bridge.muted ? "mute (M)" : "sound (M)"
                onClicked: { root.forceActiveFocus(); if (root.bridge) root.bridge.toggleMute() }
            },
            VolumeSlider {
                objectName: root.prefix + ":volumeSlider"
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                view: root
                bridge: root.bridge
                objectPrefix: root.prefix
            },
            Shell.ChromeTextButton {
                id: speedButton
                objectName: root.prefix + ":speed"
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                text: root.bridge ? root.bridge.speedLabel : "1x"
                tip: speedPop.opened ? "" : "speed (- / +)"
                on: speedPop.opened
                onClicked: { root.forceActiveFocus(); speedPop.opened ? speedPop.close() : speedPop.open() }
                SpeedPopover {
                    id: speedPop
                    view: root
                    bridge: root.bridge
                    objectPrefix: root.prefix
                }
            }
        ]
    }

    // -- viewer row: previous / next video and "n / N" (ViewFooter's), the file's name,
    //    its resolution
    Shell.ViewFooter {
        id: viewer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        viewId: root.viewId
        stepIndex: root.stepIndex
        stepCount: root.stepCount
        onStepped: (delta) => { root.forceActiveFocus(); if (root.bridge) root.bridge.selectItem(delta) }
        Shell.FooterLabel {
            objectName: root.prefix + ":caption"
            anchors.verticalCenter: parent ? parent.verticalCenter : undefined
            width: Math.min(implicitWidth, viewer.room)
            text: root.caption
            mono: true
            tone: "text"
        }
        trailing: [
            Shell.FooterLabel {
                objectName: "resolution:" + root.viewId
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                visible: mpv.fileLoaded
                text: mpv.videoWidth + "×" + mpv.videoHeight
                mono: true
            }
        ]
    }

    Keys.onPressed: (event) => {
        if (!root.bridge) return
        var k = event.key
        if (k === Qt.Key_Space) root.bridge.togglePlay()
        else if (k === Qt.Key_Left) root.bridge.step(-1)
        else if (k === Qt.Key_Right) root.bridge.step(1)
        else if (k === Qt.Key_Home) { if (root.tl) root.tl.scrubEnd(root.tl.tMin) }
        else if (k === Qt.Key_End) { if (root.tl) root.tl.scrubEnd(root.tl.tMax) }
        else if (k === Qt.Key_Minus) root.bridge.stepSpeed(-1)
        else if (k === Qt.Key_Plus || k === Qt.Key_Equal) root.bridge.stepSpeed(1)
        else if (k === Qt.Key_M) root.bridge.toggleMute()
        else if (k === Qt.Key_PageUp) root.bridge.selectItem(-1)
        else if (k === Qt.Key_PageDown) root.bridge.selectItem(1)
        else return
        event.accepted = true
    }
}
