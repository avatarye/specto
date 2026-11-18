// Texture atlas pages for GalleryItem. Render thread only (GL context current); the
// cells they upload are built by atlascell.h's prepareCell on any thread.
//
// A page is one square GL texture with a fixed grid of cells and a small mip chain
// (kLevels levels, uploaded per tile from CPU box-filtered copies, so no page-wide
// glGenerateMipmap). A tile is written as a full cell buffer with the thumbnail
// centred and the gutter filled by edge replication, which keeps linear and mip
// sampling free of neighbour bleed. A new page is cleared to the placeholder tone on
// the GPU (glClearTexImage) where the context has it, so creating one costs no CPU fill
// and no upload (21 MiB for a 2048 px page with its mips) in the frame that needs it
// (SPEC-1).
#pragma once

#include "atlascell.h"

#include <QtCore/QSize>
#include <QtGui/qopengl.h>

#include <cstdint>
#include <vector>

class QOpenGLExtraFunctions;
class QQuickWindow;
class QSGTexture;

namespace specto {

struct CellRef {
    int16_t page = -1;
    int16_t cell = -1;
};

// glClearTexImage when the context has it (GL 4.4 or ARB_clear_texture) and
// SPECTO_GALLERY_CPU_FILL is off, else null.
using ClearTexImageFn = void(QOPENGLF_APIENTRYP)(GLuint, GLint, GLenum, GLenum, const void *);
ClearTexImageFn clearTexImageFn();

class AtlasPage
{
public:
    AtlasPage(QOpenGLExtraFunctions *f, QQuickWindow *window, int size, int cell, int gutter, int levels);
    ~AtlasPage();
    AtlasPage(const AtlasPage &) = delete;
    AtlasPage &operator=(const AtlasPage &) = delete;

    bool valid() const { return m_tex != 0 && m_sgTexture != nullptr; }
    int size() const { return m_size; }
    int cell() const { return m_cell; }
    int gutter() const { return m_gutter; }
    int cols() const { return m_cols; }
    int capacity() const { return m_cols * m_cols; }
    int used() const { return capacity() - int(m_free.size()); }
    bool full() const { return m_free.empty(); }
    QSGTexture *sgTexture() const { return m_sgTexture; }

    int allocate();            // -1 when full
    void release(int cellIdx);
    void releaseAll();
    // Upload a prepared cell (prepareCell with this page's cell, gutter and levels) into
    // `cellIdx`; one glTexSubImage2D per mip level, nothing built here. A cell with too
    // few levels (another tier's) is refused with a warning.
    void upload(QOpenGLExtraFunctions *f, int cellIdx, const PreparedTier &tier);
    // Texture coordinates (0..1) of the thumbnail area of a cell holding a w*h thumb.
    void texRect(int cellIdx, int w, int h, float &u0, float &v0, float &u1, float &v1) const;

private:
    GLuint m_tex = 0;
    QSGTexture *m_sgTexture = nullptr;
    int m_size, m_cell, m_gutter, m_levels, m_cols;
    std::vector<int> m_free;
};

} // namespace specto
