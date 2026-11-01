import QtQuick

// The one view-chrome button (#126, design 05 section 2): 22 px round, a 14 px line icon,
// five states learnt once. Rest: the muted icon. Hover: the surface-hover fill. On: the
// soft accent fill under the accent icon (a state the user set: locked, floating,
// following files). ``disabled``: the faint icon, no hover fill, taps ignored, the tip
// still says why (refresh while a rescan runs). ``danger`` (close): the soft
// removed fill and the removed icon on hover. ``news`` puts a small accent dot on its
// top-right corner (refresh: new files wait). Header and footer controls alike.
Rectangle {
    id: root
    property string name: ""
    property string tip: ""
    property bool on: false
    property bool danger: false
    property bool news: false
    property bool disabled: false
    property real size: 22
    property real iconSize: 14
    readonly property bool hovered: hover.hovered
    signal clicked()

    width: size
    height: size
    radius: size / 2
    color: on ? theme.colors.accent_soft
              : (hover.hovered && !disabled ? (danger ? theme.colors.removed_soft : theme.colors.surface_hover)
                                          : "transparent")
    // refresh fades out of its slot while auto-update is on (ViewHeader)
    Behavior on opacity { NumberAnimation { duration: theme.metrics.anim_ms } }

    Icon {
        anchors.centerIn: parent
        name: root.name
        size: root.iconSize
        color: root.disabled ? theme.colors.text_faint
             : root.on ? theme.colors.accent
             : root.danger && hover.hovered ? theme.colors.removed
             : hover.hovered ? theme.colors.text : theme.colors.text_muted
    }
    Rectangle {
        objectName: root.objectName ? root.objectName + ":news" : ""
        visible: root.news
        width: 6
        height: 6
        radius: 3
        x: root.width - 7
        y: 2
        color: theme.colors.accent
    }
    HoverHandler { id: hover }
    TapHandler {
        // the tap is this button's alone (an exclusive grab): a control of a view under
        // this one, in the same spot, never taps on the release (#158)
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: if (!root.disabled) root.clicked()
    }
    Tip {
        visible: hover.hovered && root.tip !== ""
        text: root.tip
        tag: root.objectName
    }
}
