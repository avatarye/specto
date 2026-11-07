// Texture atlas pages for GalleryItem. Pages: render thread only (GL context current);
// prepareCell: any thread.
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

#include <QtCore/QSize>
#include <QtGui/qopengl.h>

#include <cstdint>
#include <vector>

class QOpenGLExtraFunctions;
class QQuickWindow;
class QSGTexture;

namespace specto {

// Area-average resample of straight-alpha RGBA (any factor >= 1 in each axis).
void boxDownsample(const uint8_t *src, int sw, int sh, uint8_t *dst, int dw, int dh);

// Build a `cell`x`cell` RGBA buffer with the `w`x`h` thumbnail centred in the usable
// square (cell - 2*gutter) and the remaining pixels edge-replicated. Returns the
// thumbnail's offset inside the cell through ox/oy.
void buildCell(const uint8_t *thumb, int w, int h, int cell, int gutter, std::vector<uint8_t> &out,
               int &ox, int &oy);

struct CellRef {
    int16_t page = -1;
    int16_t cell = -1;
};

// One tier's cell, ready to upload (SPEC-1): the thumbnail's size in the tier and the
// cell buffer with every mip level, built off the render thread so an upload is only
// glTexSubImage2D calls.
struct PreparedTier {
    int w = 0, h = 0;                           // 0: the thumb has no picture in this tier
    std::vector<std::vector<uint8_t>> levels;   // levels[lv]: (cell >> lv)^2 * 4 bytes
};

// Build the cell (buildCell) and its box-filtered mip chain for a w*h thumb. CPU only:
// any thread.
void prepareCell(const uint8_t *thumb, int w, int h, int cell, int gutter, int levels,
                 PreparedTier &out);

// glClearTexImage when the context has it (GL 4.4 or ARB_clear_texture), else null.
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
    // `cellIdx`; one glTexSubImage2D per mip level, nothing built here.
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
