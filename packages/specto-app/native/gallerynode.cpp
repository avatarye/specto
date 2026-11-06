#include "gallerynode.h"

#include <QtCore/QDebug>
#include <QtCore/QElapsedTimer>
#include <QtGui/QFontMetricsF>
#include <QtGui/QMatrix4x4>
#include <QtGui/QOpenGLContext>
#include <QtGui/QOpenGLExtraFunctions>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGGeometry>
#include <QtQuick/QSGRendererInterface>
#include <QtQuick/QSGTextNode>
#include <QtQuick/QSGTexture>

#include <algorithm>
#include <cstring>
#include <limits>

namespace specto {

// ---------------------------------------------------------------------------------
// material
// ---------------------------------------------------------------------------------
namespace {
constexpr int kUniformBytes = 192;
constexpr int kMaxPagesPerTier = 64;

const QSGGeometry::AttributeSet &tileAttributes()
{
    static const QSGGeometry::Attribute attrs[] = {
        QSGGeometry::Attribute::createWithAttributeType(0, 2, QSGGeometry::FloatType,
                                                        QSGGeometry::PositionAttribute),
        QSGGeometry::Attribute::createWithAttributeType(1, 2, QSGGeometry::FloatType,
                                                        QSGGeometry::TexCoordAttribute),
        QSGGeometry::Attribute::createWithAttributeType(2, 2, QSGGeometry::FloatType,
                                                        QSGGeometry::UnknownAttribute),
        QSGGeometry::Attribute::createWithAttributeType(3, 4, QSGGeometry::FloatType,
                                                        QSGGeometry::UnknownAttribute),
    };
    static const QSGGeometry::AttributeSet set = {4, sizeof(TileVertex), attrs};
    return set;
}

void putColor(QByteArray *buf, int offset, const QColor &c)
{
    const float v[4] = {float(c.redF()), float(c.greenF()), float(c.blueF()), float(c.alphaF())};
    std::memcpy(buf->data() + offset, v, sizeof v);
}
} // namespace

TileMaterial::TileMaterial()
{
    setFlag(Blending, true);
    // Unmergeable: the renderer keeps one draw per node with its own matrix instead of
    // re-transforming every vertex on the CPU each time the scroll matrix changes.
    setFlag(RequiresFullMatrix, true);
}

QSGMaterialType *TileMaterial::type() const
{
    static QSGMaterialType t;
    return &t;
}

QSGMaterialShader *TileMaterial::createShader(QSGRendererInterface::RenderMode) const
{
    return new TileShader;
}

int TileMaterial::compare(const QSGMaterial *other) const
{
    const auto *o = static_cast<const TileMaterial *>(other);
    if (texture != o->texture)
        return texture < o->texture ? -1 : 1;
    return 0;
}

TileShader::TileShader()
{
    setShaderFileName(VertexStage, QStringLiteral(":/specto/shaders/tile.vert.qsb"));
    setShaderFileName(FragmentStage, QStringLiteral(":/specto/shaders/tile.frag.qsb"));
}

bool TileShader::updateUniformData(RenderState &state, QSGMaterial *newMaterial, QSGMaterial *)
{
    QByteArray *buf = state.uniformData();
    if (buf->size() < kUniformBytes)
        buf->resize(kUniformBytes);
    if (state.isMatrixDirty()) {
        const QMatrix4x4 m = state.combinedMatrix();
        std::memcpy(buf->data(), m.constData(), 64);
    }
    const float opacity = state.opacity();
    std::memcpy(buf->data() + 64, &opacity, 4);
    const auto *mat = static_cast<TileMaterial *>(newMaterial);
    const TileUniforms *u = mat->uniforms;
    static const TileUniforms fallback;
    if (!u)
        u = &fallback;
    std::memcpy(buf->data() + 68, &u->scale, 4);
    std::memcpy(buf->data() + 72, &u->radius, 4);
    std::memcpy(buf->data() + 76, &u->border, 4);
    putColor(buf, 80, u->selection);
    putColor(buf, 96, u->hover);
    putColor(buf, 112, u->placeholder);
    putColor(buf, 128, u->failed);
    putColor(buf, 144, u->current);
    putColor(buf, 160, u->scrim);
    std::memcpy(buf->data() + 176, &u->scrimPx, 4);
    std::memcpy(buf->data() + 180, &u->barPx, 4);
    std::memcpy(buf->data() + 184, &u->scrimShare, 4);
    return true;
}

void TileShader::updateSampledImage(RenderState &state, int binding, QSGTexture **texture,
                                    QSGMaterial *newMaterial, QSGMaterial *)
{
    if (binding != 1)
        return;
    auto *mat = static_cast<TileMaterial *>(newMaterial);
    if (mat->texture) {
        mat->texture->commitTextureOperations(state.rhi(), state.resourceUpdateBatch());
        *texture = mat->texture;
    }
}

// ---------------------------------------------------------------------------------
// node
// ---------------------------------------------------------------------------------
GalleryNode::GalleryNode(GalleryItem *item, QQuickWindow *window)
    : m_item(item), m_window(window)
{
    m_batches.resize(1 + TierCount * kMaxPagesPerTier);
    m_captionRoot = new QSGNode;
    appendChildNode(m_captionRoot);
}

GalleryNode::~GalleryNode()
{
    // Children (geometry nodes, caption root) are deleted by QSGNode; materials and
    // geometries are owned by their nodes (OwnsMaterial | OwnsGeometry). Pages release GL
    // textures while the context is still current (scene graph cleanup on the render
    // thread).
    for (auto &tier : m_pages)
        tier.clear();
}

int GalleryNode::batchKey(int tier, int page) const
{
    return 1 + tier * kMaxPagesPerTier + page;
}

GalleryNode::Batch &GalleryNode::batchFor(int key, QSGTexture *tex)
{
    Batch &b = m_batches[size_t(key)];
    if (!b.node) {
        b.node = new QSGGeometryNode;
        b.material = new TileMaterial;
        b.material->uniforms = &m_uniforms;
        b.node->setMaterial(b.material);
        auto *g = new QSGGeometry(tileAttributes(), 0, 0, QSGGeometry::UnsignedIntType);
        g->setDrawingMode(QSGGeometry::DrawTriangles);
        b.node->setGeometry(g);
        b.node->setFlags(QSGNode::OwnsMaterial | QSGNode::OwnsGeometry);
        // tiles draw under the captions
        insertChildNodeBefore(b.node, m_captionRoot);
    }
    if (b.material->texture != tex) {
        b.material->texture = tex;
        b.node->markDirty(QSGNode::DirtyMaterial);
    }
    return b;
}

void GalleryNode::appendQuad(Batch &b, float x, float y, float w, float h, float u0, float v0,
                             float u1, float v1, int bits, float alpha)
{
    const float state = float(bits);   // an integer: exact in a float
    const quint32 base = quint32(b.verts.size());
    b.verts.push_back({x, y, u0, v0, 0.f, 0.f, state, alpha, w, h});
    b.verts.push_back({x + w, y, u1, v0, 1.f, 0.f, state, alpha, w, h});
    b.verts.push_back({x, y + h, u0, v1, 0.f, 1.f, state, alpha, w, h});
    b.verts.push_back({x + w, y + h, u1, v1, 1.f, 1.f, state, alpha, w, h});
    const quint32 ids[6] = {base, base + 1, base + 2, base + 2, base + 1, base + 3};
    b.idx.insert(b.idx.end(), ids, ids + 6);
}

void GalleryNode::ensureMiniPage0(QOpenGLExtraFunctions *f)
{
    if (m_pages[TierMini].empty())
        m_pages[TierMini].push_back(std::make_unique<AtlasPage>(
            f, m_window, GalleryItem::kPageSize, GalleryItem::tierCell(TierMini),
            GalleryItem::tierGutter(TierMini), GalleryItem::tierLevels(TierMini)));
}

bool GalleryNode::hasFree(int tier) const
{
    if (int(m_pages[tier].size()) < m_item->pagesOf(tier))
        return true;
    for (const auto &p : m_pages[tier])
        if (!p->full())
            return true;
    return false;
}

void GalleryNode::releaseTier(TileRec &t, int tier)
{
    if (t.page[tier] >= 0) {
        m_pages[tier][size_t(t.page[tier])]->release(t.cell[tier]);
        t.page[tier] = t.cell[tier] = -1;
        t.w[tier] = t.h[tier] = 0;
    }
}

CellRef GalleryNode::alloc(int tier, QOpenGLExtraFunctions *f, const GalleryItem::Frame &fp,
                           int forIndex, int &evictions)
{
    auto &pages = m_pages[tier];
    for (size_t p = 0; p < pages.size(); ++p) {
        const int c = pages[p]->allocate();
        if (c >= 0)
            return {int16_t(p), int16_t(c)};
    }
    if (int(pages.size()) < m_item->pagesOf(tier) && int(pages.size()) < kMaxPagesPerTier) {
        pages.push_back(std::make_unique<AtlasPage>(f, m_window, GalleryItem::kPageSize,
                                                    GalleryItem::tierCell(tier),
                                                    GalleryItem::tierGutter(tier),
                                                    GalleryItem::tierLevels(tier)));
        return {int16_t(pages.size() - 1), int16_t(pages.back()->allocate())};
    }
    if (tier == TierMini)
        return {};   // the mini tier is never evicted
    // Evict: least recently used tile of this tier, preferring tiles outside the wanted
    // range (visible + margin: what the feeder keeps asking for), then the farthest
    // from the viewport centre.
    auto &tiles = m_item->tiles();
    const int center = (fp.builtFirst + fp.builtLast) / 2;
    int victim = -1;
    uint64_t bestScore = 0;
    for (int i = 0; i < int(tiles.size()); ++i) {
        const TileRec &t = tiles[size_t(i)];
        if (t.page[tier] < 0 || i == forIndex)
            continue;
        const bool inRange = i >= fp.wantedFirst && i <= fp.wantedLast;
        const uint64_t age = uint64_t(fp.frameNo - t.lastUse);
        const uint64_t dist = uint64_t(std::abs(i - center));
        const uint64_t score = (inRange ? 0ull : (1ull << 60)) + (std::min<uint64_t>(age, 1u << 20) << 32)
                               + dist;
        if (victim < 0 || score > bestScore) {
            victim = i;
            bestScore = score;
        }
    }
    if (victim < 0)
        return {};
    TileRec &v = tiles[size_t(victim)];
    const CellRef ref{v.page[tier], v.cell[tier]};
    releaseTier(v, tier);
    ++evictions;
    const int c = pages[size_t(ref.page)]->allocate();
    return {ref.page, int16_t(c)};
}

void GalleryNode::resetAll()
{
    for (auto &tier : m_pages)
        for (auto &p : tier)
            p->releaseAll();
}

bool GalleryNode::drainUploads(QOpenGLExtraFunctions *f, const GalleryItem::Frame &fp, int &uploads,
                               int &evictions)
{
    auto &tiles = m_item->tiles();
    auto &queue = m_item->stagedQueue();
    std::vector<uint8_t> scratch;
    int n = 0;
    while (n < GalleryItem::kMaxUploadsPerFrame) {
        StagedThumb st;
        {
            QMutexLocker lock(&m_item->stagedMutex());
            if (queue.empty())
                break;
            st = std::move(queue.front());
            queue.pop_front();
        }
        if (st.index < 0 || st.index >= int(tiles.size()))
            continue;
        TileRec &t = tiles[size_t(st.index)];
        t.staged = false;
        t.failed = false;
        if (st.w <= 0 || st.h <= 0) {
            t.failed = true;
            continue;
        }
        // The decoded size picks the top tier it fills: > thumbPx is a large decode.
        const int largeMax = GalleryItem::tierPx(TierLarge), fullMax = GalleryItem::tierPx(TierFull);
        int topTier = std::max(st.w, st.h) > fullMax ? TierLarge : TierFull;
        if (std::max(st.w, st.h) > largeMax) {
            // oversized: shrink to the large tier rather than refuse
            const double s = double(largeMax) / std::max(st.w, st.h);
            const int nw = std::max(1, int(st.w * s)), nh = std::max(1, int(st.h * s));
            std::vector<uint8_t> small(size_t(nw) * nh * 4);
            boxDownsample(st.rgba.data(), st.w, st.h, small.data(), nw, nh);
            st.rgba.swap(small);
            st.w = nw;
            st.h = nh;
        }
        // Walk the tiers top-down, downsampling as we go: the top tier is kept when the
        // view wants it (or its atlas has room), full likewise, mini always.
        const uint8_t *src = st.rgba.data();
        int sw = st.w, sh = st.h;
        std::vector<uint8_t> cur;
        for (int tier = topTier; tier >= TierMini; --tier) {
            const int maxPx = GalleryItem::tierPx(tier);
            if (std::max(sw, sh) > maxPx) {
                const double s = double(maxPx) / std::max(sw, sh);
                const int nw = std::max(1, int(sw * s + 0.5)), nh = std::max(1, int(sh * s + 0.5));
                scratch.resize(size_t(nw) * nh * 4);
                boxDownsample(src, sw, sh, scratch.data(), nw, nh);
                cur.swap(scratch);
                src = cur.data();
                sw = nw;
                sh = nh;
            }
            const bool keep = tier == TierMini || fp.useTier >= tier || hasFree(tier);
            if (keep) {
                if (t.page[tier] < 0) {
                    const CellRef r = alloc(tier, f, fp, st.index, evictions);
                    t.page[tier] = r.page;
                    t.cell[tier] = r.cell;
                }
                if (t.page[tier] >= 0) {
                    m_pages[tier][size_t(t.page[tier])]->upload(f, t.cell[tier], src, sw, sh);
                    t.w[tier] = uint16_t(sw);
                    t.h[tier] = uint16_t(sh);
                }
            } else {
                releaseTier(t, tier);
            }
        }
        t.lastUse = fp.frameNo;
        t.loadedMs = fp.now;
        ++n;
    }
    uploads += n;
    QMutexLocker lock(&m_item->stagedMutex());
    return !queue.empty();
}

void GalleryNode::buildGeometry(const GalleryItem::Frame &fp, int &drawCalls, int &quads)
{
    for (Batch &b : m_batches) {
        b.verts.clear();
        b.idx.clear();
    }
    auto &tiles = m_item->tiles();
    QSGTexture *fallbackTex = m_pages[TierMini].empty() ? nullptr : m_pages[TierMini][0]->sgTexture();
    const int cols = std::max(1, fp.cols);
    const float fadeMs = 180.0f;
    const int first = std::max(0, fp.builtFirst), last = std::min(fp.count - 1, fp.builtLast);
    const float cellW = float(fp.cellW), cellH = float(fp.cellH);
    const bool captionsOn = fp.captionH > 0 && fp.captionMode > 0;
    m_thumbFirst = first;
    m_thumbs.clear();
    for (int i = first; i <= last; ++i) {
        TileRec &t = tiles[size_t(i)];
        t.lastUse = fp.frameNo;
        const int c = i % cols, r = i / cols;
        int bits = 0;
        if (i == fp.selected)
            bits |= TileBits::Selected;
        else if (i == fp.hover)
            bits |= TileBits::Hover;
        if (i == fp.current)
            bits |= TileBits::Current;
        // which tier: the wanted one, else the best resident below it, else any above
        int tier = -1;
        for (int k = fp.useTier; k >= TierMini && tier < 0; --k)
            if (t.page[k] >= 0)
                tier = k;
        for (int k = fp.useTier + 1; k < TierCount && tier < 0; ++k)
            if (t.page[k] >= 0)
                tier = k;
        AtlasPage *page = tier >= 0 ? m_pages[tier][size_t(t.page[tier])].get() : nullptr;
        const int cell = tier >= 0 ? t.cell[tier] : -1;
        const int tw = tier >= 0 ? t.w[tier] : 0, th = tier >= 0 ? t.h[tier] : 0;
        const float aspect = page ? float(tw) / float(std::max(1, th)) : float(fp.cellAspect);
        float w, h;
        if (aspect >= float(fp.cellAspect)) {
            w = cellW;
            h = cellW / aspect;
        } else {
            h = cellH;
            w = cellH * aspect;
        }
        const float x = float(fp.margin) + c * (cellW + float(fp.gap)) + (cellW - w) * 0.5f;
        const float y = float(fp.margin) + r * float(fp.rowPitch) + (cellH - h) * 0.5f;
        const bool caption = captionsOn && GalleryItem::captionFits(fp.captionH, w, h);
        if (caption)
            bits |= TileBits::Caption;
        m_thumbs.push_back({QRectF(x, y, w, h), caption});
        if (page) {
            float u0, v0, u1, v1;
            page->texRect(cell, tw, th, u0, v0, u1, v1);
            float alpha = 1.f;
            if (t.loadedMs >= 0) {
                const float dt = float(fp.now - t.loadedMs);
                alpha = dt >= fadeMs ? 1.f : std::max(0.f, dt / fadeMs);
            }
            appendQuad(batchFor(batchKey(tier, t.page[tier]), page->sgTexture()), x, y, w, h, u0,
                       v0, u1, v1, bits, alpha);
        } else {
            bits |= t.failed ? TileBits::Failed : TileBits::Placeholder;
            appendQuad(batchFor(0, fallbackTex), x, y, w, h, 0.f, 0.f, 0.f, 0.f, bits, 1.f);
        }
    }
    drawCalls = 0;
    quads = 0;
    for (Batch &b : m_batches) {
        if (!b.node)
            continue;
        QSGGeometry *g = b.node->geometry();
        if (b.verts.empty()) {
            if (g->vertexCount() != 0) {
                g->allocate(0, 0);
                b.node->markDirty(QSGNode::DirtyGeometry);
            }
            continue;
        }
        g->allocate(int(b.verts.size()), int(b.idx.size()));
        std::memcpy(g->vertexData(), b.verts.data(), b.verts.size() * sizeof(TileVertex));
        std::memcpy(g->indexData(), b.idx.data(), b.idx.size() * sizeof(quint32));
        b.node->markDirty(QSGNode::DirtyGeometry);
        ++drawCalls;
        quads += int(b.verts.size() / 4);
    }
}

void GalleryNode::dropCaption(int index, Caption &cap)
{
    if (cap.xform) {
        m_captionRoot->removeChildNode(cap.xform);
        delete cap.xform;
        cap.xform = nullptr;
    }
    m_item->setDrawnCaption(index, QString());
}

void GalleryNode::buildCaptions(const GalleryItem::Frame &fp, int &captions)
{
    // One QSGTextNode per captioned tile, cached by index and rebuilt only when the text
    // width, the caption mode, the colours or the text at that index changes (a re-sort
    // moves keys between indices; the current item gains its suffix); per frame only its
    // transform moves. Layouts run here in the sync phase (GUI thread blocked), where
    // font access is safe. Each caption sits on its thumb's bottom edge, centred in the
    // band the shader's scrim darkens, inset from the thumb's left edge; a thumb it does
    // not fit on (buildGeometry's call, which also left the scrim off) has none.
    captions = 0;
    const bool on = fp.captionH > 0 && fp.captionMode > 0 && fp.count > 0;
    const int first = std::max(0, fp.builtFirst), last = std::min(fp.count - 1, fp.builtLast);
    for (auto it = m_captions.begin(); it != m_captions.end();) {
        const bool keep = on && it->first >= first && it->first <= last;
        if (!keep) {
            dropCaption(it->first, it->second);
            it = m_captions.erase(it);
        } else {
            ++it;
        }
    }
    if (!on)
        return;
    const QStringList &keys = m_item->captionKeys();
    const QStringList &names = m_item->captionNames();
    QFontMetricsF fm(fp.captionFont);
    const qreal lineH = std::ceil(fm.height());
    const int lines = fp.captionMode >= 2 ? 2 : 1;
    const qreal inset = GalleryItem::kCaptionInset;
    const QRgb color = fp.captionColor.rgba(), scrim = fp.captionScrimColor.rgba();
    // the shadow under the glyphs: the scrim's colour, opaque enough to edge them
    QColor shadow = fp.captionScrimColor;
    shadow.setAlphaF(std::min(1.0, shadow.alphaF() * 1.4));
    const QString &suffix = fp.currentSuffix;
    const qreal suffixW = suffix.isEmpty() ? 0.0 : fm.horizontalAdvance(suffix);
    auto layoutLine = [&](QSGTextNode *tn, const QString &text, qreal y, qreal width) {
        QTextLayout layout(text, fp.captionFont);
        layout.setCacheEnabled(true);
        layout.beginLayout();
        QTextLine line = layout.createLine();
        line.setLineWidth(width);
        line.setPosition(QPointF(0, 0));
        layout.endLayout();
        tn->addTextLayout(QPointF(0, y), &layout);
    };
    for (int i = first; i <= last; ++i) {
        Caption &cap = m_captions[i];
        const size_t k = size_t(i - m_thumbFirst);
        if (k >= m_thumbs.size() || !m_thumbs[k].caption) {
            dropCaption(i, cap);
            continue;
        }
        const QRectF thumb = m_thumbs[k].rect;
        const qreal textW = thumb.width() - 2 * inset;
        const int textWi = int(textW);
        const QString key = i < keys.size() ? keys.at(i) : QString::number(i);
        const bool current = i == fp.current && !suffix.isEmpty();
        const QString name = (fp.captionMode >= 2 && i < names.size()) ? names.at(i) : QString();
        if (!cap.xform || cap.textW != textWi || cap.mode != fp.captionMode || cap.key != key
            || cap.current != current || cap.name != name || cap.color != color
            || cap.scrim != scrim) {
            dropCaption(i, cap);
            QSGTextNode *tn = m_window->createTextNode();
            tn->setColor(fp.captionColor);
            tn->setTextStyle(QSGTextNode::Raised);
            tn->setStyleColor(shadow);
            tn->setRenderType(QSGTextNode::NativeRendering);
            // the current item keeps its whole suffix: only the key elides, into the room
            // the suffix leaves
            const QString shown =
                current ? fm.elidedText(key, Qt::ElideMiddle, std::max<qreal>(0.0, textW - suffixW))
                              + suffix
                        : fm.elidedText(key, Qt::ElideMiddle, textW);
            layoutLine(tn, shown, 0, textW);
            QString shownName;
            if (!name.isEmpty()) {
                shownName = fm.elidedText(name, Qt::ElideMiddle, textW);
                layoutLine(tn, shownName, lineH, textW);
            }
            auto *xf = new QSGTransformNode;
            xf->appendChildNode(tn);
            m_captionRoot->appendChildNode(xf);
            cap.xform = xf;
            cap.textW = textWi;
            cap.mode = fp.captionMode;
            cap.key = key;
            cap.current = current;
            cap.name = name;
            cap.color = color;
            cap.scrim = scrim;
            m_item->setDrawnCaption(i, shownName.isEmpty() ? shown
                                                           : shown + QLatin1Char('\n') + shownName);
        }
        // centred in the band at the thumb's bottom edge
        const qreal top = thumb.bottom() - fp.captionH + (fp.captionH - lines * lineH) * 0.5;
        QMatrix4x4 m;
        m.translate(float(std::round(thumb.x() + inset)), float(std::round(top)));
        cap.xform->setMatrix(m);
        cap.xform->markDirty(QSGNode::DirtyMatrix);
        cap.lastUse = fp.frameNo;
        ++captions;
    }
}

void GalleryNode::sync()
{
    QElapsedTimer clock;
    clock.start();
    if (!m_glChecked) {
        m_glChecked = true;
        m_glOk = m_window->rendererInterface()->graphicsApi() == QSGRendererInterface::OpenGL;
        if (!m_glOk)
            qWarning() << "[ERROR] GalleryItem needs the OpenGL RHI backend (QSG_RHI_BACKEND=opengl), got"
                       << m_window->rendererInterface()->graphicsApi();
    }
    const GalleryItem::Frame fp = m_item->frameParams();
    int uploads = 0, evictions = 0, drawCalls = 0, quads = 0, captions = 0;
    if (!m_glOk) {
        // another backend (the software renderer in headless tests): draw nothing, but
        // keep the stats flowing so the host logic still runs
        const int none[TierCount] = {0, 0, 0};
        m_item->reportStats(none, 0, 0, 0, 0, 0, clock.nsecsElapsed());
        return;
    }
    {
        auto *ctx = QOpenGLContext::currentContext();
        if (ctx) {
            auto *f = ctx->extraFunctions();
            m_window->beginExternalCommands();
            ensureMiniPage0(f);
            if (m_item->takeResetFlag())
                resetAll();
            drainUploads(f, fp, uploads, evictions);
            m_window->endExternalCommands();
        }
    }
    // uniforms for every batch
    m_uniforms.scale = float(fp.dpr);
    m_uniforms.radius = float(m_item->radius() * fp.dpr);
    m_uniforms.border = float(m_item->borderWidth() * fp.dpr);
    QColor sel = m_item->selectionColor();
    if (!fp.focused)
        sel.setAlphaF(sel.alphaF() * 0.55);   // unfocused: a quieter ring (style B)
    m_uniforms.selection = sel;
    m_uniforms.hover = m_item->hoverColor();
    m_uniforms.placeholder = m_item->placeholderColor();
    m_uniforms.failed = m_item->failedColor();
    m_uniforms.current = m_item->currentColor();
    m_uniforms.scrim = fp.captionScrimColor;
    m_uniforms.scrimPx = float(fp.captionH * fp.dpr);
    m_uniforms.barPx = float(GalleryItem::kCurrentBar * fp.dpr);
    m_uniforms.scrimShare = float(GalleryItem::kCaptionShare);
    for (Batch &b : m_batches)
        if (b.node)
            b.node->markDirty(QSGNode::DirtyMaterial);

    QMatrix4x4 m;
    m.translate(0.0f, float(-fp.scrollY));
    setMatrix(m);
    markDirty(QSGNode::DirtyMatrix);

    buildGeometry(fp, drawCalls, quads);
    if (m_glOk)
        buildCaptions(fp, captions);

    int resident[TierCount] = {0, 0, 0};
    for (int t = 0; t < TierCount; ++t)
        for (auto &p : m_pages[t])
            resident[t] += p->used();
    m_item->reportStats(resident, uploads, evictions, drawCalls, quads, captions, clock.nsecsElapsed());
}

} // namespace specto
