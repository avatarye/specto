// MpvItem: libmpv's render API (OpenGL) inside a Qt Quick item (HLD 4.1 `MpvItem` of
// _spectoqt; M2 "mpv video item"). One mpv core per item; the video is rendered into an
// FBO the item owns and the FBO's colour texture is handed to the scene graph through
// QNativeInterface::QSGOpenGLTexture::fromNative (the spike S2 path, same as the
// gallery atlas). Used from PySide6 through the meta-object only: properties,
// invokables and signals. The item never calls into Python.
//
// Threads (spike S1 rule: frames are requested from the GUI thread only):
//   - mpv's threads (demux, decode) call two callbacks: the wakeup callback (events) and
//     the render-update callback (a new frame is ready). Both only post a queued call to
//     the GUI thread; nothing else runs there.
//   - GUI thread: `pollEvents` drains mpv events into the cached properties (position,
//     duration, pause, speed, A-B loop, fps, frame, size, eof, mute, volume, audio
//     track) and `renderUpdate` calls
//     QQuickItem::update(), which is the only way a frame is requested. Commands (seek,
//     frame-step, loadfile) and property writes go to mpv asynchronously from here.
//   - render thread, inside updatePaintNode (GUI blocked, GL current): the render
//     context is created on first use, `mpv_render_context_update` is asked whether a
//     frame is pending and `mpv_render_context_render` draws into the FBO without
//     blocking for target time. No QQuickWindow::update() is ever called from here.
//
// libmpv is loaded at runtime (mpvapi.h); when it is missing the item stays empty and
// `error` says what to do. Without an OpenGL context (software backend, tests) nothing
// is drawn and `backend` stays "none".
//
// Sound (#216): the core opens the platform's audio output (mpv `ao` auto: wasapi on
// Windows, pulse / pipewire / alsa on Linux) with the file's first audio track selected,
// `mute` on and `volume` 100 to start; `muted` / `volume` are the item's properties,
// `hasAudio` says whether the open file has a track. An audio device that fails to open
// is logged, never an `error`: the picture plays on, timed by the system clock as before.
#pragma once

#include <QtCore/QMutex>
#include <QtCore/QString>
#include <QtCore/QStringList>
#include <QtCore/QVariant>
#include <QtQuick/QQuickItem>

#include <atomic>
#include <memory>

#if defined(SPECTONATIVE_BUILD)
#define SPECTONATIVE_EXPORT Q_DECL_EXPORT
#else
#define SPECTONATIVE_EXPORT Q_DECL_IMPORT
#endif

struct mpv_handle;
struct mpv_render_context;
struct mpv_event;
class MpvItem;

// Shared by the item and its scene-graph node so the mpv core outlives whichever dies
// first: the render context must be freed on the render thread with GL current (the
// node's destructor) and the core destroyed only after that.
struct MpvCore {
    ~MpvCore();
    void freeRender();   // render thread, GL current

    mpv_handle *mpv = nullptr;
    mpv_render_context *renderCtx = nullptr;
    QMutex itemMutex;    // guards `item` against the callbacks on mpv's threads
    MpvItem *item = nullptr;
    std::atomic<bool> eventsPosted{false};
    std::atomic<bool> updatePosted{false};
    std::atomic<int> renderFrames{0};
};

class SPECTONATIVE_EXPORT MpvItem : public QQuickItem
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QString source READ source WRITE setSource NOTIFY sourceChanged)
    Q_PROPERTY(bool ready READ ready NOTIFY readyChanged)           // libmpv loaded and core up
    Q_PROPERTY(bool fileLoaded READ fileLoaded NOTIFY fileLoadedChanged)
    Q_PROPERTY(QString error READ error NOTIFY errorChanged)
    Q_PROPERTY(QString backend READ backend NOTIFY backendChanged)  // "opengl" | "none"
    Q_PROPERTY(QString mpvVersion READ mpvVersion CONSTANT)
    Q_PROPERTY(double position READ position NOTIFY positionChanged)   // time-pos, s
    Q_PROPERTY(double duration READ duration NOTIFY durationChanged)
    Q_PROPERTY(bool paused READ paused WRITE setPaused NOTIFY pausedChanged)
    Q_PROPERTY(double speed READ speed WRITE setSpeed NOTIFY speedChanged)
    Q_PROPERTY(double loopA READ loopA WRITE setLoopA NOTIFY loopChanged)   // < 0: none
    Q_PROPERTY(double loopB READ loopB WRITE setLoopB NOTIFY loopChanged)
    Q_PROPERTY(double fps READ fps NOTIFY fpsChanged)                 // container-fps
    Q_PROPERTY(qint64 frame READ frame NOTIFY frameChanged)           // estimated-frame-number
    Q_PROPERTY(qint64 frameCount READ frameCount NOTIFY frameChanged) // estimated-frame-count
    Q_PROPERTY(int videoWidth READ videoWidth NOTIFY videoSizeChanged)
    Q_PROPERTY(int videoHeight READ videoHeight NOTIFY videoSizeChanged)
    Q_PROPERTY(bool eof READ eof NOTIFY eofChanged)
    Q_PROPERTY(bool seeking READ seeking NOTIFY seekingChanged)
    Q_PROPERTY(bool muted READ muted WRITE setMuted NOTIFY mutedChanged)       // mute
    Q_PROPERTY(double volume READ volume WRITE setVolume NOTIFY volumeChanged) // 0 .. 100
    Q_PROPERTY(bool hasAudio READ hasAudio NOTIFY hasAudioChanged)             // aid is a track
    Q_PROPERTY(int renderFrames READ renderFrames NOTIFY positionChanged)

public:
    explicit MpvItem(QQuickItem *parent = nullptr);
    ~MpvItem() override;

    QString source() const { return m_source; }
    void setSource(const QString &path);
    bool ready() const { return m_ready; }
    bool fileLoaded() const { return m_fileLoaded; }
    QString error() const { return m_error; }
    QString backend() const { return m_backend; }
    QString mpvVersion() const;
    double position() const { return m_position; }
    double duration() const { return m_duration; }
    bool paused() const { return m_paused; }
    void setPaused(bool on);
    double speed() const { return m_speed; }
    void setSpeed(double s);
    double loopA() const { return m_loopA; }
    void setLoopA(double t);
    double loopB() const { return m_loopB; }
    void setLoopB(double t);
    double fps() const { return m_fps; }
    qint64 frame() const { return m_frame; }
    qint64 frameCount() const { return m_frameCount; }
    int videoWidth() const { return m_videoW; }
    int videoHeight() const { return m_videoH; }
    bool eof() const { return m_eof; }
    bool seeking() const { return m_seeking; }
    bool muted() const { return m_muted; }
    void setMuted(bool on);
    double volume() const { return m_volume; }
    void setVolume(double v);
    bool hasAudio() const { return m_hasAudio; }
    int renderFrames() const;

    // -- control (GUI thread; all asynchronous towards mpv) --------------------------
    Q_INVOKABLE void play();
    Q_INVOKABLE void pause();
    Q_INVOKABLE void togglePause();
    Q_INVOKABLE void stop();
    // exact seeks land on the frame containing `t` (hr-seek); `relative` seeks by `t`
    Q_INVOKABLE void seek(double t, bool exact = true, bool relative = false);
    Q_INVOKABLE void frameStep();       // one frame forward (pauses)
    Q_INVOKABLE void frameBackStep();   // one frame back (pauses)
    Q_INVOKABLE void clearLoop();
    // raw access: mpv commands and properties, for scripts and the host
    Q_INVOKABLE bool command(const QStringList &args);        // synchronous
    Q_INVOKABLE bool commandAsync(const QStringList &args);
    Q_INVOKABLE QVariant mpvProperty(const QString &name) const;
    Q_INVOKABLE bool setMpvProperty(const QString &name, const QVariant &value);

    // render-thread entry points (MpvNode)
    std::shared_ptr<MpvCore> core() const { return m_core; }

signals:
    void sourceChanged();
    void readyChanged();
    void fileLoadedChanged();
    void loaded();                      // the file is open: duration, fps and size are known
    void errorChanged();
    void backendChanged();
    void positionChanged();
    void durationChanged();
    void pausedChanged();
    void speedChanged();
    void loopChanged();
    void fpsChanged();
    void frameChanged();
    void videoSizeChanged();
    void eofChanged();
    void seekingChanged();
    void mutedChanged();
    void volumeChanged();
    void hasAudioChanged();
    void playbackRestart();             // a seek or frame step finished: the frame shown is exact
    void endFile(const QString &reason);
    void logMessage(const QString &level, const QString &prefix, const QString &text);

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;

private slots:
    void pollEvents();     // GUI thread, posted by the wakeup callback
    void renderUpdate();   // GUI thread, posted by the render-update callback
    void renderReady();    // GUI thread, posted when a render context was created

private:
    static void wakeup(void *ctx);
    static void onRenderUpdate(void *ctx);
    void initCore();
    void handleEvent(mpv_event *ev);
    void refreshFrame();
    void refreshAfterLoad();
    void setError(const QString &msg);
    bool setPropertyAsync(const char *name, const QVariant &value);

    std::shared_ptr<MpvCore> m_core;
    QString m_source;
    QString m_error;
    QString m_backend = QStringLiteral("none");
    bool m_ready = false;
    bool m_fileLoaded = false;
    bool m_glWarned = false;
    // vo=libmpv needs the render context before a file opens, else "no video stream":
    // loadfile waits for the first updatePaintNode, and runs again when the context
    // is recreated (the item left and re-entered a window), restoring the position
    int m_ctxGeneration = 0;      // bumped on the GUI thread by renderReady
    int m_loadedGeneration = -1;  // the context generation the current file opened with
    double m_restorePos = -1.0;
    double m_position = 0.0, m_duration = 0.0, m_speed = 1.0, m_fps = 0.0;
    double m_loopA = -1.0, m_loopB = -1.0;
    bool m_paused = true, m_eof = false, m_seeking = false;
    bool m_muted = true, m_hasAudio = false;
    double m_volume = 100.0;
    qint64 m_frame = -1, m_frameCount = 0;
    int m_videoW = 0, m_videoH = 0;
};
