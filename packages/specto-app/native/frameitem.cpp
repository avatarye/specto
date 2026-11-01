#include "frameitem.h"

#include "shm.h"

#include <QtCore/QElapsedTimer>
#include <QtCore/QMetaObject>
#include <QtCore/QMutexLocker>
#include <QtCore/QTimer>
#include <QtGui/QOpenGLContext>
#include <QtGui/QOpenGLExtraFunctions>
#include <QtNetwork/QLocalSocket>
#include <QtQuick/QQuickOpenGLUtils>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGSimpleTextureNode>
#include <QtQuick/QSGTexture>
#include <QtQuick/qsgtexture_platform.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace {

// specto_app/frames/ring.py, version 1
constexpr char kMagic[4] = {'S', 'P', 'F', 'R'};
constexpr uint32_t kVersion = 1;
constexpr size_t kHeaderBytes = 128;
constexpr size_t kSlotHeaderBytes = 64;
constexpr size_t kSuccessorBytes = 64;
constexpr uint32_t kFormatRgba8 = 1;
constexpr int kRetryMs = 250;

struct RingHeader {
    char magic[4];
    uint32_t version;
    uint32_t slotCount;   // not `slots`: a Qt keyword
    uint32_t headerBytes;
    uint64_t slotBytes;
    uint64_t capacity;
    int32_t latest;      // source
    int32_t reading;     // reader
    uint32_t closed;
    uint32_t reserved;
    char successor[kSuccessorBytes];
};
static_assert(offsetof(RingHeader, latest) == 32, "ring layout");
static_assert(offsetof(RingHeader, successor) == 48, "ring layout");

struct SlotHeader {
    uint64_t guard;      // odd while the source writes the slot
    uint64_t seq;
    uint32_t width, height, stride, format;
    double timestamp;
    double renderMs;
};
static_assert(sizeof(SlotHeader) <= kSlotHeaderBytes, "slot layout");

// Loads and stores the source also makes from another process: volatile keeps the
// compiler from caching or reordering them, the fences order them around the copy.
template <typename T> T load(const T &v) { return *static_cast<const volatile T *>(&v); }
template <typename T> void store(T &v, T x) { *static_cast<volatile T *>(&v) = x; }

} // namespace

// ---------------------------------------------------------------------------------------
// FrameNode: render-thread side. Owns the ring mapping, the GL texture, the unpack buffer
// and the QSGTexture wrapper; frees the GL objects in its destructor (render thread, GL
// current, scene-graph cleanup).
// ---------------------------------------------------------------------------------------
class FrameNode : public QObject, public QSGSimpleTextureNode
{
public:
    FrameNode(std::shared_ptr<FrameShared> shared, QQuickWindow *window)
        : m_shared(std::move(shared)), m_window(window)
    {
        connect(window, &QQuickWindow::beforeRendering, this, &FrameNode::upload,
                Qt::DirectConnection);
        setFiltering(QSGTexture::Linear);
        setOwnsTexture(true);
    }

    ~FrameNode() override
    {
        QOpenGLContext *ctx = QOpenGLContext::currentContext();
        if (ctx) {
            QOpenGLExtraFunctions *f = ctx->extraFunctions();
            if (m_pbo)
                f->glDeleteBuffers(1, &m_pbo);
            if (m_tex)
                f->glDeleteTextures(1, &m_tex);
        }
    }

    // sync phase, GUI blocked: follow `ring`, size the texture to the newest frame
    void sync(const QString &ring)
    {
        if (!m_tex)
            ensureTexture(QSize(1, 1));   // transparent until the first frame
        if (ring != m_ringName) {
            m_ringName = ring;
            m_map.close();
            m_header = nullptr;
        }
        if (!m_header && !ring.isEmpty()) {   // retried on every update until it opens
            QString err;
            if (m_map.open(ring, &err) && validate(&err))
                m_header = reinterpret_cast<RingHeader *>(m_map.data());
            else
                m_map.close();
            report(err);
        }
        if (!m_header)
            return;
        if (load(m_header->closed)) {
            const QString next = QString::fromUtf8(
                m_header->successor, int(qstrnlen(m_header->successor, kSuccessorBytes)));
            if (!next.isEmpty() && next != m_ringName)
                follow(next);
        }
        const int32_t latest = load(m_header->latest);
        if (latest < 0 || uint32_t(latest) >= m_header->slotCount)
            return;
        const SlotHeader *slot = slotAt(latest);
        if (load(slot->seq) == m_shownSeq)
            return;
        const QSize size(int(load(slot->width)), int(load(slot->height)));
        if (size.width() > 0 && size.height() > 0)
            ensureTexture(size);
        m_pending = true;
    }

    // render phase, GUI running: copy the newest slot under its guard and upload it
    void upload()
    {
        if (!m_pending || !m_header || !m_tex)
            return;
        QOpenGLContext *ctx = QOpenGLContext::currentContext();
        if (!ctx)
            return;
        m_pending = false;
        int32_t index = announce();
        if (index < 0)
            return;
        SlotHeader *slot = slotAt(index);
        QElapsedTimer timer;
        timer.start();
        const uint64_t before = load(slot->guard);
        std::atomic_thread_fence(std::memory_order_acquire);
        const uint64_t seq = load(slot->seq);
        const uint32_t w = load(slot->width), h = load(slot->height);
        const uint32_t stride = load(slot->stride), format = load(slot->format);
        const double timestamp = load(slot->timestamp), renderMs = load(slot->renderMs);
        const size_t bytes = size_t(stride) * h;
        const bool sane = format == kFormatRgba8 && w > 0 && h > 0 && stride >= w * 4
                          && stride % 4 == 0 && bytes <= m_header->capacity;
        const bool writing = before & 1;
        if (writing || !sane || seq == m_shownSeq || QSize(int(w), int(h)) != m_texSize) {
            store(m_header->reading, int32_t(-1));
            // being written (the source raced the announcement), or resized after sync:
            // try again next frame
            if (writing || (sane && seq != m_shownSeq))
                requestUpdate();
            return;
        }

        QOpenGLExtraFunctions *f = ctx->extraFunctions();
        m_window->beginExternalCommands();
        QQuickOpenGLUtils::resetOpenGLState();
        if (!m_pbo)
            f->glGenBuffers(1, &m_pbo);
        f->glBindBuffer(GL_PIXEL_UNPACK_BUFFER, m_pbo);
        f->glBufferData(GL_PIXEL_UNPACK_BUFFER, GLsizeiptr(bytes), nullptr, GL_STREAM_DRAW);
        void *dst = f->glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, GLsizeiptr(bytes),
                                        GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT);
        bool torn = true;
        if (dst) {
            std::memcpy(dst, pixelsAt(index), bytes);
            std::atomic_thread_fence(std::memory_order_acquire);
            torn = load(slot->guard) != before;
            f->glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER);
        }
        store(m_header->reading, int32_t(-1));
        if (!torn) {
            f->glBindTexture(GL_TEXTURE_2D, m_tex);
            f->glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
            f->glPixelStorei(GL_UNPACK_ROW_LENGTH, GLint(stride / 4));
            f->glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, GLsizei(w), GLsizei(h), GL_RGBA,
                               GL_UNSIGNED_BYTE, nullptr);
            f->glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
            f->glBindTexture(GL_TEXTURE_2D, 0);
        }
        f->glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
        QQuickOpenGLUtils::resetOpenGLState();
        m_window->endExternalCommands();
        const double ms = double(timer.nsecsElapsed()) / 1e6;

        QMutexLocker lock(&m_shared->mutex);
        if (torn) {
            ++m_shared->torn;
            lock.unlock();
            post();
            requestUpdate();
            return;
        }
        m_shownSeq = seq;
        m_shared->seq = qint64(seq);
        m_shared->width = int(w);
        m_shared->height = int(h);
        m_shared->timestamp = timestamp;
        m_shared->renderMs = renderMs;
        m_shared->uploadMs = ms;
        ++m_shared->frames;
        lock.unlock();
        post();
        markDirty(QSGNode::DirtyMaterial);
    }

private:
    bool validate(QString *err)
    {
        const auto *h = reinterpret_cast<const RingHeader *>(m_map.data());
        if (m_map.size() < kHeaderBytes || std::memcmp(h->magic, kMagic, 4) != 0
            || h->version != kVersion || h->headerBytes != kHeaderBytes || h->slotCount < 2
            || h->slotBytes < kSlotHeaderBytes + h->capacity
            || m_map.size() < kHeaderBytes + size_t(h->slotCount) * h->slotBytes) {
            *err = QStringLiteral("%1 is not a version %2 frame ring").arg(m_ringName).arg(kVersion);
            return false;
        }
        return true;
    }

    SlotHeader *slotAt(int32_t i) const
    {
        return reinterpret_cast<SlotHeader *>(m_map.data() + kHeaderBytes
                                              + size_t(i) * m_header->slotBytes);
    }

    const char *pixelsAt(int32_t i) const
    {
        return reinterpret_cast<const char *>(slotAt(i)) + kSlotHeaderBytes;
    }

    // claim the newest slot so the source writes elsewhere; re-read in case it moved on
    int32_t announce()
    {
        for (int tries = 0; tries < 8; ++tries) {
            const int32_t latest = load(m_header->latest);
            if (latest < 0 || uint32_t(latest) >= m_header->slotCount)
                return -1;
            store(m_header->reading, latest);
            std::atomic_thread_fence(std::memory_order_seq_cst);
            if (load(m_header->latest) == latest)
                return latest;
        }
        store(m_header->reading, int32_t(-1));
        return -1;
    }

    void ensureTexture(const QSize &size)
    {
        if (m_tex && size == m_texSize && !m_placeholder)
            return;
        QOpenGLContext *ctx = QOpenGLContext::currentContext();
        if (!ctx)
            return;
        QOpenGLExtraFunctions *f = ctx->extraFunctions();
        const GLuint old = m_tex;
        f->glGenTextures(1, &m_tex);
        f->glBindTexture(GL_TEXTURE_2D, m_tex);
        f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        const bool placeholder = !m_tex && size == QSize(1, 1);
        const uint32_t clear = 0;
        f->glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        f->glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, size.width(), size.height(), 0, GL_RGBA,
                        GL_UNSIGNED_BYTE, placeholder ? &clear : nullptr);
        f->glBindTexture(GL_TEXTURE_2D, 0);
        m_texSize = size;
        m_placeholder = placeholder;
        // the node owns the wrapper: setTexture deletes the previous one
        const auto options = placeholder ? QQuickWindow::TextureHasAlphaChannel
                                         : QQuickWindow::CreateTextureOptions();
        setTexture(QNativeInterface::QSGOpenGLTexture::fromNative(m_tex, m_window, size, options));
        if (old)
            f->glDeleteTextures(1, &old);
        m_shownSeq = 0;
    }

    void post()
    {
        if (m_shared->posted.exchange(true))
            return;
        QMutexLocker lock(&m_shared->mutex);
        if (m_shared->item)
            QMetaObject::invokeMethod(m_shared->item, "takeStats", Qt::QueuedConnection);
        else
            m_shared->posted = false;
    }

    void requestUpdate()
    {
        QMutexLocker lock(&m_shared->mutex);
        if (FrameItem *item = m_shared->item)
            QMetaObject::invokeMethod(item, [item] { item->update(); }, Qt::QueuedConnection);
    }

    void report(const QString &err)
    {
        {
            QMutexLocker lock(&m_shared->mutex);
            if (m_shared->error == err)
                return;
            m_shared->error = err;
        }
        post();
    }

    void follow(const QString &next)
    {
        {
            QMutexLocker lock(&m_shared->mutex);
            m_shared->successor = next;
        }
        post();
    }

    std::shared_ptr<FrameShared> m_shared;
    QQuickWindow *m_window;
    specto::SharedMemoryMap m_map;
    RingHeader *m_header = nullptr;
    QString m_ringName;
    GLuint m_tex = 0, m_pbo = 0;
    QSize m_texSize;
    uint64_t m_shownSeq = 0;
    bool m_pending = false;
    bool m_placeholder = false;
};

// ---------------------------------------------------------------------------------------
// FrameItem
// ---------------------------------------------------------------------------------------
FrameItem::FrameItem(QQuickItem *parent)
    : QQuickItem(parent), m_shared(std::make_shared<FrameShared>())
{
    setFlag(ItemHasContents, true);
    m_shared->item = this;
    m_socket = new QLocalSocket(this);
    m_retry = new QTimer(this);
    m_retry->setSingleShot(true);
    m_retry->setInterval(kRetryMs);
    connect(m_retry, &QTimer::timeout, this, &FrameItem::connectWake);
    connect(m_socket, &QLocalSocket::connected, this, [this] { setConnected(true); update(); });
    connect(m_socket, &QLocalSocket::readyRead, this, &FrameItem::readWake);
    connect(m_socket, &QLocalSocket::disconnected, this, &FrameItem::wakeLost);
    connect(m_socket, &QLocalSocket::errorOccurred, this, &FrameItem::wakeLost);
}

FrameItem::~FrameItem()
{
    QMutexLocker lock(&m_shared->mutex);
    m_shared->item = nullptr;
    // the node (if any) keeps the shared block alive until the render thread drops it
}

void FrameItem::setRing(const QString &name)
{
    if (name == m_ring)
        return;
    m_ring = name;
    emit ringChanged();
    update();
}

void FrameItem::setWake(const QString &name)
{
    if (name == m_wake)
        return;
    m_wake = name;
    emit wakeChanged();
    m_retry->stop();
    m_socket->abort();
    setConnected(false);
    connectWake();
}

void FrameItem::connectWake()
{
    if (m_wake.isEmpty() || m_socket->state() != QLocalSocket::UnconnectedState)
        return;
    m_socket->connectToServer(m_wake);
}

void FrameItem::readWake()
{
    m_socket->readAll();   // the bytes carry nothing: the ring says what is newest
    update();
}

void FrameItem::wakeLost()
{
    setConnected(false);
    if (!m_wake.isEmpty() && !m_retry->isActive())
        m_retry->start();
}

void FrameItem::setConnected(bool on)
{
    if (on == m_connected)
        return;
    m_connected = on;
    emit connectedChanged();
}

void FrameItem::setError(const QString &msg)
{
    if (msg == m_error)
        return;
    m_error = msg;
    emit errorChanged();
}

void FrameItem::takeStats()
{
    QString successor, error;
    bool changed = false;
    {
        QMutexLocker lock(&m_shared->mutex);
        m_shared->posted = false;
        changed = m_shared->frames != m_frames || m_shared->torn != m_torn;
        m_seq = m_shared->seq;
        m_width = m_shared->width;
        m_height = m_shared->height;
        m_timestamp = m_shared->timestamp;
        m_renderMs = m_shared->renderMs;
        m_uploadMs = m_shared->uploadMs;
        m_frames = m_shared->frames;
        m_torn = m_shared->torn;
        successor.swap(m_shared->successor);
        error = m_shared->error;
    }
    setError(error);
    if (changed)
        emit frameChanged();
    if (!successor.isEmpty())
        setRing(successor);
}

QSGNode *FrameItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    auto *node = static_cast<FrameNode *>(oldNode);
    QQuickWindow *win = window();
    QOpenGLContext *gl = QOpenGLContext::currentContext();
    if (!gl || !win || width() < 1 || height() < 1) {
        if (!gl && !m_glWarned) {
            m_glWarned = true;   // set on the render thread, read on the GUI thread: only a hint
            QMetaObject::invokeMethod(this, [this] {
                m_backend = QStringLiteral("none");
                emit backendChanged();
            }, Qt::QueuedConnection);
        }
        delete node;
        return nullptr;
    }
    if (!node) {
        node = new FrameNode(m_shared, win);
        QMetaObject::invokeMethod(this, [this] {
            if (m_backend != QLatin1String("opengl")) {
                m_backend = QStringLiteral("opengl");
                emit backendChanged();
            }
        }, Qt::QueuedConnection);
    }
    node->sync(m_ring);
    node->setRect(boundingRect());
    return node;
}
