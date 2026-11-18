// The CPU side of the gallery's atlas (SPEC-1): building a cell and its mip chain from
// a thumbnail. No GL here, so GalleryItem's prepare thread and header use it without
// the GL headers; the pages that upload the cells are atlas.h's. Defined in atlas.cpp.
#pragma once

#include <cstdint>
#include <vector>

namespace specto {

// Area-average resample of straight-alpha RGBA (any factor >= 1 in each axis).
void boxDownsample(const uint8_t *src, int sw, int sh, uint8_t *dst, int dw, int dh);

// Build a `cell`x`cell` RGBA buffer with the `w`x`h` thumbnail centred in the usable
// square (cell - 2*gutter) and the remaining pixels edge-replicated. Returns the
// thumbnail's offset inside the cell through ox/oy.
void buildCell(const uint8_t *thumb, int w, int h, int cell, int gutter, std::vector<uint8_t> &out,
               int &ox, int &oy);

// One tier's cell, ready to upload: the thumbnail's size in the tier and the cell
// buffer with every mip level, built off the render thread so an upload is only
// glTexSubImage2D calls.
struct PreparedTier {
    int w = 0, h = 0;                           // 0: the thumb has no picture in this tier
    std::vector<std::vector<uint8_t>> levels;   // levels[lv]: (cell >> lv)^2 * 4 bytes
};

// Build the cell (buildCell) and its box-filtered mip chain for a w*h thumb. CPU only:
// any thread.
void prepareCell(const uint8_t *thumb, int w, int h, int cell, int gutter, int levels,
                 PreparedTier &out);

// A measuring switch in the environment (docs/setup/developer.md): on when it is set to
// 1, true, yes or on (any case); unset, empty, 0 or anything else is off.
bool envSwitch(const char *name);

} // namespace specto
