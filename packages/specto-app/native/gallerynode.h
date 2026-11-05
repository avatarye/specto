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
};

// A vertex's `state` adds this when the tile is the current item (the shader takes it
// off before reading the kind and the ring).
constexpr float kCurrentState = 16.0f;

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
    float state;      // 0 normal, 1 selected, 2 hover, 3 placeholder, 4 failed; +16 current
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
        QRgb color = 0;
        uint32_t lastUse = 0;
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
                    float v1, float state, float alpha);
    int batchKey(int tier, int page) const;

    GalleryItem *m_item;
    QQuickWindow *m_window;
    std::vector<std::unique_ptr<AtlasPage>> m_pages[TierCount];
    std::vector<Batch> m_batches;       // index: 0 placeholder, then per tier/page
    TileUniforms m_uniforms;
    QSGNode *m_captionRoot = nullptr;
    std::unordered_map<int, Caption> m_captions;
    // the thumb rect (content px) of every built tile this frame, from builtFirst on:
    // buildGeometry fills it, buildCaptions places the overlay captions on it
    std::vector<QRectF> m_thumbRects;
    int m_thumbFirst = 0;
    bool m_glChecked = false, m_glOk = false;
};

} // namespace specto
