#include "textureitem.h"

#include <QtCore/QDebug>
#include <QtCore/QRunnable>
#include <QtGui/QMatrix4x4>
#include <QtGui/QOpenGLContext>
#include <QtGui/QOpenGLExtraFunctions>
#include <QtGui/QVector3D>
#include <QtOpenGL/QOpenGLBuffer>
#include <QtOpenGL/QOpenGLFramebufferObject>
#include <QtOpenGL/QOpenGLShaderProgram>
#include <QtOpenGL/QOpenGLTimerQuery>
#include <QtOpenGL/QOpenGLVertexArrayObject>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGSimpleTextureNode>
#include <QtQuick/QSGTexture>
#include <QtQuick/qsgtexture_platform.h>

#include <cmath>
#include <vector>

namespace {

const char *kVertexSrc = R"(#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNrm;
uniform mat4 uMvp;
uniform mat4 uModel;
out vec3 vNrm;
out vec3 vPos;
void main() {
    vNrm = mat3(uModel) * aNrm;
    vPos = (uModel * vec4(aPos, 1.0)).xyz;
    gl_Position = uMvp * vec4(aPos, 1.0);
})";

const char *kFragmentSrc = R"(#version 330 core
in vec3 vNrm;
in vec3 vPos;
out vec4 fragColor;
uniform vec3 uLightDir;
void main() {
    vec3 n = normalize(vNrm);
    vec3 l = normalize(uLightDir);
    vec3 v = normalize(vec3(0.0, 0.0, 4.0) - vPos);
    vec3 h = normalize(l + v);
    float diff = max(dot(n, l), 0.0);
    float spec = pow(max(dot(n, h), 0.0), 48.0);
    vec3 base = mix(vec3(0.93, 0.56, 0.25), vec3(0.25, 0.55, 0.85), n.y * 0.5 + 0.5);
    vec3 c = base * (0.15 + 0.85 * diff) + vec3(0.6) * spec;
    fragColor = vec4(c, 1.0);
})";

// Torus with ~`targetTriangles` triangles: interleaved position(3) + normal(3).
void buildTorus(int targetTriangles, std::vector<float> &verts, std::vector<unsigned> &idx)
{
    int quads = std::max(64, targetTriangles / 2);
    int rings = std::max(8, int(std::sqrt(double(quads) / 3.0)));
    int segs = std::max(8, quads / rings);
    const float R = 1.0f, r = 0.42f;
    verts.clear();
    idx.clear();
    verts.reserve(size_t(rings + 1) * size_t(segs + 1) * 6);
    for (int i = 0; i <= rings; ++i) {
        float u = float(i) / rings * 6.2831853f;
        float cu = std::cos(u), su = std::sin(u);
        for (int j = 0; j <= segs; ++j) {
            float v = float(j) / segs * 6.2831853f;
            float cv = std::cos(v), sv = std::sin(v);
            float x = (R + r * cv) * cu, y = (R + r * cv) * su, z = r * sv;
            float nx = cv * cu, ny = cv * su, nz = sv;
            verts.insert(verts.end(), {x, y, z, nx, ny, nz});
        }
    }
    idx.reserve(size_t(rings) * size_t(segs) * 6);
    for (int i = 0; i < rings; ++i) {
        for (int j = 0; j < segs; ++j) {
            unsigned a = unsigned(i * (segs + 1) + j);
            unsigned b = unsigned((i + 1) * (segs + 1) + j);
            idx.insert(idx.end(), {a, b, a + 1, a + 1, b, b + 1});
        }
    }
}

} // namespace

// ---------------------------------------------------------------------------
// TextureNode lives on the render thread. It owns the FBO, the GL program and
// the QSGTexture wrapper created with QSGOpenGLTexture::fromNative.
// ---------------------------------------------------------------------------
class TextureNode : public QObject, public QSGSimpleTextureNode
{
public:
    TextureNode(TextureItem *item, QQuickWindow *window)
        : m_item(item), m_window(window)
    {
        // DirectConnection: render() runs on the render thread, before the scene
        // graph's own render pass, exactly like QQuickFramebufferObject does it.
        connect(window, &QQuickWindow::beforeRendering, this, &TextureNode::render,
                Qt::DirectConnection);
        setFiltering(QSGTexture::Linear);
        setTextureCoordinatesTransform(QSGSimpleTextureNode::MirrorVertically);
        setOwnsTexture(true);
    }

    ~TextureNode() override
    {
        // Render thread, context current (scene graph cleanup).
        for (auto &q : m_queries) {
            delete q.upload;
            delete q.draw;
        }
        delete m_fbo;
        delete m_program;
        delete m_vbo;
        delete m_ibo;
        delete m_vao;
    }

    // Called from updatePaintNode (render thread, GUI thread blocked, GL current).
    void ensureResources(const QSize &size)
    {
        if (m_fbo && m_fbo->size() == size)
            return;
        delete m_fbo;
        QOpenGLFramebufferObjectFormat fmt;
        fmt.setAttachment(QOpenGLFramebufferObject::CombinedDepthStencil);
        fmt.setInternalTextureFormat(GL_RGBA8);
        m_fbo = new QOpenGLFramebufferObject(size, fmt);
        // S2 check: wrap an existing native GL texture id as a QSGTexture.
        QSGTexture *wrapper = QNativeInterface::QSGOpenGLTexture::fromNative(
            m_fbo->texture(), m_window, size, {});
        setTexture(wrapper);   // ownsTexture: the previous wrapper is deleted
        m_renderPending = true;
        m_uploadRows.clear();
    }

    void scheduleRender() { m_renderPending = true; }

private:
    void initGl(QOpenGLExtraFunctions *f)
    {
        if (m_program)
            return;
        m_program = new QOpenGLShaderProgram;
        m_program->addShaderFromSourceCode(QOpenGLShader::Vertex, kVertexSrc);
        m_program->addShaderFromSourceCode(QOpenGLShader::Fragment, kFragmentSrc);
        if (!m_program->link())
            qWarning() << "[ERROR] TextureItem shader link:" << m_program->log();
        m_vao = new QOpenGLVertexArrayObject;
        m_vao->create();
        m_vbo = new QOpenGLBuffer(QOpenGLBuffer::VertexBuffer);
        m_vbo->create();
        m_ibo = new QOpenGLBuffer(QOpenGLBuffer::IndexBuffer);
        m_ibo->create();
        for (auto &q : m_queries) {
            q.upload = new QOpenGLTimerQuery;
            q.draw = new QOpenGLTimerQuery;
            q.haveTimers = q.upload->create() && q.draw->create();
            q.used = false;
        }
        m_item->setGlStrings(
            QString::fromLatin1(reinterpret_cast<const char *>(f->glGetString(GL_RENDERER))),
            QString::fromLatin1(reinterpret_cast<const char *>(f->glGetString(GL_VERSION))));
    }

    void ensureMesh(int triangles)
    {
        if (m_meshTriangles == triangles)
            return;
        std::vector<float> verts;
        std::vector<unsigned> idx;
        buildTorus(triangles, verts, idx);
        m_vao->bind();
        m_vbo->bind();
        m_vbo->allocate(verts.data(), int(verts.size() * sizeof(float)));
        m_ibo->bind();
        m_ibo->allocate(idx.data(), int(idx.size() * sizeof(unsigned)));
        auto *f = QOpenGLContext::currentContext()->extraFunctions();
        f->glEnableVertexAttribArray(0);
        f->glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 6 * sizeof(float), nullptr);
        f->glEnableVertexAttribArray(1);
        f->glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 6 * sizeof(float),
                                 reinterpret_cast<void *>(3 * sizeof(float)));
        m_vao->release();
        m_indexCount = int(idx.size());
        m_meshTriangles = triangles;
    }

    // Synthetic "shared-memory frame": (H + 256) rows, scrolled by row offset so
    // each frame uploads different bytes without any CPU fill per frame.
    void ensureUploadRows(int w, int h)
    {
        const size_t need = size_t(w) * size_t(h + 256) * 4;
        if (m_uploadRows.size() == need)
            return;
        m_uploadRows.resize(need);
        for (int y = 0; y < h + 256; ++y) {
            for (int x = 0; x < w; ++x) {
                unsigned char *p = &m_uploadRows[(size_t(y) * w + x) * 4];
                bool stripe = ((x + y) / 24) % 2 == 0;
                p[0] = (unsigned char)(stripe ? 230 : 40 + (x * 200) / w);
                p[1] = (unsigned char)(stripe ? 150 : 90 + (y * 120) / (h + 256));
                p[2] = (unsigned char)(stripe ? 60 : 200);
                p[3] = 255;
            }
        }
    }

    void drawMesh(QOpenGLExtraFunctions *f, const QSize &size, qint64 frame)
    {
        f->glEnable(GL_DEPTH_TEST);
        f->glDisable(GL_BLEND);
        f->glDisable(GL_SCISSOR_TEST);
        f->glViewport(0, 0, size.width(), size.height());
        f->glClearColor(0.97f, 0.95f, 0.91f, 1.0f);   // Calm Canvas warm paper
        f->glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

        QMatrix4x4 proj, view, model;
        proj.perspective(40.0f, float(size.width()) / float(size.height()), 0.1f, 20.0f);
        view.lookAt(QVector3D(0, 0, 4), QVector3D(0, 0, 0), QVector3D(0, 1, 0));
        float t = float(frame) / 60.0f;
        model.rotate(t * 40.0f, 0.3f, 1.0f, 0.1f);
        model.rotate(t * 17.0f, 1.0f, 0.0f, 0.0f);

        m_program->bind();
        m_program->setUniformValue("uMvp", proj * view * model);
        m_program->setUniformValue("uModel", model);
        m_program->setUniformValue("uLightDir", QVector3D(0.4f, 0.8f, 0.6f));
        m_vao->bind();
        f->glDrawElements(GL_TRIANGLES, m_indexCount, GL_UNSIGNED_INT, nullptr);
        m_vao->release();
        m_program->release();
        f->glDisable(GL_DEPTH_TEST);
    }

public:
    void render()
    {
        const bool continuous = m_item->continuous();
        if (!m_renderPending && !continuous)
            return;
        m_renderPending = false;
        QOpenGLContext *ctx = QOpenGLContext::currentContext();
        if (!ctx || !m_fbo)
            return;
        auto *f = ctx->extraFunctions();
        auto &c = m_item->counters();
        const int mode = m_item->mode();
        const QSize size = m_fbo->size();

        m_window->beginExternalCommands();
        initGl(f);
        Slot &slot = m_queries[m_frame % kSlots];
        if (slot.used && slot.haveTimers) {
            // Results from kSlots frames ago: never stalls in practice.
            c.uploadGpuNs += slot.upload->waitForResult();
            c.renderGpuNs += slot.draw->waitForResult();
            c.gpuSamples += 1;
            slot.used = false;
        }

        QElapsedTimer clock;
        clock.start();
        qint64 uploadNs = 0;
        if (mode == 1 || mode == 2) {
            const int w = size.width(), h = size.height();
            const unsigned char *src = nullptr;
            QByteArray pyFrame;
            if (mode == 1) {
                ensureUploadRows(w, h);
                src = &m_uploadRows[size_t(m_frame % 256) * size_t(w) * 4];
            } else {
                pyFrame = m_item->takeFrameForRender();
                if (pyFrame.size() >= w * h * 4)
                    src = reinterpret_cast<const unsigned char *>(pyFrame.constData());
            }
            if (src) {
                if (slot.haveTimers) slot.upload->begin();
                f->glBindTexture(GL_TEXTURE_2D, m_fbo->texture());
                f->glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
                f->glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, src);
                f->glBindTexture(GL_TEXTURE_2D, 0);
                if (slot.haveTimers) slot.upload->end();
                c.uploadBytes = qint64(w) * h * 4;
                uploadNs = clock.nsecsElapsed();
            }
        } else if (slot.haveTimers) {
            slot.upload->begin();
            slot.upload->end();
        }
        qint64 drawNs = 0;
        if (mode == 0) {
            ensureMesh(m_item->triangles());
            m_fbo->bind();
            if (slot.haveTimers) slot.draw->begin();
            drawMesh(f, size, m_frame);
            if (slot.haveTimers) slot.draw->end();
            m_fbo->release();
            drawNs = clock.nsecsElapsed() - uploadNs;
        } else if (slot.haveTimers) {
            slot.draw->begin();
            slot.draw->end();
        }
        slot.used = slot.haveTimers;
        m_window->endExternalCommands();

        c.uploadCpuNs += uploadNs;
        c.renderCpuNs += drawNs;
        c.frames += 1;
        ++m_frame;

        markDirty(QSGNode::DirtyMaterial);
        if (continuous)
            m_window->update();   // thread-safe, same as QQuickFramebufferObject
    }

private:
    static constexpr int kSlots = 3;
    struct Slot {
        QOpenGLTimerQuery *upload = nullptr;
        QOpenGLTimerQuery *draw = nullptr;
        bool haveTimers = false;
        bool used = false;
    };

    TextureItem *m_item;
    QQuickWindow *m_window;
    QOpenGLFramebufferObject *m_fbo = nullptr;
    QOpenGLShaderProgram *m_program = nullptr;
    QOpenGLVertexArrayObject *m_vao = nullptr;
    QOpenGLBuffer *m_vbo = nullptr;
    QOpenGLBuffer *m_ibo = nullptr;
    int m_indexCount = 0;
    int m_meshTriangles = -1;
    std::vector<unsigned char> m_uploadRows;
    Slot m_queries[kSlots];
    qint64 m_frame = 0;
    bool m_renderPending = true;
};

// ---------------------------------------------------------------------------
// TextureItem (GUI thread, except the three render-thread entry points)
// ---------------------------------------------------------------------------
TextureItem::TextureItem(QQuickItem *parent)
    : QQuickItem(parent)
{
    setFlag(ItemHasContents, true);
    m_statClock.start();
    m_statTimer.setInterval(500);
    connect(&m_statTimer, &QTimer::timeout, this, &TextureItem::publishStats);
    m_statTimer.start();
}

TextureItem::~TextureItem() = default;

void TextureItem::setMode(int mode)
{
    if (m_mode.exchange(mode) != mode) {
        emit modeChanged();
        update();
    }
}

void TextureItem::setContinuous(bool on)
{
    if (m_continuous.exchange(on) != on) {
        emit continuousChanged();
        update();
    }
}

void TextureItem::setTextureWidth(int w)
{
    if (m_texW != w) { m_texW = w; emit textureSizeChanged(); update(); }
}

void TextureItem::setTextureHeight(int h)
{
    if (m_texH != h) { m_texH = h; emit textureSizeChanged(); update(); }
}

void TextureItem::setTriangles(int n)
{
    if (m_triangles.exchange(n) != n) { emit trianglesChanged(); update(); }
}

QByteArray TextureItem::frame() const
{
    QMutexLocker lock(&m_frameMutex);
    return m_frame;
}

void TextureItem::setFrame(const QByteArray &frame)
{
    {
        QMutexLocker lock(&m_frameMutex);
        m_frame = frame;   // implicitly shared: no copy here
    }
    emit frameChanged();
    update();
}

QByteArray TextureItem::takeFrameForRender()
{
    QMutexLocker lock(&m_frameMutex);
    return m_frame;
}

void TextureItem::setGlStrings(const QString &renderer, const QString &version)
{
    QMutexLocker lock(&m_glStringMutex);
    m_glRenderer = renderer;
    m_glVersion = version;
}

void TextureItem::resetStats()
{
    auto reset = [](Counters &c) {
        c.frames = 0; c.renderCpuNs = 0; c.renderGpuNs = 0; c.uploadCpuNs = 0;
        c.uploadGpuNs = 0; c.gpuSamples = 0; c.syncNs = 0; c.syncCalls = 0;
    };
    reset(m_counters);
    reset(m_lastSnapshot);
    m_statClock.restart();
}

void TextureItem::publishStats()
{
    const double dt = m_statClock.nsecsElapsed() / 1e9;
    if (dt <= 0) return;
    const qint64 frames = m_counters.frames - m_lastSnapshot.frames;
    const qint64 gpuSamples = m_counters.gpuSamples - m_lastSnapshot.gpuSamples;
    const qint64 syncCalls = m_counters.syncCalls - m_lastSnapshot.syncCalls;
    m_statFrames = int(m_counters.frames.load());
    m_statFps = frames / dt;
    auto avgMs = [](qint64 ns, qint64 n) { return n > 0 ? double(ns) / n / 1e6 : 0.0; };
    m_statRenderCpuMs = avgMs(m_counters.renderCpuNs - m_lastSnapshot.renderCpuNs, frames);
    m_statUploadCpuMs = avgMs(m_counters.uploadCpuNs - m_lastSnapshot.uploadCpuNs, frames);
    m_statRenderGpuMs = avgMs(m_counters.renderGpuNs - m_lastSnapshot.renderGpuNs, gpuSamples);
    m_statUploadGpuMs = avgMs(m_counters.uploadGpuNs - m_lastSnapshot.uploadGpuNs, gpuSamples);
    m_statSyncMs = avgMs(m_counters.syncNs - m_lastSnapshot.syncNs, syncCalls);
    m_statUploadBytes = int(m_counters.uploadBytes.load());
    m_lastSnapshot.frames = m_counters.frames.load();
    m_lastSnapshot.renderCpuNs = m_counters.renderCpuNs.load();
    m_lastSnapshot.uploadCpuNs = m_counters.uploadCpuNs.load();
    m_lastSnapshot.renderGpuNs = m_counters.renderGpuNs.load();
    m_lastSnapshot.uploadGpuNs = m_counters.uploadGpuNs.load();
    m_lastSnapshot.gpuSamples = m_counters.gpuSamples.load();
    m_lastSnapshot.syncNs = m_counters.syncNs.load();
    m_lastSnapshot.syncCalls = m_counters.syncCalls.load();
    m_statClock.restart();
    emit statsChanged();
}

void TextureItem::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    update();
}

QSGNode *TextureItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    QElapsedTimer clock;
    clock.start();
    auto *node = static_cast<TextureNode *>(oldNode);
    if (!node) {
        if (!window())
            return nullptr;
        if (window()->rendererInterface()->graphicsApi() != QSGRendererInterface::OpenGL) {
            qWarning() << "[ERROR] TextureItem needs the OpenGL RHI backend, got"
                       << window()->rendererInterface()->graphicsApi();
            return nullptr;
        }
        node = new TextureNode(this, window());
    }
    const qreal dpr = window()->effectiveDevicePixelRatio();
    QSize size(m_texW > 0 ? m_texW : int(width() * dpr),
               m_texH > 0 ? m_texH : int(height() * dpr));
    size = size.expandedTo(QSize(16, 16));
    node->ensureResources(size);
    node->setRect(boundingRect());
    node->scheduleRender();
    m_counters.syncNs += clock.nsecsElapsed();
    m_counters.syncCalls += 1;
    return node;
}

void TextureItem::releaseResources()
{
    // Node deletion must happen on the render thread with the context current;
    // the scene graph does that when it drops the node, nothing extra needed here.
}

