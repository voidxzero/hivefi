"""Seek bar with selectable looks: classic slider, progress bar, waveform, waveform outline."""
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget

STYLES = {"slider": "Slider", "bar": "Progress bar", "waveform": "Waveform", "wave_line": "Waveform outline"}
WAVE_STYLES = ("waveform", "wave_line")


def fmt(ms):
    s = int(ms // 1000)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class SeekBar(QWidget):
    sliderMoved = pyqtSignal(int)            # ms, while dragging (throttled) and on release/click

    def __init__(self, style="slider"):
        super().__init__()
        self._style = style if style in STYLES else "slider"
        self._min, self._max, self._val = 0, 0, 0
        self._peaks: list[int] | None = None
        self._busy = False                    # waveform analysis running
        self._down = False
        self._last_emit = 0.0
        self._cache: tuple | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fit_height()

    # ---- QSlider-like API used by the main window
    def setRange(self, lo, hi):
        self._min, self._max = lo, max(lo, hi); self._val = min(self._val, self._max); self.update()

    def setValue(self, v):
        v = max(self._min, min(self._max, int(v)))
        if v != self._val:
            self._val = v; self.update()

    def value(self): return self._val
    def isSliderDown(self): return self._down

    # ---- look
    def style_name(self): return self._style

    def set_style(self, name):
        if name in STYLES and name != self._style:
            self._style = name; self._cache = None; self._fit_height(); self.update()

    def wants_waveform(self): return self._style in WAVE_STYLES

    def set_peaks(self, peaks, busy=False):
        self._peaks, self._busy, self._cache = peaks or None, busy, None
        self.update()

    def _fit_height(self):
        h = {"slider": 24, "bar": 18, "waveform": 54, "wave_line": 54}[self._style]
        self.setFixedHeight(h)
        self.updateGeometry()

    def sizeHint(self): return QSize(320, self.height())
    def minimumSizeHint(self): return QSize(120, self.height())

    # ---- geometry helpers
    def _frac(self):
        return (self._val - self._min) / (self._max - self._min) if self._max > self._min else 0.0

    def _ms_at(self, x):
        pad = 8 if self._style == "slider" else 0
        w = max(1, self.width() - 2 * pad)
        return int(self._min + (self._max - self._min) * max(0.0, min(1.0, (x - pad) / w)))

    # ---- painting
    def paintEvent(self, _e):
        p = QPainter(self)
        pal = self.palette()
        accent, dim = pal.highlight().color(), pal.mid().color()
        w, h = self.width(), self.height()
        f = self._frac()
        if self._style == "slider":
            self._paint_slider(p, w, h, f, accent, dim, pal.button().color(), pal.text().color())
        elif self._style == "bar":
            r = QRectF(0, 3, w, h - 6)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(dim); p.drawRoundedRect(r, 4, 4)
            p.setBrush(accent); p.drawRoundedRect(QRectF(0, 3, w * f, h - 6), 4, 4)
        else:
            self._paint_wave(p, w, h, f, accent, dim)
        p.end()

    def _paint_slider(self, p, w, h, f, accent, dim, knob, outline):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pad = 8
        track = QRectF(pad, h / 2 - 3, w - 2 * pad, 6)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(dim); p.drawRoundedRect(track, 3, 3)
        x = pad + (w - 2 * pad) * f
        p.setBrush(accent); p.drawRoundedRect(QRectF(pad, h / 2 - 3, x - pad, 6), 3, 3)
        p.setBrush(knob); p.setPen(QPen(outline, 1))
        p.drawEllipse(QPointF(x, h / 2), 7, 7)

    def _wave_pixmaps(self, w, h, accent, dim):
        key = (w, h, self._style, id(self._peaks), accent.rgb(), dim.rgb())
        if self._cache and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        pm = []
        for col in (dim, accent):
            img = QPixmap(w, h); img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img); q.setRenderHint(QPainter.RenderHint.Antialiasing)
            peaks = self._peaks
            mid, amp = h / 2, h / 2 - 2
            if peaks:
                n = len(peaks)
                col_peaks = [max(peaks[int(x * n / w):max(int(x * n / w) + 1, int((x + 1) * n / w))]) / 255
                             for x in range(w)]
                if self._style == "waveform":
                    q.setPen(QPen(col, 1))
                    for x, v in enumerate(col_peaks):
                        a = max(1.0, v * amp)
                        q.drawLine(QPointF(x + .5, mid - a), QPointF(x + .5, mid + a))
                else:
                    path = QPainterPath(QPointF(0, mid))
                    for x, v in enumerate(col_peaks):
                        path.lineTo(x + .5, mid - max(0.5, v * amp))
                    for x in range(w - 1, -1, -1):
                        path.lineTo(x + .5, mid + max(0.5, col_peaks[x] * amp))
                    path.closeSubpath()
                    fill = QColor(col); fill.setAlpha(110)
                    q.setPen(QPen(col, 1.2)); q.setBrush(fill); q.drawPath(path)
            else:                                    # not analysed (yet): a flat line
                q.setPen(QPen(col, 2)); q.drawLine(QPointF(0, mid), QPointF(w, mid))
            q.end(); pm.append(img)
        self._cache = (key, pm[0], pm[1])
        return pm[0], pm[1]

    def _paint_wave(self, p, w, h, f, accent, dim):
        dim_pm, acc_pm = self._wave_pixmaps(w, h, accent, dim)
        p.drawPixmap(0, 0, dim_pm)
        x = int(w * f)
        p.setClipRect(0, 0, x, h); p.drawPixmap(0, 0, acc_pm); p.setClipping(False)
        p.setPen(QPen(accent.lighter(130), 2)); p.drawLine(x, 0, x, h)
        if self._busy and not self._peaks:
            p.setPen(self.palette().text().color()); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                                                              "analysing waveform…")

    # ---- mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._max > self._min:
            self._down = True
            self._seek_to(e.position().x(), force=True)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._down:
            self._seek_to(x)
        elif self._max > self._min:
            QToolTip.showText(e.globalPosition().toPoint(), fmt(self._ms_at(x)), self)

    def mouseReleaseEvent(self, e):
        if self._down:
            self._seek_to(e.position().x(), force=True)
            self._down = False

    def _seek_to(self, x, force=False):
        ms = self._ms_at(x)
        self._val = ms; self.update()
        now = time.monotonic()
        if force or now - self._last_emit > 0.08:           # dragging must not flood the engine with seeks
            self._last_emit = now
            self.sliderMoved.emit(ms)
