// Runtime binding to libmpv (libmpv-2.dll on Windows, libmpv.so.2 elsewhere). The plugin
// is not linked against mpv: the DLL is resolved with QLibrary the first time an MpvItem
// needs it, so a machine without libmpv still loads Specto.Native (the gallery works, the
// video view reports health `error` with the fetch hint). Only the headers (types, enums)
// are compiled in; every function goes through this table.
//
// Search order: $SPECTO_LIBMPV (a DLL path), $SPECTO_LIBMPV_DIR/libmpv-2.dll, next to
// this plugin's own DLL (build.py copies it there on Windows), the SPECTO_LIBMPV_DIR the
// plugin was built with (tools/fetch_libmpv.py's default), then the loader's own search
// path (libmpv.so.2 from libmpv-dev on Linux).
#pragma once

#include <mpv/client.h>
#include <mpv/render.h>
#include <mpv/render_gl.h>

#include <QtCore/QString>

#include <cstdint>

struct MpvApi {
    // client.h
    unsigned long (*client_api_version)(void) = nullptr;
    const char *(*error_string)(int) = nullptr;
    void (*free)(void *) = nullptr;
    mpv_handle *(*create)(void) = nullptr;
    int (*initialize)(mpv_handle *) = nullptr;
    void (*terminate_destroy)(mpv_handle *) = nullptr;
    int (*set_option_string)(mpv_handle *, const char *, const char *) = nullptr;
    int (*command)(mpv_handle *, const char **) = nullptr;
    int (*command_async)(mpv_handle *, uint64_t, const char **) = nullptr;
    int (*set_property)(mpv_handle *, const char *, mpv_format, void *) = nullptr;
    int (*set_property_async)(mpv_handle *, uint64_t, const char *, mpv_format, void *) = nullptr;
    int (*get_property)(mpv_handle *, const char *, mpv_format, void *) = nullptr;
    int (*observe_property)(mpv_handle *, uint64_t, const char *, mpv_format) = nullptr;
    void (*set_wakeup_callback)(mpv_handle *, void (*)(void *), void *) = nullptr;
    mpv_event *(*wait_event)(mpv_handle *, double) = nullptr;
    int (*request_log_messages)(mpv_handle *, const char *) = nullptr;
    void (*free_node_contents)(mpv_node *) = nullptr;
    // render.h
    int (*render_context_create)(mpv_render_context **, mpv_handle *, mpv_render_param *) = nullptr;
    void (*render_context_set_update_callback)(mpv_render_context *, mpv_render_update_fn, void *) = nullptr;
    uint64_t (*render_context_update)(mpv_render_context *) = nullptr;
    int (*render_context_render)(mpv_render_context *, mpv_render_param *) = nullptr;
    void (*render_context_free)(mpv_render_context *) = nullptr;

    QString path;    // the library that was loaded
    QString error;   // why it was not
    QString version; // "mpv client API 2.5" once loaded
    bool ok() const { return create != nullptr; }
};

// Loads once (thread-safe); never fails loudly, check ok() / error.
const MpvApi *mpvApi();
