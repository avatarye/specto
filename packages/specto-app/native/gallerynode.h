// Scene-graph side of GalleryItem: atlas pages per tier, tile material, the geometry
// batches and the caption text nodes. Lives on the render thread; touched only from
// updatePaintNode (sync) and the node destructor.
#pragma once

#include "atlas.h"
#include "galleryitem.h"

#include <QtGui/QColor>
#include <QtGui/QTextLayout>
#include <QtQuick/QSGGeometryNode>
#include <QtQuick/QSGMaterial>
#include <QtQuick/QSGMaterialShader>
#include <QtQuick/QSGTransformNode>

#include <memory>
#include <unordered_map>
#include <vector>

class QSGTextNode;

namespace specto {

struct TileUniforms {
    float scale = 1.0f;     // device px per gallery unit (px)
    float radius = 8.0f;    // device px
    float border = 2.0f;    // device px
    QColor selection, hover, placeholder, failed;
    QColor current;         // the current item's bar
    QColor scrim;           // under the overlay captions
    float scrimPx = 0.0f;   // device px: the caption band on the thumb's bottom edge; 0 = none
    float barPx = 3.0f;     // device px: the current item's bar
    float scrimShare = 0.4f; // band plus fade stay within this share of the thumb's height
};

// A tile's flags, carried in TileVertex::bits as an integer (exact in a float) and read
// by tile.frag, whose #defines mirror these values (test_gallery_native checks the two
// agree).
namespace TileBits {
constexpr int Placeholder = 1;   // no thumb yet: the placeholder colour
constexpr int Failed = 2;        // the thumb failed: the failed colour
constexpr int KindMask = 3;      // 0 = the atlas texture
constexpr int Selected = 4;      // the selection ring
constexpr int Hover = 8;         // the hover ring
constexpr int Current = 16;      // the current item's bar
constexpr int Caption = 32;      // a caption is drawn on it: the scrim under it
} // namespace TileBits

class TileMaterial : public QSGMaterial
{
public:
    TileMaterial();
    QSGMaterialType *type() const override;
    QSGMaterialShader *createShader(QSGRendererInterface::RenderMode) const override;
    int compare(const QSGMaterial *other) const override;

    QSGTexture *texture = nullptr;
    const TileUniforms *uniforms = nullptr;
};

class TileShader : public QSGMaterialShader
{
public:
    TileShader();
    bool updateUniformData(RenderState &state, QSGMaterial *newMaterial,
                           QSGMaterial *oldMaterial) override;
    void updateSampledImage(RenderState &state, int binding, QSGTexture **texture,
                            QSGMaterial *newMaterial, QSGMaterial *oldMaterial) override;
};

struct TileVertex {
    float x, y;       // content px
    float u, v;       // atlas
    float lx, ly;     // 0..1 inside the thumb quad
    float bits;       // TileBits, an integer
    float alpha;      // fade-in
    float w, h;       // thumb size in px
};

class GalleryNode : public QSGTransformNode
{
public:
    GalleryNode(GalleryItem *item, QQuickWindow *window);
    ~GalleryNode() override;

    // The whole per-frame sync: drain staged uploads, evict, rebuild geometry, captions.
    void sync();

private:
    struct Batch {
        QSGGeometryNode *node = nullptr;
        TileMaterial *material = nullptr;
        std::vector<TileVertex> verts;
        std::vector<quint32> idx;
    };
    struct Caption {
        QSGTransformNode *xform = nullptr;   // child of m_captionRoot; owns the text node
        int textW = -1;                      // layout width the text was built for
        int mode = -1;
        QString key, name;                   // the text it was built for (a re-sort changes it)
        bool current = false;                // built with the current item's suffix
        QRgb color = 0, scrim = 0;           // text colour; the scrim (the shadow derives from it)
        uint32_t lastUse = 0;
    };
    struct Thumb {
        QRectF rect;           // content px
        bool caption = false;  // a caption fits on it (and the scrim is drawn under it)
    };

    void ensureMiniPage0(QOpenGLExtraFunctions *f);
    bool drainUploads(QOpenGLExtraFunctions *f, const GalleryItem::Frame &fp, int &uploads, int &evictions);
    CellRef alloc(int tier, QOpenGLExtraFunctions *f, const GalleryItem::Frame &fp, int forIndex,
                  int &evictions);
    bool hasFree(int tier) const;
    void releaseTier(TileRec &t, int tier);
    void resetAll();
    void buildGeometry(const GalleryItem::Frame &fp, int &drawCalls, int &quads);
    void buildCaptions(const GalleryItem::Frame &fp, int &captions);
    Batch &batchFor(int key, QSGTexture *tex);
    void appendQuad(Batch &b, float x, float y, float w, float h, float u0, float v0, float u1,
                    float v1, int bits, float alpha);
    void dropCaption(int index, Caption &cap);
    int batchKey(int tier, int page) const;

    GalleryItem *m_item;
    QQuickWindow *m_window;
    std::vector<std::unique_ptr<AtlasPage>> m_pages[TierCount];
    std::vector<Batch> m_batches;       // index: 0 placeholder, then per tier/page
    TileUniforms m_uniforms;
    QSGNode *m_captionRoot = nullptr;
    std::unordered_map<int, Caption> m_captions;
    // every built tile's thumb this frame, from builtFirst on: buildGeometry decides
    // where a caption fits (the scrim's flag), buildCaptions places the captions there
    std::vector<Thumb> m_thumbs;
    int m_thumbFirst = 0;
    bool m_glChecked = false, m_glOk = false;
};

} // namespace specto
