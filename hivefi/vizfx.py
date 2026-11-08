"""Full-screen visualizations.

Two families, both driven by the same audio analysis (`Audio`):

* feedback effects in the spirit of MilkDrop: every frame the previous picture is warped (zoom, rotation, drift, a sine
  ripple per pixel), faded and colour-shifted with numpy, and fresh shapes driven by the music are drawn on top, so
  trails melt into each other. They need numpy;
* classic media-player style effects (bars, scope, ambience, battery, particles, spikes, starfield) that are drawn
  from scratch every frame.

The audio side only has the GStreamer analysers (spectrum + level), not raw samples, so the "scope" waveform is rebuilt
from the spectrum."""
import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient

try:
    import numpy as np
except ImportError:                                     # the classic effects still work without it
    np = None

from .ui.visualizer import FLOOR_DB, to_bars


# ======================================================================================= audio analysis
class Audio:
    """Smoothed spectrum, band energies and beat detection for one frame at a time."""
    N = 64

    def __init__(self):
        self.bars = [0.0] * self.N
        self.raw = [0.0] * self.N
        self.bass = self.mid = self.treb = self.level = 0.0
        self.beat = False
        self.beat_count = 0
        self.t = 0.0
        self.dt = 0.033
        self._avg = 0.0
        self._last_beat = -1.0
        self._idle = 0.0                  # seconds since the last analyser message
        self._phase = [random.random() * 6.28 for _ in range(self.N)]

    def feed(self, mags, levels):
        self._idle = 0.0
        if mags:
            self.raw = to_bars(mags, self.N)
        if levels and levels.get("rms"):
            rms = max(levels["rms"])
            self.level = max(0.0, min(1.0, (rms - FLOOR_DB) / -FLOOR_DB))

    def update(self, dt: float):
        """Advance one frame: smooth the bars, work out bass/mid/treble and detect a beat."""
        self.dt, self.t = dt, self.t + dt
        self._idle += dt
        if self._idle > 0.4:                                       # no data (paused / silent): everything relaxes to zero
            self.raw = [0.0] * self.N
            self.level *= 0.9
        for i in range(self.N):
            r, b = self.raw[i], self.bars[i]
            self.bars[i] = b + (r - b) * (0.65 if r > b else 0.2)
        n = self.N
        third = n // 3
        self.bass = sum(self.bars[: n // 8]) / (n // 8)
        self.mid = sum(self.bars[n // 8: n // 2]) / (n // 2 - n // 8)
        self.treb = sum(self.bars[n // 2:]) / (n - n // 2)
        self.beat = False
        e = sum(self.raw[: n // 8]) / (n // 8)
        if e > self._avg * 1.3 + 0.08 and self.t - self._last_beat > 0.22:
            self.beat, self._last_beat = True, self.t
            self.beat_count += 1
        self._avg += (e - self._avg) * 0.06

    def wave(self, count: int) -> list:
        """A made-up oscilloscope trace (-1..1) built from the spectrum: one sine per band, phases drifting."""
        ks = range(0, self.N, 2)
        for k in range(self.N):
            self._phase[k] += self.dt * (2.0 + k * 0.9)
        if np is not None:
            k = np.arange(0, self.N, 2)
            x = np.linspace(0.0, 1.0, count, dtype=np.float32)
            amp = np.array(self.bars, np.float32)[k] / (1 + k * 0.12)
            ph = np.array(self._phase, np.float32)[k]
            v = (amp[:, None] * np.sin(ph[:, None] + (k[:, None] + 1) * x[None, :] * 3.1416)).sum(axis=0) * 0.9
            return np.tanh(v).tolist()                         # soft clipping: loud music bends the trace, never flattens it
        step = 3                                               # without numpy: fewer bands and points keep it light
        return [math.tanh(sum(self.bars[k] * math.sin(self._phase[k] + (k + 1) * (i / max(1, count - 1)) * 3.14)
                                       / (1 + k * 0.12) for k in range(0, self.N, 2 * step)) * 1.6)
                for i in range(0, count)]


def hsv(h, s=1.0, v=1.0, a=255):
    c = QColor.fromHsvF(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    c.setAlpha(a)
    return c


class Effect:
    name = "effect"
    family = "classic"                    # "classic" | "milkdrop"
    needs_numpy = False

    def reset(self, w, h):
        """Called when the effect is shown or the window size changes."""

    def paint(self, p: QPainter, w: int, h: int, a: Audio):
        raise NotImplementedError


# ======================================================================================= classic effects
class Bars(Effect):
    name = "Bars"

    def __init__(self):
        self.caps = [0.0] * Audio.N

    def paint(self, p, w, h, a):
        n = Audio.N
        bw = w / n
        for i, v in enumerate(a.bars):
            self.caps[i] = max(v, self.caps[i] - 0.012)
            x, bh = i * bw, max(2.0, v * h * 0.85)
            g = QLinearGradient(0, h, 0, h - h * 0.85)
            g.setColorAt(0, QColor("#12c92a")); g.setColorAt(0.6, QColor("#e8d81c")); g.setColorAt(1, QColor("#ec2a1c"))
            p.fillRect(QRectF(x + 1, h - bh, bw - 2, bh), QBrush(g))
            p.fillRect(QRectF(x + 1, h - self.caps[i] * h * 0.85 - 5, bw - 2, 4), QColor("#ffffff"))


class Scope(Effect):
    name = "Scope"

    def paint(self, p, w, h, a):
        pts = a.wave(160)
        for width, alpha in ((14, 28), (7, 60), (2.5, 255)):
            pen = QPen(hsv(a.t * 0.05 + 0.5, 0.7, 1.0, alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                       Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            poly = QPolygonF([QPointF(i / (len(pts) - 1) * w, h / 2 + v * h * 0.38) for i, v in enumerate(pts)])
            p.drawPolyline(poly)


class Ambience(Effect):
    name = "Ambience"

    def __init__(self):
        rnd = random.Random(7)
        self.blobs = [(rnd.random() * 6.28, rnd.random() * 6.28, 0.15 + rnd.random() * 0.2, rnd.random()) for _ in range(9)]

    def paint(self, p, w, h, a):
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for i, (px, py, sp, hue) in enumerate(self.blobs):
            x = w * (0.5 + 0.38 * math.sin(px + a.t * sp))
            y = h * (0.5 + 0.36 * math.cos(py + a.t * sp * 1.3))
            r = min(w, h) * (0.16 + 0.22 * a.bars[(i * 5) % Audio.N] + 0.1 * a.bass)
            g = QRadialGradient(x, y, r)
            c = hsv(hue + a.t * 0.02, 0.8, 0.9, 190)
            g.setColorAt(0, c); g.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g)); p.drawEllipse(QPointF(x, y), r, r)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


class Battery(Effect):
    name = "Battery"

    def __init__(self):
        self.rings: list[list[float]] = []            # [radius fraction, hue]

    def paint(self, p, w, h, a):
        if a.beat or (not self.rings and a.level > 0.05):
            self.rings.append([0.0, a.t * 0.1])
        self.rings = [[r + a.dt * (0.35 + a.level), hue] for r, hue in self.rings if r < 1.3]
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r, hue in self.rings:
            half = r * max(w, h) * 0.5
            p.setPen(QPen(hsv(hue, 0.9, 1.0, int(255 * max(0.0, 1 - r / 1.3))), 3 + 8 * (1 - min(1, r))))
            p.drawRoundedRect(QRectF(w / 2 - half, h / 2 - half * h / w, half * 2, half * 2 * h / w), 18, 18)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(hsv(a.t * 0.1, 0.8, 1.0, 230))
        core = min(w, h) * (0.04 + 0.12 * a.bass)
        p.drawEllipse(QPointF(w / 2, h / 2), core, core)


class Particles(Effect):
    name = "Particles"

    def __init__(self):
        self.ps: list[list[float]] = []                # x y vx vy life hue

    def paint(self, p, w, h, a):
        if a.beat or a.level > 0.25:
            for _ in range(int(6 + 60 * a.bass) if a.beat else 2):
                ang = random.uniform(math.pi * 1.1, math.pi * 1.9)
                sp = random.uniform(0.25, 0.7 + a.bass) * h
                self.ps.append([random.uniform(0.3, 0.7) * w, h, math.cos(ang) * sp * 0.6, math.sin(ang) * sp,
                                1.0, random.random()])
        alive = []
        for q in self.ps:
            q[0] += q[2] * a.dt; q[1] += q[3] * a.dt; q[3] += 0.9 * h * a.dt; q[4] -= a.dt * 0.55
            if q[4] > 0 and q[1] < h + 20:
                alive.append(q)
        self.ps = alive[-900:]
        p.setPen(Qt.PenStyle.NoPen)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for x, y, _vx, _vy, life, hue in self.ps:
            r = 2 + 6 * life
            p.setBrush(hsv(hue + a.t * 0.05, 0.8, 1.0, int(230 * life)))
            p.drawEllipse(QPointF(x, y), r, r)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


class Spikes(Effect):
    name = "Spikes"

    def paint(self, p, w, h, a):
        cx, cy, n = w / 2, h / 2, Audio.N * 2
        base = min(w, h) * (0.12 + 0.05 * a.bass)
        rot = a.t * 0.25
        for i in range(n):
            v = a.bars[i % Audio.N] if i < Audio.N else a.bars[n - 1 - i]
            ang = rot + i / n * 6.2832
            r1, r2 = base, base + v * min(w, h) * 0.42 + 4
            p.setPen(QPen(hsv(i / n + a.t * 0.04, 0.85, 1.0), max(2.0, w / 260), Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1),
                       QPointF(cx + math.cos(ang) * r2, cy + math.sin(ang) * r2))


class Starfield(Effect):
    name = "Starfield"

    def __init__(self):
        rnd = random.Random(3)
        self.stars = [[rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.random()] for _ in range(420)]

    def paint(self, p, w, h, a):
        speed = 0.15 + 1.6 * a.level + (0.8 if a.beat else 0)
        p.setPen(Qt.PenStyle.NoPen)
        for s in self.stars:
            s[2] -= a.dt * speed * 0.4
            if s[2] <= 0.02:
                s[0], s[1], s[2] = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
            x, y = w / 2 + s[0] / s[2] * w * 0.25, h / 2 + s[1] / s[2] * h * 0.25
            if 0 <= x < w and 0 <= y < h:
                r = 0.6 + (1 - s[2]) * 3.2
                c = QColor(255, 255, 255, int(60 + 195 * (1 - s[2])))
                p.setBrush(c); p.drawEllipse(QPointF(x, y), r, r)


CLASSIC = [Bars, Scope, Ambience, Battery, Particles, Spikes, Starfield]


# ======================================================================================= feedback ("MilkDrop-style")
class Feedback:
    """The picture that is warped into itself every frame (BGRA, `Format_RGB32`)."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = np.zeros((h, w, 4), np.uint8)
        self.buf[..., 3] = 255
        aspect = w / h
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        self.U = (xs / (w - 1) * 2 - 1) * aspect                 # -aspect..aspect
        self.V = ys / (h - 1) * 2 - 1                            # -1..1
        self.aspect = aspect
        self.image = QImage(self.buf.data, w, h, w * 4, QImage.Format.Format_RGB32)

    def clear(self):
        self.buf[..., :3] = 0

    def warp(self, zoom=1.0, rot=0.0, cx=0.0, cy=0.0, dx=0.0, dy=0.0, ripple=0.0, freq=6.0, t=0.0, decay=0.95,
             hue=(0.0, 0.0, 0.0), mirror=False):
        """Replace the buffer with a transformed, faded copy of itself. Pixel p takes its colour from
        centre + R(-rot)(p - centre)/zoom + (dx, dy) + ripple, so zoom > 1 grows the picture outward."""
        U, V = self.U - cx, self.V - cy
        if ripple:
            U = U + ripple * np.sin(V * freq + t * 1.3).astype(np.float32)
            V = V + ripple * np.cos(U * freq + t * 0.9).astype(np.float32)
        c, s = math.cos(-rot), math.sin(-rot)
        inv = 1.0 / max(0.05, zoom)
        su = (c * U - s * V) * inv + cx + dx
        sv = (s * U + c * V) * inv + cy + dy
        ix = np.clip(((su / self.aspect + 1) * 0.5 * (self.w - 1) + 0.5).astype(np.int32), 0, self.w - 1)
        iy = np.clip(((sv + 1) * 0.5 * (self.h - 1) + 0.5).astype(np.int32), 0, self.h - 1)
        flat = self.buf.reshape(-1, 4)[iy * self.w + ix]
        out = flat[..., :3].astype(np.float32) * decay
        if any(hue):                                             # per-channel drift: the palette slowly turns
            out *= np.array([1.0 + hue[2], 1.0 + hue[1], 1.0 + hue[0]], np.float32)
            roll = int(round(abs(hue[0]) * 40)) % 3
            if roll:
                out = np.roll(out, roll if hue[0] > 0 else -roll, axis=2)
        if mirror:
            out = np.maximum(out, out[:, ::-1])
            out = np.maximum(out, out[::-1])
        self.buf[..., :3] = np.clip(out, 0, 255).astype(np.uint8)


class MilkEffect(Effect):
    """Base of the feedback effects: `motion()` says how to warp this frame, `shapes()` draws the new content."""
    family = "milkdrop"
    needs_numpy = True
    mirror = False

    def motion(self, t, a: Audio) -> dict:
        return {}

    def shapes(self, p: QPainter, w, h, t, a: Audio):
        pass

    def paint_into(self, fb: Feedback, a: Audio):
        fb.warp(t=a.t, mirror=self.mirror, **self.motion(a.t, a))
        p = QPainter(fb.image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # normal blending: new shapes replace what is under them, the old picture only survives as warped, fading trails
        # (adding or taking the maximum of many hues piles up into white)
        self.shapes(p, fb.w, fb.h, a.t, a)
        p.end()


def _ring(p, w, h, radius, color, width):
    p.setPen(QPen(color, width)); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(w / 2, h / 2), radius, radius)


class TunnelVision(MilkEffect):
    name = "Tunnel Vision"

    def motion(self, t, a):
        return dict(zoom=1.035 + 0.05 * a.bass, rot=0.004 * math.sin(t * 0.4), decay=0.93, ripple=0.004 + 0.01 * a.mid,
                    freq=5, hue=(0.012, 0, 0))

    def shapes(self, p, w, h, t, a):
        _ring(p, w, h, min(w, h) * (0.08 + 0.25 * a.bass), hsv(t * 0.07, 0.9, 1.0, 230), 2 + 5 * a.level)
        for i in range(0, Audio.N, 3):
            ang = i / Audio.N * 6.283 + t * 0.3
            r0, r1 = min(w, h) * 0.12, min(w, h) * (0.12 + 0.3 * a.bars[i])
            p.setPen(QPen(hsv(i / Audio.N + t * 0.05, 0.8, 1.0, 220), 2))
            p.drawLine(QPointF(w / 2 + math.cos(ang) * r0, h / 2 + math.sin(ang) * r0),
                       QPointF(w / 2 + math.cos(ang) * r1, h / 2 + math.sin(ang) * r1))


class SpiralGalaxy(MilkEffect):
    name = "Spiral Galaxy"

    def motion(self, t, a):
        return dict(zoom=1.012 + 0.02 * a.bass, rot=0.03 + 0.04 * a.mid, decay=0.94, ripple=0.003, freq=7,
                    hue=(0.01, 0, 0))

    def shapes(self, p, w, h, t, a):
        for k in range(Audio.N):
            ang = t * 0.9 + k * 0.4
            r = min(w, h) * (0.04 + 0.5 * k / Audio.N)
            x, y = w / 2 + math.cos(ang) * r, h / 2 + math.sin(ang) * r
            size = 1.5 + 6 * a.bars[k]
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(hsv(k / Audio.N * 0.6 + t * 0.05, 0.8, 1.0, 235))
            p.drawEllipse(QPointF(x, y), size, size)


class LiquidGold(MilkEffect):
    name = "Liquid Gold"

    def motion(self, t, a):
        return dict(zoom=1.0 + 0.012 * a.bass, rot=0.002, dy=-0.004, ripple=0.02 + 0.03 * a.mid, freq=4.5, decay=0.955,
                    hue=(0.0, 0.015, -0.01))

    def shapes(self, p, w, h, t, a):
        pts = a.wave(120)
        poly = QPolygonF([QPointF(i / 119 * w, h / 2 + v * h * 0.33) for i, v in enumerate(pts)])
        p.setPen(QPen(QColor(255, 196, 64, 240), 3)); p.drawPolyline(poly)


class NeonRain(MilkEffect):
    name = "Neon Rain"

    def motion(self, t, a):
        return dict(zoom=0.995, dy=-0.012 - 0.01 * a.level, decay=0.9, ripple=0.003, freq=9, hue=(0.0, 0, 0))

    def shapes(self, p, w, h, t, a):
        n = Audio.N
        for i in range(n):
            if a.bars[i] > 0.12:
                x = i / n * w + random.uniform(-3, 3)
                p.setPen(QPen(hsv(i / n * 0.8 + t * 0.05, 0.9, 1.0, 230), 2 + 3 * a.bars[i]))
                p.drawLine(QPointF(x, 0), QPointF(x, a.bars[i] * h * 0.25))


class KaleidoPulse(MilkEffect):
    name = "Kaleido Pulse"
    mirror = True

    def motion(self, t, a):
        return dict(zoom=1.02 + 0.04 * a.bass, rot=0.02 * math.sin(t * 0.5) + (0.12 if a.beat else 0),
                    decay=0.92, ripple=0.006, freq=6, hue=(0.015, 0, 0))

    def shapes(self, p, w, h, t, a):
        for i in range(0, Audio.N, 4):
            x = w * (0.55 + 0.3 * math.sin(t * 0.7 + i)); y = h * (0.5 + 0.35 * math.cos(t * 0.9 + i * 0.5))
            r = 4 + 40 * a.bars[i]
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(hsv(i / Audio.N + t * 0.08, 0.85, 1.0, 220))
            p.drawEllipse(QPointF(x, y), r, r)


class DeepSea(MilkEffect):
    name = "Deep Sea"

    def __init__(self):
        self.bubbles: list[list[float]] = []

    def motion(self, t, a):
        return dict(zoom=0.998, dy=0.006, ripple=0.03, freq=3.2, decay=0.965, hue=(-0.004, 0.0, 0.008))

    def shapes(self, p, w, h, t, a):
        if a.level > 0.05 and random.random() < 0.3 + a.treb:
            self.bubbles.append([random.uniform(0, w), h + 10, random.uniform(10, 40), random.random()])
        for b in self.bubbles:
            b[1] -= a.dt * (40 + 200 * a.level); b[0] += math.sin(t * 2 + b[3] * 9) * 0.6
        self.bubbles = [b for b in self.bubbles if b[1] > -20][-160:]
        for x, y, vol, hue in self.bubbles:
            r = 3 + vol * 0.3 * (0.5 + a.bass)
            p.setPen(QPen(QColor(120, 190 + int(60 * hue), 255, 220), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, y), r, r)


class Fireworks(MilkEffect):
    name = "Fireworks"

    def __init__(self):
        self.sparks: list[list[float]] = []

    def motion(self, t, a):
        return dict(zoom=1.0, decay=0.9, dy=0.003, hue=(0.0, 0, 0))

    def shapes(self, p, w, h, t, a):
        if a.beat:
            cx, cy, hue = random.uniform(0.2, 0.8) * w, random.uniform(0.15, 0.6) * h, random.random()
            for k in range(int(24 + 40 * a.bass)):
                ang, sp = random.uniform(0, 6.283), random.uniform(0.15, 0.55) * h
                self.sparks.append([cx, cy, math.cos(ang) * sp, math.sin(ang) * sp, 1.0, hue + random.uniform(-0.05, 0.05)])
        alive = []
        for s in self.sparks:
            s[0] += s[2] * a.dt; s[1] += s[3] * a.dt; s[3] += 0.25 * h * a.dt; s[4] -= a.dt * 0.9
            if s[4] > 0:
                alive.append(s)
        self.sparks = alive[-1200:]
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _vx, _vy, life, hue in self.sparks:
            p.setBrush(hsv(hue, 0.8, 1.0, int(255 * life))); p.drawEllipse(QPointF(x, y), 2.5, 2.5)


class EchoChamber(MilkEffect):
    name = "Echo Chamber"
    mirror = True

    def motion(self, t, a):
        return dict(zoom=0.97 + 0.02 * a.bass, rot=0.02 + (0.2 if a.beat else 0), decay=0.9, ripple=0.01 * a.treb,
                    freq=8, hue=(0.02, 0.0, 0.0))

    def shapes(self, p, w, h, t, a):
        n = Audio.N
        bw = w / n * 0.5
        for i in range(n):
            v = a.bars[i]
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(hsv(i / n * 0.8 + t * 0.03, 0.9, 1.0, 235))
            p.drawRect(QRectF(w / 2 + i * bw, h / 2 - v * h * 0.3, bw - 1, v * h * 0.6))


MILK = [TunnelVision, SpiralGalaxy, LiquidGold, NeonRain, KaleidoPulse, DeepSea, Fireworks, EchoChamber]
QUALITY = {"low": 320, "medium": 480, "high": 720}


def effect_classes(with_numpy=None):
    with_numpy = np is not None if with_numpy is None else with_numpy
    return MILK + CLASSIC if with_numpy else list(CLASSIC)
