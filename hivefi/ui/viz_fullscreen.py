"""Full-screen visualizer window: MilkDrop-style feedback effects and classic media-player effects."""
import random
import time

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QWidget

from .. import vizfx


class FullscreenVisualizer(QWidget):
    closed = pyqtSignal()
    effectChanged = pyqtSignal(str)
    effectFailed = pyqtSignal(str, str)           # effect name, what went wrong (the effect is skipped from then on)
    doubleClicked = pyqtSignal()                  # embedded in a page: double-click asks for the fullscreen window

    def __init__(self, cfg, parent=None, embedded=False):
        super().__init__(parent, Qt.WindowType.Widget if embedded else Qt.WindowType.Window)
        self.embedded = embedded
        self.setWindowTitle("Hivefi Visualizer")
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setStyleSheet("background: black;")
        self.audio = vizfx.Audio()
        self.effects = [cls() for cls in vizfx.effect_classes()]
        self.index = 0
        self.auto = cfg.vis_full_effect == "auto"
        self.cycle = int(cfg.vis_full_cycle)
        self._since_switch = 0.0
        self.track_text = ""
        self.overlay = True
        self._overlay_at = time.monotonic()
        self._fb: vizfx.Feedback | None = None
        self._soft: QImage | None = None                   # reduced-size buffer for the soft classic effects
        self._last = time.monotonic()
        self._cursor_at = time.monotonic()
        self._order: list[int] = []
        self.broken: set[str] = set()
        self.timer = QTimer(self, interval=33, timeout=self._tick)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)    # even frame spacing: a coarse timer jitters by +-5%
        self.frames = 0
        # last: switching effects touches the state above (the feedback buffer...). Selecting the saved effect before it
        # existed raised AttributeError, which took the whole program down whenever a fixed effect had been chosen.
        if not self.auto:
            self.select(cfg.vis_full_effect, remember=False)

    # ---- control
    @property
    def effect(self) -> vizfx.Effect:
        return self.effects[self.index]

    def names(self):
        return [e.name for e in self.effects]

    def select(self, name, remember=True):
        for i, e in enumerate(self.effects):
            if e.name == name:
                self._switch_to(i)
                self.auto = False
                if remember:
                    self.cfg.vis_full_effect = name
                return True
        return False

    def set_auto(self, on, seconds=None):
        self.auto = on
        if seconds is not None:
            self.cycle = seconds
            self.cfg.vis_full_cycle = seconds
        self.cfg.vis_full_effect = "auto" if on else self.effect.name

    def next_effect(self, step=1, manual=True):
        if manual:
            self.auto = False
            self.cfg.vis_full_effect = self.effects[(self.index + step) % len(self.effects)].name
        self._switch_to((self.index + step) % len(self.effects))

    def _random_next(self):
        if not self._order:                                    # a shuffled round of all effects, no repeats within it
            self._order = [i for i in range(len(self.effects)) if i != self.index and self.effects[i].name not in self.broken]
            random.shuffle(self._order)
        if self._order:
            self._switch_to(self._order.pop())

    def _switch_to(self, i):
        """Show another effect on a clean screen: nothing of the previous one (trails, particles, rings) is left."""
        self.index = i
        self.effects[i] = type(self.effects[i])()                # fresh state: no leftover particles, rings, sparks...
        self._since_switch = 0.0
        self._overlay_at = time.monotonic()
        if self._fb is not None:
            self._fb.clear()
        self.effectChanged.emit(self.effect.name)

    def feed(self, mags, levels):
        self.audio.feed(mags, levels)

    def set_track(self, t):
        self.track_text = f'{t["artist"]} – {t["title"]}' if t else ""
        self._overlay_at = time.monotonic()

    def start(self):
        if not self.embedded:
            self.showFullScreen()
        self.timer.start()
        self.setFocus()

    # ---- frames
    def _feedback(self):
        w = vizfx.QUALITY.get(self.cfg.vis_full_quality, 480)
        h = max(90, int(w * self.height() / max(1, self.width())))
        if self._fb is None or abs(self._fb.w - w) > 1 or abs(self._fb.h - h) > 1:
            self._fb = vizfx.Feedback(w, h)
        return self._fb

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        self.audio.update(dt)
        self._since_switch += dt
        if self.auto and self.cycle > 0 and self._since_switch >= self.cycle:
            self._random_next()
        if now - self._cursor_at > 2.0 and self.cursor().shape() != Qt.CursorShape.BlankCursor:
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.frames += 1
        self.update()

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        if w < 2 or h < 2:                                     # not laid out yet / minimised: nothing to draw
            return
        p = QPainter(self)
        e = self.effect
        try:
            if e.family == "milkdrop":
                fb = self._feedback()
                e.paint_into(fb, self.audio)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawImage(QRectF(0, 0, w, h), fb.image)
            elif e.soft:
                self._paint_soft(p, e, w, h)
            else:
                p.fillRect(self.rect(), QColor(0, 0, 0))
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                e.paint(p, w, h, self.audio)
            self._paint_overlay(p, w, h)
        except Exception as ex:  # noqa: BLE001 - an exception escaping paintEvent aborts the whole application
            self._effect_failed(e, ex)
        finally:
            p.end()

    def _paint_soft(self, p, e, w, h):
        """Draw a soft effect into a smaller image and scale it up: the cost no longer grows with the screen size
        (Scope took 130-200 ms a frame at 1080p-1440p, 4-6 times the frame budget) and glows look the same."""
        bw = min(w, 2 * vizfx.QUALITY.get(self.cfg.vis_full_quality, 480))
        bh = max(1, round(bw * h / w))
        if self._soft is None or self._soft.width() != bw or self._soft.height() != bh:
            self._soft = QImage(bw, bh, QImage.Format.Format_ARGB32_Premultiplied)
        self._soft.fill(0xFF000000)
        q = QPainter(self._soft)
        try:
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.scale(bw / w, bh / h)                            # the effect still works in window coordinates
            e.paint(q, w, h, self.audio)
        finally:
            q.end()                                            # never leave the buffer locked if the effect fails
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(0, 0, w, h), self._soft)

    def _effect_failed(self, e, ex):
        """A broken effect must never take the program down: report it, skip it from now on, show the next one."""
        import traceback
        traceback.print_exception(type(ex), ex, ex.__traceback__)
        self.broken.add(e.name)
        self.effectFailed.emit(e.name, f"{type(ex).__name__}: {ex}")
        ok = [i for i, x in enumerate(self.effects) if x.name not in self.broken]
        if ok:
            nxt = next((i for i in ok if i > self.index), ok[0])
            self._switch_to(nxt)

    def _paint_overlay(self, p, w, h):
        age = time.monotonic() - self._overlay_at
        if not self.overlay or age > 4.0:
            return
        alpha = int(255 * min(1.0, (4.0 - age) / 1.5))
        f = QFont(self.font()); f.setPointSize(max(14, h // 40)); p.setFont(f)
        p.setPen(QColor(255, 255, 255, alpha))
        title = f"{self.track_text}" if self.track_text else ""
        p.drawText(QRectF(24, h - 110, w - 48, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        f.setPointSize(max(11, h // 60)); p.setFont(f); p.setPen(QColor(200, 200, 200, alpha))
        mode = f"auto ({self.cycle}s)" if self.auto and self.cycle else "auto" if self.auto else "fixed"
        p.drawText(QRectF(24, h - 70, w - 48, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{self.effect.name}  ·  {mode}  ·  ← → change   Space auto   O overlay   " + ("double-click: fullscreen" if self.embedded else "Esc exit"))

    # ---- input
    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_F8) and not self.embedded:
            self.close()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_N):
            self.next_effect(1)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp, Qt.Key.Key_P):
            self.next_effect(-1)
        elif k == Qt.Key.Key_Space:
            self.set_auto(not self.auto)
            self._overlay_at = time.monotonic()
        elif k == Qt.Key.Key_O:
            self.overlay = not self.overlay
        elif k == Qt.Key.Key_Up:
            self.set_auto(True, min(300, (self.cycle or 15) + 15)); self._overlay_at = time.monotonic()
        elif k == Qt.Key.Key_Down:
            self.set_auto(True, max(5, (self.cycle or 30) - 15)); self._overlay_at = time.monotonic()
        else:
            super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.embedded:
            self.doubleClicked.emit()
        else:
            self.close()

    def mouseMoveEvent(self, e):
        self._cursor_at = time.monotonic()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)
        self.closed.emit()
