#include "atlas.h"

#include <QtCore/QDebug>
#include <QtGui/QOpenGLContext>
#include <QtGui/QOpenGLExtraFunctions>
#include <QtGui/QSurfaceFormat>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGTexture>
#include <QtQuick/qsgtexture_platform.h>

#include <algorithm>
#include <cstring>

namespace specto {

void boxDownsample(const uint8_t *src, int sw, int sh, uint8_t *dst, int dw, int dh)
{
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0)
        return;
    for (int y = 0; y < dh; ++y) {
        const int y0 = (y * sh) / dh;
        const int y1 = std::max(y0 + 1, ((y + 1) * sh) / dh);
        for (int x = 0; x < dw; ++x) {
            const int x0 = (x * sw) / dw;
            const int x1 = std::max(x0 + 1, ((x + 1) * sw) / dw);
            uint32_t acc[4] = {0, 0, 0, 0};
            for (int yy = y0; yy < y1; ++yy) {
                const uint8_t *row = src + (size_t(yy) * sw + x0) * 4;
                for (int xx = x0; xx < x1; ++xx, row += 4) {
                    acc[0] += row[0];
                    acc[1] += row[1];
                    acc[2] += row[2];
                    acc[3] += row[3];
                }
            }
            const uint32_t n = uint32_t(y1 - y0) * uint32_t(x1 - x0);
            uint8_t *d = dst + (size_t(y) * dw + x) * 4;
            d[0] = uint8_t((acc[0] + n / 2) / n);
            d[1] = uint8_t((acc[1] + n / 2) / n);
            d[2] = uint8_t((acc[2] + n / 2) / n);
            d[3] = uint8_t((acc[3] + n / 2) / n);
        }
    }
}

void buildCell(const uint8_t *thumb, int w, int h, int cell, int gutter, std::vector<uint8_t> &out,
               int &ox, int &oy)
{
    const int usable = cell - 2 * gutter;
    ox = gutter + (usable - w) / 2;
    oy = gutter + (usable - h) / 2;
    out.resize(size_t(cell) * cell * 4);
    for (int y = 0; y < cell; ++y) {
        const int sy = std::clamp(y - oy, 0, h - 1);
        const uint8_t *srow = thumb + size_t(sy) * w * 4;
        uint8_t *drow = out.data() + size_t(y) * cell * 4;
        // left replicated, middle copied, right replicated
        const int xStart = std::clamp(ox, 0, cell);
        const int xEnd = std::clamp(ox + w, 0, cell);
        for (int x = 0; x < xStart; ++x)
            std::memcpy(drow + size_t(x) * 4, srow, 4);
        if (xEnd > xStart)
            std::memcpy(drow + size_t(xStart) * 4, srow + size_t(xStart - ox) * 4,
                        size_t(xEnd - xStart) * 4);
        for (int x = xEnd; x < cell; ++x)
            std::memcpy(drow + size_t(x) * 4, srow + size_t(w - 1) * 4, 4);
    }
}

void prepareCell(const uint8_t *thumb, int w, int h, int cell, int gutter, int levels,
                 PreparedTier &out)
{
    out.w = w;
    out.h = h;
    out.levels.resize(size_t(levels));
    int ox, oy;
    buildCell(thumb, w, h, cell, gutter, out.levels[0], ox, oy);
    int prev = cell;
    for (int lv = 1; lv < levels; ++lv) {
        const int sz = cell >> lv;
        out.levels[size_t(lv)].resize(size_t(sz) * sz * 4);
        boxDownsample(out.levels[size_t(lv - 1)].data(), prev, prev, out.levels[size_t(lv)].data(),
                      sz, sz);
        prev = sz;
    }
}

ClearTexImageFn clearTexImageFn()
{
    // resolved for the current context each time: a page is made a few times a session,
    // and WGL entry points may differ between contexts
    auto *ctx = QOpenGLContext::currentContext();
    if (!ctx || ctx->isOpenGLES())
        return nullptr;
    const QSurfaceFormat fmt = ctx->format();
    const bool has = fmt.version() >= qMakePair(4, 4) || ctx->hasExtension("GL_ARB_clear_texture");
    if (!has || qEnvironmentVariableIsSet("SPECTO_GALLERY_CPU_FILL"))
        return nullptr;
    return reinterpret_cast<ClearTexImageFn>(ctx->getProcAddress("glClearTexImage"));
}

AtlasPage::AtlasPage(QOpenGLExtraFunctions *f, QQuickWindow *window, int size, int cell, int gutter,
                     int levels)
    : m_size(size), m_cell(cell), m_gutter(gutter), m_levels(levels), m_cols(size / cell)
{
    f->glGenTextures(1, &m_tex);
    f->glBindTexture(GL_TEXTURE_2D, m_tex);
    f->glTexStorage2D(GL_TEXTURE_2D, levels, GL_RGBA8, size, size);
    f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR);
    f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    f->glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, levels - 1);
    // Fresh pages start as the warm placeholder tone so an unwritten cell never flashes
    // black if it is ever sampled: cleared on the GPU where the context can (SPEC-1: the
    // CPU fill and its 22 MB upload stalled the frame that opened a page), else filled.
    static const uint8_t kTone[4] = {0xe9, 0xe3, 0xd9, 0xff};
    if (ClearTexImageFn clear = clearTexImageFn()) {
        for (int lv = 0; lv < levels; ++lv)
            clear(m_tex, lv, GL_RGBA, GL_UNSIGNED_BYTE, kTone);
    } else {
        std::vector<uint8_t> fill(size_t(size) * size * 4);
        for (size_t i = 0; i < fill.size(); i += 4) {
            fill[i] = 0xe9;
            fill[i + 1] = 0xe3;
            fill[i + 2] = 0xd9;
            fill[i + 3] = 0xff;
        }
        for (int lv = 0; lv < levels; ++lv)
            f->glTexSubImage2D(GL_TEXTURE_2D, lv, 0, 0, size >> lv, size >> lv, GL_RGBA,
                               GL_UNSIGNED_BYTE, fill.data());
    }
    f->glBindTexture(GL_TEXTURE_2D, 0);
    const GLenum err = f->glGetError();
    if (err != GL_NO_ERROR)
        qWarning() << "[ERROR] GalleryItem atlas page: GL error" << Qt::hex << err;

    m_sgTexture = QNativeInterface::QSGOpenGLTexture::fromNative(
        m_tex, window, QSize(size, size), QQuickWindow::TextureHasMipmaps);
    if (m_sgTexture) {
        m_sgTexture->setFiltering(QSGTexture::Linear);
        m_sgTexture->setMipmapFiltering(QSGTexture::Linear);
        m_sgTexture->setHorizontalWrapMode(QSGTexture::ClampToEdge);
        m_sgTexture->setVerticalWrapMode(QSGTexture::ClampToEdge);
    }
    m_free.reserve(capacity());
    for (int i = capacity() - 1; i >= 0; --i)
        m_free.push_back(i);
}

AtlasPage::~AtlasPage()
{
    delete m_sgTexture;   // wrapper only; it does not own the GL texture
    if (m_tex) {
        if (auto *ctx = QOpenGLContext::currentContext())
            ctx->extraFunctions()->glDeleteTextures(1, &m_tex);
    }
}

int AtlasPage::allocate()
{
    if (m_free.empty())
        return -1;
    const int c = m_free.back();
    m_free.pop_back();
    return c;
}

void AtlasPage::release(int cellIdx)
{
    if (cellIdx >= 0 && cellIdx < capacity())
        m_free.push_back(cellIdx);
}

void AtlasPage::releaseAll()
{
    m_free.clear();
    for (int i = capacity() - 1; i >= 0; --i)
        m_free.push_back(i);
}

void AtlasPage::upload(QOpenGLExtraFunctions *f, int cellIdx, const PreparedTier &tier)
{
    if (int(tier.levels.size()) < m_levels)
        return;
    const int cx = (cellIdx % m_cols) * m_cell;
    const int cy = (cellIdx / m_cols) * m_cell;
    f->glBindTexture(GL_TEXTURE_2D, m_tex);
    f->glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    f->glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
    for (int lv = 0; lv < m_levels; ++lv) {
        const int sz = m_cell >> lv;
        f->glTexSubImage2D(GL_TEXTURE_2D, lv, cx >> lv, cy >> lv, sz, sz, GL_RGBA, GL_UNSIGNED_BYTE,
                           tier.levels[size_t(lv)].data());
    }
    f->glBindTexture(GL_TEXTURE_2D, 0);
}

void AtlasPage::texRect(int cellIdx, int w, int h, float &u0, float &v0, float &u1, float &v1) const
{
    const int usable = m_cell - 2 * m_gutter;
    const int ox = m_gutter + (usable - w) / 2;
    const int oy = m_gutter + (usable - h) / 2;
    const float px = float((cellIdx % m_cols) * m_cell + ox);
    const float py = float((cellIdx / m_cols) * m_cell + oy);
    const float inv = 1.0f / float(m_size);
    u0 = px * inv;
    v0 = py * inv;
    u1 = (px + float(w)) * inv;
    v1 = (py + float(h)) * inv;
}

} // namespace specto
