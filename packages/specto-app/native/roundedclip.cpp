#include "roundedclip.h"

#include <QtQuick/private/qquickclipnode_p.h>
#include <QtQuick/private/qquickitem_p.h>

RoundedClip::RoundedClip(QQuickItem *parent)
    : QQuickItem(parent)
{
    // the item's own clip node is what carries the radius; ItemHasContents gets
    // updatePaintNode called (on the render thread, GUI blocked) where the node exists
    setClip(true);
    setFlag(ItemHasContents, true);
}

void RoundedClip::setRadius(qreal radius)
{
    radius = qMax<qreal>(0, radius);
    if (qFuzzyCompare(m_radius, radius))
        return;
    m_radius = radius;
    emit radiusChanged();
    update();
}

void RoundedClip::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    update();  // the window resets the clip rect; the radius must be reapplied
}

QSGNode *RoundedClip::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    // The window handles the Clip dirty bit before the content update, so the clip
    // node is there with the current rect; the radius persists in the node and only the
    // geometry needs regenerating when it changes.
    QQuickDefaultClipNode *clip = QQuickItemPrivate::get(this)->clipNode();
    if (clip) {
        const qreal r = qMin(m_radius, qMin(width(), height()) / 2);
        if (!qFuzzyCompare(clip->radius(), r)) {
            clip->setRadius(r);
            clip->update();
        }
    }
    return oldNode;  // no content of its own
}
