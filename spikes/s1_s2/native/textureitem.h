// TextureItem: a C++ QQuickItem that renders raw OpenGL on the Qt Quick render
// thread into its own FBO and hands the FBO's colour texture to the scene graph
// through QNativeInterface::QSGOpenGLTexture::fromNative (the S2 "QSGTexture
// from native" path). Stand-in for StormItem / MpvItem: no Python, no GIL, ever.
//
// Modes:
//   0 native   draw a spinning shaded mesh into the FBO (texture shared in-process,
//              zero copy)
//   1 upload   glTexSubImage2D a CPU RGBA buffer into the texture every frame
//              (simulates a shared-memory frame from a frame-source process)
//   2 pyframe  same upload, but the buffer is whatever Python last pushed into the
//              `frame` property (measures the Python -> C++ handoff too)
#pragma once

#include <QtQuick/QQuickItem>
#include <QtCore/QByteArray>
#include <QtCore/QElapsedTimer>
#include <QtCore/QMutex>
#include <QtCore/QTimer>
#include <QtCore/QtGlobal>
#include <atomic>

#if defined(SPECTOSPIKE_BUILD)
#define SPECTOSPIKE_EXPORT Q_DECL_EXPORT
#else
#define SPECTOSPIKE_EXPORT Q_DECL_IMPORT
#endif

class TextureNode;

class SPECTOSPIKE_EXPORT TextureItem : public QQuickItem
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(int mode READ mode WRITE setMode NOTIFY modeChanged)
    Q_PROPERTY(bool continuous READ continuous WRITE setContinuous NOTIFY continuousChanged)
    Q_PROPERTY(int textureWidth READ textureWidth WRITE setTextureWidth NOTIFY textureSizeChanged)
    Q_PROPERTY(int textureHeight READ textureHeight WRITE setTextureHeight NOTIFY textureSizeChanged)
    Q_PROPERTY(int triangles READ triangles WRITE setTriangles NOTIFY trianglesChanged)
    Q_PROPERTY(QByteArray frame READ frame WRITE setFrame NOTIFY frameChanged)
    // stats, refreshed on the GUI thread once per `statsInterval` ms
    Q_PROPERTY(int frames READ frames NOTIFY statsChanged)
    Q_PROPERTY(double renderFps READ renderFps NOTIFY statsChanged)
    Q_PROPERTY(double renderCpuMs READ renderCpuMs NOTIFY statsChanged)
    Q_PROPERTY(double renderGpuMs READ renderGpuMs NOTIFY statsChanged)
    Q_PROPERTY(double uploadCpuMs READ uploadCpuMs NOTIFY statsChanged)
    Q_PROPERTY(double uploadGpuMs READ uploadGpuMs NOTIFY statsChanged)
    Q_PROPERTY(double syncMs READ syncMs NOTIFY statsChanged)
    Q_PROPERTY(QString glRenderer READ glRenderer NOTIFY statsChanged)
    Q_PROPERTY(QString glVersion READ glVersion NOTIFY statsChanged)
    Q_PROPERTY(int uploadBytes READ uploadBytes NOTIFY statsChanged)

public:
    explicit TextureItem(QQuickItem *parent = nullptr);
    ~TextureItem() override;

    int mode() const { return m_mode.load(); }
    void setMode(int mode);
    bool continuous() const { return m_continuous.load(); }
    void setContinuous(bool on);
    int textureWidth() const { return m_texW; }
    void setTextureWidth(int w);
    int textureHeight() const { return m_texH; }
    void setTextureHeight(int h);
    int triangles() const { return m_triangles.load(); }
    void setTriangles(int n);
    QByteArray frame() const;
    void setFrame(const QByteArray &frame);

    int frames() const { return m_statFrames; }
    double renderFps() const { return m_statFps; }
    double renderCpuMs() const { return m_statRenderCpuMs; }
    double renderGpuMs() const { return m_statRenderGpuMs; }
    double uploadCpuMs() const { return m_statUploadCpuMs; }
    double uploadGpuMs() const { return m_statUploadGpuMs; }
    double syncMs() const { return m_statSyncMs; }
    QString glRenderer() const { return m_glRenderer; }
    QString glVersion() const { return m_glVersion; }
    int uploadBytes() const { return m_statUploadBytes; }

    Q_INVOKABLE void resetStats();

    // ---- shared with the render thread (TextureNode) ----
    struct Counters {
        std::atomic<qint64> frames{0};
        std::atomic<qint64> renderCpuNs{0};
        std::atomic<qint64> renderGpuNs{0};
        std::atomic<qint64> uploadCpuNs{0};
        std::atomic<qint64> uploadGpuNs{0};
        std::atomic<qint64> gpuSamples{0};
        std::atomic<qint64> syncNs{0};     // time spent inside updatePaintNode (GUI blocked)
        std::atomic<qint64> syncCalls{0};
        std::atomic<qint64> uploadBytes{0};
    };
    Counters &counters() { return m_counters; }
    QByteArray takeFrameForRender();   // render thread: latest Python frame (implicitly shared, no copy)
    void setGlStrings(const QString &renderer, const QString &version);

signals:
    void modeChanged();
    void continuousChanged();
    void textureSizeChanged();
    void trianglesChanged();
    void frameChanged();
    void statsChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;
    void releaseResources() override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private:
    void publishStats();

    std::atomic<int> m_mode{0};
    std::atomic<bool> m_continuous{true};
    std::atomic<int> m_triangles{200000};
    int m_texW = 0;   // 0 = follow item size * devicePixelRatio
    int m_texH = 0;

    mutable QMutex m_frameMutex;
    QByteArray m_frame;

    Counters m_counters;
    Counters m_lastSnapshot;   // values at last publishStats
    QElapsedTimer m_statClock;
    QTimer m_statTimer;
    int m_statFrames = 0;
    double m_statFps = 0, m_statRenderCpuMs = 0, m_statRenderGpuMs = 0;
    double m_statUploadCpuMs = 0, m_statUploadGpuMs = 0, m_statSyncMs = 0;
    int m_statUploadBytes = 0;
    QString m_glRenderer, m_glVersion;
    mutable QMutex m_glStringMutex;
};
