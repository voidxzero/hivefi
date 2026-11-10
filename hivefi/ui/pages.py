"""Whole-window pages that open as tabs next to the library tabs (like MusicBee's Visualizer and Now Playing tabs):

* VisualizerPage: the visualizer effects drawn inside the window instead of only fullscreen;
* NowPlayingPage: the cover blurred into a background, the cover with the previous ones fanned out behind it, the
  title, the queue, a waveform seek bar and a small spectrum."""
from PyQt6.QtCore import QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .. import art, vizfx
from .models import fmt_time
from .panels import blurred
from .viz_fullscreen import FullscreenVisualizer

PAGE_TITLES = {"visualizer": "Visualizer", "nowplaying": "Now Playing"}


class VisualizerPage(QWidget):
    """The fullscreen visualizer as a child widget; only runs (and asks for analyser data) while it is on screen."""
    fullscreenRequested = pyqtSignal()

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.viz = FullscreenVisualizer(win.cfg, self, embedded=True)
        self.viz.doubleClicked.connect(win.open_fullscreen_vis)
        lay.addWidget(self.viz)
        win.player.mp.visData.connect(self.viz.feed)
        win.player.trackChanged.connect(self.viz.set_track)
        self.viz.set_track(win.player.current)
        self._was_active = None

    def showEvent(self, e):
        super().showEvent(e)
        w = self.win
        self._was_active = w.cfg.vis_active
        w.cfg.vis_active = True
        w.player.configure(relook=False)                      # the analysers must be posting while this is shown
        self.viz.start(); self.viz.setFocus()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.viz.timer.stop()
        w = self.win
        if self._was_active is not None:
            w.cfg.vis_active = bool(self._was_active or w.dock_vis.toggleViewAction().isChecked())
            w.player.configure(relook=False)
            self._was_active = None


class _Wave(QWidget):
    seekRequested = pyqtSignal(float)

    def __init__(self, win):
        super().__init__()
        self.win = win; self.frac = 0.0
        self.setFixedHeight(46); self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _e):
        p = QPainter(self); peaks = getattr(self.win.seek, "_peaks", None)
        w, h = self.width(), self.height()
        n = max(1, w // 3)
        for i in range(n):
            v = peaks[min(len(peaks) - 1, i * len(peaks) // n)] / 255 if peaks else 0.08
            bh = max(2, int(v * (h - 4)))
            done = i / n <= self.frac
            p.fillRect(QRect(i * 3, (h - bh) // 2, 2, bh), QColor(255, 255, 255, 235 if done else 90))

    def mousePressEvent(self, e):
        self.seekRequested.emit(max(0.0, min(1.0, e.position().x() / max(1, self.width()))))


class _Spectrum(QWidget):
    def __init__(self):
        super().__init__()
        self.audio = vizfx.Audio(); self.setFixedSize(360, 70)
        self._t = QTimer(self, interval=40, timeout=self._tick)

    def feed(self, mags, levels):
        self.audio.feed(mags, levels)

    def showEvent(self, e):
        super().showEvent(e); self._t.start()

    def hideEvent(self, e):
        super().hideEvent(e); self._t.stop()

    def _tick(self):
        self.audio.update(0.04); self.update()

    def paintEvent(self, _e):
        p = QPainter(self); n = self.audio.N; bw = self.width() / n
        for i in range(n):
            bh = self.audio.bars[i] * self.height()
            if bh < 1.5:                                       # silence: no row of dots
                continue
            p.fillRect(QRectF(i * bw, self.height() - bh, bw - 1, bh), QColor(255, 255, 255, 190))


class NowPlayingPage(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.cover: QPixmap | None = None
        self.bg: QPixmap | None = None
        self.covers: dict = {}                                # track key -> QPixmap (previous tracks of the queue)
        self._asked: set = set()
        self.setMouseTracking(True)
        self.wave = _Wave(win); self.wave.setParent(self)
        self.wave.seekRequested.connect(self._seek)
        self.spec = _Spectrum(); self.spec.setParent(self)
        win.player.mp.visData.connect(self.spec.feed)
        win.player.trackChanged.connect(self._track_changed)
        self._timer = QTimer(self, interval=250, timeout=self._poll)
        self._was_active = None

    # ---- data
    def set_cover(self, pm: QPixmap | None):
        self.cover = pm if pm is not None and not pm.isNull() else None
        self.bg = None
        if self.cover is not None:
            self.bg = blurred(self.cover)
        self.update()

    def _key(self, t):
        return t.get("_ak") or t.get("cover_id") or t.get("id")

    def _track_changed(self, _t=None):
        self._want_neighbours(); self.update()

    def _want_neighbours(self):
        P = self.win.player
        from .main_window import Worker
        for k in range(1, 4):
            i = P.index - k
            if 0 <= i < len(P.queue):
                t = P.queue[i]; key = self._key(t)
                if key in self.covers or key in self._asked:
                    continue
                self._asked.add(key)
                tc, api = dict(t), self.win.api
                w = Worker(lambda _p, tc=tc, api=api: art.get_art(tc, api, 400), self)
                w.done.connect(lambda data, key=key: self._got(key, data))
                self.win._run(w)

    def _got(self, key, data):
        pm = QPixmap()
        if data and pm.loadFromData(data):
            self.covers[key] = pm; self.update()

    def _seek(self, frac):
        d = self.win.player.mp.duration()
        if d:
            self.win.player.seek(int(frac * d))

    def _poll(self):
        mp = self.win.player.mp
        d = mp.duration()
        self.wave.frac = (mp.position() / d) if d else 0.0
        self.wave.update(); self.update()

    # ---- window events
    def showEvent(self, e):
        super().showEvent(e)
        w = self.win
        self._was_active = w.cfg.vis_active
        w.cfg.vis_active = True; w.player.configure(relook=False)    # the spectrum needs the analysers
        self._timer.start(); self._want_neighbours()
        if self.cover is None and w.player.current:
            self.set_cover(getattr(w, "_last_cover", None))

    def hideEvent(self, e):
        super().hideEvent(e); self._timer.stop()
        w = self.win
        if self._was_active is not None:
            w.cfg.vis_active = bool(self._was_active or w.dock_vis.toggleViewAction().isChecked())
            w.player.configure(relook=False); self._was_active = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        sw = max(120, min(360, int(w * 0.22)))                # the player bar below has the transport buttons
        self.spec.setFixedSize(sw, 70)
        self.spec.move(w - sw - 30, h - 86)
        self.wave.setGeometry(90, h - 74, max(100, w - sw - 30 - 90 - 90), 46)

    # ---- painting
    def paintEvent(self, _e):
        p = QPainter(self); w, h = self.width(), self.height()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(24, 24, 28))
        if self.bg is not None:
            p.drawPixmap(self.rect(), self.bg)
            p.fillRect(self.rect(), QColor(0, 0, 0, 70))       # white text stays readable on a light cover
        P = self.win.player; t = P.current
        side = int(min(h - 260, w * 0.32)); side = max(120, side)
        x0, y0 = int(w * 0.17), 56
        step = min(int(side * 0.30), max(0, (x0 - 16) // 3))    # the fan stays inside the window
        # previous covers fanned out behind the current one, each smaller and darker
        for k in range(3, 0, -1):
            i = P.index - k
            if not (0 <= i < len(P.queue)):
                continue
            pm = self.covers.get(self._key(P.queue[i]))
            s = int(side * (1 - 0.13 * k))
            r = QRect(x0 - step * k, y0 + (side - s) // 2, s, s)
            if pm is not None:
                p.drawPixmap(r, pm); p.fillRect(r, QColor(0, 0, 0, 40 * k))
            else:
                p.fillRect(r, QColor(255, 255, 255, 25))
        cover_r = QRect(x0, y0, side, side)
        if self.cover is not None:
            p.drawPixmap(cover_r, self.cover)
        else:
            p.fillRect(cover_r, QColor(60, 60, 66))
            f = QFont(self.font()); f.setPixelSize(max(24, side // 3)); p.setFont(f); p.setPen(QColor(255, 255, 255, 60))
            p.drawText(cover_r, Qt.AlignmentFlag.AlignCenter, "♪")
        white = QColor(255, 255, 255)
        left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        el = Qt.TextElideMode.ElideRight
        if t:
            tw = max(side, int(w * 0.52) - x0 - 24)             # never runs into the queue
            f = QFont(self.font()); f.setPointSizeF(f.pointSizeF() * 2.0); p.setFont(f); p.setPen(white)
            p.drawText(QRect(x0, y0 + side + 18, tw, 40), left, p.fontMetrics().elidedText(t["title"] or "", el, tw))
            f.setPointSizeF(self.font().pointSizeF() * 1.15); p.setFont(f); p.setPen(QColor(255, 255, 255, 215))
            p.drawText(QRect(x0, y0 + side + 62, tw, 24), left, p.fontMetrics().elidedText(t["album"] or "", el, tw))
            p.drawText(QRect(x0, y0 + side + 88, tw, 24), left, p.fontMetrics().elidedText(t["artist"] or "", el, tw))
        else:
            p.setPen(QColor(255, 255, 255, 170)); p.setFont(self.font())
            p.drawText(QRect(x0, y0 + side + 18, side, 30), left, "Nothing is playing")
        # the queue, on the right
        rx, ry, rw = int(w * 0.52), 90, int(w * 0.34)
        f = QFont(self.font()); p.setFont(f)
        rows = max(1, (h - 260) // 37)
        start = max(0, min(P.index - 2, len(P.queue) - rows)) if P.queue else 0
        for n, i in enumerate(range(start, min(len(P.queue), start + rows))):
            q = P.queue[i]; y = ry + n * 37
            cur = i == P.index
            p.setPen(QColor(255, 255, 255, 255 if cur else 175))
            fb = QFont(f); fb.setBold(cur); p.setFont(fb)
            p.drawText(QRect(rx, y, 40, 30), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{i + 1}.")
            label = f'{q["artist"]} - {q["title"]}'
            p.drawText(QRect(rx + 40, y, rw - 110, 30), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, rw - 110))
            p.drawText(QRect(rx + rw - 60, y, 60, 30), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fmt_time(q.get("duration") or 0))
        # times around the waveform
        p.setFont(f); p.setPen(QColor(255, 255, 255, 200))
        d, pos = P.mp.duration(), P.mp.position()
        wg = self.wave.geometry()
        p.drawText(QRect(wg.left() - 60, wg.top(), 54, wg.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   fmt_time(pos / 1000))
        p.drawText(QRect(wg.right() + 6, wg.top(), 60, wg.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fmt_time(d / 1000))
