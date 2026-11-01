// RoundedClip: an Item whose children are clipped to a rounded rectangle (the view
// frame's shape) on the scene graph itself. `Item.clip` always clips to a rectangle; the
// clip node Qt gives an item (QQuickDefaultClipNode) can carry a radius, and this item
// sets it. The clip is a stencil clip, so it holds for everything in the subtree: plain
// nodes (the gallery), texture nodes (the video, web views), layered children; it costs
// no extra texture per view. The software renderer cannot clip to a curve and falls
// back to the rectangle (tests see square corners there). Edges are not antialiased:
// the frame stroke drawn on top covers them.
#pragma once

#include <QtQuick/QQuickItem>

#include "mpvitem.h"  // SPECTONATIVE_EXPORT

class SPECTONATIVE_EXPORT RoundedClip : public QQuickItem
{
    Q_OBJECT
    Q_PROPERTY(qreal radius READ radius WRITE setRadius NOTIFY radiusChanged)
    QML_ELEMENT

public:
    explicit RoundedClip(QQuickItem *parent = nullptr);

    qreal radius() const { return m_radius; }
    void setRadius(qreal radius);

signals:
    void radiusChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private:
    qreal m_radius = 0;
};
