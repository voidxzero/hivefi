"""Transport icons drawn in the current skin's text colour.

Fusion's standard media icons are fixed dark-grey pixmaps that all but disappear on dark skins (AMOLED, Midnight...).
These are painted at draw time from the application palette, so they follow every skin change without being rebuilt."""
from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle

S = QStyle.StandardPixmap
KINDS = {S.SP_MediaPlay: "play", S.SP_MediaPause: "pause", S.SP_MediaStop: "stop",
         S.SP_MediaSkipBackward: "prev", S.SP_MediaSkipForward: "next", S.SP_MediaVolume: "volume",
         S.SP_MediaVolumeMuted: "muted"}


def _tri(x0, y0, x1, y1, pointing_right=True) -> QPainterPath:
    p = QPainterPath()
    if pointing_right:
        p.moveTo(x0, y0); p.lineTo(x1, (y0 + y1) / 2); p.lineTo(x0, y1)
    else:
        p.moveTo(x1, y0); p.lineTo(x0, (y0 + y1) / 2); p.lineTo(x1, y1)
    p.closeSubpath()
    return p


def draw(painter: QPainter, rect: QRectF, kind: str, color: QColor):
    """Paint one glyph into rect (designed on a 24x24 grid)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = min(rect.width(), rect.height()) / 24.0
    painter.translate(rect.x() + (rect.width() - 24 * s) / 2, rect.y() + (rect.height() - 24 * s) / 2)
    painter.scale(s, s)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color)
    path = QPainterPath()
    if kind == "play":
        path = _tri(7, 4.5, 19.5, 19.5)
    elif kind == "pause":
        path.addRoundedRect(QRectF(6, 5, 4.2, 14), 1, 1); path.addRoundedRect(QRectF(13.8, 5, 4.2, 14), 1, 1)
    elif kind == "stop":
        path.addRoundedRect(QRectF(6, 6, 12, 12), 1.5, 1.5)
    elif kind in ("prev", "next"):
        right = kind == "next"
        if right:
            path.addPath(_tri(4.5, 6, 12.5, 18)); path.addPath(_tri(11.5, 6, 19.5, 18))
        else:
            path.addPath(_tri(4.5, 6, 12.5, 18, False)); path.addPath(_tri(11.5, 6, 19.5, 18, False))
        bar_x = 19.5 if right else 2.7
        path.addRoundedRect(QRectF(bar_x, 6, 1.8, 12), 0.6, 0.6)
    elif kind in ("volume", "muted"):
        path.moveTo(3.5, 9); path.lineTo(7.5, 9); path.lineTo(12.5, 4.5); path.lineTo(12.5, 19.5)
        path.lineTo(7.5, 15); path.lineTo(3.5, 15); path.closeSubpath()
    painter.drawPath(path)
    pen = QPen(color, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "volume":
        painter.drawArc(QRectF(10, 8, 6, 8), -60 * 16, 120 * 16)
        painter.drawArc(QRectF(9, 4.5, 11, 15), -60 * 16, 120 * 16)
    elif kind == "muted":
        painter.drawLine(QPointF(15.5, 9), QPointF(21, 15)); painter.drawLine(QPointF(21, 9), QPointF(15.5, 15))
    painter.restore()


class _Engine(QIconEngine):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind

    def paint(self, painter, rect, mode, state):
        pal = QApplication.palette()
        group = QPalette.ColorGroup.Disabled if mode == QIcon.Mode.Disabled else QPalette.ColorGroup.Active
        role = QPalette.ColorRole.HighlightedText if mode == QIcon.Mode.Selected else QPalette.ColorRole.ButtonText
        draw(painter, QRectF(rect), self.kind, pal.color(group, role))

    def pixmap(self, size, mode, state):
        pm = QPixmap(size); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state); p.end()
        return pm

    def clone(self):
        return _Engine(self.kind)


def media_icon(sp) -> QIcon | None:
    """A skin-coloured icon for a QStyle media StandardPixmap, or None for anything else."""
    kind = KINDS.get(sp)
    return QIcon(_Engine(kind)) if kind else None


def icon(widget, sp) -> QIcon:
    return media_icon(sp) or widget.style().standardIcon(sp)

