#include "galleryitem.h"

#include "gallerynode.h"
#include "shm.h"

#include <QtCore/QDebug>
#include <QtGui/QFocusEvent>
#include <QtGui/QGuiApplication>
#include <QtGui/QStyleHints>
#include <QtGui/QHoverEvent>
#include <QtGui/QKeyEvent>
#include <QtGui/QMouseEvent>
#include <QtGui/QWheelEvent>
#include <QtQuick/QQuickWindow>

#include <algorithm>
#include <cmath>
#include <cstring>

namespace {
constexpr qreal kDragThresholdPx = 4.0;
constexpr int kWheelIdleMs = 160;
constexpr int kZoomAnimMs = 160;
constexpr int kMaxThumbEdge = 4096;
constexpr int kMaxColumns = 512;
constexpr qreal kDefaultCellW = 160.0;
constexpr qreal kInertiaDecel = 2600.0;   // px/s^2
constexpr qreal kMinFlingVel = 120.0;     // px/s
constexpr qreal kStopVel = 24.0;          // px/s
constexpr qint64 kVelocityWindowMs = 90;
constexpr qreal kScrollEaseMs = 70.0;     // smooth wheel scroll time constant

qreal easeOut(qreal t)
{
    return 1.0 - (1.0 - t) * (1.0 - t);
}
} // namespace

GalleryItem::GalleryItem(QQuickItem *parent)
    : QQuickItem(parent)
{
    setFlag(ItemHasContents, true);
    setAcceptedMouseButtons(Qt::LeftButton | Qt::RightButton);
    setAcceptHoverEvents(true);
    setActiveFocusOnTab(true);
    m_clock.start();
    m_captionFont = QGuiApplication::font();
    m_captionFont.setPixelSize(11);

    m_wheelIdle.setSingleShot(true);
    m_wheelIdle.setInterval(kWheelIdleMs);
    connect(&m_wheelIdle, &QTimer::timeout, this, [this] {
        m_wheelGesture = false;
        endGesture();
    });
    m_anim.setInterval(16);
    m_anim.setTimerType(Qt::PreciseTimer);
    connect(&m_anim, &QTimer::timeout, this, &GalleryItem::animTick);
}

GalleryItem::~GalleryItem() = default;

// ---------------------------------------------------------------------------------
// data and layout
// ---------------------------------------------------------------------------------
void GalleryItem::setCount(int n)
{
    n = std::max(0, n);
    if (n == m_count)
        return;
    m_count = n;
    m_tiles.resize(size_t(n));
    if (m_selected >= n) {
        m_selected = -1;
        emit selectedIndexChanged();
    }
    if (m_current >= n) {
        m_current = -1;
        emit currentIndexChanged();
    }
    if (m_hover >= n)
        setHover(-1, m_hoverPos);
    emit countChanged();
    relayout();
}

int GalleryItem::maxColumns() const
{
    const qreal w = width() - 2 * m_margin + m_gap;
    if (w <= 0)
        return kMaxColumns;
    return std::clamp(int(std::floor(w / (m_minCellW + m_gap))), 1, kMaxColumns);
}

int GalleryItem::effectiveColumns() const
{
    const int lo = std::max(1, m_minColumns), hi = std::max(lo, maxColumns());
    const qreal w = width() - 2 * m_margin + m_gap;
    if (w <= 0 || m_pendingCols > 0)
        return std::clamp(m_pendingCols > 0 ? m_pendingCols : 4, lo, hi);
    // as many columns of about tileWidth as fit; the cells stretch to fill the row
    const qreal tile = m_tileW > 0 ? m_tileW : kDefaultCellW;
    return std::clamp(int(std::lround(w / (tile + m_gap))), lo, hi);
}

int GalleryItem::columns() const
{
    if (m_pendingCols > 0)
        return m_pendingCols;
    return m_tileW > 0 ? effectiveColumns() : 0;
}

void GalleryItem::applyColumns(int c)
{
    c = std::clamp(c, 0, kMaxColumns);
    m_pendingCols = 0;
    if (c == 0) {
        m_tileW = 0;
        return;
    }
    if (width() - 2 * m_margin + m_gap <= 0) {
        m_pendingCols = c;   // converted to a tile width at the first real layout
        return;
    }
    c = std::clamp(c, std::max(1, m_minColumns), std::max(1, maxColumns()));
    m_tileW = targetCellWidth(c);
}

int GalleryItem::rows() const
{
    const int cols = effectiveColumns();
    return m_count <= 0 ? 0 : (m_count + cols - 1) / cols;
}

qreal GalleryItem::targetCellWidth(int cols) const
{
    cols = std::max(1, cols);
    if (width() <= 0)
        return kDefaultCellW;
    return std::max<qreal>(1.0, (width() - 2 * m_margin - (cols - 1) * m_gap) / cols);
}

qreal GalleryItem::targetCellWidth() const
{
    return targetCellWidth(effectiveColumns());
}

qreal GalleryItem::captionHeight() const
{
    // the band on the thumb's bottom edge the scrim covers: the lines plus the padding
    // above and below them (kCaptionPad each)
    if (m_captionMode <= 0 || m_cellW < m_captionMinW)
        return 0.0;
    const qreal px = m_captionFont.pixelSize() > 0 ? m_captionFont.pixelSize()
                                                    : m_captionFont.pointSizeF() * 1.33;
    const qreal line = std::ceil(px * 1.45);
    return (m_captionMode >= 2 ? 2 * line : line) + 2 * kCaptionPad;
}

qreal GalleryItem::contentHeight() const
{
    const int n = rows();
    return n <= 0 ? 0.0 : n * rowPitch() - m_gap + 2 * m_margin;
}

void GalleryItem::relayout()
{
    // Called when width, tile size, gap, margin or aspect change outside a zoom
    // animation: snap the cell width to the target and keep the item at the top-left
    // corner at the top (the column count may have changed under it).
    const qreal oldPitch = rowPitch();
    const qreal rowsF = oldPitch > 0 ? m_scrollY / oldPitch : 0.0;
    const int topRow = int(std::floor(rowsF));
    const int topIndex = std::clamp(topRow * std::max(1, m_laidCols), 0, std::max(0, m_count - 1));
    m_zoomAnim = false;
    m_cellW = targetCellWidth();
    // a re-flow starts the top item's row at the top edge (a part-hidden first row reads
    // as tiles tucked under the header); the same columns keep the exact offset. Before
    // the first real layout the position is a restored one, in rows: keep it.
    const int cols = effectiveColumns();
    const qreal frac = cols == m_laidCols ? rowsF - topRow : 0.0;
    const qreal y = m_laidCols <= 0 ? rowsF * rowPitch() : (topIndex / cols + frac) * rowPitch();
    m_scrollY = std::clamp(y, 0.0, maxScroll());
    emit layoutChanged();
    recomputeRanges();
    emit scrollChanged();
    update();
}

void GalleryItem::setColumns(int c)
{
    c = std::clamp(c, 0, kMaxColumns);
    if (c == columns())
        return;
    applyColumns(c);
    relayout();
}

void GalleryItem::setTileWidth(qreal px)
{
    px = px <= 0 ? 0.0 : std::clamp<qreal>(px, 8.0, 2048.0);
    if (m_pendingCols == 0 && qFuzzyCompare(1.0 + px, 1.0 + m_tileW))
        return;
    m_tileW = px;
    m_pendingCols = 0;
    relayout();
}

void GalleryItem::setMinColumns(int c)
{
    c = std::clamp(c, 1, kMaxColumns);
    if (c == m_minColumns)
        return;
    m_minColumns = c;
    relayout();
}

void GalleryItem::setCellAspect(qreal a)
{
    a = std::clamp<qreal>(a, 0.2, 5.0);
    if (qFuzzyCompare(a, m_cellAspect))
        return;
    m_cellAspect = a;
    relayout();
}

void GalleryItem::setAutoAspect(bool on)
{
    if (on == m_autoAspect)
        return;
    m_autoAspect = on;
    m_aspectSamples = 0;
    emit layoutChanged();
    if (on)
        maybeUpdateAspect();
}

void GalleryItem::setGap(qreal px)
{
    px = std::clamp<qreal>(px, 0.0, 64.0);
    if (qFuzzyCompare(1.0 + px, 1.0 + m_gap))
        return;
    m_gap = px;
    relayout();
}

void GalleryItem::setMargin(qreal px)
{
    px = std::clamp<qreal>(px, 0.0, 64.0);
    if (qFuzzyCompare(1.0 + px, 1.0 + m_margin))
        return;
    m_margin = px;
    relayout();
}

void GalleryItem::setMinCellWidth(qreal px)
{
    px = std::clamp<qreal>(px, 8.0, 1024.0);
    if (qFuzzyCompare(px, m_minCellW))
        return;
    m_minCellW = px;
    relayout();
}

void GalleryItem::setCaptionMode(int mode)
{
    mode = std::clamp(mode, 0, 2);
    if (mode == m_captionMode)
        return;
    m_captionMode = mode;
    // captions overlay the tiles: the layout stays, only the band changes
    emit layoutChanged();
    update();
}

void GalleryItem::setCaptionMinWidth(qreal px)
{
    px = std::clamp<qreal>(px, 0.0, 1024.0);
    if (qFuzzyCompare(1.0 + px, 1.0 + m_captionMinW))
        return;
    m_captionMinW = px;
    emit layoutChanged();
    update();
}

void GalleryItem::setInteractive(bool on)
{
    if (on == m_interactive)
        return;
    m_interactive = on;
    emit interactiveChanged();
}

void GalleryItem::setTileDrag(bool on)
{
    if (on == m_tileDrag)
        return;
    m_tileDrag = on;
    emit tileDragChanged();
}

void GalleryItem::setFullPages(int n)
{
    n = std::clamp(n, 1, 64);
    if (n == m_pages[TierFull])
        return;
    m_pages[TierFull] = n;
    emit statsChanged();
    recomputeRanges();
}

void GalleryItem::setLargePages(int n)
{
    n = std::clamp(n, 1, 32);
    if (n == m_pages[TierLarge])
        return;
    m_pages[TierLarge] = n;
    emit statsChanged();
    recomputeRanges();
}

// ---------------------------------------------------------------------------------
// style
// ---------------------------------------------------------------------------------
#define SPECTO_STYLE_SETTER(Name, member, Type)                                                    \
    void GalleryItem::Name(const Type &v)                                                          \
    {                                                                                              \
        if (v == member)                                                                           \
            return;                                                                                \
        member = v;                                                                                \
        emit styleChanged();                                                                       \
        update();                                                                                  \
    }
SPECTO_STYLE_SETTER(setSelectionColor, m_selectionColor, QColor)
SPECTO_STYLE_SETTER(setHoverColor, m_hoverColor, QColor)
SPECTO_STYLE_SETTER(setPlaceholderColor, m_placeholderColor, QColor)
SPECTO_STYLE_SETTER(setFailedColor, m_failedColor, QColor)
SPECTO_STYLE_SETTER(setCaptionColor, m_captionColor, QColor)
SPECTO_STYLE_SETTER(setCaptionScrimColor, m_captionScrimColor, QColor)
SPECTO_STYLE_SETTER(setCurrentColor, m_currentColor, QColor)
SPECTO_STYLE_SETTER(setCurrentSuffix, m_currentSuffix, QString)
#undef SPECTO_STYLE_SETTER

void GalleryItem::setCaptionFont(const QFont &f)
{
    if (f == m_captionFont)
        return;
    m_captionFont = f;
    emit styleChanged();
    emit layoutChanged();   // the caption band's height follows the font
    update();
}

void GalleryItem::setRadius(qreal r)
{
    r = std::clamp<qreal>(r, 0.0, 64.0);
    if (qFuzzyCompare(1.0 + r, 1.0 + m_radius))
        return;
    m_radius = r;
    emit styleChanged();
    update();
}

void GalleryItem::setBorderWidth(qreal w)
{
    w = std::clamp<qreal>(w, 0.0, 16.0);
    if (qFuzzyCompare(1.0 + w, 1.0 + m_borderWidth))
        return;
    m_borderWidth = w;
    emit styleChanged();
    update();
}

// ---------------------------------------------------------------------------------
// geometry (content px: y grows down, scroll not applied)
// ---------------------------------------------------------------------------------
qreal GalleryItem::aspectOf(int index) const
{
    if (index >= 0 && index < int(m_tiles.size())) {
        const qreal a = m_tiles[size_t(index)].aspect();
        if (a > 0)
            return a;
    }
    return m_cellAspect;
}

QRectF GalleryItem::contentCellRect(int index) const
{
    const int cols = effectiveColumns();
    const int c = index % cols, r = index / cols;
    return {m_margin + c * (m_cellW + m_gap), m_margin + r * rowPitch(), m_cellW, cellHeight()};
}

QRectF GalleryItem::contentThumbRect(int index) const
{
    const QRectF cell = contentCellRect(index);
    const qreal a = aspectOf(index), ch = cellHeight();
    qreal w, h;
    if (a >= m_cellAspect) {
        w = m_cellW;
        h = m_cellW / a;
    } else {
        h = ch;
        w = ch * a;
    }
    return {cell.x() + (m_cellW - w) * 0.5, cell.y() + (ch - h) * 0.5, w, h};
}

QRectF GalleryItem::rectOf(int index) const
{
    if (index < 0 || index >= m_count)
        return {};
    QRectF r = contentThumbRect(index).translated(0, -m_scrollY);
    if (!r.intersects(QRectF(0, 0, width(), height())))
        return {};
    return r;
}

QRectF GalleryItem::cellRectOf(int index) const
{
    if (index < 0 || index >= m_count)
        return {};
    QRectF r = contentCellRect(index).translated(0, -m_scrollY);
    if (!r.intersects(QRectF(0, 0, width(), height())))
        return {};
    return r;
}

int GalleryItem::indexAt(qreal x, qreal y) const
{
    if (m_count <= 0)
        return -1;
    const qreal cx = x - m_margin, cy = y + m_scrollY - m_margin;
    if (cx < 0 || cy < 0)
        return -1;
    const int cols = effectiveColumns();
    const int c = int(std::floor(cx / (m_cellW + m_gap)));
    if (c >= cols || cx - c * (m_cellW + m_gap) > m_cellW)
        return -1;
    const qreal pitch = rowPitch();
    const int r = int(std::floor(cy / pitch));
    if (cy - r * pitch > cellHeight())
        return -1;
    const int i = r * cols + c;
    return i < m_count ? i : -1;
}

int GalleryItem::centerIndex() const
{
    if (m_count <= 0)
        return -1;
    const int cols = effectiveColumns();
    const int r = std::max(0, int(std::floor((m_scrollY + height() * 0.5 - m_margin) / rowPitch())));
    return std::clamp(r * cols + cols / 2, 0, m_count - 1);
}

int GalleryItem::visibleRows() const
{
    return std::max(1, int(std::floor(height() / rowPitch())));
}

// ---------------------------------------------------------------------------------
// scroll and ranges
// ---------------------------------------------------------------------------------
void GalleryItem::setScroll(qreal y, bool fromUser)
{
    if (!std::isfinite(y))
        return;
    y = std::clamp(y, 0.0, maxScroll());
    if (qFuzzyCompare(1.0 + y, 1.0 + m_scrollY))
        return;
    m_scrollY = y;
    recomputeRanges();
    emit scrollChanged();
    if (fromUser)
        emit userViewChanged();
    update();
}

void GalleryItem::recomputeRanges(bool emitSignals)
{
    int first = -1, last = -1, wantFirst = -1, wantLast = -1, bFirst = -1, bLast = -1;
    int tier = m_wantTier;
    m_laidCols = width() > 0 ? effectiveColumns() : 0;
    if (m_count > 0 && width() > 0 && height() > 0) {
        const int cols = effectiveColumns();
        const int nrows = rows();
        const qreal pitch = rowPitch();
        // a row whose cell ended inside the trailing gap is not visible
        const int rowA = std::clamp(int(std::floor((m_scrollY - m_margin + m_gap) / pitch)), 0, nrows - 1);
        const int rowB = std::clamp(int(std::floor((m_scrollY + height() - m_margin) / pitch)), 0,
                                    nrows - 1);
        first = rowA * cols;
        last = std::min(m_count - 1, (rowB + 1) * cols - 1);
        const int visRows = rowB - rowA + 1;
        const int visCount = last - first + 1;
        const qreal dpr = window() ? window()->effectiveDevicePixelRatio() : 1.0;
        const qreal px = std::max(m_cellW, cellHeight()) * dpr;
        // The tier is the smallest one that still draws crisp at this size, provided its
        // atlas can hold the whole visible set with slack; otherwise step down a tier so
        // the atlas never thrashes.
        if (px > qreal(tierPx(TierFull)) * 1.15 && visCount <= capacityOf(TierLarge) * 9 / 10)
            tier = TierLarge;
        else if (px > qreal(tierPx(TierMini)) * 1.25 && visCount <= capacityOf(TierFull) * 9 / 10)
            tier = TierFull;
        else
            tier = TierMini;
        int margin = std::max(1, visRows / 2);
        if (tier > TierMini) {
            const int spare = capacityOf(tier) * 8 / 10 - visCount;
            margin = std::clamp(spare / std::max(1, cols) / 2, 0, margin);
        }
        wantFirst = std::max(0, (rowA - margin) * cols);
        wantLast = std::min(m_count - 1, (rowB + margin + 1) * cols - 1);
        bFirst = std::max(0, (rowA - 1) * cols);
        bLast = std::min(m_count - 1, (rowB + 2) * cols - 1);
    }
    m_firstVisible = first;
    m_lastVisible = last;
    m_builtFirst = bFirst;
    m_builtLast = bLast;
    const bool changed = wantFirst != m_firstWanted || wantLast != m_lastWanted || tier != m_wantTier;
    m_firstWanted = wantFirst;
    m_lastWanted = wantLast;
    m_wantTier = tier;
    if (emitSignals && changed)
        emit wantedChanged();
}

void GalleryItem::scrollTo(qreal y, bool animate)
{
    if (!std::isfinite(y))
        return;
    y = std::clamp(y, 0.0, maxScroll());
    m_inertia = false;
    if (!animate || !isVisible()) {
        m_scrollAnim = false;
        setScroll(y, false);
        return;
    }
    m_scrollTarget = y;
    m_scrollAnim = true;
    startAnim();
}

void GalleryItem::revealIndex(int index, bool animate)
{
    if (index < 0 || index >= m_count)
        return;
    if (width() <= 0 || height() <= 0) {
        // not laid out yet (parked, or before the slot sized it): reveal on the first layout
        m_pendingReveal = index;
        return;
    }
    m_pendingReveal = -1;
    const QRectF cell = contentCellRect(index);
    const qreal top = cell.top() - m_margin, bottom = cell.bottom() + m_margin;
    if (top >= m_scrollY && bottom <= m_scrollY + height())
        return;   // already fully visible
    const qreal y = top < m_scrollY ? top : bottom - height();
    scrollTo(y, animate);
}

void GalleryItem::zoomTo(int cols, qreal ax, qreal ay, bool animate)
{
    cols = std::clamp(cols, std::max(1, m_minColumns), std::max(1, maxColumns()));
    if (columns() > 0 && cols == effectiveColumns() && !m_zoomAnim)
        return;
    // anchor: the point (ax, ay) stays over the same content
    if (ax < 0 || ay < 0) {
        const QRectF sel = rectOf(m_selected);
        if (!sel.isEmpty()) {
            ax = sel.center().x();
            ay = sel.center().y();
        } else {
            ax = width() * 0.5;
            ay = height() * 0.5;
        }
    }
    const qreal pitch = rowPitch();
    const int oldCols = effectiveColumns();
    // Notches of one gesture keep the item picked by the first one: a reflow moves that
    // item sideways (only its row can stay under the cursor), so re-picking at every
    // notch would drift to its neighbours.
    const qint64 now = m_clock.elapsed();
    const bool sameGesture = m_anchorIndex >= 0 && m_anchorIndex < m_count
                             && now - m_anchorMs < kWheelIdleMs + kZoomAnimMs
                             && std::abs(ay - m_anchorScreenY) < 4.0;
    m_anchorMs = now;
    int a = sameGesture ? m_anchorIndex : indexAt(ax, ay);
    if (a < 0) {
        // between cells: anchor at the row under the cursor, column from x
        const int r = std::max(0, int(std::floor((ay + m_scrollY - m_margin) / pitch)));
        const int c = std::clamp(int(std::floor((ax - m_margin) / (m_cellW + m_gap))), 0, oldCols - 1);
        a = std::clamp(r * oldCols + c, 0, std::max(0, m_count - 1));
    }
    if (sameGesture) {
        // keep the anchor's fraction and screen position from the gesture's first notch
    } else if (m_count > 0) {
        const QRectF cell = contentCellRect(a);
        m_anchorIndex = a;
        m_anchorFy = std::clamp((ay + m_scrollY - cell.top()) / pitch, 0.0, 1.0);
        m_anchorScreenY = ay;
    } else {
        m_anchorIndex = -1;
    }
    const qreal fromW = m_cellW;
    applyColumns(cols);
    m_zoomFromW = fromW;
    m_zoomToW = targetCellWidth(cols);
    emit layoutChanged();
    if (animate && isVisible() && window()) {
        m_zoomAnim = true;
        m_zoomStartMs = m_clock.elapsed();
        beginGesture();
        startAnim();
        animTick();
    } else {
        m_zoomAnim = false;
        m_cellW = m_zoomToW;
        if (m_anchorIndex >= 0) {
            const QRectF cell = contentCellRect(m_anchorIndex);
            m_scrollY = std::clamp(cell.top() + m_anchorFy * rowPitch() - m_anchorScreenY, 0.0,
                                   maxScroll());
        } else {
            m_scrollY = std::clamp(m_scrollY, 0.0, maxScroll());
        }
        emit layoutChanged();
        recomputeRanges();
        emit scrollChanged();
        update();
    }
}

void GalleryItem::zoomBy(int delta, qreal ax, qreal ay)
{
    if (delta == 0)
        return;
    beginGesture();
    const int cols = effectiveColumns() + delta;
    zoomTo(cols, ax, ay, true);
    emit userViewChanged();
}

void GalleryItem::setView(int cols, qreal scrollRowsF, bool fromUser)
{
    if (cols > 0 && cols != columns()) {
        applyColumns(cols);
        m_zoomAnim = false;
        m_cellW = targetCellWidth();
        emit layoutChanged();
    }
    m_scrollAnim = false;
    m_inertia = false;
    const qreal y = std::clamp(scrollRowsF * rowPitch(), 0.0, maxScroll());
    const bool moved = !qFuzzyCompare(1.0 + y, 1.0 + m_scrollY);
    m_scrollY = y;
    recomputeRanges();
    emit scrollChanged();
    if (fromUser && moved)
        emit userViewChanged();
    update();
}

void GalleryItem::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() == oldGeometry.size())
        return;
    // any width change re-flows: same tile size, as many columns as fit
    if (m_pendingCols > 0 && width() - 2 * m_margin + m_gap > 0)
        applyColumns(m_pendingCols);
    relayout();
    if (m_pendingReveal >= 0 && width() > 0 && height() > 0) {
        const int i = m_pendingReveal;
        m_pendingReveal = -1;
        revealIndex(i, false);
    }
}

// ---------------------------------------------------------------------------------
// animation: smooth scroll, inertia, zoom (GUI thread, 16 ms)
// ---------------------------------------------------------------------------------
void GalleryItem::startAnim()
{
    if (!m_anim.isActive()) {
        m_lastTickMs = m_clock.elapsed();
        m_anim.start();
    }
}

void GalleryItem::animTick()
{
    const qint64 now = m_clock.elapsed();
    const qreal dt = std::clamp(qreal(now - m_lastTickMs) / 1000.0, 0.001, 0.05);
    m_lastTickMs = now;
    bool busy = false;
    bool userMoved = false;
    bool zoomEnded = false;

    if (m_zoomAnim) {
        const qreal t = std::clamp(qreal(now - m_zoomStartMs) / kZoomAnimMs, 0.0, 1.0);
        m_cellW = m_zoomFromW + (m_zoomToW - m_zoomFromW) * easeOut(t);
        if (m_anchorIndex >= 0 && m_anchorIndex < m_count) {
            const QRectF cell = contentCellRect(m_anchorIndex);
            m_scrollY = std::clamp(cell.top() + m_anchorFy * rowPitch() - m_anchorScreenY, 0.0,
                                   maxScroll());
        } else {
            m_scrollY = std::clamp(m_scrollY, 0.0, maxScroll());
        }
        if (t >= 1.0) {
            m_zoomAnim = false;
            m_cellW = m_zoomToW;
            zoomEnded = true;
        } else {
            busy = true;
        }
        emit layoutChanged();
        recomputeRanges();
        emit scrollChanged();
        userMoved = true;
        update();
    }
    if (m_inertia) {
        const qreal before = m_scrollY;
        const qreal y = m_scrollY + m_inertiaVel * dt;
        const qreal decel = kInertiaDecel * dt;
        if (std::abs(m_inertiaVel) <= decel)
            m_inertiaVel = 0;
        else
            m_inertiaVel -= std::copysign(decel, m_inertiaVel);
        setScroll(y, false);
        userMoved = true;
        const bool stuck = qFuzzyCompare(1.0 + before, 1.0 + m_scrollY);
        if (std::abs(m_inertiaVel) < kStopVel || stuck)
            m_inertia = false;
        else
            busy = true;
    } else if (m_scrollAnim) {
        const qreal d = m_scrollTarget - m_scrollY;
        if (std::abs(d) < 0.5) {
            m_scrollAnim = false;
            setScroll(m_scrollTarget, false);
        } else {
            const qreal k = 1.0 - std::exp(-dt * 1000.0 / kScrollEaseMs);
            setScroll(m_scrollY + d * k, false);
            busy = true;
        }
        userMoved = m_wheelGesture || m_gestureFromAnim;
    }
    if (userMoved && (m_wheelGesture || m_gestureFromAnim || m_inertia || m_zoomAnim))
        emit userViewChanged();
    if (!busy) {
        m_anim.stop();
        endGesture();
    } else if (zoomEnded) {
        // the wheel-idle timer may have fired while the zoom still ran (a coarse timer
        // fires up to 5 % early on Linux); a scroll ease that keeps the timer busy must
        // not hold the gesture open, so the zoom's end is a gesture end too
        endGesture();
    }
}

void GalleryItem::beginGesture()
{
    if (!m_gestureFromAnim) {
        m_gestureFromAnim = true;
        emit gestureStarted();
    }
}

void GalleryItem::endGesture()
{
    if (!m_gestureFromAnim)
        return;
    if (m_dragging || m_wheelGesture || m_inertia || m_zoomAnim)
        return;
    m_gestureFromAnim = false;
    setScrolling(false);
    emit gestureEnded();
}

void GalleryItem::setScrolling(bool on)
{
    if (on == m_scrolling)
        return;
    m_scrolling = on;
    emit scrollingChanged();
}

// ---------------------------------------------------------------------------------
// selection and hover
// ---------------------------------------------------------------------------------
void GalleryItem::setSelectedIndex(int i)
{
    i = (i < 0 || i >= m_count) ? -1 : i;
    if (i == m_selected)
        return;
    m_selected = i;
    emit selectedIndexChanged();
    update();
}

void GalleryItem::setCurrentIndex(int i)
{
    // marked, never selected or scrolled to: the host reveals it when it follows a time
    i = (i < 0 || i >= m_count) ? -1 : i;
    if (i == m_current)
        return;
    m_current = i;
    emit currentIndexChanged();
    update();
}

void GalleryItem::setHover(int i, const QPointF &pos)
{
    i = (i < 0 || i >= m_count) ? -1 : i;
    m_hoverPos = pos;
    if (i == m_hover)
        return;
    m_hover = i;
    emit hoverIndexChanged();
    update();
}

void GalleryItem::stepSelection(int delta, int modifiers)
{
    if (m_count <= 0)
        return;
    const int from = m_selected < 0 ? centerIndex() : m_selected;
    const int to = std::clamp(from + delta, 0, m_count - 1);
    setSelectedIndex(to);
    revealIndex(to, true);
    emit userSelected(to, modifiers);
}

// ---------------------------------------------------------------------------------
// input
// ---------------------------------------------------------------------------------
void GalleryItem::mousePressEvent(QMouseEvent *event)
{
    if (!m_interactive) {
        event->ignore();
        return;
    }
    forceActiveFocus(Qt::MouseFocusReason);
    m_pressed = true;
    m_pressPos = m_lastPos = event->position();
    m_pressIndex = indexAt(m_pressPos.x(), m_pressPos.y());
    m_pressButton = event->button();
    m_inertia = false;
    m_scrollAnim = false;
    m_velocity.clear();
    m_velocity.push_back({m_clock.elapsed(), m_pressPos.y()});
    // the second press of a double-click on the same tile opens it
    const QStyleHints *hints = QGuiApplication::styleHints();
    const QPointF d = m_pressPos - m_lastClickPos;
    m_pressActivated = m_pressButton == Qt::LeftButton && m_pressIndex >= 0
                       && m_pressIndex == m_lastClickIndex
                       && m_lastClickMs >= 0
                       && qint64(event->timestamp()) - m_lastClickMs
                              <= hints->mouseDoubleClickInterval()
                       && std::abs(d.x()) + std::abs(d.y()) <= hints->mouseDoubleClickDistance();
    m_lastClickMs = -1;
    if (m_pressActivated) {
        setSelectedIndex(m_pressIndex);
        emit activated(m_pressIndex);
    }
    event->accept();
}

void GalleryItem::mouseMoveEvent(QMouseEvent *event)
{
    if (!m_pressed) {
        event->ignore();
        return;
    }
    const QPointF pos = event->position();
    if (!m_dragging && m_tileDrag && m_pressIndex >= 0 && m_pressButton == Qt::LeftButton
        && event->source() == Qt::MouseEventNotSynthesized) {
        // a mouse press on a tile: past the platform's drag distance the tile is dragged
        // out (#185), never scrolled; the press ends here (the drag loop takes the release)
        const qreal far = (pos - m_pressPos).manhattanLength();
        if (far >= QGuiApplication::styleHints()->startDragDistance()) {
            const int index = m_pressIndex;
            m_pressed = false;
            m_pressIndex = -1;
            m_pressActivated = false;
            m_lastClickMs = -1;
            setHover(-1, pos);
            emit dragRequested(index, int(event->modifiers()));
        }
        event->accept();
        return;
    }
    if (!m_dragging) {
        const QPointF d = pos - m_pressPos;
        if (std::abs(d.x()) < kDragThresholdPx && std::abs(d.y()) < kDragThresholdPx) {
            event->accept();
            return;
        }
        m_dragging = true;
        emit draggingChanged();
        beginGesture();
        setScrolling(true);
        setHover(-1, pos);
    }
    const QPointF d = pos - m_lastPos;
    m_lastPos = pos;
    const qint64 now = m_clock.elapsed();
    m_velocity.push_back({now, pos.y()});
    while (m_velocity.size() > 2 && now - m_velocity.front().t > kVelocityWindowMs)
        m_velocity.pop_front();
    setScroll(m_scrollY - d.y(), true);
    event->accept();
}

void GalleryItem::mouseUngrabEvent()
{
    // the grab went elsewhere (another item took it, or the item left the scene while
    // pressed, SPEC-3): no release follows, so end the press and any drag here
    if (!m_pressed)
        return;
    m_pressed = false;
    m_pressIndex = -1;
    if (m_dragging) {
        m_dragging = false;
        emit draggingChanged();
        endGesture();
    }
}

void GalleryItem::mouseReleaseEvent(QMouseEvent *event)
{
    if (!m_pressed) {
        event->ignore();
        return;
    }
    m_pressed = false;
    const int mods = int(event->modifiers());
    if (m_dragging) {
        m_dragging = false;
        emit draggingChanged();
        // fling: velocity over the last samples, content moves against the finger
        qreal vel = 0;
        const qint64 now = m_clock.elapsed();
        if (m_velocity.size() >= 2 && now - m_velocity.back().t < 60) {
            const VelSample &a = m_velocity.front(), &b = m_velocity.back();
            const qreal dt = qreal(b.t - a.t) / 1000.0;
            if (dt > 0.004)
                vel = -(b.y - a.y) / dt;
        }
        if (std::abs(vel) >= kMinFlingVel) {
            m_inertia = true;
            m_inertiaVel = std::clamp(vel, -8000.0, 8000.0);
            startAnim();
        } else {
            endGesture();
        }
    } else if (m_pressActivated) {
        // the release of a double-click: already opened on the press
    } else {
        const int i = indexAt(event->position().x(), event->position().y());
        if (i >= 0 && i == m_pressIndex && m_pressButton == Qt::LeftButton) {
            // event time, not processing time: a busy GUI thread must not split a pair
            m_lastClickMs = qint64(event->timestamp());
            m_lastClickIndex = i;
            m_lastClickPos = event->position();
        }
        if (i >= 0 && i == m_pressIndex) {
            emit clicked(i, int(m_pressButton), mods);
            if (m_pressButton == Qt::LeftButton) {
                setSelectedIndex(i);
                emit userSelected(i, mods);
            }
        }
    }
    m_pressIndex = -1;
    event->accept();
}

void GalleryItem::mouseDoubleClickEvent(QMouseEvent *event)
{
    const int i = indexAt(event->position().x(), event->position().y());
    if (i >= 0 && !m_pressActivated) {
        setSelectedIndex(i);
        emit activated(i);
    }
    event->accept();
}

void GalleryItem::wheelEvent(QWheelEvent *event)
{
    if (!m_interactive || m_count <= 0) {
        event->ignore();
        return;
    }
    const QPoint ad = event->angleDelta();
    const QPoint pd = event->pixelDelta();
    const int dy = ad.y() != 0 ? ad.y() : ad.x();
    const QPointF pos = event->position();
    const bool zoom = event->modifiers() & (Qt::ControlModifier | Qt::MetaModifier);
    if (zoom) {
        // Ctrl+wheel: one column per notch, anchored under the cursor
        if (dy == 0) {
            event->accept();
            return;
        }
        m_wheelZoomAccum += dy;
        int steps = 0;
        while (m_wheelZoomAccum >= 120) {
            m_wheelZoomAccum -= 120;
            --steps;
        }
        while (m_wheelZoomAccum <= -120) {
            m_wheelZoomAccum += 120;
            ++steps;
        }
        if (steps != 0) {
            m_wheelGesture = true;
            m_wheelIdle.start();
            zoomBy(steps, pos.x(), pos.y());
        }
        event->accept();
        return;
    }
    if (dy == 0 && pd.y() == 0) {
        event->accept();
        return;
    }
    beginGesture();
    setScrolling(true);
    m_wheelGesture = true;
    m_wheelIdle.start();
    m_inertia = false;
    if (!pd.isNull() && event->source() != Qt::MouseEventNotSynthesized) {
        // touchpad: pixel precise, already smooth
        m_scrollAnim = false;
        setScroll(m_scrollY - pd.y(), true);
    } else {
        // mouse wheel: a notch scrolls ~60 % of a row (at least 80 px), eased
        const qreal step = std::clamp(rowPitch() * 0.6, 80.0, 400.0);
        const qreal base = m_scrollAnim ? m_scrollTarget : m_scrollY;
        scrollTo(base - qreal(dy) / 120.0 * step, true);
    }
    setHover(indexAt(pos.x(), pos.y()), pos);
    event->accept();
}

bool GalleryItem::event(QEvent *ev)
{
    if (ev->type() == QEvent::NativeGesture && m_interactive) {
        auto *g = static_cast<QNativeGestureEvent *>(ev);
        const QPointF pos = g->position();
        switch (g->gestureType()) {
        case Qt::BeginNativeGesture:
            m_pinchAccum = 1.0;
            break;
        case Qt::ZoomNativeGesture: {
            m_pinchAccum *= (1.0 + g->value());
            if (m_pinchAccum > 1.12) {
                m_pinchAccum = 1.0;
                zoomBy(-1, pos.x(), pos.y());
            } else if (m_pinchAccum < 1.0 / 1.12) {
                m_pinchAccum = 1.0;
                zoomBy(1, pos.x(), pos.y());
            }
            break;
        }
        case Qt::EndNativeGesture:
            endGesture();
            break;
        default:
            break;
        }
        ev->accept();
        return true;
    }
    return QQuickItem::event(ev);
}

void GalleryItem::hoverMoveEvent(QHoverEvent *event)
{
    if (!m_dragging)
        setHover(indexAt(event->position().x(), event->position().y()), event->position());
    event->accept();
}

void GalleryItem::hoverLeaveEvent(QHoverEvent *event)
{
    setHover(-1, m_hoverPos);
    event->accept();
}

void GalleryItem::keyPressEvent(QKeyEvent *event)
{
    if (!m_interactive) {
        event->ignore();
        return;
    }
    const int mods = int(event->modifiers());
    const int cols = effectiveColumns();
    switch (event->key()) {
    case Qt::Key_Left:
        stepSelection(-1, mods);
        break;
    case Qt::Key_Right:
        stepSelection(1, mods);
        break;
    case Qt::Key_Up:
        stepSelection(-cols, mods);
        break;
    case Qt::Key_Down:
        stepSelection(cols, mods);
        break;
    case Qt::Key_Home:
        stepSelection(-m_count, mods);
        break;
    case Qt::Key_End:
        stepSelection(m_count, mods);
        break;
    case Qt::Key_PageUp:
        stepSelection(-cols * visibleRows(), mods);
        break;
    case Qt::Key_PageDown:
        stepSelection(cols * visibleRows(), mods);
        break;
    case Qt::Key_Plus:
    case Qt::Key_Equal:
        zoomBy(-1);
        break;
    case Qt::Key_Minus:
        zoomBy(1);
        break;
    case Qt::Key_Return:
    case Qt::Key_Enter:
        if (m_selected >= 0)
            emit activated(m_selected);
        break;
    case Qt::Key_Space:
        if (m_selected >= 0 || m_hover >= 0)
            emit quickLook(m_selected >= 0 ? m_selected : m_hover);
        break;
    default:
        event->ignore();
        return;
    }
    event->accept();
}

void GalleryItem::focusInEvent(QFocusEvent *event)
{
    QQuickItem::focusInEvent(event);
    m_focused = true;
    update();
}

void GalleryItem::focusOutEvent(QFocusEvent *event)
{
    QQuickItem::focusOutEvent(event);
    m_focused = false;
    update();
}

// ---------------------------------------------------------------------------------
// thumbnails in
// ---------------------------------------------------------------------------------
bool GalleryItem::pushThumbShm(int index, const QString &shmName, int offset, int w, int h)
{
    if (index < 0 || index >= m_count || w <= 0 || h <= 0 || w > kMaxThumbEdge || h > kMaxThumbEdge
        || offset < 0)
        return false;
    StagedThumb st;
    st.index = index;
    st.w = w;
    st.h = h;
    st.rgba.resize(size_t(w) * h * 4);
    QString err;
    if (!specto::readSharedMemory(shmName, size_t(offset), st.rgba.size(), st.rgba.data(), &err)) {
        static int warned = 0;
        if (warned++ < 5)
            qWarning() << "[WARN] GalleryItem: cannot read shared memory" << shmName << err;
        return false;
    }
    {
        QMutexLocker lock(&m_stagedMutex);
        m_staged.push_back(std::move(st));
    }
    m_tiles[size_t(index)].staged = true;
    m_tiles[size_t(index)].failed = false;
    update();
    return true;
}

bool GalleryItem::pushThumb(int index, const QByteArray &rgba, int w, int h)
{
    if (index < 0 || index >= m_count || w <= 0 || h <= 0 || w > kMaxThumbEdge || h > kMaxThumbEdge)
        return false;
    const size_t need = size_t(w) * h * 4;
    if (size_t(rgba.size()) < need)
        return false;
    StagedThumb st;
    st.index = index;
    st.w = w;
    st.h = h;
    st.rgba.resize(need);
    std::memcpy(st.rgba.data(), rgba.constData(), need);
    {
        QMutexLocker lock(&m_stagedMutex);
        m_staged.push_back(std::move(st));
    }
    m_tiles[size_t(index)].staged = true;
    m_tiles[size_t(index)].failed = false;
    update();
    return true;
}

void GalleryItem::setFailed(int index, bool failed)
{
    if (index < 0 || index >= m_count)
        return;
    m_tiles[size_t(index)].failed = failed;
    update();
}

void GalleryItem::clearThumbs()
{
    {
        QMutexLocker lock(&m_stagedMutex);
        m_staged.clear();
    }
    for (TileRec &t : m_tiles)
        t = TileRec{};
    m_aspectSamples = 0;
    m_resetAtlas = true;
    update();
}

QVariantList GalleryItem::missing(int first, int last, int tier) const
{
    QVariantList out;
    if (m_count <= 0)
        return out;
    first = std::max(0, first);
    last = std::min(m_count - 1, last);
    if (last < first)
        return out;
    std::vector<int> idx;
    idx.reserve(size_t(last - first + 1));
    for (int i = first; i <= last; ++i) {
        const TileRec &t = m_tiles[size_t(i)];
        if (t.staged || t.failed)
            continue;
        bool have = false;
        if (tier < 0) {
            have = t.any();
        } else {
            // a bigger tier serves a smaller one
            for (int k = tier; k < TierCount && !have; ++k)
                have = t.has(k);
        }
        if (!have)
            idx.push_back(i);
    }
    const int center = centerIndex();
    std::stable_sort(idx.begin(), idx.end(), [center](int a, int b) {
        return std::abs(a - center) < std::abs(b - center);
    });
    out.reserve(int(idx.size()));
    for (int i : idx)
        out.push_back(i);
    return out;
}

bool GalleryItem::hasTier(int index, int tier) const
{
    if (index < 0 || index >= m_count || tier < 0 || tier >= TierCount)
        return false;
    return m_tiles[size_t(index)].has(tier);
}

void GalleryItem::setCaptions(const QStringList &keys, const QStringList &names)
{
    m_keys = keys;
    m_names = names;
    emit captionsChanged();
    update();
}

QString GalleryItem::captionOf(int index) const
{
    if (index < 0)
        return {};
    QString s = index < m_keys.size() ? m_keys.at(index) : QString();
    if (index < m_names.size() && !m_names.at(index).isEmpty()) {
        if (!s.isEmpty())
            s += QStringLiteral("  ");
        s += m_names.at(index);
    }
    return s;
}

// ---------------------------------------------------------------------------------
// auto aspect: the median aspect of what has been decoded so far
// ---------------------------------------------------------------------------------
void GalleryItem::maybeUpdateAspect()
{
    if (!m_autoAspect || m_tiles.empty())
        return;
    std::vector<float> aspects;
    aspects.reserve(std::min<size_t>(m_tiles.size(), 4096));
    for (const TileRec &t : m_tiles) {
        const qreal a = t.aspect();
        if (a > 0)
            aspects.push_back(float(a));
        if (aspects.size() >= 4096)
            break;
    }
    const int n = int(aspects.size());
    // re-evaluate at 4, 8, 16, ... samples, then stop at 1024: enough for a median
    if (n < 4 || m_aspectSamples >= 1024 || n < 2 * m_aspectSamples)
        return;
    m_aspectSamples = n;
    std::nth_element(aspects.begin(), aspects.begin() + n / 2, aspects.end());
    const qreal median = aspects[size_t(n / 2)];
    if (std::abs(median / m_cellAspect - 1.0) > 0.03)
        setCellAspect(median);
}

// ---------------------------------------------------------------------------------
// render-thread hand-off
// ---------------------------------------------------------------------------------
GalleryItem::Frame GalleryItem::frameParams() const
{
    Frame f;
    f.scrollY = m_scrollY;
    f.dpr = window() ? window()->effectiveDevicePixelRatio() : 1.0;
    f.margin = m_margin;
    f.gap = m_gap;
    f.cellW = m_cellW;
    f.cellH = cellHeight();
    f.captionH = captionHeight();
    f.rowPitch = rowPitch();
    f.cellAspect = m_cellAspect;
    f.cols = effectiveColumns();
    f.count = m_count;
    f.builtFirst = m_builtFirst;
    f.builtLast = m_builtLast;
    f.wantedFirst = m_firstWanted;
    f.wantedLast = m_lastWanted;
    f.useTier = m_wantTier;
    f.captionMode = m_captionMode;
    f.selected = m_selected;
    f.hover = m_hover;
    f.current = m_current;
    f.focused = m_focused;
    f.now = m_clock.elapsed();
    f.frameNo = m_frameNo;
    f.captionFont = m_captionFont;
    f.captionColor = m_captionColor;
    f.captionScrimColor = m_captionScrimColor;
    f.currentSuffix = m_currentSuffix;
    return f;
}

void GalleryItem::reportStats(const int resident[TierCount], int uploads, int evictions,
                              int drawCalls, int quads, int captions, qint64 syncNs)
{
    // render thread, GUI blocked (sync phase)
    for (int t = 0; t < TierCount; ++t)
        m_statResident[t] = resident[t];
    m_statUploads += uploads;
    m_statEvictions += evictions;
    m_statDrawCalls = drawCalls;
    m_statQuads = quads;
    m_statCaptions = captions;
    m_statFrames += 1;
    m_statSyncNsAccum += syncNs;
    m_statSyncSamples += 1;
    {
        QMutexLocker lock(&m_stagedMutex);
        m_statStaged = int(m_staged.size());
    }
    m_lastSyncMs = m_clock.elapsed();
    if (uploads > 0) {
        m_lastUploadMs = m_clock.elapsed();
        m_uploadsSinceAspect += uint32_t(uploads);
    }
    m_statsDirty = true;
    // Never re-arm frames from the render thread: queue the follow-up to the GUI thread.
    QMetaObject::invokeMethod(this, [this] { publishStats(); }, Qt::QueuedConnection);
}

void GalleryItem::publishStats()
{
    if (!m_statsDirty)
        return;
    m_statsDirty = false;
    m_statSyncMs = m_statSyncSamples > 0 ? double(m_statSyncNsAccum) / 1e6 / m_statSyncSamples : 0.0;
    bool more = false;
    {
        QMutexLocker lock(&m_stagedMutex);
        more = !m_staged.empty();
    }
    if (m_uploadsSinceAspect > 0) {
        m_uploadsSinceAspect = 0;
        maybeUpdateAspect();
    }
    // the fade-in of freshly uploaded tiles needs a few more frames: judged by the frame
    // reported, not by now, so a GUI thread that runs this late still asks for the frame
    // that finishes the fade (the tiles' alpha is baked into the vertices at sync)
    if (more || (m_lastUploadMs >= 0 && m_lastSyncMs - m_lastUploadMs < 220))
        update();
    emit statsChanged();
}

void GalleryItem::resetStats()
{
    m_statUploads = 0;
    m_statEvictions = 0;
    m_statFrames = 0;
    m_statSyncNsAccum = 0;
    m_statSyncSamples = 0;
    m_statSyncMs = 0;
    emit statsChanged();
}

QSGNode *GalleryItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    auto *node = static_cast<specto::GalleryNode *>(oldNode);
    if (!node) {
        // a new scene graph: nothing is resident any more
        for (TileRec &t : m_tiles) {
            for (int k = 0; k < TierCount; ++k) {
                t.page[k] = t.cell[k] = -1;
                t.w[k] = t.h[k] = 0;
            }
        }
        node = new specto::GalleryNode(this, window());
    }
    ++m_frameNo;
    node->sync();
    return node;
}

void GalleryItem::releaseResources()
{
    QQuickItem::releaseResources();
}
