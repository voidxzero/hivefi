"""Audio visualizer panel: spectrum bars, mirrored bars, spectrum line, radial, spectrogram and VU meters.

Fed ~30x per second with the GStreamer `spectrum` (dB per bin) and `level` (dB per channel) analysers."""
import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QMenu, QSizePolicy, QWidget

STYLES = {"bars": "Spectrum bars", "mirror": "Mirrored bars", "line": "Spectrum line", "radial": "Radial",
          "spectrogram": "Spectrogram", "vu": "VU meters"}
FLOOR_DB = -75.0
F_LO, F_HI = 30.0, 18000.0


def band_edges(n_bars, n_bins, rate=44100):
    """[(first_bin, last_bin_exclusive)] per bar on a log frequency axis (every bar gets at least one bin)."""
    nyq = rate / 2
    out = []
    for i in range(n_bars):
        f0 = F_LO * (F_HI / F_LO) ** (i / n_bars)
        f1 = F_LO * (F_HI / F_LO) ** ((i + 1) / n_bars)
        a = min(n_bins - 1, int(f0 / nyq * n_bins))
        b = max(a + 1, min(n_bins, int(round(f1 / nyq * n_bins))))
        out.append((a, b))
    return out


def to_bars(mags, n_bars, rate=44100):
    """Reduce a linear-frequency dB spectrum to n_bars log-spaced values in 0..1 (peak within each bar)."""
    if not mags:
        return [0.0] * n_bars
    edges = band_edges(n_bars, len(mags), rate)
    return [max(0.0, min(1.0, (max(mags[a:b]) - FLOOR_DB) / -FLOOR_DB)) for a, b in edges]


class VisualizerPanel(QWidget):
    styleChanged = pyqtSignal(str)
    optionsChanged = pyqtSignal()
    activeChanged = pyqtSignal(bool)           # needs data now (visible)?

    def __init__(self, style="bars", bars=48, peaks=True):
        super().__init__()
        self.style_name, self.n_bars, self.peaks_on = style if style in STYLES else "bars", bars, peaks
        self.rate = 44100
        self.available = True
        self.note = ""
        self._target = [0.0] * 64
        self._bars = [0.0] * bars
        self._caps = [0.0] * bars
        self._cap_hold = [0.0] * bars
        self._levels = {"rms": [FLOOR_DB, FLOOR_DB], "peak": [FLOOR_DB, FLOOR_DB]}
        self._vu_hold = [FLOOR_DB, FLOOR_DB]
        self._spectro: QImage | None = None
        self._last_data = 0.0
        self._hue = 0.0
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._timer = QTimer(self, interval=33, timeout=self._tick)

    # ---- data in
    def feed(self, mags, levels):
        self._last_data = time.monotonic()
        if mags:
            self._target = to_bars(mags, 64 if self.style_name == "radial" else self.n_bars, self.rate)
            self._raw = mags
        if levels and levels.get("rms"):
            self._levels = levels

    def set_style(self, name):
        if name in STYLES:
            self.style_name = name; self._spectro = None; self._reset_bars(); self.update()

    def set_bars(self, n):
        self.n_bars = n; self._reset_bars(); self.update()

    def _reset_bars(self):
        n = 64 if self.style_name == "radial" else self.n_bars
        self._bars, self._caps, self._cap_hold = [0.0] * n, [0.0] * n, [0.0] * n
        self._target = [0.0] * n

    # ---- animation
    def showEvent(self, e):
        super().showEvent(e); self._timer.start(); self.activeChanged.emit(True)

    def hideEvent(self, e):
        super().hideEvent(e); self._timer.stop(); self.activeChanged.emit(False)

    def _tick(self):
        silent = time.monotonic() - self._last_data > 0.4
        if silent:
            self._target = [0.0] * len(self._target)
            self._levels = {"rms": [FLOOR_DB] * 2, "peak": [FLOOR_DB] * 2}
        n = len(self._bars)
        if len(self._target) != n:
            self._reset_bars(); n = len(self._bars)
        for i in range(n):
            t, b = self._target[i], self._bars[i]
            self._bars[i] = b + (t - b) * (0.6 if t > b else 0.18)            # fast attack, slow release
            if self._bars[i] >= self._caps[i]:
                self._caps[i], self._cap_hold[i] = self._bars[i], 14          # hold a peak cap ~0.5 s, then let it fall
            elif self._cap_hold[i] > 0:
                self._cap_hold[i] -= 1
            else:
                self._caps[i] = max(self._bars[i], self._caps[i] - 0.02)
        for c in range(2):
            pk = self._levels["peak"][min(c, len(self._levels["peak"]) - 1)]
            self._vu_hold[c] = pk if pk >= self._vu_hold[c] else max(pk, self._vu_hold[c] - 0.8)
        bass = sum(self._bars[: max(1, n // 8)]) / max(1, n // 8)
        self._hue = (self._hue + 0.002 + bass * 0.01) % 1.0
        if self.style_name == "spectrogram":
            self._push_spectro()
        if self.isVisible():
            self.update()

    # ---- colours
    def _colors(self):
        pal = self.palette()
        return pal.highlight().color(), pal.window().color(), pal.text().color()

    def _grad(self, accent, top, bottom):
        g = QLinearGradient(top, bottom)
        low = QColor(accent); low.setHsv(low.hue(), min(255, low.saturation() + 30), max(90, low.value() - 60))
        hot = QColor.fromHsv((accent.hue() + 40) % 360, 220, 255)
        g.setColorAt(0.0, hot); g.setColorAt(0.5, accent); g.setColorAt(1.0, low)
        return g

    # ---- painting
    def paintEvent(self, _e):
        p = QPainter(self)
        accent, bg, fg = self._colors()
        p.fillRect(self.rect(), bg.darker(115))
        if not self.available:
            p.setPen(fg); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                     self.note or "Visualizer needs the GStreamer engine.")
            p.end(); return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        getattr(self, "_paint_" + self.style_name)(p, self.width(), self.height(), accent, fg)
        p.end()

    def _paint_bars(self, p, w, h, accent, fg, mirror=False):
        n = len(self._bars)
        gap = 2 if w / n > 5 else 1
        bw = max(1.0, w / n - gap)
        mid = h / 2
        span = (h / 2 - 2) if mirror else (h - 4)
        for i, v in enumerate(self._bars):
            x = i * w / n + gap / 2
            bh = max(1.0, v * span)
            if mirror:
                p.setBrush(self._grad(accent, QPointF(0, mid - span), QPointF(0, mid)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRectF(x, mid - bh, bw, bh * 2))
            else:
                p.setBrush(self._grad(accent, QPointF(0, h - span), QPointF(0, h)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRectF(x, h - 2 - bh, bw, bh))
            if self.peaks_on and self._caps[i] > 0.02:
                c = self._caps[i] * span
                p.setBrush(fg)
                y = (mid - c - 2) if mirror else (h - 2 - c - 2)
                p.drawRect(QRectF(x, y, bw, 2))
                if mirror:
                    p.drawRect(QRectF(x, mid + c, bw, 2))

    def _paint_mirror(self, p, w, h, accent, fg):
        self._paint_bars(p, w, h, accent, fg, mirror=True)

    def _paint_line(self, p, w, h, accent, fg):
        n = len(self._bars)
        pts = [QPointF(i * w / (n - 1), h - 3 - v * (h - 8)) for i, v in enumerate(self._bars)]
        path = QPainterPath(QPointF(0, h)); path.lineTo(pts[0])
        for a, b in zip(pts, pts[1:]):                          # smooth curve through the points
            mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
            path.quadTo(a, mid)
        path.lineTo(pts[-1]); line = QPainterPath(path)
        path.lineTo(w, h); path.closeSubpath()
        fill = self._grad(accent, QPointF(0, 0), QPointF(0, h))
        p.setOpacity(0.55); p.setBrush(fill); p.setPen(Qt.PenStyle.NoPen); p.drawPath(path); p.setOpacity(1)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(accent.lighter(150), 2)); p.drawPath(line)

    def _paint_radial(self, p, w, h, accent, fg):
        n = len(self._bars)
        cx, cy = w / 2, h / 2
        r0 = min(w, h) * 0.2
        rmax = min(w, h) * 0.46
        p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 40), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r0, r0)
        for i, v in enumerate(self._bars):
            a = 2 * math.pi * i / n - math.pi / 2
            r1 = r0 + 2 + v * (rmax - r0)
            col = QColor.fromHsvF((self._hue + i / n * 0.6) % 1.0, 0.65, 1.0)
            p.setPen(QPen(col, max(2.0, 2 * math.pi * r0 / n * 0.7), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                       QPointF(cx + math.cos(a) * r1, cy + math.sin(a) * r1))

    def _push_spectro(self):
        w, h = max(2, self.width()), max(2, self.height())
        if self._spectro is None or self._spectro.height() != h or self._spectro.width() != w:
            self._spectro = QImage(w, h, QImage.Format.Format_RGB32); self._spectro.fill(Qt.GlobalColor.black)
        step = 2
        old = self._spectro.copy()
        img = self._spectro
        q = QPainter(img)
        q.drawImage(-step, 0, old)                               # scroll left, then draw the newest column
        n = len(self._bars)
        for y in range(h):
            v = self._bars[min(n - 1, int((h - 1 - y) * n / h))]
            c = QColor.fromHsvF(max(0.0, 0.7 - v * 0.7), 1.0, min(1.0, 0.15 + v * 1.1)) if v > 0.02 else QColor(0, 0, 0)
            q.fillRect(w - step, y, step, 1, c)
        q.end()

    def _paint_spectrogram(self, p, w, h, accent, fg):
        if self._spectro is not None:
            p.drawImage(0, 0, self._spectro)

    def _paint_vu(self, p, w, h, accent, fg):
        rows = 2
        top, rh = 14, (h - 28) / rows
        segs = 40
        for c in range(rows):
            y = top + c * rh
            rms = self._levels["rms"][min(c, len(self._levels["rms"]) - 1)]
            frac = max(0.0, min(1.0, (rms - FLOOR_DB) / -FLOOR_DB))
            hold = max(0.0, min(1.0, (self._vu_hold[c] - FLOOR_DB) / -FLOOR_DB))
            sw = (w - 40) / segs
            for s in range(segs):
                x = 28 + s * sw
                pos = s / segs
                col = QColor("#3fbf5f") if pos < 0.7 else QColor("#e6c229") if pos < 0.88 else QColor("#e5483f")
                if pos > frac:
                    col.setAlpha(45)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
                p.drawRect(QRectF(x, y + 4, max(1.0, sw - 2), rh - 12))
            hx = 28 + hold * (w - 40)
            p.setBrush(fg); p.drawRect(QRectF(hx - 1, y + 2, 2, rh - 8))
            p.setPen(fg); p.drawText(QRectF(4, y, 22, rh), Qt.AlignmentFlag.AlignVCenter, "LR"[c])
        p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 160))
        for db in (-60, -40, -20, -10, -3, 0):
            x = 28 + (db - FLOOR_DB) / -FLOOR_DB * (w - 40)
            p.drawText(QRectF(x - 14, h - 14, 28, 12), Qt.AlignmentFlag.AlignCenter, f"{db}")

    # ---- menu
    def _menu(self, pos):
        m = QMenu(self)
        for key, label in STYLES.items():
            a = m.addAction(label); a.setCheckable(True); a.setChecked(key == self.style_name)
            a.triggered.connect(lambda _c, k=key: self.styleChanged.emit(k))
        m.addSeparator()
        bm = m.addMenu("Bars")
        for n in (24, 32, 48, 64, 96):
            a = bm.addAction(str(n)); a.setCheckable(True); a.setChecked(n == self.n_bars)
            a.triggered.connect(lambda _c, n=n: (self.set_bars(n), self.optionsChanged.emit()))
        pk = m.addAction("Peak caps"); pk.setCheckable(True); pk.setChecked(self.peaks_on)
        pk.toggled.connect(lambda on: (setattr(self, "peaks_on", on), self.optionsChanged.emit()))
        m.exec(self.mapToGlobal(pos))
