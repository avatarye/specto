import QtQuick
import QtQuick.Layouts
import QtWebEngine
import Specto.Spike

// Spike S1/S2 scene: a QtWebEngine view (web panel stand-in) beside a C++
// TextureItem (StormItem / MpvItem stand-in), both inside one Qt Quick window on
// the OpenGL RHI backend. Calm Canvas colours, but no attempt at the real shell.
Window {
    id: root
    width: 1920
    height: 1080
    visible: true
    color: "#f4efe6"
    title: "Specto spike S1/S2"

    property string webUrl: "specto://app/index.html"
    property int texMode: 0
    property int triangles: 200000
    property bool showWeb: true
    property bool continuous: true
    property bool driveGui: false

    RowLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 16

        // Card: web panel
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 14
            color: "#ffffff"
            border.color: "#e3dccd"
            visible: root.showWeb
            clip: true
            WebEngineView {
                id: web
                objectName: "web"
                anchors.fill: parent
                anchors.margins: 2
                url: root.showWeb ? root.webUrl : ""
                backgroundColor: "#ffffff"
            }
        }

        // Card: native texture
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 14
            color: "#ffffff"
            border.color: "#e3dccd"
            clip: true
            TextureItem {
                id: tex
                objectName: "tex"
                anchors.fill: parent
                anchors.margins: 2
                mode: root.texMode
                triangles: root.triangles
                continuous: root.continuous
            }
            // GUI-driven frames: one item.update() per scene-graph frame, so the
            // render thread never re-arms itself.
            FrameAnimation {
                running: root.driveGui
                onTriggered: tex.update()
            }
            // Something the QML scene itself animates, so a stalled scene graph is visible.
            Rectangle {
                id: spinner
                width: 40; height: 40; radius: 8
                color: "#2f6fab"
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 24
                RotationAnimation on rotation {
                    from: 0; to: 360; duration: 2000; loops: Animation.Infinite
                }
            }
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: 16
                radius: 10
                color: "#f4efe6ee"
                width: stats.implicitWidth + 24
                height: stats.implicitHeight + 16
                Text {
                    id: stats
                    anchors.centerIn: parent
                    font.family: "Consolas"
                    font.pixelSize: 14
                    color: "#3a3630"
                    text: "render " + tex.renderFps.toFixed(1) + " fps  mode " + tex.mode
                        + "\ncpu draw " + tex.renderCpuMs.toFixed(3) + " ms  gpu draw " + tex.renderGpuMs.toFixed(3) + " ms"
                        + "\ncpu upload " + tex.uploadCpuMs.toFixed(3) + " ms  gpu upload " + tex.uploadGpuMs.toFixed(3) + " ms"
                        + "\nsync " + tex.syncMs.toFixed(3) + " ms  tris " + tex.triangles
                        + "\n" + tex.glRenderer
                }
            }
        }
    }
}
