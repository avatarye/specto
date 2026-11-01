pragma Singleton
import QtQuick

// Which tip has the slot (#127): one at a time. A port tip lingers after its port loses
// the pointer so the pointer can reach its links; any other tip taking the slot closes it.
// `open`: the owner is showing (a port tip then waits for a dwell before taking over).
QtObject {
    property string owner: ""
    property bool open: false
}
