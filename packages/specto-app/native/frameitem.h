// FrameItem: shows the frames of an out-of-process frame source (ovrtx, Storm, Blender;
// HLD 4.5, the design's `TextureItem`, #136). The source writes RGBA8 frames into a
// three-slot shared-memory ring (specto_app/frames/ring.py has the layout) and sends a
// byte on a local socket after each one; the item uploads the newest complete slot into
// a GL texture it owns, wrapped once through QNativeInterface::QSGOpenGLTexture. Used
// from PySide6 through the meta-object only; the item never calls into Python, and no
// Python runs per frame (ADR 0004).
//
// Threads:
//   - GUI thread: the wake socket (QLocalSocket, reconnecting) drains its bytes and calls
//     QQuickItem::update(), the only way a frame is requested. Stats posted by the render
//     thread land here as properties and one frameChanged per upload.
//   - render thread, updatePaintNode (sync, GUI blocked): the ring is (re)opened when
//     `ring` changed, the newest slot's size is peeked and the texture resized to it; a
//     closed ring with a successor makes the item follow it (posted to the GUI thread).
//   - render thread, beforeRendering (render, GUI running): the slot is announced to the
//     source, copied into a pixel-unpack buffer under its seqlock guard and uploaded with
//     glTexSubImage2D. A copy whose guard moved (the source rewrote the slot) is dropped
//     and another update requested, so a torn frame is never shown.
//
// Without an OpenGL context (software backend, tests) nothing is drawn and `backend`
// stays "none". The texture is stretched over the item: the source renders at the
// item's pixel size, and the last frame is scaled while a resize is in flight.
#pragma once

#include <QtCore/QMutex>
#include <QtCore/QString>
#include <QtQuick/QQuickItem>

#include <atomic>
#include <memory>

#if defined(SPECTONATIVE_BUILD)
#define SPECTONATIVE_EXPORT Q_DECL_EXPORT
#else
#define SPECTONATIVE_EXPORT Q_DECL_IMPORT
#endif

class QLocalSocket;
class QTimer;
class FrameItem;

// What the render thread hands the GUI thread: guarded by `mutex`, posted once per batch.
struct FrameShared {
    QMutex mutex;
    FrameItem *item = nullptr;   // null once the item is gone
    qint64 seq = 0;
    int width = 0, height = 0;
    double timestamp = 0.0, renderMs = 0.0, uploadMs = 0.0;
    int frames = 0, torn = 0;
    QString error;
    QString successor;
    std::atomic<bool> posted{false};
};

class SPECTONATIVE_EXPORT FrameItem : public QQuickItem
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QString ring READ ring WRITE setRing NOTIFY ringChanged)    // shm block name
    Q_PROPERTY(QString wake READ wake WRITE setWake NOTIFY wakeChanged)    // local socket name
    Q_PROPERTY(bool connected READ connected NOTIFY connectedChanged)      // wake socket up
    Q_PROPERTY(QString backend READ backend NOTIFY backendChanged)         // "opengl" | "none"
    Q_PROPERTY(QString error READ error NOTIFY errorChanged)
    Q_PROPERTY(qint64 frameSeq READ frameSeq NOTIFY frameChanged)          // frame on screen
    Q_PROPERTY(int frameWidth READ frameWidth NOTIFY frameChanged)
    Q_PROPERTY(int frameHeight READ frameHeight NOTIFY frameChanged)
    Q_PROPERTY(double frameTimestamp READ frameTimestamp NOTIFY frameChanged)   // source s
    Q_PROPERTY(double renderMs READ renderMs NOTIFY frameChanged)          // source's render
    Q_PROPERTY(double uploadMs READ uploadMs NOTIFY frameChanged)          // copy + upload, CPU
    Q_PROPERTY(int frames READ frames NOTIFY frameChanged)                 // uploads done
    Q_PROPERTY(int tornFrames READ tornFrames NOTIFY frameChanged)         // copies dropped

public:
    explicit FrameItem(QQuickItem *parent = nullptr);
    ~FrameItem() override;

    QString ring() const { return m_ring; }
    void setRing(const QString &name);
    QString wake() const { return m_wake; }
    void setWake(const QString &name);
    bool connected() const { return m_connected; }
    QString backend() const { return m_backend; }
    QString error() const { return m_error; }
    qint64 frameSeq() const { return m_seq; }
    int frameWidth() const { return m_width; }
    int frameHeight() const { return m_height; }
    double frameTimestamp() const { return m_timestamp; }
    double renderMs() const { return m_renderMs; }
    double uploadMs() const { return m_uploadMs; }
    int frames() const { return m_frames; }
    int tornFrames() const { return m_torn; }

signals:
    void ringChanged();
    void wakeChanged();
    void connectedChanged();
    void backendChanged();
    void errorChanged();
    void frameChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;

private slots:
    void takeStats();      // GUI thread, posted by the render thread
    void connectWake();
    void readWake();
    void wakeLost();

private:
    void setConnected(bool on);
    void setError(const QString &msg);

    std::shared_ptr<FrameShared> m_shared;
    QLocalSocket *m_socket = nullptr;
    QTimer *m_retry = nullptr;
    QString m_ring, m_wake, m_error;
    QString m_backend = QStringLiteral("none");
    bool m_connected = false;
    bool m_glWarned = false;
    qint64 m_seq = 0;
    int m_width = 0, m_height = 0, m_frames = 0, m_torn = 0;
    double m_timestamp = 0.0, m_renderMs = 0.0, m_uploadMs = 0.0;
};
