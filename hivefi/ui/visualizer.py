"""Audio visualizer panel: spectrum bars, mirrored bars, spectrum line, radial, spectrogram and VU meters.

Fed ~30x per second with the GStreamer `spectrum` (dB per bin) and `level` (dB per channel) analysers."""
import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QColorDialog, QMenu, QSizePolicy, QWidget

SCHEMES = {            # name: (label, low, mid, hot, peak cap) - "skin" and "rainbow" are computed
    "skin": ("Skin accent", None, None, None, None),
    "fire": ("Fire", "#5a0a00", "#ff6a00", "#ffe14a", "#fff4c2"),
    "ocean": ("Ocean", "#032b5a", "#00a3ff", "#7ff3ff", "#e6fbff"),
    "ice": ("Ice", "#1b2f4a", "#7ab8ff", "#ffffff", "#ffffff"),
    "neon": ("Neon", "#ff00aa", "#00f0ff", "#b6ff00", "#ffffff"),
    "sunset": ("Sunset", "#4a1060", "#ff3d7f", "#ffd166", "#fff1d6"),
    "forest": ("Forest", "#0b3d1e", "#2fbf5f", "#d6ff7a", "#f2ffd6"),
    "mono": ("Monochrome", "#3a3a3a", "#a8a8a8", "#ffffff", "#ffffff"),
    "rainbow": ("Rainbow", None, None, None, "#ffffff"),
    "custom": ("Custom colours…", None, None, None, None),
}
DEFAULT_CUSTOM = ["#7a1fd8", "#ff4fb8", "#ffe14a"]
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
    colorsChanged = pyqtSignal(str, list)      # scheme name, custom colours (low, mid, peak)
    fullscreenRequested = pyqtSignal()
    activeChanged = pyqtSignal(bool)           # needs data now (visible)?

    def __init__(self, style="bars", bars=48, peaks=True, scheme="skin", custom=None):
        super().__init__()
        self.scheme = scheme if scheme in SCHEMES else "skin"
        self.custom = list(custom) if custom and len(custom) == 3 else list(DEFAULT_CUSTOM)
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
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._paused = False
        self._theme_key = self._theme_val = None
        self._spectro_x = 0                         # ring buffer: the column the next slice goes into

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
        super().showEvent(e)
        if not self._paused:
            self._timer.start()
        self.activeChanged.emit(True)

    def set_paused(self, on):
        """Stop animating while the full-screen visualizer covers the window (being covered does not hide a widget,
        so it would otherwise keep drawing every frame behind it and slow the full-screen one down)."""
        self._paused = bool(on)
        if on:
            self._timer.stop()
        elif self.isVisible():
            self._timer.start()

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
    def set_colors(self, scheme, custom=None):
        self.scheme = scheme if scheme in SCHEMES else "skin"
        if custom and len(custom) == 3:
            self.custom = list(custom)
        self._spectro = None
        self.update()

    def _theme(self):
        """(low, mid, hot, cap) colours of the chosen scheme; None entries mean 'rainbow'. Worked out once per
        scheme / skin, not for every bar of every frame."""
        accent, _bg, fg = self._colors()
        key = (self.scheme, tuple(self.custom), accent.rgba(), fg.rgba())
        if key != self._theme_key:
            self._theme_key, self._theme_val = key, self._make_theme(accent, fg)
            self._ramp_lut = None
        return self._theme_val

    def _make_theme(self, accent, fg):
        if self.scheme == "skin":
            low = QColor(accent); low.setHsv(low.hue(), min(255, low.saturation() + 30), max(90, low.value() - 60))
            return low, accent, QColor.fromHsv((accent.hue() + 40) % 360, 220, 255), fg
        if self.scheme == "custom":
            lo, mid, hot = (QColor(c) for c in self.custom)
            return lo, mid, hot, QColor(hot).lighter(150)
        if self.scheme == "rainbow":
            return None, None, None, QColor(SCHEMES["rainbow"][4])
        _l, lo, mid, hot, cap = SCHEMES[self.scheme]
        return QColor(lo), QColor(mid), QColor(hot), QColor(cap)

    def _ramp(self, v):
        """Colour for an intensity 0..1 along low -> mid -> hot (rainbow: a hue sweep)."""
        low, mid, hot, _cap = self._theme()
        return self._ramp_of(v, low, mid, hot)

    def _spectro_color(self, v):
        """Spectrogram colour for 0..1, from a 256-step table built once per scheme."""
        self._theme()
        if self._ramp_lut is None:
            lut = []
            for i in range(256):
                x = i / 255
                if x <= 0.02:
                    lut.append(QColor(0, 0, 0)); continue
                c = self._ramp(x)
                if self.scheme != "rainbow":
                    k = min(1.0, 0.25 + x)
                    c = QColor(round(c.red() * k), round(c.green() * k), round(c.blue() * k))
                lut.append(c)
            self._ramp_lut = lut
        return self._ramp_lut[max(0, min(255, int(v * 255)))]

    @staticmethod
    def _ramp_of(v, low, mid, hot):
        if low is None:
            return QColor.fromHsvF(max(0.0, 0.75 - v * 0.75), 0.9, 1.0)
        if v < 0.5:
            a, b, t = low, mid, v / 0.5
        else:
            a, b, t = mid, hot, (v - 0.5) / 0.5
        return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                      round(a.blue() + (b.blue() - a.blue()) * t))

    def _bar_brush(self, i, n, top, bottom):
        """Vertical gradient for one bar (rainbow: each bar has its own hue)."""
        low, mid, hot, _cap = self._theme()
        g = QLinearGradient(top, bottom)
        if low is None:
            c = QColor.fromHsvF(i / max(1, n) * 0.85, 0.85, 1.0)
            g.setColorAt(0.0, c); g.setColorAt(1.0, c.darker(210))
        else:
            g.setColorAt(0.0, hot); g.setColorAt(0.5, mid); g.setColorAt(1.0, low)
        return g

    def _colors(self):
        pal = self.palette()
        return pal.highlight().color(), pal.window().color(), pal.text().color()

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
                p.setBrush(self._bar_brush(i, n, QPointF(0, mid - span), QPointF(0, mid)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRectF(x, mid - bh, bw, bh * 2))
            else:
                p.setBrush(self._bar_brush(i, n, QPointF(0, h - span), QPointF(0, h)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRectF(x, h - 2 - bh, bw, bh))
            if self.peaks_on and self._caps[i] > 0.02:
                c = self._caps[i] * span
                p.setBrush(self._theme()[3])
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
        low, mid, hot, _cap = self._theme()
        fill = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        if low is None:                                          # rainbow: hue sweeps along the frequency axis
            fill = QLinearGradient(QPointF(0, 0), QPointF(w, 0))
            for k in range(7):
                fill.setColorAt(k / 6, QColor.fromHsvF(k / 6 * 0.85, 0.85, 1.0))
            edge = QColor(255, 255, 255, 230)
        else:
            fill.setColorAt(0.0, hot); fill.setColorAt(0.5, mid); fill.setColorAt(1.0, low)
            edge = mid.lighter(150)
        p.setOpacity(0.55); p.setBrush(fill); p.setPen(Qt.PenStyle.NoPen); p.drawPath(path); p.setOpacity(1)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(edge, 2)); p.drawPath(line)

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
            col = (QColor.fromHsvF((self._hue + i / n * 0.6) % 1.0, 0.65, 1.0) if self.scheme == "rainbow"
                   else self._ramp(min(1.0, 0.25 + v * 0.75) * (0.55 + 0.45 * i / n)))
            p.setPen(QPen(col, max(2.0, 2 * math.pi * r0 / n * 0.7), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                       QPointF(cx + math.cos(a) * r1, cy + math.sin(a) * r1))

    def _push_spectro(self):
        """Add the newest slice. The image is a ring: nothing is scrolled or copied, the slice goes into the next
        column and painting starts after it. One block per bar instead of one fill per pixel row."""
        w, h = max(2, self.width()), max(2, self.height())
        if self._spectro is None or self._spectro.height() != h or self._spectro.width() != w:
            self._spectro = QImage(w, h, QImage.Format.Format_RGB32); self._spectro.fill(Qt.GlobalColor.black)
            self._spectro_x = 0
        step = 2
        x = self._spectro_x
        q = QPainter(self._spectro)
        n = len(self._bars)
        for i, v in enumerate(self._bars):                       # bar i covers rows [top, bottom) counted from the bottom
            top, bottom = h - round((i + 1) * h / n), h - round(i * h / n)
            q.fillRect(x, top, step, bottom - top, self._spectro_color(v))
        q.end()
        self._spectro_x = (x + step) % (w - w % step or step)

    def _paint_spectrogram(self, p, w, h, accent, fg):
        img = self._spectro
        if img is None:
            return
        x, iw = self._spectro_x, img.width() - img.width() % 2
        p.drawImage(0, 0, img, x, 0, iw - x, img.height())        # oldest part (after the write position) first
        if x:
            p.drawImage(iw - x, 0, img, 0, 0, x, img.height())

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
                low, mid, hot, _cap = self._theme()
                if self.scheme in ("skin", "rainbow") or low is None:      # the classic green / amber / red meter
                    col = QColor("#3fbf5f") if pos < 0.7 else QColor("#e6c229") if pos < 0.88 else QColor("#e5483f")
                else:
                    col = QColor(low if pos < 0.7 else mid if pos < 0.88 else hot)
                if pos > frac:
                    col.setAlpha(45)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
                p.drawRect(QRectF(x, y + 4, max(1.0, sw - 2), rh - 12))
            hx = 28 + hold * (w - 40)
            p.setBrush(self._theme()[3] or fg); p.drawRect(QRectF(hx - 1, y + 2, 2, rh - 8))
            p.setPen(fg); p.drawText(QRectF(4, y, 22, rh), Qt.AlignmentFlag.AlignVCenter, "LR"[c])
        p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 160))
        for db in (-60, -40, -20, -10, -3, 0):
            x = 28 + (db - FLOOR_DB) / -FLOOR_DB * (w - 40)
            p.drawText(QRectF(x - 14, h - 14, 28, 12), Qt.AlignmentFlag.AlignCenter, f"{db}")

    # ---- menu
    def colors_menu(self, parent) -> QMenu:
        """A 'Colors' submenu (also used by the main window's View menu)."""
        cm = parent.addMenu("Colors")
        self.fill_colors_menu(cm)
        return cm

    def fill_colors_menu(self, cm):
        cm.clear()
        for key, (label, *_rest) in SCHEMES.items():
            if key == "custom":
                continue
            a = cm.addAction(label); a.setCheckable(True); a.setChecked(key == self.scheme)
            a.triggered.connect(lambda _c, k=key: self.colorsChanged.emit(k, list(self.custom)))
        cm.addSeparator()
        a = cm.addAction("Custom colours…"); a.setCheckable(True); a.setChecked(self.scheme == "custom")
        a.triggered.connect(self.choose_custom_colors)

    def choose_custom_colors(self):
        """Pick the low / middle / peak colour of the gradient."""
        picked = []
        for title, cur in zip(("Low level colour", "Middle colour", "Peak colour"), self.custom):
            c = QColorDialog.getColor(QColor(cur), self, title)
            if not c.isValid():
                return
            picked.append(c.name())
        self.colorsChanged.emit("custom", picked)

    def mouseDoubleClickEvent(self, e):
        self.fullscreenRequested.emit()

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("Fullscreen visualizer (F8)", self.fullscreenRequested.emit)
        m.addSeparator()
        for key, label in STYLES.items():
            a = m.addAction(label); a.setCheckable(True); a.setChecked(key == self.style_name)
            a.triggered.connect(lambda _c, k=key: self.styleChanged.emit(k))
        m.addSeparator()
        self.colors_menu(m)
        bm = m.addMenu("Bars")
        for n in (24, 32, 48, 64, 96):
            a = bm.addAction(str(n)); a.setCheckable(True); a.setChecked(n == self.n_bars)
            a.triggered.connect(lambda _c, n=n: (self.set_bars(n), self.optionsChanged.emit()))
        pk = m.addAction("Peak caps"); pk.setCheckable(True); pk.setChecked(self.peaks_on)
        pk.toggled.connect(lambda on: (setattr(self, "peaks_on", on), self.optionsChanged.emit()))
        m.exec(self.mapToGlobal(pos))
