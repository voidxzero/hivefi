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
    elif kind == "shuffle":
        a = QPainterPath(); a.moveTo(3, 7); a.lineTo(7, 7); a.cubicTo(12, 7, 12, 17, 17, 17); a.lineTo(18.5, 17)
        b = QPainterPath(); b.moveTo(3, 17); b.lineTo(7, 17); b.cubicTo(12, 17, 12, 7, 17, 7); b.lineTo(18.5, 7)
        painter.drawPath(a); painter.drawPath(b)
        _heads(painter, color, ((21.5, 7, True), (21.5, 17, True)))
    elif kind in ("repeat", "repeat_one"):
        top = QPainterPath(); top.moveTo(4, 12.5); top.lineTo(4, 9.5); top.quadTo(4, 7, 6.5, 7); top.lineTo(17, 7)
        bot = QPainterPath(); bot.moveTo(20, 11.5); bot.lineTo(20, 14.5); bot.quadTo(20, 17, 17.5, 17); bot.lineTo(7, 17)
        painter.drawPath(top); painter.drawPath(bot)
        _heads(painter, color, ((20.5, 7, True), (3.5, 17, False)))
        if kind == "repeat_one":
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color)
            one = QPainterPath()
            one.moveTo(10.6, 10.4); one.lineTo(12.4, 9.2); one.lineTo(13.4, 9.2); one.lineTo(13.4, 14.8)
            one.lineTo(11.9, 14.8); one.lineTo(11.9, 11.3); one.lineTo(10.6, 12.1); one.closeSubpath()
            painter.drawPath(one)
    painter.restore()


def _heads(painter: QPainter, color: QColor, heads):
    """Filled arrowheads; (tip x, tip y, pointing right)."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color)
    for x, y, right in heads:
        d = -4.2 if right else 4.2
        tri = QPainterPath(); tri.moveTo(x, y); tri.lineTo(x + d, y - 3.2); tri.lineTo(x + d, y + 3.2); tri.closeSubpath()
        painter.drawPath(tri)
    painter.restore()


class _Engine(QIconEngine):
    def __init__(self, kind: str, accent_when_on: bool = False):
        super().__init__()
        self.kind, self.accent_when_on = kind, accent_when_on

    def paint(self, painter, rect, mode, state):
        pal = QApplication.palette()
        group = QPalette.ColorGroup.Disabled if mode == QIcon.Mode.Disabled else QPalette.ColorGroup.Active
        role = QPalette.ColorRole.HighlightedText if mode == QIcon.Mode.Selected else QPalette.ColorRole.ButtonText
        if self.accent_when_on and state == QIcon.State.On and mode != QIcon.Mode.Disabled:
            role = QPalette.ColorRole.Link                     # the skin's accent colour
        draw(painter, QRectF(rect), self.kind, pal.color(group, role))

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        """Drawn at the screen's pixel density, so the glyphs stay sharp on HiDPI displays."""
        scale = max(1.0, scale)
        pm = QPixmap(round(size.width() * scale), round(size.height() * scale)); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); self.paint(p, QRect(0, 0, pm.width(), pm.height()), mode, state); p.end()
        pm.setDevicePixelRatio(scale)
        return pm

    def clone(self):
        return _Engine(self.kind, self.accent_when_on)


def media_icon(sp) -> QIcon | None:
    """A skin-coloured icon for a QStyle media StandardPixmap, or None for anything else."""
    kind = KINDS.get(sp)
    return QIcon(_Engine(kind)) if kind else None


def toggle_icon(kind: str) -> QIcon:
    """shuffle / repeat / repeat_one: text colour when off, the skin's accent when the (checkable) button is on."""
    return QIcon(_Engine(kind, accent_when_on=True))


def icon(widget, sp) -> QIcon:
    return media_icon(sp) or widget.style().standardIcon(sp)

