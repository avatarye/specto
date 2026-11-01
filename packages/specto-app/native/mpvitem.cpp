#include "mpvitem.h"

#include "mpvapi.h"

#include <QtCore/QByteArray>
#include <QtCore/QDebug>
#include <QtCore/QMetaObject>
#include <QtCore/QMutexLocker>
#include <QtGui/QOpenGLContext>
#include <QtOpenGL/QOpenGLFramebufferObject>
#include <QtQuick/QQuickOpenGLUtils>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGSimpleTextureNode>
#include <QtQuick/QSGTexture>
#include <QtQuick/qsgtexture_platform.h>

#include <clocale>
#include <cstring>
#include <vector>

namespace {

enum ObservedProperty : uint64_t {
    PropTimePos = 1,
    PropDuration,
    PropPause,
    PropSpeed,
    PropLoopA,
    PropLoopB,
    PropFps,
    PropFrame,
    PropFrameCount,
    PropWidth,
    PropHeight,
    PropEof,
    PropSeeking,
    PropMute,
    PropVolume,
    PropAid,
};

void *getProcAddress(void *, const char *name)
{
    QOpenGLContext *ctx = QOpenGLContext::currentContext();
    return ctx ? reinterpret_cast<void *>(ctx->getProcAddress(name)) : nullptr;
}

QVariant fromNode(const mpv_node &n)
{
    switch (n.format) {
    case MPV_FORMAT_FLAG:
        return bool(n.u.flag);
    case MPV_FORMAT_INT64:
        return qint64(n.u.int64);
    case MPV_FORMAT_DOUBLE:
        return n.u.double_;
    case MPV_FORMAT_STRING:
        return QString::fromUtf8(n.u.string);
    case MPV_FORMAT_NODE_ARRAY: {
        QVariantList out;
        for (int i = 0; i < n.u.list->num; ++i)
            out << fromNode(n.u.list->values[i]);
        return out;
    }
    case MPV_FORMAT_NODE_MAP: {
        QVariantMap out;
        for (int i = 0; i < n.u.list->num; ++i)
            out[QString::fromUtf8(n.u.list->keys[i])] = fromNode(n.u.list->values[i]);
        return out;
    }
    default:
        return QVariant();
    }
}

// "no" for a cleared A-B loop point, else the time
double loopFrom(const mpv_event_property *p)
{
    if (p->format == MPV_FORMAT_DOUBLE)
        return *static_cast<double *>(p->data);
    if (p->format == MPV_FORMAT_NODE) {
        const mpv_node *n = static_cast<mpv_node *>(p->data);
        if (n->format == MPV_FORMAT_DOUBLE)
            return n->u.double_;
    }
    return -1.0;
}

} // namespace

// ---------------------------------------------------------------------------------------
// MpvCore
// ---------------------------------------------------------------------------------------
void MpvCore::freeRender()
{
    if (!renderCtx)
        return;
    const MpvApi *api = mpvApi();
    api->render_context_set_update_callback(renderCtx, nullptr, nullptr);
    api->render_context_free(renderCtx);
    renderCtx = nullptr;
}

MpvCore::~MpvCore()
{
    freeRender();   // normally already done by the node on the render thread
    if (mpv) {
        const MpvApi *api = mpvApi();
        api->set_wakeup_callback(mpv, nullptr, nullptr);
        api->terminate_destroy(mpv);
        mpv = nullptr;
    }
}

// ---------------------------------------------------------------------------------------
// MpvNode: render-thread side. Owns the FBO and the QSGTexture wrapper; frees the mpv
// render context in its destructor (render thread, GL current, scene-graph cleanup).
// The video is drawn in the window's beforeRendering (render phase, the spike's proven
// slot for FBO rendering, between begin/endExternalCommands); updatePaintNode (sync)
// only decides whether a frame is pending.
// ---------------------------------------------------------------------------------------
class MpvNode : public QObject, public QSGSimpleTextureNode
{
public:
    MpvNode(std::shared_ptr<MpvCore> core, QQuickWindow *window)
        : m_core(std::move(core)), m_window(window)
    {
        connect(window, &QQuickWindow::beforeRendering, this, &MpvNode::render,
                Qt::DirectConnection);
        setFiltering(QSGTexture::Linear);
        // No texture flip: mpv's unflipped FBO output (FLIP_Y 0 below) already lands in
        // the scene graph's orientation; MirrorVertically on top of it played every
        // video upside down (test_video_orientation.py).
        setOwnsTexture(true);
    }

    ~MpvNode() override
    {
        m_core->freeRender();
        delete m_fbo;
    }

    // true when a new FBO was made (needs a redraw)
    bool ensureFbo(const QSize &size)
    {
        if (m_fbo && m_fbo->size() == size)
            return false;
        delete m_fbo;
        QOpenGLFramebufferObjectFormat fmt;
        fmt.setInternalTextureFormat(GL_RGBA8);
        m_fbo = new QOpenGLFramebufferObject(size, fmt);
        QSGTexture *wrapper = QNativeInterface::QSGOpenGLTexture::fromNative(
            m_fbo->texture(), m_window, size, {});
        setTexture(wrapper);
        m_pending = true;
        return true;
    }

    void scheduleRender() { m_pending = true; }

    // render thread, render phase, GL current
    void render()
    {
        if (!m_pending || !m_fbo || !m_core->renderCtx)
            return;
        QOpenGLContext *gl = QOpenGLContext::currentContext();
        if (!gl)
            return;
        m_pending = false;
        const MpvApi *api = mpvApi();
        const QSize size = m_fbo->size();
        m_window->beginExternalCommands();
        // The scene graph leaves its own state behind (scissor from clipping, stencil,
        // blend, bound buffers); mpv assumes defaults and draws its intermediate passes
        // through that scissor otherwise: chroma-less green frames, then garbage.
        QQuickOpenGLUtils::resetOpenGLState();
        mpv_opengl_fbo fbo{int(m_fbo->handle()), size.width(), size.height(), 0};
        int flipY = 0;
        int block = 0;   // never wait for the target time on the render thread
        mpv_render_param rp[] = {
            {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
            {MPV_RENDER_PARAM_FLIP_Y, &flipY},
            {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block},
            {MPV_RENDER_PARAM_INVALID, nullptr},
        };
        api->render_context_render(m_core->renderCtx, rp);
        QQuickOpenGLUtils::resetOpenGLState();
        m_window->endExternalCommands();
        m_core->renderFrames.fetch_add(1);
        markDirty(QSGNode::DirtyMaterial);
    }

private:
    std::shared_ptr<MpvCore> m_core;
    QQuickWindow *m_window;
    QOpenGLFramebufferObject *m_fbo = nullptr;
    bool m_pending = false;
};

// ---------------------------------------------------------------------------------------
// MpvItem
// ---------------------------------------------------------------------------------------
MpvItem::MpvItem(QQuickItem *parent)
    : QQuickItem(parent), m_core(std::make_shared<MpvCore>())
{
    setFlag(ItemHasContents, true);
    m_core->item = this;
    initCore();
}

MpvItem::~MpvItem()
{
    {
        QMutexLocker lock(&m_core->itemMutex);
        m_core->item = nullptr;
    }
    if (m_core->mpv)
        mpvApi()->set_wakeup_callback(m_core->mpv, nullptr, nullptr);
    // the node (if any) keeps the core alive until the render thread frees the GL side
}

QString MpvItem::mpvVersion() const
{
    return mpvApi()->version;
}

int MpvItem::renderFrames() const
{
    return m_core->renderFrames.load();
}

void MpvItem::setError(const QString &msg)
{
    if (msg == m_error)
        return;
    m_error = msg;
    emit errorChanged();
}

void MpvItem::initCore()
{
    const MpvApi *api = mpvApi();
    if (!api->ok()) {
        setError(api->error);
        return;
    }
    // libmpv refuses to start under a non-C LC_NUMERIC (it parses floats with the C
    // library), and QGuiApplication applies the user's locale on Unix
    std::setlocale(LC_NUMERIC, "C");
    mpv_handle *mpv = api->create();
    if (!mpv) {
        setError(QStringLiteral("mpv_create failed"));
        return;
    }
    // a viewer: no window, no OSD, exact seeks, hold the last frame; sound through the
    // platform's audio output (ao auto; #216), muted until a view asks for it
    static const char *options[][2] = {
        {"vo", "libmpv"},
        {"mute", "yes"},
        {"volume", "100"},
        {"keep-open", "yes"},
        {"idle", "yes"},
        {"pause", "yes"},
        {"hr-seek", "yes"},
        {"hr-seek-framedrop", "no"},
        {"terminal", "no"},
        {"osc", "no"},
        {"osd-level", "0"},
        {"input-default-bindings", "no"},
        {"input-vo-keyboard", "no"},
        // software decode into plain CPU buffers: with hwdec copy modes or direct rendering
        // (decoder threads writing GPU-mapped buffers) the frames arrived torn or as chroma
        // noise, because this item renders only when Qt asks and never reports swaps
        {"hwdec", "no"},
        {"vd-lavc-dr", "no"},
        {"opengl-pbo", "no"},
        {"loop-file", "no"},
        {"video-sync", "audio"},
        {"msg-level", "all=warn"},
        // no Lua: LuaJIT unwinds every Lua error, even a caught one, as an SEH exception
        // (0xE24C4A02) that a host's faulthandler logs from libmpv's thread, racing the
        // Python thread states (#172); thumbs.NO_LUA turns the same scripts off
        {"load-scripts", "no"},
        {"ytdl", "no"},
        {"load-stats-overlay", "no"},
        {"load-console", "no"},
        {"load-auto-profiles", "no"},
        {"load-select", "no"},
        {"load-positioning", "no"},
        {"load-commands", "no"},
        {"load-context-menu", "no"},
    };
    for (const auto &kv : options)
        api->set_option_string(mpv, kv[0], kv[1]);
    const int r = api->initialize(mpv);
    if (r < 0) {
        setError(QStringLiteral("mpv_initialize: %1").arg(QString::fromUtf8(api->error_string(r))));
        api->terminate_destroy(mpv);
        return;
    }
    api->observe_property(mpv, PropTimePos, "time-pos", MPV_FORMAT_DOUBLE);
    api->observe_property(mpv, PropDuration, "duration", MPV_FORMAT_DOUBLE);
    api->observe_property(mpv, PropPause, "pause", MPV_FORMAT_FLAG);
    api->observe_property(mpv, PropSpeed, "speed", MPV_FORMAT_DOUBLE);
    api->observe_property(mpv, PropLoopA, "ab-loop-a", MPV_FORMAT_NODE);
    api->observe_property(mpv, PropLoopB, "ab-loop-b", MPV_FORMAT_NODE);
    api->observe_property(mpv, PropFps, "container-fps", MPV_FORMAT_DOUBLE);
    api->observe_property(mpv, PropFrame, "estimated-frame-number", MPV_FORMAT_INT64);
    api->observe_property(mpv, PropFrameCount, "estimated-frame-count", MPV_FORMAT_INT64);
    api->observe_property(mpv, PropWidth, "width", MPV_FORMAT_INT64);
    api->observe_property(mpv, PropHeight, "height", MPV_FORMAT_INT64);
    api->observe_property(mpv, PropEof, "eof-reached", MPV_FORMAT_FLAG);
    api->observe_property(mpv, PropSeeking, "seeking", MPV_FORMAT_FLAG);
    api->observe_property(mpv, PropMute, "mute", MPV_FORMAT_FLAG);
    api->observe_property(mpv, PropVolume, "volume", MPV_FORMAT_DOUBLE);
    api->observe_property(mpv, PropAid, "aid", MPV_FORMAT_NODE);   // int64 for a track, else "no"
    api->request_log_messages(mpv, "warn");
    m_core->mpv = mpv;
    api->set_wakeup_callback(mpv, &MpvItem::wakeup, m_core.get());
    m_ready = true;
    emit readyChanged();
}

// -- callbacks on mpv's threads: post to the GUI thread, nothing else ------------------
void MpvItem::wakeup(void *ctx)
{
    auto *core = static_cast<MpvCore *>(ctx);
    if (core->eventsPosted.exchange(true))
        return;
    QMutexLocker lock(&core->itemMutex);
    if (core->item)
        QMetaObject::invokeMethod(core->item, "pollEvents", Qt::QueuedConnection);
    else
        core->eventsPosted = false;
}

void MpvItem::onRenderUpdate(void *ctx)
{
    auto *core = static_cast<MpvCore *>(ctx);
    if (core->updatePosted.exchange(true))
        return;
    QMutexLocker lock(&core->itemMutex);
    if (core->item)
        QMetaObject::invokeMethod(core->item, "renderUpdate", Qt::QueuedConnection);
    else
        core->updatePosted = false;
}

void MpvItem::renderUpdate()
{
    m_core->updatePosted = false;
    update();   // GUI thread: the one place a frame is requested
}

// -- events (GUI thread) ----------------------------------------------------------------
void MpvItem::pollEvents()
{
    m_core->eventsPosted = false;
    if (!m_core->mpv)
        return;
    const MpvApi *api = mpvApi();
    for (;;) {
        mpv_event *ev = api->wait_event(m_core->mpv, 0.0);
        if (!ev || ev->event_id == MPV_EVENT_NONE)
            break;
        handleEvent(ev);
    }
}

void MpvItem::handleEvent(mpv_event *ev)
{
    switch (ev->event_id) {
    case MPV_EVENT_PROPERTY_CHANGE: {
        auto *p = static_cast<mpv_event_property *>(ev->data);
        const bool hasDouble = p->format == MPV_FORMAT_DOUBLE;
        const double d = hasDouble ? *static_cast<double *>(p->data) : 0.0;
        const bool hasFlag = p->format == MPV_FORMAT_FLAG;
        const bool f = hasFlag ? *static_cast<int *>(p->data) != 0 : false;
        const bool hasInt = p->format == MPV_FORMAT_INT64;
        const qint64 i = hasInt ? *static_cast<int64_t *>(p->data) : 0;
        switch (ev->reply_userdata) {
        case PropTimePos:
            if (hasDouble && d != m_position) {
                m_position = d;
                refreshFrame();   // estimated-frame-number is derived: observe never fires
                emit positionChanged();
            }
            break;
        case PropDuration:
            if (hasDouble && d != m_duration) {
                m_duration = d;
                emit durationChanged();
            }
            break;
        case PropPause:
            if (hasFlag && f != m_paused) {
                m_paused = f;
                emit pausedChanged();
            }
            break;
        case PropSpeed:
            if (hasDouble && d != m_speed) {
                m_speed = d;
                emit speedChanged();
            }
            break;
        case PropLoopA: {
            const double t = loopFrom(p);
            if (t != m_loopA) {
                m_loopA = t;
                emit loopChanged();
            }
            break;
        }
        case PropLoopB: {
            const double t = loopFrom(p);
            if (t != m_loopB) {
                m_loopB = t;
                emit loopChanged();
            }
            break;
        }
        case PropFps:
            if (hasDouble && d != m_fps) {
                m_fps = d;
                emit fpsChanged();
            }
            break;
        case PropFrame:
            if (hasInt && i != m_frame) {
                m_frame = i;
                emit frameChanged();
            }
            break;
        case PropFrameCount:
            if (hasInt && i != m_frameCount) {
                m_frameCount = i;
                emit frameChanged();
            }
            break;
        case PropWidth:
            if (hasInt && int(i) != m_videoW) {
                m_videoW = int(i);
                emit videoSizeChanged();
            }
            break;
        case PropHeight:
            if (hasInt && int(i) != m_videoH) {
                m_videoH = int(i);
                emit videoSizeChanged();
            }
            break;
        case PropEof:
            if (hasFlag && f != m_eof) {
                m_eof = f;
                emit eofChanged();
            }
            break;
        case PropSeeking:
            if (hasFlag && f != m_seeking) {
                m_seeking = f;
                emit seekingChanged();
            }
            break;
        case PropMute:
            if (hasFlag && f != m_muted) {
                m_muted = f;
                emit mutedChanged();
            }
            break;
        case PropVolume:
            if (hasDouble && d != m_volume) {
                m_volume = d;
                emit volumeChanged();
            }
            break;
        case PropAid: {
            const bool has = p->format == MPV_FORMAT_NODE
                && static_cast<mpv_node *>(p->data)->format == MPV_FORMAT_INT64;
            if (has != m_hasAudio) {
                m_hasAudio = has;
                emit hasAudioChanged();
            }
            break;
        }
        default:
            break;
        }
        break;
    }
    case MPV_EVENT_FILE_LOADED:
        m_fileLoaded = true;
        refreshAfterLoad();   // duration, fps, size, frame count before `loaded` fires
        setError(QString());
        if (m_restorePos >= 0) {
            seek(m_restorePos, true, false);
            m_restorePos = -1.0;
        }
        emit fileLoadedChanged();
        emit loaded();
        break;
    case MPV_EVENT_END_FILE: {
        auto *e = static_cast<mpv_event_end_file *>(ev->data);
        QString reason;
        switch (e->reason) {
        case MPV_END_FILE_REASON_EOF: reason = QStringLiteral("eof"); break;
        case MPV_END_FILE_REASON_STOP: reason = QStringLiteral("stop"); break;
        case MPV_END_FILE_REASON_QUIT: reason = QStringLiteral("quit"); break;
        case MPV_END_FILE_REASON_ERROR:
            reason = QStringLiteral("error: %1").arg(QString::fromUtf8(mpvApi()->error_string(e->error)));
            setError(QStringLiteral("cannot play %1 (%2)").arg(m_source, reason));
            break;
        case MPV_END_FILE_REASON_REDIRECT: reason = QStringLiteral("redirect"); break;
        default: reason = QStringLiteral("unknown"); break;
        }
        if (m_fileLoaded && e->reason != MPV_END_FILE_REASON_EOF) {
            m_fileLoaded = false;
            emit fileLoadedChanged();
        }
        emit endFile(reason);
        break;
    }
    case MPV_EVENT_PLAYBACK_RESTART:
        emit playbackRestart();
        break;
    case MPV_EVENT_LOG_MESSAGE: {
        auto *m = static_cast<mpv_event_log_message *>(ev->data);
        const QString level = QString::fromUtf8(m->level);
        const QString prefix = QString::fromUtf8(m->prefix);
        const QString text = QString::fromUtf8(m->text).trimmed();
        // an audio device that will not open ("ao/wasapi: ...", "cplayer: Could not
        // open/initialize audio device -> no sound") leaves the picture playing: not
        // the view's error, only its log
        const bool audio = prefix.startsWith(QLatin1String("ao"))
            || prefix.startsWith(QLatin1String("audio"))
            || text.contains(QLatin1String("audio device"));
        if ((level == QLatin1String("error") || level == QLatin1String("fatal")) && !audio)
            setError(prefix + QLatin1String(": ") + text);
        emit logMessage(level, prefix, text);
        break;
    }
    default:
        break;
    }
}

void MpvItem::refreshFrame()
{
    int64_t fr = -1;
    if (mpvApi()->get_property(m_core->mpv, "estimated-frame-number", MPV_FORMAT_INT64, &fr) >= 0
        && fr != m_frame) {
        m_frame = fr;
        emit frameChanged();
    }
}

void MpvItem::refreshAfterLoad()
{
    const MpvApi *api = mpvApi();
    double d = 0.0;
    if (api->get_property(m_core->mpv, "duration", MPV_FORMAT_DOUBLE, &d) >= 0 && d != m_duration) {
        m_duration = d;
        emit durationChanged();
    }
    if (api->get_property(m_core->mpv, "container-fps", MPV_FORMAT_DOUBLE, &d) >= 0 && d != m_fps) {
        m_fps = d;
        emit fpsChanged();
    }
    int64_t i = 0;
    if (api->get_property(m_core->mpv, "estimated-frame-count", MPV_FORMAT_INT64, &i) >= 0
        && i != m_frameCount) {
        m_frameCount = i;
        emit frameChanged();
    }
    int64_t w = 0, h = 0;
    if (api->get_property(m_core->mpv, "width", MPV_FORMAT_INT64, &w) >= 0
        && api->get_property(m_core->mpv, "height", MPV_FORMAT_INT64, &h) >= 0
        && (int(w) != m_videoW || int(h) != m_videoH)) {
        m_videoW = int(w);
        m_videoH = int(h);
        emit videoSizeChanged();
    }
    refreshFrame();
}

// -- control ---------------------------------------------------------------------------
void MpvItem::setSource(const QString &path)
{
    if (path == m_source)
        return;
    m_source = path;
    emit sourceChanged();
    if (m_fileLoaded) {
        m_fileLoaded = false;
        emit fileLoadedChanged();
    }
    if (!m_core->mpv)
        return;
    m_restorePos = -1.0;
    if (path.isEmpty()) {
        commandAsync({QStringLiteral("stop")});
        m_loadedGeneration = -1;
    } else if (m_ctxGeneration > 0) {
        m_loadedGeneration = m_ctxGeneration;
        commandAsync({QStringLiteral("loadfile"), path, QStringLiteral("replace")});
    }   // else: renderReady() opens it once the render context exists
}

void MpvItem::renderReady()
{
    ++m_ctxGeneration;
    if (m_backend != QLatin1String("opengl")) {
        m_backend = QStringLiteral("opengl");
        emit backendChanged();
    }
    if (m_source.isEmpty() || m_loadedGeneration == m_ctxGeneration)
        return;
    if (m_loadedGeneration > 0 && m_fileLoaded)
        m_restorePos = m_position;   // the old context took the video with it: reopen
    m_loadedGeneration = m_ctxGeneration;
    commandAsync({QStringLiteral("loadfile"), m_source, QStringLiteral("replace")});
}

bool MpvItem::setPropertyAsync(const char *name, const QVariant &value)
{
    if (!m_core->mpv)
        return false;
    const MpvApi *api = mpvApi();
    int r;
    switch (value.typeId()) {
    case QMetaType::Bool: {
        int f = value.toBool() ? 1 : 0;
        r = api->set_property_async(m_core->mpv, 0, name, MPV_FORMAT_FLAG, &f);
        break;
    }
    case QMetaType::Int:
    case QMetaType::LongLong:
    case QMetaType::UInt:
    case QMetaType::ULongLong: {
        int64_t i = value.toLongLong();
        r = api->set_property_async(m_core->mpv, 0, name, MPV_FORMAT_INT64, &i);
        break;
    }
    case QMetaType::Double:
    case QMetaType::Float: {
        double d = value.toDouble();
        r = api->set_property_async(m_core->mpv, 0, name, MPV_FORMAT_DOUBLE, &d);
        break;
    }
    default: {
        QByteArray s = value.toString().toUtf8();
        char *p = s.data();
        r = api->set_property_async(m_core->mpv, 0, name, MPV_FORMAT_STRING, &p);
        break;
    }
    }
    return r >= 0;
}

void MpvItem::setPaused(bool on)
{
    setPropertyAsync("pause", on);
}

void MpvItem::play() { setPaused(false); }
void MpvItem::pause() { setPaused(true); }
void MpvItem::togglePause() { setPaused(!m_paused); }
void MpvItem::stop() { commandAsync({QStringLiteral("stop")}); }

void MpvItem::setSpeed(double s)
{
    s = qBound(0.01, s, 100.0);
    setPropertyAsync("speed", s);
}

void MpvItem::setLoopA(double t)
{
    if (t < 0)
        setPropertyAsync("ab-loop-a", QStringLiteral("no"));
    else
        setPropertyAsync("ab-loop-a", t);
}

void MpvItem::setLoopB(double t)
{
    if (t < 0)
        setPropertyAsync("ab-loop-b", QStringLiteral("no"));
    else
        setPropertyAsync("ab-loop-b", t);
}

void MpvItem::clearLoop()
{
    setLoopA(-1);
    setLoopB(-1);
}

void MpvItem::setMuted(bool on)
{
    setPropertyAsync("mute", on);
}

void MpvItem::setVolume(double v)
{
    setPropertyAsync("volume", qBound(0.0, v, 100.0));
}

void MpvItem::seek(double t, bool exact, bool relative)
{
    QString flags = relative ? QStringLiteral("relative") : QStringLiteral("absolute");
    flags += exact ? QStringLiteral("+exact") : QStringLiteral("+keyframes");
    commandAsync({QStringLiteral("seek"), QString::number(t, 'f', 6), flags});
}

void MpvItem::frameStep() { commandAsync({QStringLiteral("frame-step")}); }
void MpvItem::frameBackStep() { commandAsync({QStringLiteral("frame-back-step")}); }

bool MpvItem::command(const QStringList &args)
{
    if (!m_core->mpv || args.isEmpty())
        return false;
    std::vector<QByteArray> keep;
    std::vector<const char *> argv;
    for (const QString &a : args) {
        keep.push_back(a.toUtf8());
        argv.push_back(keep.back().constData());
    }
    argv.push_back(nullptr);
    const int r = mpvApi()->command(m_core->mpv, argv.data());
    if (r < 0)
        setError(QStringLiteral("mpv %1: %2").arg(args.first(), QString::fromUtf8(mpvApi()->error_string(r))));
    return r >= 0;
}

bool MpvItem::commandAsync(const QStringList &args)
{
    if (!m_core->mpv || args.isEmpty())
        return false;
    std::vector<QByteArray> keep;
    std::vector<const char *> argv;
    for (const QString &a : args) {
        keep.push_back(a.toUtf8());
        argv.push_back(keep.back().constData());
    }
    argv.push_back(nullptr);
    return mpvApi()->command_async(m_core->mpv, 0, argv.data()) >= 0;
}

QVariant MpvItem::mpvProperty(const QString &name) const
{
    if (!m_core->mpv)
        return QVariant();
    const MpvApi *api = mpvApi();
    mpv_node node;
    std::memset(&node, 0, sizeof node);
    const QByteArray n = name.toUtf8();
    if (api->get_property(m_core->mpv, n.constData(), MPV_FORMAT_NODE, &node) < 0)
        return QVariant();
    QVariant out = fromNode(node);
    api->free_node_contents(&node);
    return out;
}

bool MpvItem::setMpvProperty(const QString &name, const QVariant &value)
{
    const QByteArray n = name.toUtf8();
    return setPropertyAsync(n.constData(), value);
}

// -- render thread ----------------------------------------------------------------------
QSGNode *MpvItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    auto *node = static_cast<MpvNode *>(oldNode);
    QQuickWindow *win = window();
    QOpenGLContext *gl = QOpenGLContext::currentContext();
    if (!gl || !win || !m_core->mpv || width() < 1 || height() < 1) {
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
    const MpvApi *api = mpvApi();
    if (!node)
        node = new MpvNode(m_core, win);
    const qreal dpr = win->effectiveDevicePixelRatio();
    const QSize size(qMax(1, int(width() * dpr)), qMax(1, int(height() * dpr)));
    const bool resized = node->ensureFbo(size);
    node->setRect(boundingRect());

    if (!m_core->renderCtx) {
        mpv_opengl_init_params glInit{&getProcAddress, nullptr};
        mpv_render_param params[] = {
            {MPV_RENDER_PARAM_API_TYPE, const_cast<char *>(MPV_RENDER_API_TYPE_OPENGL)},
            {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &glInit},
            {MPV_RENDER_PARAM_INVALID, nullptr},
        };
        const int r = api->render_context_create(&m_core->renderCtx, m_core->mpv, params);
        if (r < 0) {
            m_core->renderCtx = nullptr;
            const QString msg = QStringLiteral("mpv_render_context_create: %1")
                                    .arg(QString::fromUtf8(api->error_string(r)));
            QMetaObject::invokeMethod(this, [this, msg] { setError(msg); }, Qt::QueuedConnection);
            return node;
        }
        api->render_context_set_update_callback(m_core->renderCtx, &MpvItem::onRenderUpdate,
                                                m_core.get());
        QMetaObject::invokeMethod(this, "renderReady", Qt::QueuedConnection);
    }

    const uint64_t flags = api->render_context_update(m_core->renderCtx);
    if ((flags & MPV_RENDER_UPDATE_FRAME) || resized)
        node->scheduleRender();   // drawn in beforeRendering, this same frame
    return node;
}
