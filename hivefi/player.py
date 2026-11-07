"""Playback engine front-end: queue, shuffle, repeat, gapless look-ahead, scrobble detection."""
import random

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer

from .audio import Backend


def curve_gain(s: float, curve: str) -> float:
    """Map the 0..1 volume slider to a linear gain."""
    s = max(0.0, min(1.0, s))
    if curve == "cubic":
        return s ** 3
    if curve == "db":                        # -60 dB .. 0 dB across the slider
        return 0.0 if s <= 0 else 10 ** ((s - 1) * 60 / 20)
    return s


class Player(QObject):
    trackChanged = pyqtSignal(object)        # track dict | None
    stateChanged = pyqtSignal(bool)          # playing?
    positionChanged = pyqtSignal(int)        # ms
    durationChanged = pyqtSignal(int)
    queueChanged = pyqtSignal()
    played = pyqtSignal(object)              # emitted once a track counts as "played"
    nowPlaying = pyqtSignal(object)
    error = pyqtSignal(str)
    notice = pyqtSignal(str)

    def __init__(self, url_resolver, cfg, parent=None):
        super().__init__(parent)
        self.resolve = url_resolver           # track dict -> QUrl
        self.cfg = cfg
        self.mp = Backend(cfg, self)
        self.out = self.mp.out
        self.queue: list[dict] = []
        self.index = -1
        self.shuffle = False
        self.repeat = "off"                   # off | all | one
        self.auto_dj = None                   # callable -> list of tracks, or None when disabled
        self._volume = 80
        self._next_idx: int | None = None
        self._order: list[int] = []           # play order (indices into queue)
        self._counted = False
        self._in_update = False
        self.mp.positionChanged.connect(self._on_pos)
        self.mp.durationChanged.connect(lambda d: self.durationChanged.emit(int(d)))
        self.mp.playbackStateChanged.connect(
            lambda s: self.stateChanged.emit(s == QMediaPlayer.PlaybackState.PlayingState))
        self.mp.mediaStatusChanged.connect(self._on_status)
        self.mp.errorOccurred.connect(self.error.emit)
        self.mp.advanced.connect(self._on_advanced)
        self.mp.notice.connect(self.notice.emit)
        self.mp.engineChanged.connect(lambda _e: self._apply_volume())

    # --- settings
    def configure(self, relook=True):
        """Re-read audio settings from the config (volume curve, ReplayGain for the Qt engine, engine DSP).
        relook=False skips re-evaluating the next track (equalizer drags call this dozens of times a second)."""
        self.mp.apply_settings()
        self._apply_volume()
        if relook:
            self._update_next()

    # --- queue management
    @property
    def current(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def _reorder(self):
        self._order = list(range(len(self.queue)))
        if self.shuffle:
            random.shuffle(self._order)
            if 0 <= self.index < len(self.queue):   # current track first
                self._order.remove(self.index)
                self._order.insert(0, self.index)

    def set_shuffle(self, v):
        self.shuffle = v
        self._reorder()
        self._update_next()

    def set_repeat(self, mode):
        self.repeat = mode
        self._update_next()

    def play_list(self, tracks, start=0, manual=False):
        self.queue = list(tracks)
        self.index = start
        self._reorder()
        self.queueChanged.emit()
        self._load(start, manual)

    def restore(self, tracks, index, position_ms=0, play=False):
        """Bring back a saved queue: the track is loaded (paused at position_ms unless play=True)."""
        if not tracks or not 0 <= index < len(tracks):
            return
        self.queue = list(tracks)
        self.index = index
        self._reorder()
        self.queueChanged.emit()
        t = self.queue[index]
        self._counted = position_ms > 0
        self._apply_volume()
        self.mp.setSource(self.resolve(t))
        self.trackChanged.emit(t)
        if play:
            self.mp.play()
            if position_ms:
                self.mp.setPosition(position_ms)
            self.nowPlaying.emit(t)
        else:
            self.mp.preload_paused(position_ms)             # prerolls the track so the seek works
            if position_ms:
                self.positionChanged.emit(position_ms)
        self._update_next()

    def enqueue(self, tracks, next_=False):
        if next_ and self.index >= 0:
            at = self.index + 1
            self.queue[at:at] = list(tracks)
        else:
            self.queue.extend(tracks)
        cur = self.index
        self._reorder()
        self.index = cur
        self.queueChanged.emit()
        if self.index < 0 and self.queue:
            self._load(0)
        else:
            self._update_next()

    def remove(self, rows):
        """Remove queued tracks by queue row (the currently playing row is kept)."""
        rows = sorted({r for r in rows if 0 <= r < len(self.queue) and r != self.index})
        if not rows:
            return
        before = sum(1 for r in rows if r < self.index)
        for r in reversed(rows):
            del self.queue[r]
        self.index -= before
        self._reorder()
        self.queueChanged.emit()
        self._update_next()

    def clear(self):
        self.mp.stop(); self.queue = []; self.index = -1; self._order = []
        self.mp.set_next(None)
        self._next_idx = None
        self.queueChanged.emit(); self.trackChanged.emit(None)

    def jump(self, i):
        if 0 <= i < len(self.queue):
            self._load(i, manual=True)

    def _load(self, i, manual=False):
        self.index = i
        t = self.queue[i]
        self._counted = False
        self._apply_volume()
        self.mp.setSource(self.resolve(t), crossfade=manual)
        self.trackChanged.emit(t)
        self.mp.play()
        self.nowPlaying.emit(t)
        self._update_next()

    # --- gapless / crossfade look-ahead
    def _peek_next(self):
        if not self.queue or self.index < 0:
            return None
        if self.repeat == "one":
            return self.index
        i = self._step(1)
        if i is None and self.auto_dj:
            more = self.auto_dj()
            if more:
                cur = self.index
                self.queue.extend(more)
                self._reorder()
                self.index = cur
                self.queueChanged.emit()
                i = self._step(1)
        return i

    def _update_next(self):
        if self._in_update:
            return
        self._in_update = True
        try:
            self._next_idx = self._peek_next()
            if self._next_idx is None:
                self.mp.set_next(None)
                return
            nxt, cur = self.queue[self._next_idx], self.current
            same = bool(cur) and nxt["album"] == cur["album"] and (nxt["album_artist"] or nxt["artist"]) == \
                (cur["album_artist"] or cur["artist"])
            self.mp.set_next(self.resolve(nxt), same)
        finally:
            self._in_update = False

    def _on_advanced(self):
        """The engine started the queued next track by itself (gapless / crossfade)."""
        if self._next_idx is None:
            return
        self.index = self._next_idx
        self._counted = False
        self._apply_volume()
        t = self.current
        self.trackChanged.emit(t)
        self.nowPlaying.emit(t)
        self._update_next()

    # --- transport
    def toggle(self):
        if self.mp.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.mp.pause()
        elif self.current:
            self.mp.play()
        elif self.queue:
            self._load(0)

    def stop(self): self.mp.stop()
    def seek(self, ms): self.mp.setPosition(ms)

    def set_volume(self, v):
        self._volume = v
        self._apply_volume()

    def _apply_volume(self):
        g = curve_gain(self._volume / 100, self.cfg.volume_curve)
        if self.mp.engine == "qt" and self.cfg.rg_mode != "off":   # GStreamer applies ReplayGain itself (rgvolume)
            gain = (self.current or {}).get("rg_gain")
            db = gain + self.cfg.rg_preamp if gain is not None else self.cfg.rg_fallback
            g *= min(1.0, 10 ** (db / 20))                          # never boost: avoids clipping
        self.out.setVolume(g)

    def _step(self, d):
        if not self.queue:
            return None
        pos = self._order.index(self.index) if self.index in self._order else -1
        n = pos + d
        if 0 <= n < len(self._order):
            return self._order[n]
        if self.repeat == "all":
            if self.shuffle and d > 0:
                self._reorder()
            return self._order[n % len(self._order)]
        return None

    def next(self, *, manual=True):      # keyword-only: Qt signals must not smuggle in `checked`
        i = self._step(1)
        if i is None and self.auto_dj:
            more = self.auto_dj()
            if more:
                self.enqueue(more)
                i = self._step(1)
        if i is not None:
            self._load(i, manual)
        else:
            self.mp.stop()

    def previous(self):
        if self.mp.position() > 3000:
            self.mp.setPosition(0); return
        i = self._step(-1)
        if i is not None:
            self._load(i, manual=True)

    # --- internals
    def _on_pos(self, ms):
        self.positionChanged.emit(ms)
        t = self.current
        if t and not self._counted:
            dur = self.mp.duration() or int((t.get("duration") or 0) * 1000)
            if dur and ms >= min(dur / 2, 240_000):      # Last.fm rule: 50% or 4 min
                self._counted = True
                self.played.emit(t)

    def _on_status(self, st):
        if st == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat == "one":
                self._load(self.index)
            else:
                self.next(manual=False)
