#include "mpvapi.h"

#include <QtCore/QCoreApplication>
#include <QtCore/QDir>
#include <QtCore/QFileInfo>
#include <QtCore/QLibrary>
#include <QtCore/QMutex>
#include <QtCore/QMutexLocker>
#include <QtCore/QStringList>

#if defined(Q_OS_WIN)
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#ifndef SPECTO_LIBMPV_DIR
#define SPECTO_LIBMPV_DIR ""
#endif

namespace {

#if defined(Q_OS_WIN)
constexpr const char *kLibName = "libmpv-2.dll";
#elif defined(Q_OS_MACOS)
constexpr const char *kLibName = "libmpv.2.dylib";
#else
constexpr const char *kLibName = "libmpv.so.2";
#endif

QString pluginDir()
{
#if defined(Q_OS_WIN)
    HMODULE mod = nullptr;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
                               | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&pluginDir), &mod)) {
        wchar_t buf[MAX_PATH];
        DWORD n = GetModuleFileNameW(mod, buf, MAX_PATH);
        if (n > 0)
            return QFileInfo(QString::fromWCharArray(buf, int(n))).absolutePath();
    }
#else
    Dl_info info{};
    if (dladdr(reinterpret_cast<const void *>(&pluginDir), &info) && info.dli_fname)
        return QFileInfo(QString::fromLocal8Bit(info.dli_fname)).absolutePath();
#endif
    return QString();
}

QStringList candidates()
{
    QStringList out;
    const QString one = qEnvironmentVariable("SPECTO_LIBMPV");
    if (!one.isEmpty())
        out << one;
    const QString dir = qEnvironmentVariable("SPECTO_LIBMPV_DIR");
    if (!dir.isEmpty())
        out << QDir(dir).filePath(QLatin1String(kLibName));
    const QString here = pluginDir();
    if (!here.isEmpty())
        out << QDir(here).filePath(QLatin1String(kLibName));
    const QString built = QString::fromUtf8(SPECTO_LIBMPV_DIR);
    if (!built.isEmpty()) {
        out << QDir(built).filePath(QLatin1String(kLibName));
#if defined(Q_OS_MACOS)
        // Homebrew's prefix: headers in include/, the dylib in lib/ (dyld's default
        // search path does not cover /opt/homebrew/lib)
        out << QDir(built).filePath(QLatin1String("lib/") + QLatin1String(kLibName));
#endif
    }
    out << QString::fromLatin1(kLibName);   // the loader's own search path (PATH)
#if !defined(Q_OS_WIN)
    out << QStringLiteral("mpv");
#endif
    return out;
}

template <typename F>
bool resolve(QLibrary &lib, F &slot, const char *name, QString &missing)
{
    slot = reinterpret_cast<F>(lib.resolve(name));
    if (!slot) {
        if (!missing.isEmpty())
            missing += QLatin1String(", ");
        missing += QString::fromLatin1(name);
        return false;
    }
    return true;
}

bool bind(QLibrary &lib, MpvApi &api, QString &missing)
{
    bool ok = true;
    ok &= resolve(lib, api.client_api_version, "mpv_client_api_version", missing);
    ok &= resolve(lib, api.error_string, "mpv_error_string", missing);
    ok &= resolve(lib, api.free, "mpv_free", missing);
    ok &= resolve(lib, api.create, "mpv_create", missing);
    ok &= resolve(lib, api.initialize, "mpv_initialize", missing);
    ok &= resolve(lib, api.terminate_destroy, "mpv_terminate_destroy", missing);
    ok &= resolve(lib, api.set_option_string, "mpv_set_option_string", missing);
    ok &= resolve(lib, api.command, "mpv_command", missing);
    ok &= resolve(lib, api.command_async, "mpv_command_async", missing);
    ok &= resolve(lib, api.set_property, "mpv_set_property", missing);
    ok &= resolve(lib, api.set_property_async, "mpv_set_property_async", missing);
    ok &= resolve(lib, api.get_property, "mpv_get_property", missing);
    ok &= resolve(lib, api.observe_property, "mpv_observe_property", missing);
    ok &= resolve(lib, api.set_wakeup_callback, "mpv_set_wakeup_callback", missing);
    ok &= resolve(lib, api.wait_event, "mpv_wait_event", missing);
    ok &= resolve(lib, api.request_log_messages, "mpv_request_log_messages", missing);
    ok &= resolve(lib, api.free_node_contents, "mpv_free_node_contents", missing);
    ok &= resolve(lib, api.render_context_create, "mpv_render_context_create", missing);
    ok &= resolve(lib, api.render_context_set_update_callback,
                  "mpv_render_context_set_update_callback", missing);
    ok &= resolve(lib, api.render_context_update, "mpv_render_context_update", missing);
    ok &= resolve(lib, api.render_context_render, "mpv_render_context_render", missing);
    ok &= resolve(lib, api.render_context_free, "mpv_render_context_free", missing);
    return ok;
}

MpvApi load()
{
    MpvApi api;
    QStringList tried;
    for (const QString &cand : candidates()) {
        if (cand.contains(QLatin1Char('/')) || cand.contains(QLatin1Char('\\'))) {
            if (!QFileInfo::exists(cand)) {
                tried << cand + QLatin1String(" (missing)");
                continue;
            }
        }
        // the library stays loaded for the life of the process (never unloaded)
        auto *lib = new QLibrary(cand);
        lib->setLoadHints(QLibrary::ResolveAllSymbolsHint);
        if (!lib->load()) {
            tried << cand + QLatin1String(" (") + lib->errorString() + QLatin1Char(')');
            delete lib;
            continue;
        }
        QString missing;
        MpvApi bound;
        if (!bind(*lib, bound, missing)) {
            tried << cand + QLatin1String(" (lacks ") + missing + QLatin1Char(')');
            lib->unload();
            delete lib;
            continue;
        }
        bound.path = lib->fileName();
        const unsigned long v = bound.client_api_version();
        bound.version = QStringLiteral("mpv client API %1.%2").arg(v >> 16).arg(v & 0xffff);
        return bound;
    }
    api.error = QStringLiteral("libmpv not found; tried: %1. Fetch it with "
                               "`uv run python tools/fetch_libmpv.py` and rebuild the native "
                               "plugin, or point SPECTO_LIBMPV at the DLL.")
                    .arg(tried.join(QLatin1String("; ")));
    return api;
}

} // namespace

const MpvApi *mpvApi()
{
    static QMutex mutex;
    static MpvApi *api = nullptr;
    QMutexLocker lock(&mutex);
    if (!api)
        api = new MpvApi(load());
    return api;
}
