import QtQuick
import QtWebEngine
import QtWebChannel

// One web view: a WebEngineView on its own specto://<pageId>.page origin with the
// Python PageBridge registered as "specto" on its channel. Health and console output
// go to the bridge; Python never touches the page directly. Theme comes from the shell's
// `theme` context object (ThemeObject) when present.
Item {
    id: root
    objectName: "webView"

    required property QtObject bridge
    required property url source
    property string pageId: ""
    property alias view: web
    SystemPalette { id: sys }
    property color background: typeof theme !== "undefined" && theme !== null ? theme.colors.surface : sys.base

    WebChannel {
        id: channel
    }

    Component.onCompleted: {
        if (root.bridge)
            channel.registerObject("specto", root.bridge)
    }

    WebEngineView {
        id: web
        objectName: "webView"
        anchors.fill: parent
        url: root.source
        webChannel: channel
        backgroundColor: root.background
        settings.localContentCanAccessRemoteUrls: false
        settings.localContentCanAccessFileUrls: false
        settings.javascriptCanAccessClipboard: false
        settings.javascriptCanOpenWindows: false
        settings.pluginsEnabled: false
        settings.screenCaptureEnabled: false
        // no page zoom (#189): Chromium's zoom scales the whole page, its side panels and
        // footer too; the pages refuse Ctrl+wheel (zoom.js), this catches what no page
        // script sees (a wheel inside the html view's iframe)
        onZoomFactorChanged: if (zoomFactor !== 1.0) zoomFactor = 1.0

        onJavaScriptConsoleMessage: function(level, message, lineNumber, sourceID) {
            if (root.bridge)
                root.bridge.consoleMessage(level, message, lineNumber, sourceID)
        }
        onLoadingChanged: function(info) {
            if (!root.bridge)
                return
            if (info.status === WebEngineView.LoadStartedStatus)
                root.bridge.loadState("started", "")
            else if (info.status === WebEngineView.LoadFailedStatus)
                root.bridge.loadState("error", info.errorString)
            else if (info.status === WebEngineView.LoadSucceededStatus)
                root.bridge.loadState("ok", "")
        }
        onRenderProcessTerminated: function(status, code) {
            if (root.bridge)
                root.bridge.loadState("error", "render process terminated (" + status + ", code " + code + ")")
        }
        // The main frame stays on the page's own origin; a frame inside the page (the
        // html view's iframe) may also load the view's items origin. The bridge decides
        // (scheme.navigation_allowed) and logs every refusal; this fires for frames too.
        onNavigationRequested: function(request) {
            if (!root.bridge || !root.bridge.allowNavigation(request.url.toString(), request.isMainFrame))
                request.reject()
        }
        onNewWindowRequested: function(request) { /* refused: no popups */ }
    }
}
