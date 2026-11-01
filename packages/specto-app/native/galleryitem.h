// GalleryItem: a native Qt Quick item that draws a vertically scrolling, column-reflowed
// grid of thumbnails from a three-tier texture atlas (HLD 3.7 / 4.1, the "GalleryItem"
// of _spectoqt; M1.5 "a real gallery"). Built as the QML module plugin `Specto.Native`
// (see CMakeLists.txt and build.py) and used from PySide6 through the meta-object:
// properties, invokables and signals only. The item never calls into Python.
//
// Layout model (M1.5): the zoom variable is the thumbnail size, `tileWidth` (px). The
// column count follows from the width (`columns = round((width - 2*margin + gap) /
// (tileWidth + gap))`) and cells stretch a little to fill the row exactly
// (`cellWidth = (width - 2*margin - (columns-1)*gap) / columns`), so a wider slot shows
// more columns of the same size, never bigger tiles. Cell height follows the
// collection's aspect (`cellAspect`, the median of decoded thumbs when `autoAspect`), and
// every thumb is letterboxed inside its cell. Zooming (Ctrl+wheel, pinch, +/-) steps the
// column count by one at the current width (it sets `tileWidth` to the size that lands
// there), keeps the item under the cursor anchored and animates the cell size; a resize
// keeps the top-left item at the top. The plain wheel scrolls, dragging pans with inertia. Captions (key, optional name) are
// drawn under the tiles by the scene graph (QSGTextNode), nothing per-frame in QML.
//
// Data path: a worker decodes a thumbnail (uint8 RGBA, HWC) into shared memory; the
// GUI thread calls pushThumbShm(index, shm name, offset, w, h) (or pushThumb with the
// bytes), which copies the pixels into a staging queue; the render thread uploads
// staged tiles into atlas pages in updatePaintNode (GUI blocked, GL context current),
// building the mip chain and the smaller tiers on the CPU as it goes.
//
// Tiers: mini (48 px in a 56 px cell, all resident), full (176 px in 192 px, LRU over
// `fullPages` pages) and large (384 px in 400 px, LRU over `largePages` pages, used when
// tiles draw bigger than the full tier). `wantTier` says which tier the view needs for
// its size; Python asks `missing(first, last, tier)` and decodes at that tier's size.
//
// Threading rule from spike S1: frames are requested from the GUI thread only
// (QQuickItem::update()); nothing here calls QQuickWindow::update() from the render
// thread.
#pragma once

#include <QtCore/QByteArray>
#include <QtCore/QElapsedTimer>
#include <QtCore/QMutex>
#include <QtCore/QPointF>
#include <QtCore/QRectF>
#include <QtCore/QStringList>
#include <QtCore/QTimer>
#include <QtCore/QVariant>
#include <QtGui/QColor>
#include <QtGui/QFont>
#include <QtQuick/QQuickItem>

#include <atomic>
#include <cstdint>
#include <deque>
#include <vector>

#if defined(SPECTONATIVE_BUILD)
#define SPECTONATIVE_EXPORT Q_DECL_EXPORT
#else
#define SPECTONATIVE_EXPORT Q_DECL_IMPORT
#endif

class GalleryNode;

enum GalleryTier : int { TierMini = 0, TierFull = 1, TierLarge = 2, TierCount = 3 };

// Per-item residency record. Written by the render thread during sync (GUI blocked)
// and by the GUI thread otherwise, so no lock is needed.
struct TileRec {
    int16_t page[TierCount] = {-1, -1, -1};
    int16_t cell[TierCount] = {-1, -1, -1};
    uint16_t w[TierCount] = {0, 0, 0};   // decoded size held in that tier
    uint16_t h[TierCount] = {0, 0, 0};
    uint32_t lastUse = 0;   // frame counter when the tile was last in the built range
    qint64 loadedMs = -1;   // item clock when a tier first landed (fade-in)
    bool failed = false;
    bool staged = false;    // pushed, not yet uploaded
    bool has(int tier) const { return page[tier] >= 0; }
    bool any() const { return page[0] >= 0 || page[1] >= 0 || page[2] >= 0; }
    // aspect of the best decoded size we know
    qreal aspect() const
    {
        for (int t = TierLarge; t >= TierMini; --t)
            if (w[t] > 0 && h[t] > 0)
                return qreal(w[t]) / qreal(h[t]);
        return 0.0;
    }
};

struct StagedThumb {
    int index;
    int w, h;
    std::vector<uint8_t> rgba;   // w*h*4, straight alpha
};

class GalleryItem : public QQuickItem
{
    Q_OBJECT
    QML_ELEMENT
    // -- data and layout (px) --------------------------------------------------------
    Q_PROPERTY(int count READ count WRITE setCount NOTIFY countChanged)
    Q_PROPERTY(int columns READ columns WRITE setColumns NOTIFY layoutChanged)
    Q_PROPERTY(qreal tileWidth READ tileWidth WRITE setTileWidth NOTIFY layoutChanged)
    Q_PROPERTY(int effectiveColumns READ effectiveColumns NOTIFY layoutChanged)
    Q_PROPERTY(int minColumns READ minColumns WRITE setMinColumns NOTIFY layoutChanged)
    Q_PROPERTY(int rows READ rows NOTIFY layoutChanged)
    Q_PROPERTY(qreal cellWidth READ cellWidth NOTIFY layoutChanged)
    Q_PROPERTY(qreal cellHeight READ cellHeight NOTIFY layoutChanged)
    Q_PROPERTY(qreal cellAspect READ cellAspect WRITE setCellAspect NOTIFY layoutChanged)
    Q_PROPERTY(bool autoAspect READ autoAspect WRITE setAutoAspect NOTIFY layoutChanged)
    Q_PROPERTY(qreal gap READ gap WRITE setGap NOTIFY layoutChanged)
    Q_PROPERTY(qreal margin READ margin WRITE setMargin NOTIFY layoutChanged)
    Q_PROPERTY(qreal minCellWidth READ minCellWidth WRITE setMinCellWidth NOTIFY layoutChanged)
    Q_PROPERTY(int captionMode READ captionMode WRITE setCaptionMode NOTIFY layoutChanged)
    Q_PROPERTY(qreal captionHeight READ captionHeight NOTIFY layoutChanged)
    Q_PROPERTY(qreal captionMinWidth READ captionMinWidth WRITE setCaptionMinWidth NOTIFY layoutChanged)
    Q_PROPERTY(qreal rowPitch READ rowPitch NOTIFY layoutChanged)
    // -- scroll ----------------------------------------------------------------------
    Q_PROPERTY(qreal scrollY READ scrollY WRITE setScrollY NOTIFY scrollChanged)
    Q_PROPERTY(qreal scrollRows READ scrollRows WRITE setScrollRows NOTIFY scrollChanged)
    Q_PROPERTY(qreal contentHeight READ contentHeight NOTIFY layoutChanged)
    Q_PROPERTY(qreal maxScroll READ maxScroll NOTIFY layoutChanged)
    Q_PROPERTY(bool scrolling READ scrolling NOTIFY scrollingChanged)
    Q_PROPERTY(bool dragging READ dragging NOTIFY draggingChanged)
    Q_PROPERTY(bool interactive READ interactive WRITE setInteractive NOTIFY interactiveChanged)
    // a mouse drag that starts on a tile drags the tile out (dragRequested, #185) instead of
    // scrolling; a drag on the gaps, and touch, still scroll
    Q_PROPERTY(bool tileDrag READ tileDrag WRITE setTileDrag NOTIFY tileDragChanged)
    Q_PROPERTY(int firstVisible READ firstVisible NOTIFY scrollChanged)
    Q_PROPERTY(int lastVisible READ lastVisible NOTIFY scrollChanged)
    // -- selection and hover ---------------------------------------------------------
    Q_PROPERTY(int selectedIndex READ selectedIndex WRITE setSelectedIndex NOTIFY selectedIndexChanged)
    Q_PROPERTY(int hoverIndex READ hoverIndex NOTIFY hoverIndexChanged)
    Q_PROPERTY(QPointF hoverPos READ hoverPos NOTIFY hoverIndexChanged)
    Q_PROPERTY(QRectF selectedRect READ selectedRect NOTIFY scrollChanged)
    Q_PROPERTY(QRectF hoverRect READ hoverRect NOTIFY hoverIndexChanged)
    // -- what the feeder should decode -----------------------------------------------
    Q_PROPERTY(int firstWanted READ firstWanted NOTIFY wantedChanged)
    Q_PROPERTY(int lastWanted READ lastWanted NOTIFY wantedChanged)
    Q_PROPERTY(int wantTier READ wantTier NOTIFY wantedChanged)
    Q_PROPERTY(int thumbPx READ thumbPx CONSTANT)
    Q_PROPERTY(int largePx READ largePx CONSTANT)
    Q_PROPERTY(int miniPx READ miniPx CONSTANT)
    Q_PROPERTY(int fullPages READ fullPages WRITE setFullPages NOTIFY statsChanged)
    Q_PROPERTY(int largePages READ largePages WRITE setLargePages NOTIFY statsChanged)
    // -- stats (GUI thread, refreshed after each sync) -------------------------------
    Q_PROPERTY(int residentFull READ residentFull NOTIFY statsChanged)
    Q_PROPERTY(int residentMini READ residentMini NOTIFY statsChanged)
    Q_PROPERTY(int residentLarge READ residentLarge NOTIFY statsChanged)
    Q_PROPERTY(int staged READ staged NOTIFY statsChanged)
    Q_PROPERTY(int uploads READ uploads NOTIFY statsChanged)
    Q_PROPERTY(int evictions READ evictions NOTIFY statsChanged)
    Q_PROPERTY(int frames READ frames NOTIFY statsChanged)
    Q_PROPERTY(double syncMs READ syncMs NOTIFY statsChanged)
    Q_PROPERTY(int drawCalls READ drawCalls NOTIFY statsChanged)
    Q_PROPERTY(int quads READ quads NOTIFY statsChanged)
    Q_PROPERTY(int captions READ captions NOTIFY statsChanged)
    // -- style tokens (B Calm Canvas; QML sets them from the theme table) --------------
    Q_PROPERTY(QColor selectionColor READ selectionColor WRITE setSelectionColor NOTIFY styleChanged)
    Q_PROPERTY(QColor hoverColor READ hoverColor WRITE setHoverColor NOTIFY styleChanged)
    Q_PROPERTY(QColor placeholderColor READ placeholderColor WRITE setPlaceholderColor NOTIFY styleChanged)
    Q_PROPERTY(QColor failedColor READ failedColor WRITE setFailedColor NOTIFY styleChanged)
    Q_PROPERTY(QColor captionColor READ captionColor WRITE setCaptionColor NOTIFY styleChanged)
    Q_PROPERTY(QFont captionFont READ captionFont WRITE setCaptionFont NOTIFY styleChanged)
    Q_PROPERTY(qreal radius READ radius WRITE setRadius NOTIFY styleChanged)
    Q_PROPERTY(qreal borderWidth READ borderWidth WRITE setBorderWidth NOTIFY styleChanged)

public:
    explicit GalleryItem(QQuickItem *parent = nullptr);
    ~GalleryItem() override;

    // atlas geometry, fixed for the life of the item
    static constexpr int kPageSize = 2048;
    static constexpr int kMiniCell = 56, kMiniGutter = 4, kMiniLevels = 4;      // 48 px thumbs
    static constexpr int kFullCell = 192, kFullGutter = 8, kFullLevels = 4;     // 176 px thumbs
    static constexpr int kLargeCell = 400, kLargeGutter = 8, kLargeLevels = 5;  // 384 px thumbs
    static constexpr int kMaxUploadsPerFrame = 24;
    static int tierCell(int tier)
    {
        return tier == TierMini ? kMiniCell : tier == TierFull ? kFullCell : kLargeCell;
    }
    static int tierGutter(int tier)
    {
        return tier == TierMini ? kMiniGutter : tier == TierFull ? kFullGutter : kLargeGutter;
    }
    static int tierLevels(int tier)
    {
        return tier == TierMini ? kMiniLevels : tier == TierFull ? kFullLevels : kLargeLevels;
    }
    static int tierPx(int tier) { return tierCell(tier) - 2 * tierGutter(tier); }
    static int tierPerPage(int tier)
    {
        const int n = kPageSize / tierCell(tier);
        return n * n;
    }

    int count() const { return m_count; }
    void setCount(int n);
    // 0 while the size is automatic; else the column count the tile size gives now
    int columns() const;
    void setColumns(int c);
    // the zoom level: the target cell width (0 = automatic, about 160 px)
    qreal tileWidth() const { return m_tileW; }
    void setTileWidth(qreal px);
    int effectiveColumns() const;
    int minColumns() const { return m_minColumns; }
    void setMinColumns(int c);
    int maxColumns() const;
    int rows() const;
    qreal cellWidth() const { return m_cellW; }
    qreal cellHeight() const { return m_cellW / m_cellAspect; }
    qreal cellAspect() const { return m_cellAspect; }
    void setCellAspect(qreal a);
    bool autoAspect() const { return m_autoAspect; }
    void setAutoAspect(bool on);
    qreal gap() const { return m_gap; }
    void setGap(qreal px);
    qreal margin() const { return m_margin; }
    void setMargin(qreal px);
    qreal minCellWidth() const { return m_minCellW; }
    void setMinCellWidth(qreal px);
    int captionMode() const { return m_captionMode; }
    void setCaptionMode(int mode);
    qreal captionHeight() const;
    qreal captionMinWidth() const { return m_captionMinW; }
    void setCaptionMinWidth(qreal px);
    qreal rowPitch() const { return cellHeight() + captionHeight() + m_gap; }

    qreal scrollY() const { return m_scrollY; }
    void setScrollY(qreal y) { setScroll(y, false); }
    qreal scrollRows() const { return m_scrollY / rowPitch(); }
    void setScrollRows(qreal rowsF) { setScroll(rowsF * rowPitch(), false); }
    qreal contentHeight() const;
    qreal maxScroll() const { return std::max<qreal>(0.0, contentHeight() - height()); }
    bool scrolling() const { return m_scrolling; }
    bool dragging() const { return m_dragging; }
    bool interactive() const { return m_interactive; }
    bool tileDrag() const { return m_tileDrag; }
    void setTileDrag(bool on);
    void setInteractive(bool on);
    int firstVisible() const { return m_firstVisible; }
    int lastVisible() const { return m_lastVisible; }

    int selectedIndex() const { return m_selected; }
    void setSelectedIndex(int i);
    int hoverIndex() const { return m_hover; }
    QPointF hoverPos() const { return m_hoverPos; }
    QRectF selectedRect() const { return rectOf(m_selected); }
    QRectF hoverRect() const { return rectOf(m_hover); }

    int firstWanted() const { return m_firstWanted; }
    int lastWanted() const { return m_lastWanted; }
    int wantTier() const { return m_wantTier; }
    int thumbPx() const { return tierPx(TierFull); }
    int largePx() const { return tierPx(TierLarge); }
    int miniPx() const { return tierPx(TierMini); }
    int fullPages() const { return m_pages[TierFull]; }
    void setFullPages(int n);
    int largePages() const { return m_pages[TierLarge]; }
    void setLargePages(int n);
    int pagesOf(int tier) const { return m_pages[tier]; }
    int capacityOf(int tier) const { return m_pages[tier] * tierPerPage(tier); }

    int residentFull() const { return m_statResident[TierFull]; }
    int residentMini() const { return m_statResident[TierMini]; }
    int residentLarge() const { return m_statResident[TierLarge]; }
    int staged() const { return m_statStaged; }
    int uploads() const { return m_statUploads; }
    int evictions() const { return m_statEvictions; }
    int frames() const { return m_statFrames; }
    double syncMs() const { return m_statSyncMs; }
    int drawCalls() const { return m_statDrawCalls; }
    int quads() const { return m_statQuads; }
    int captions() const { return m_statCaptions; }

    QColor selectionColor() const { return m_selectionColor; }
    void setSelectionColor(const QColor &c);
    QColor hoverColor() const { return m_hoverColor; }
    void setHoverColor(const QColor &c);
    QColor placeholderColor() const { return m_placeholderColor; }
    void setPlaceholderColor(const QColor &c);
    QColor failedColor() const { return m_failedColor; }
    void setFailedColor(const QColor &c);
    QColor captionColor() const { return m_captionColor; }
    void setCaptionColor(const QColor &c);
    QFont captionFont() const { return m_captionFont; }
    void setCaptionFont(const QFont &f);
    qreal radius() const { return m_radius; }
    void setRadius(qreal r);
    qreal borderWidth() const { return m_borderWidth; }
    void setBorderWidth(qreal w);

    // -- thumbnails in ---------------------------------------------------------------
    // Copy a uint8 RGBA HWC thumbnail out of a named shared-memory block (the worker
    // pool's result). Returns false when the block cannot be opened or the index is out
    // of range. The block may be released as soon as this returns. A thumb larger than
    // thumbPx lands in the large tier (and is downsampled into the smaller tiers).
    Q_INVOKABLE bool pushThumbShm(int index, const QString &shmName, int offset, int w, int h);
    Q_INVOKABLE bool pushThumb(int index, const QByteArray &rgba, int w, int h);
    Q_INVOKABLE void setFailed(int index, bool failed = true);
    Q_INVOKABLE void clearThumbs();
    // Indices in [first, last] that lack `tier` (or any tier when tier < 0) and are not
    // staged or failed. Order: nearest to the viewport centre first.
    Q_INVOKABLE QVariantList missing(int first, int last, int tier) const;
    Q_INVOKABLE bool hasTier(int index, int tier) const;
    // captions: `keys[i]` under tile i (mode 1), plus `names[i]` (mode 2)
    Q_INVOKABLE void setCaptions(const QStringList &keys, const QStringList &names);
    Q_INVOKABLE QString captionOf(int index) const;

    // -- geometry queries (item coordinates) -----------------------------------------
    Q_INVOKABLE QRectF rectOf(int index) const;      // thumb rect on screen; empty if hidden
    Q_INVOKABLE QRectF cellRectOf(int index) const;  // cell incl. caption; empty if hidden
    Q_INVOKABLE int indexAt(qreal x, qreal y) const; // -1 when not over a cell
    Q_INVOKABLE int centerIndex() const;
    Q_INVOKABLE int visibleRows() const;
    // -- navigation ------------------------------------------------------------------
    Q_INVOKABLE void scrollTo(qreal y, bool animate = true);
    Q_INVOKABLE void revealIndex(int index, bool animate = true);
    // Zoom to `columns` at the current width (sets tileWidth) keeping the content under
    // (ax, ay) anchored; animated when `animate`. ax/ay < 0 anchor at the selected tile
    // (if visible) or the centre.
    Q_INVOKABLE void zoomTo(int columns, qreal ax = -1, qreal ay = -1, bool animate = true);
    Q_INVOKABLE void zoomBy(int deltaColumns, qreal ax = -1, qreal ay = -1);
    Q_INVOKABLE void setView(int columns, qreal scrollRows, bool fromUser);
    Q_INVOKABLE void resetStats();

    // render-thread entry points (called from GalleryNode during sync)
    std::vector<TileRec> &tiles() { return m_tiles; }
    std::deque<StagedThumb> &stagedQueue() { return m_staged; }
    QMutex &stagedMutex() { return m_stagedMutex; }
    bool takeResetFlag() { return m_resetAtlas.exchange(false); }
    qint64 nowMs() const { return m_clock.elapsed(); }
    struct Frame {
        qreal scrollY, dpr;
        qreal margin, gap, cellW, cellH, captionH, rowPitch, cellAspect;
        int cols, count;
        int builtFirst, builtLast;   // index range to build geometry for
        int wantedFirst, wantedLast; // range the feeder keeps resident (eviction avoids it)
        int useTier;                 // tier the view prefers
        int captionMode;
        int selected, hover;
        bool focused;
        qint64 now;
        uint32_t frameNo;
        QFont captionFont;
        QColor captionColor;
    };
    Frame frameParams() const;
    const QStringList &captionKeys() const { return m_keys; }
    const QStringList &captionNames() const { return m_names; }
    void reportStats(const int resident[TierCount], int uploads, int evictions, int drawCalls,
                     int quads, int captions, qint64 syncNs);

signals:
    void countChanged();
    void layoutChanged();
    void scrollChanged();
    void scrollingChanged();
    void draggingChanged();
    void interactiveChanged();
    void tileDragChanged();
    void selectedIndexChanged();
    void hoverIndexChanged();
    void wantedChanged();
    void statsChanged();
    void styleChanged();
    void captionsChanged();
    // user intent (the Python host publishes these on channels / as commands)
    void userSelected(int index, int modifiers);
    void clicked(int index, int button, int modifiers);
    void activated(int index);          // double-click or Enter
    void quickLook(int index);          // Space
    void userViewChanged();             // scroll/columns changed by drag, wheel or keys
    void gestureStarted();
    void gestureEnded();                // drag released, inertia settled, wheel idle, key step
    void dragRequested(int index, int modifiers);  // a left mouse drag left a tile (#185)

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void mouseReleaseEvent(QMouseEvent *event) override;
    void mouseDoubleClickEvent(QMouseEvent *event) override;
    void wheelEvent(QWheelEvent *event) override;
    void hoverMoveEvent(QHoverEvent *event) override;
    void hoverLeaveEvent(QHoverEvent *event) override;
    void keyPressEvent(QKeyEvent *event) override;
    void focusInEvent(QFocusEvent *event) override;
    void focusOutEvent(QFocusEvent *event) override;
    bool event(QEvent *event) override;
    void releaseResources() override;

private:
    qreal targetCellWidth() const;
    qreal targetCellWidth(int cols) const;
    void relayout();
    void applyColumns(int c);   // tileWidth for `c` columns now, or pending until laid out
    void setScroll(qreal y, bool fromUser);
    void recomputeRanges(bool emitSignals = true);
    QRectF contentCellRect(int index) const;      // content px (scroll not applied)
    QRectF contentThumbRect(int index) const;
    qreal aspectOf(int index) const;
    void setHover(int i, const QPointF &pos);
    void stepSelection(int delta, int modifiers);
    void beginGesture();
    void endGesture();
    void setScrolling(bool on);
    void animTick();
    void startAnim();
    void publishStats();
    void maybeUpdateAspect();
    void recomputeLayoutFromScratch();

    int m_count = 0;
    qreal m_tileW = 0.0;      // zoom level: target cell width; 0 = kDefaultCellW
    int m_pendingCols = 0;    // columns asked for before the item had a width
    int m_laidCols = 0;       // columns the scroll position was laid out in; 0 = not yet
    int m_pendingReveal = -1; // reveal asked for before the item had a height
    int m_minColumns = 1;
    qreal m_cellW = 160.0;    // live (animated) cell width
    qreal m_cellAspect = 1.5;
    bool m_autoAspect = true;
    int m_aspectSamples = 0;
    qreal m_gap = 8.0;
    qreal m_margin = 8.0;
    qreal m_minCellW = 40.0;
    int m_captionMode = 1;
    qreal m_captionMinW = 72.0;
    qreal m_scrollY = 0;
    bool m_interactive = true;
    bool m_tileDrag = true;
    bool m_scrolling = false;
    int m_selected = -1, m_hover = -1;
    QPointF m_hoverPos;
    int m_firstVisible = -1, m_lastVisible = -1;
    int m_firstWanted = -1, m_lastWanted = -1, m_builtFirst = -1, m_builtLast = -1;
    int m_wantTier = TierFull;
    int m_pages[TierCount] = {64, 12, 6};
    uint32_t m_frameNo = 0;
    bool m_focused = false;
    QStringList m_keys, m_names;

    QColor m_selectionColor{0x2f, 0x6f, 0xab};
    QColor m_hoverColor{0x2f, 0x6f, 0xab, 0x8c};
    QColor m_placeholderColor{0xec, 0xe6, 0xda};
    QColor m_failedColor{0xf6, 0xde, 0xdd};
    QColor m_captionColor{0x7a, 0x72, 0x66};
    QFont m_captionFont;
    qreal m_radius = 8.0, m_borderWidth = 2.0;

    std::vector<TileRec> m_tiles;
    std::deque<StagedThumb> m_staged;
    QMutex m_stagedMutex;
    std::atomic<bool> m_resetAtlas{false};
    QElapsedTimer m_clock;

    // input
    bool m_pressed = false, m_dragging = false;
    QPointF m_pressPos, m_lastPos;
    int m_pressIndex = -1;
    Qt::MouseButton m_pressButton = Qt::NoButton;
    // double-click detected from press timing (a real mouse's second press does not
    // always reach mouseDoubleClickEvent once the first click re-laid out the shell)
    qint64 m_lastClickMs = -1;   // event timestamp of the last click's release
    int m_lastClickIndex = -1;
    QPointF m_lastClickPos;
    bool m_pressActivated = false;
    QTimer m_wheelIdle;
    bool m_wheelGesture = false;
    int m_wheelZoomAccum = 0;
    qreal m_pinchAccum = 1.0;
    struct VelSample {
        qint64 t;
        qreal y;
    };
    std::deque<VelSample> m_velocity;

    // animation (GUI thread): smooth scroll target, inertia, zoom
    QTimer m_anim;
    bool m_scrollAnim = false;
    qreal m_scrollTarget = 0;
    bool m_inertia = false;
    qreal m_inertiaVel = 0;   // px/s
    qint64 m_lastTickMs = 0;
    bool m_zoomAnim = false;
    qreal m_zoomFromW = 0, m_zoomToW = 0;
    qint64 m_zoomStartMs = 0;
    int m_anchorIndex = -1;
    qreal m_anchorFy = 0, m_anchorScreenY = 0;
    qint64 m_anchorMs = -100000;   // last zoom step (notches of one gesture share the anchor)
    bool m_gestureFromAnim = false;
    qint64 m_lastUploadMs = -1000;

    // stats
    int m_statResident[TierCount] = {0, 0, 0};
    int m_statStaged = 0, m_statUploads = 0;
    int m_statEvictions = 0, m_statFrames = 0, m_statDrawCalls = 0, m_statQuads = 0;
    int m_statCaptions = 0;
    double m_statSyncMs = 0;
    qint64 m_statSyncNsAccum = 0;
    int m_statSyncSamples = 0;
    bool m_statsDirty = false;
    uint32_t m_uploadsSinceAspect = 0;
};
