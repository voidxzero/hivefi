"""Audio engines behind one QMediaPlayer-like interface.

* GStreamer (preferred): two `playbin` decks -> configurable DSP chain -> chosen output device.
  Gives gapless playback (`about-to-finish`), crossfade (overlapping decks), 10/15/31-band equalizer,
  ReplayGain (`rgvolume`), resampling/dither/format control, bit-perfect mode, crossfeed, limiter, balance, mono.
* Qt Multimedia: fallback when GStreamer is missing or a track fails to play (no DSP / gapless / crossfade).
"""
import re
import math
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

PS = QMediaPlayer.PlaybackState
MS = QMediaPlayer.MediaStatus

try:  # optional dependency
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    Gst.init(None)
    GST_OK = all(Gst.ElementFactory.find(e) for e in ("playbin", "equalizer-nbands", "audioconvert", "volume",
                                                      "audioresample", "capsfilter"))
except Exception:  # noqa: BLE001
    Gst = GLib = None
    GST_OK = False


def have(element: str) -> bool:
    return bool(GST_OK and Gst.ElementFactory.find(element))


# --------------------------------------------------------------------------- equalizer layouts
EQ_LAYOUTS = {
    10: [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
    15: [25, 40, 63, 100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300, 10000, 16000],
    31: [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
         2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000],
}
EQ_RANGE_DB = 12.0


def band_ratio(n: int) -> float:
    """Ratio between adjacent centres: octave (10), 2/3 octave (15), 1/3 octave (31)."""
    return {10: 2.0, 15: 2 ** (2 / 3), 31: 2 ** (1 / 3)}[n]


def eq_bandwidth_hz(n: int, f: float) -> float:
    r = math.sqrt(band_ratio(n))
    return f * (r - 1 / r)


def resample_gains(gains, src_n: int, dst_n: int):
    """Re-express an EQ curve on another band layout (linear interpolation over log-frequency)."""
    if src_n == dst_n:
        return list(gains)
    sf, df = [math.log(f) for f in EQ_LAYOUTS[src_n]], [math.log(f) for f in EQ_LAYOUTS[dst_n]]
    out = []
    for x in df:
        if x <= sf[0]:
            out.append(gains[0]); continue
        if x >= sf[-1]:
            out.append(gains[-1]); continue
        j = next(i for i in range(1, len(sf)) if sf[i] >= x)
        t = (x - sf[j - 1]) / (sf[j] - sf[j - 1])
        out.append(round((gains[j - 1] + t * (gains[j] - gains[j - 1])) * 2) / 2)
    return out


def configure_equalizer(eq, n: int, gains, shelf_ends=False):
    """Apply a layout + gains to an `equalizer-nbands` element."""
    freqs = EQ_LAYOUTS[n]
    for i, f in enumerate(freqs):
        band = eq.get_child_by_index(i)
        band.set_property("freq", float(f))
        band.set_property("bandwidth", float(eq_bandwidth_hz(n, f)))
        band.set_property("gain", float(max(-24.0, min(12.0, gains[i] if i < len(gains) else 0.0))))
        band.set_property("type", 1 if (shelf_ends and i == 0) else 2 if (shelf_ends and i == n - 1) else 0)


# --------------------------------------------------------------------------- chain description
OUT_FORMATS = {"auto": None, "16": "S16LE", "24": "S24_32LE", "32": "S32LE", "float": "F32LE"}
CROSSFEED = {"default": (700, 45), "cmoy": (700, 60), "jmeier": (650, 95)}   # fcut Hz, feed dB*10


def _caps(c):
    return f'capsfilter caps="{c}"'


def out_caps(cfg):
    fields = []
    if cfg.output_rate:
        fields.append(f"rate={int(cfg.output_rate)}")
    fmt = OUT_FORMATS.get(str(cfg.output_depth))
    if fmt:
        fields.append(f"format={fmt}")
    return ("audio/x-raw," + ",".join(fields)) if fields else None


_NUM = r"-?[\d.]+(?:e[+-]?\d+)?|-?inf|nan"


def _floats(txt: str, key: str):
    m = re.search(rf"{key}=\(\w+\)[{{<]([^}}>]*)[}}>]", txt)      # GstValueList {..} or GValueArray <..>
    return [float(x) if "inf" not in x and x != "nan" else -200.0
            for x in re.findall(_NUM, m.group(1))] if m else []


def parse_vis(structure):
    """('spectrum', [dB per bin]) | ('level', {'rms': [dB/ch], 'peak': [dB/ch]}) | (None, None)."""
    if structure is None:
        return None, None
    name = structure.get_name()
    if name == "spectrum":
        return "spectrum", _floats(structure.to_string(), "magnitude")
    if name == "level":
        txt = structure.to_string()
        return "level", {"rms": _floats(txt, "rms"), "peak": _floats(txt, "peak")}
    return None, None


VIS_BANDS = 512                     # spectrum resolution (about 43 Hz per bin at 44.1 kHz)
VIS_INTERVAL_NS = 33_000_000        # ~30 messages per second


def chain_description(cfg) -> str:
    """gst bin description for the DSP chain implied by the settings."""
    caps = out_caps(cfg)
    if cfg.bit_perfect:
        els = ["audioconvert name=inconv"]
        if caps:      # only what the user explicitly asked for (rate/format), nothing else touches samples
            els += ["audioresample name=resamp"]
        els += ["audioconvert name=outconv"]
        if caps:
            els.append(_caps(caps))
        return " ! ".join(els)
    els = ["audioconvert name=inconv"]
    if cfg.rg_mode != "off" and have("rgvolume"):
        els.append("rgvolume name=rg")
    if cfg.mono:
        els += ["audioconvert", _caps("audio/x-raw,channels=1"), "audioconvert", _caps("audio/x-raw,channels=2")]
    if cfg.swap_channels and have("audiomixmatrix"):
        els += ["audioconvert", _caps("audio/x-raw,channels=2"),
                'audiomixmatrix name=swap in-channels=2 out-channels=2 '
                'matrix="<<(double)0,(double)1>,<(double)1,(double)0>>"']
    if cfg.crossfeed != "off" and have("bs2b"):
        els += ["audioconvert", _caps("audio/x-raw,channels=2"), "bs2b name=bs2b"]
    if cfg.eq_enabled:
        els += ["audioconvert", f"equalizer-nbands name=eq num-bands={int(cfg.eq_bands)}"]
    if cfg.balance and have("audiopanorama"):
        els += ["audioconvert", "audiopanorama name=pan method=simple"]
    if cfg.limiter and have("audiodynamic"):
        els += ["audioconvert", "audiodynamic name=dyn mode=compressor characteristics=hard-knee ratio=0"]
    els += ["volume name=fade"]
    if cfg.vis_enabled and have("spectrum") and have("level"):      # transparent analysers for the visualizer
        els += ["audioconvert", f"spectrum name=spec bands={VIS_BANDS} threshold=-80 interval={VIS_INTERVAL_NS} "
                                "post-messages=false message-magnitude=true",
                f"level name=lvl interval={VIS_INTERVAL_NS} post-messages=false"]
    els += ["audioresample name=resamp",
            f"audioconvert name=outconv dithering={cfg.dither} noise-shaping={cfg.noise_shaping}"]
    if caps:
        els.append(_caps(caps))
    return " ! ".join(els)


def chain_signature(cfg) -> tuple:
    """Everything that needs the pipeline rebuilt (as opposed to a live property change)."""
    return (chain_description(cfg), cfg.output_device, int(cfg.buffer_ms))


def apply_chain_settings(h: dict, cfg):
    """Push the live-adjustable settings onto a chain's named elements (also used by the tests)."""
    if h.get("eq") is not None:
        configure_equalizer(h["eq"], int(cfg.eq_bands), cfg.eq_gains, cfg.eq_shelf_ends)
    if h.get("rg") is not None:
        h["rg"].set_property("album-mode", cfg.rg_mode == "album")
        h["rg"].set_property("pre-amp", float(cfg.rg_preamp))
        h["rg"].set_property("fallback-gain", float(cfg.rg_fallback))
        h["rg"].set_property("headroom", float(cfg.rg_headroom))
    if h.get("pan") is not None:
        h["pan"].set_property("panorama", float(max(-1.0, min(1.0, cfg.balance))))
    if h.get("dyn") is not None:
        h["dyn"].set_property("threshold", 10 ** (float(cfg.limiter_threshold_db) / 20))
    if h.get("bs2b") is not None and cfg.crossfeed in CROSSFEED:
        fcut, feed = CROSSFEED[cfg.crossfeed]
        h["bs2b"].set_property("fcut", fcut)
        h["bs2b"].set_property("feed", feed)
    if h.get("resamp") is not None:
        h["resamp"].set_property("quality", int(cfg.resample_quality))
    for n in ("spec", "lvl"):
        if h.get(n) is not None:
            h[n].set_property("post-messages", bool(cfg.vis_active))


# --------------------------------------------------------------------------- output devices
_device_cache: list | None = None


def list_output_devices(refresh=False):
    """[(key, label)] of audio sinks GStreamer can see. Key '' is the system default."""
    global _device_cache
    out = [("", "System default")]
    if not GST_OK:
        return out
    if _device_cache is None or refresh:
        found = []
        try:
            GLib.log_set_handler("GStreamer", GLib.LogLevelFlags.LEVEL_CRITICAL | GLib.LogLevelFlags.LEVEL_WARNING,
                                 lambda *a: None, None)
            mon = Gst.DeviceMonitor.new()
            mon.add_filter("Audio/Sink", None)
            mon.start()
            for d in mon.get_devices():
                props = d.get_properties()
                api = (props.get_string("device.api") if props else None) or "audio"
                found.append((f"{api}:{d.get_display_name()}", f"{d.get_display_name()}  [{api}]"))
            mon.stop()
        except Exception:  # noqa: BLE001
            pass
        _device_cache = found
    return out + _device_cache


def make_sink(key: str, buffer_ms: int):
    """Create the sink element for a device key (None -> let playbin pick the default)."""
    if not key or not GST_OK:
        return None
    try:
        mon = Gst.DeviceMonitor.new()
        mon.add_filter("Audio/Sink", None)
        mon.start()
        sink = None
        for d in mon.get_devices():
            props = d.get_properties()
            api = (props.get_string("device.api") if props else None) or "audio"
            if f"{api}:{d.get_display_name()}" == key:
                sink = d.create_element(None)
                break
        mon.stop()
        if sink is not None and buffer_ms and sink.find_property("buffer-time"):
            sink.set_property("buffer-time", int(buffer_ms) * 1000)
        return sink
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- GStreamer deck
class Deck:
    """One playbin with its DSP chain. Two decks make crossfade possible."""
    def __init__(self, idx):
        self.idx = idx
        self.pipe = Gst.ElementFactory.make("playbin", f"deck{idx}")
        self.pipe.set_property("video-sink", Gst.ElementFactory.make("fakesink", None))
        self.bus = self.pipe.get_bus()
        self.sig = None
        self.h = {}
        self.level = 1.0          # crossfade/pause fade multiplier
        self.uri = ""
        self.prerolled = False

    def build(self, cfg):
        self.pipe.set_state(Gst.State.NULL)
        flags = 0x2 | 0x100 | (0 if cfg.bit_perfect else 0x10)          # audio | buffering | soft-volume
        self.pipe.set_property("flags", flags)
        chain = Gst.parse_bin_from_description(chain_description(cfg), True)
        self.pipe.set_property("audio-filter", chain)
        self.pipe.set_property("audio-sink", make_sink(cfg.output_device, cfg.buffer_ms))
        self.h = {n: chain.get_by_name(n) for n in ("rg", "eq", "swap", "bs2b", "pan", "dyn", "fade",
                                                     "resamp", "outconv", "inconv", "spec", "lvl") if chain.get_by_name(n)}
        self.sig = chain_signature(cfg)
        self.level = 1.0
        self.prerolled = False
        self.apply_live(cfg)

    def apply_live(self, cfg):
        apply_chain_settings(self.h, cfg)
        self.set_fade(self.level)

    def set_fade(self, v):
        self.level = max(0.0, min(1.0, v))
        if "fade" in self.h:
            self.h["fade"].set_property("volume", self.level)

    def set_volume(self, v):
        self.pipe.set_property("volume", max(0.0, min(10.0, float(v))))

    def position(self) -> int:
        ok, pos = self.pipe.query_position(Gst.Format.TIME)
        return int(pos / Gst.MSECOND) if ok else 0

    def duration(self) -> int:
        ok, d = self.pipe.query_duration(Gst.Format.TIME)
        return int(d / Gst.MSECOND) if ok else 0

    def started(self) -> bool:
        return self.pipe.get_state(0)[1] != Gst.State.NULL

    def output_caps(self):
        pad = self.h["outconv"].get_static_pad("src") if "outconv" in self.h else None
        caps = pad.get_current_caps() if pad else None
        if not caps or caps.is_empty():
            return None
        s = caps.get_structure(0)
        ok_r, rate = s.get_int("rate")
        ok_c, ch = s.get_int("channels")
        return {"rate": rate if ok_r else 0, "channels": ch if ok_c else 0, "format": s.get_string("format") or ""}


class GstEngine(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(object)
    mediaStatusChanged = pyqtSignal(object)
    errorOccurred = pyqtSignal(str)
    advanced = pyqtSignal()          # the engine already moved on to the queued "next" track by itself
    notice = pyqtSignal(str)
    visData = pyqtSignal(object, object)     # (spectrum dB list | None, {'rms','peak'} | None) ~30x per second

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.decks = [Deck(0), Deck(1)]
        self.cur, self.alt = self.decks
        self._state = PS.StoppedState
        self._url = QUrl()
        self._next: QUrl | None = None
        self._next_same_album = False
        self._xf_planned = False
        self._xf_disabled = False
        self._xfading = False
        self._old: Deck | None = None
        self._preloaded: str | None = None
        self._gapless_pending: str | None = None
        self._pending_seek: int | None = None
        self._ramps: list[dict] = []
        self._ramp_last = 0.0
        self._volume = 0.8
        self._duration = 0
        self._last_pos = -1
        self._was_paused = False
        self.cur.build(cfg)
        self._timer = QTimer(self, interval=40, timeout=self._poll)
        self._ramp_timer = QTimer(self, interval=20, timeout=self._ramp_tick)
        for d in self.decks:
            d.pipe.connect("about-to-finish", self._about_to_finish, d)

    # ----------------------------------------------------------------- QMediaPlayer-like API
    def setSource(self, url: QUrl, crossfade=False):
        cfg = self.cfg
        if (crossfade and self._state == PS.PlayingState and cfg.crossfade_manual and cfg.crossfade_ms > 0
                and not cfg.bit_perfect and not self._xf_disabled and self._url.isValid()):
            self._begin_crossfade(url, min(cfg.crossfade_ms, 4000), auto=False)
            return
        self._hard_stop_all()
        self._prepare(self.cur)
        self.cur.uri = url.toString()
        self.cur.pipe.set_property("uri", self.cur.uri)
        self._url = url
        self._reset_tracking()
        self._set_state(PS.StoppedState)

    def play(self):
        if self._state == PS.PlayingState:
            return
        resume = self._state == PS.PausedState
        for d in self._live_decks():
            d.pipe.set_state(Gst.State.PLAYING)
        self._set_state(PS.PlayingState)
        self._timer.start()
        ms = self.cfg.fade_pause_ms
        if resume and ms > 0 and not self.cfg.bit_perfect:
            self.cur.set_fade(0.0)
            self._add_ramp(self.cur, lambda p: p, ms, always=True)
        elif self.cur.level < 1.0 and not self._xfading:
            self.cur.set_fade(1.0)

    def pause(self):
        if self._state != PS.PlayingState:
            return
        ms = self.cfg.fade_pause_ms
        self._set_state(PS.PausedState)
        if ms > 0 and not self.cfg.bit_perfect:
            start = self.cur.level
            self._add_ramp(self.cur, lambda p: start * (1 - p), ms, always=True, done=self._finish_pause)
        else:
            self._finish_pause()

    def stop(self):
        self._hard_stop_all()
        self._timer.stop()
        self._set_state(PS.StoppedState)
        self.positionChanged.emit(0)

    def preload_paused(self, ms=0):
        """Load the current source and sit paused at `ms` (restoring a session without making a sound)."""
        for d in self._live_decks():
            d.pipe.set_state(Gst.State.PAUSED)
        self._pending_seek = int(ms) if ms else None
        self._set_state(PS.PausedState)
        self._timer.start()

    def position(self) -> int:
        return self.cur.position()

    def setPosition(self, ms: int):
        if not self.cur.started():
            self._pending_seek = int(ms)
            return
        flags = Gst.SeekFlags.FLUSH | (Gst.SeekFlags.ACCURATE if self.cfg.seek_accurate else Gst.SeekFlags.KEY_UNIT)
        if not self.cur.pipe.seek_simple(Gst.Format.TIME, flags, int(ms) * Gst.MSECOND):
            self._pending_seek = int(ms)

    def duration(self) -> int:
        return self._duration

    def playbackState(self):
        return self._state

    # ----------------------------------------------------------------- queue lookahead
    def set_next(self, url: QUrl | None, same_album=False):
        self._next = url if (url is not None and url.isValid()) else None
        self._next_same_album = same_album
        cfg = self.cfg
        self._xf_planned = bool(self._next and cfg.crossfade_ms > 0 and not cfg.bit_perfect and not self._xf_disabled
                                and (cfg.crossfade_same_album or not same_album))
        self._preload_next()

    def _preload_next(self):
        """Pre-roll the next track on the idle deck when a crossfade is planned (also detects busy devices)."""
        if self._xfading:
            return
        want = self._next.toString() if (self._xf_planned and self._next) else None
        if want == self._preloaded:
            return
        alt = self.alt
        alt.pipe.set_state(Gst.State.NULL)
        self._preloaded = None
        if want:
            self._prepare(alt)
            alt.set_fade(1.0)
            alt.uri = want
            alt.pipe.set_property("uri", want)
            alt.prerolled = False
            alt.pipe.set_state(Gst.State.PAUSED)
            self._preloaded = want

    # ----------------------------------------------------------------- settings / volume
    def set_volume(self, v: float):
        self._volume = float(v)
        eff = 1.0 if self.cfg.bit_perfect else self._volume * 10 ** (self.cfg.eq_preamp / 20 if self.cfg.eq_enabled else 0)
        for d in self.decks:
            d.set_volume(eff)

    def apply_settings(self):
        cfg = self.cfg
        sig = chain_signature(cfg)
        for d in self.decks:
            if d.sig == sig:
                d.apply_live(cfg)
        if self.cur.sig != sig and not self._xfading:
            self._rebuild_cur()
        if self.alt.sig != sig and not self._xfading:
            self.alt.pipe.set_state(Gst.State.NULL)
            self._preloaded = None
        self.set_volume(self._volume)
        self.set_next(self._next, self._next_same_album)

    def _rebuild_cur(self):
        cur = self.cur
        pos, st = cur.position() if cur.started() else 0, self._state
        cur.pipe.set_state(Gst.State.NULL)
        cur.build(self.cfg)
        if self._url.isValid():
            cur.pipe.set_property("uri", cur.uri or self._url.toString())
            if pos:
                self._pending_seek = pos
            if st == PS.PlayingState:
                cur.pipe.set_state(Gst.State.PLAYING)
            elif st == PS.PausedState:
                cur.pipe.set_state(Gst.State.PAUSED)
        self.set_volume(self._volume)

    def _prepare(self, deck: Deck):
        if deck.sig != chain_signature(self.cfg):
            deck.pipe.set_state(Gst.State.NULL)
            deck.build(self.cfg)
            deck.set_volume(1.0 if self.cfg.bit_perfect else self._volume)

    def output_caps(self):
        return self.cur.output_caps() if self.cur.started() else None

    # ----------------------------------------------------------------- transitions
    def _about_to_finish(self, pipe, deck):
        """Streaming thread: queue the next URI on the same playbin => sample-accurate gapless."""
        nxt = self._next
        if deck is self.cur and not self._xfading and nxt is not None and self.cfg.gapless and not self._xf_planned:
            s = nxt.toString()
            pipe.set_property("uri", s)
            self._gapless_pending = s

    def _begin_crossfade(self, url: QUrl, ms: int, auto: bool):
        s = url.toString()
        alt = self.alt
        if self._preloaded != s:
            alt.pipe.set_state(Gst.State.NULL)
            self._prepare(alt)
            alt.uri = s
            alt.pipe.set_property("uri", s)
        alt.set_fade(0.0)
        alt.set_volume(1.0 if self.cfg.bit_perfect else self._volume)
        alt.pipe.set_state(Gst.State.PLAYING)
        old = self.cur
        self.cur, self.alt = alt, old
        self._old = old
        self._url = url
        self._preloaded = None
        self._gapless_pending = None
        self._reset_tracking()
        self._xfading = True
        curve = self.cfg.crossfade_curve
        fin = (lambda p: math.sin(p * math.pi / 2)) if curve == "equal-power" else (lambda p: p)
        fout = (lambda p: math.cos(p * math.pi / 2)) if curve == "equal-power" else (lambda p: 1 - p)
        self._add_ramp(alt, fin, ms)
        self._add_ramp(old, fout, ms, done=lambda: self._finish_crossfade(old))
        if auto:
            self._next, self._xf_planned = None, False       # consumed; the Player queues the following track
            self.advanced.emit()

    def _finish_crossfade(self, old: Deck):
        old.pipe.set_state(Gst.State.NULL)
        old.set_fade(1.0)
        self._xfading = False
        self._old = None
        self.cur.set_fade(1.0)
        self.apply_settings() if self.cur.sig != chain_signature(self.cfg) else self._preload_next()

    def _finish_pause(self):
        if self._state == PS.PausedState:
            for d in self._live_decks():
                d.pipe.set_state(Gst.State.PAUSED)

    def _live_decks(self):
        return [self.cur] + ([self._old] if self._xfading and self._old else [])

    def _hard_stop_all(self):
        self._ramps.clear()
        self._ramp_timer.stop()
        self._xfading = False
        self._old = None
        for d in self.decks:
            d.pipe.set_state(Gst.State.NULL)
            d.set_fade(1.0)
        self._preloaded = None
        self._gapless_pending = None

    # ----------------------------------------------------------------- ramps
    def _add_ramp(self, deck, fn, ms, always=False, done=None):
        self._ramps = [r for r in self._ramps if r["deck"] is not deck]
        self._ramps.append(dict(deck=deck, fn=fn, ms=max(1, ms), t=0.0, always=always, done=done))
        self._ramp_last = time.monotonic()
        if not self._ramp_timer.isActive():
            self._ramp_timer.start()

    def _ramp_tick(self):
        now = time.monotonic()
        dt = (now - self._ramp_last) * 1000
        self._ramp_last = now
        finished = []
        for r in self._ramps:
            if self._state != PS.PlayingState and not r["always"]:
                continue
            r["t"] += dt
            p = min(1.0, r["t"] / r["ms"])
            r["deck"].set_fade(r["fn"](p))
            if p >= 1.0:
                finished.append(r)
        for r in finished:
            if r in self._ramps:
                self._ramps.remove(r)
            if r["done"]:
                r["done"]()
        if not self._ramps:
            self._ramp_timer.stop()

    # ----------------------------------------------------------------- polling
    def _reset_tracking(self):
        self._duration = 0
        self._last_pos = -1

    def _set_state(self, st):
        if st != self._state:
            self._state = st
            self.playbackStateChanged.emit(st)

    def _query_duration(self):
        d = self.cur.duration()
        if d and d != self._duration:
            self._duration = d
            self.durationChanged.emit(d)

    def _poll(self):
        cur = self.cur
        # idle / fading-out deck: only watch for errors (busy device => crossfade impossible)
        other = self.alt if self.alt is not cur else None
        if other is not None:
            while (m := other.bus.pop()) is not None:
                if m.type == Gst.MessageType.ERROR and self._preloaded and not self._xfading:
                    err, _ = m.parse_error()
                    self._xf_disabled = True
                    other.pipe.set_state(Gst.State.NULL)
                    self._preloaded = None
                    self._xf_planned = False
                    self.notice.emit(f"Crossfade is unavailable on this output ({err.message}); using gapless.")
        mags = lv = None
        while (m := cur.bus.pop()) is not None:
            t = m.type
            if t == Gst.MessageType.ELEMENT:
                kind, val = parse_vis(m.get_structure())
                if kind == "spectrum": mags = val
                elif kind == "level": lv = val
                continue
            if t == Gst.MessageType.EOS:
                cur.pipe.set_state(Gst.State.NULL)
                self._timer.stop()
                self._set_state(PS.StoppedState)
                self.mediaStatusChanged.emit(MS.EndOfMedia)
                return
            if t == Gst.MessageType.ERROR:
                err, _ = m.parse_error()
                self._hard_stop_all()
                self._timer.stop()
                self._set_state(PS.StoppedState)
                self.errorOccurred.emit(err.message)
                return
            if t == Gst.MessageType.STREAM_START and m.src == cur.pipe and self._gapless_pending:
                self._gapless_pending = None
                self._url = self._next or self._url
                self._next, self._xf_planned = None, False
                self._reset_tracking()
                self._query_duration()
                self.advanced.emit()
            elif t in (Gst.MessageType.ASYNC_DONE, Gst.MessageType.DURATION_CHANGED):
                self._query_duration()
                if t == Gst.MessageType.ASYNC_DONE and self._pending_seek is not None and m.src == cur.pipe:
                    ms, self._pending_seek = self._pending_seek, None
                    flags = Gst.SeekFlags.FLUSH | (Gst.SeekFlags.ACCURATE if self.cfg.seek_accurate
                                                   else Gst.SeekFlags.KEY_UNIT)
                    cur.pipe.seek_simple(Gst.Format.TIME, flags, ms * Gst.MSECOND)
        if mags is not None or lv is not None:
            self.visData.emit(mags, lv)
        if self._state != PS.PlayingState:
            return
        if not self._duration:
            self._query_duration()
        pos = cur.position()
        if pos != self._last_pos:
            self._last_pos = pos
            self.positionChanged.emit(pos)
        # automatic crossfade: start when the remaining time reaches the fade length
        if self._xf_planned and self._next and not self._xfading and self._duration:
            ms = self.cfg.crossfade_ms
            if self._duration < ms * 2.2:                        # track too short to fade into: stay gapless
                self._xf_planned = False
                self._preload_next()
            elif self._duration - pos <= ms:
                self._begin_crossfade(self._next, ms, auto=True)

    def shutdown(self):
        self._timer.stop()
        self._ramp_timer.stop()
        for d in self.decks:
            d.pipe.set_state(Gst.State.NULL)


# --------------------------------------------------------------------------- facade
class _Volume:
    """Mimics the bit of QAudioOutput the Player uses."""
    def __init__(self, owner): self.o = owner
    def setVolume(self, v): self.o._volume = float(v); self.o._apply_volume()
    def volume(self): return self.o._volume


class Backend(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(object)
    mediaStatusChanged = pyqtSignal(object)
    errorOccurred = pyqtSignal(str)
    engineChanged = pyqtSignal(str)          # "qt" | "gst"
    advanced = pyqtSignal()
    notice = pyqtSignal(str)
    visData = pyqtSignal(object, object)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.qt = QMediaPlayer(self)
        self._qt_out = QAudioOutput(self); self.qt.setAudioOutput(self._qt_out)
        self.gst = GstEngine(cfg, self) if GST_OK else None
        self._want = "gst" if (cfg.engine == "auto" and self.gst) else "qt"
        self.active = self.gst if self._want == "gst" else self.qt
        self._volume = 0.8
        self._url = QUrl()
        self.out = _Volume(self)
        q = self.qt
        q.positionChanged.connect(lambda v: self._fwd(q, self.positionChanged, int(v)))
        q.durationChanged.connect(lambda v: self._fwd(q, self.durationChanged, int(v)))
        q.playbackStateChanged.connect(lambda s: self._fwd(q, self.playbackStateChanged, s))
        q.mediaStatusChanged.connect(lambda s: self._fwd(q, self.mediaStatusChanged, s))
        q.errorOccurred.connect(lambda e, msg: self._fwd(q, self.errorOccurred, msg))
        if self.gst:
            g = self.gst
            g.positionChanged.connect(lambda v: self._fwd(g, self.positionChanged, v))
            g.durationChanged.connect(lambda v: self._fwd(g, self.durationChanged, v))
            g.playbackStateChanged.connect(lambda s: self._fwd(g, self.playbackStateChanged, s))
            g.mediaStatusChanged.connect(lambda s: self._fwd(g, self.mediaStatusChanged, s))
            g.advanced.connect(lambda: self._fwd(g, self.advanced))
            g.notice.connect(self.notice)
            g.visData.connect(lambda a, b: self._fwd(g, self.visData, a, b))
            g.errorOccurred.connect(self._gst_error)

    @property
    def eq_available(self): return self.gst is not None

    @property
    def engine(self): return "gst" if self.active is self.gst else "qt"

    def _fwd(self, src, sig, *a):
        if src is self.active:
            sig.emit(*a)

    # --- player API
    def setSource(self, url, crossfade=False):
        self._url = url
        if self._want == "gst" and self.active is self.qt:       # recover from a one-track fallback
            self.qt.stop()
            self.active = self.gst
            self.engineChanged.emit("gst")
        if self.active is self.gst:
            self.gst.setSource(url, crossfade)
        else:
            self.qt.setSource(url)

    def set_next(self, url, same_album=False):
        if self.gst:
            self.gst.set_next(url, same_album)

    def play(self): self.active.play()
    def pause(self): self.active.pause()

    def preload_paused(self, ms=0):
        if self.active is self.gst:
            self.gst.preload_paused(ms)
        else:
            self.active.pause()
            if ms:
                self.active.setPosition(ms)
    def stop(self): self.active.stop()
    def position(self): return self.active.position()
    def setPosition(self, ms): self.active.setPosition(int(ms))
    def duration(self): return self.active.duration()
    def playbackState(self): return self.active.playbackState()

    def output_caps(self):
        return self.gst.output_caps() if self.active is self.gst else None

    # --- volume / settings
    def _apply_volume(self):
        self._qt_out.setVolume(min(1.0, self._volume))
        if self.gst:
            self.gst.set_volume(self._volume)

    def apply_settings(self):
        """Call after changing any audio field on the config."""
        want = "gst" if (self.cfg.engine == "auto" and self.gst) else "qt"
        if self.gst:
            self.gst.apply_settings()
        if want != self._want:
            self._want = want
            self._switch(want)

    def _switch(self, which):
        new = self.gst if which == "gst" else self.qt
        if new is None or new is self.active:
            return
        old = self.active
        state, pos = old.playbackState(), old.position()
        self.active = new           # silence the old engine's signals before stopping it
        old.stop()
        if self._url.isValid():
            if new is self.gst:
                new.setSource(self._url)
            else:
                new.setSource(self._url)
            if pos:
                (new.setPosition(pos) if new is self.gst else QTimer.singleShot(250, lambda: new.setPosition(pos)))
            if state == PS.PlayingState: new.play()
            elif state == PS.PausedState: new.pause()
        self.playbackStateChanged.emit(new.playbackState())
        self.engineChanged.emit(which)

    def _gst_error(self, msg):
        if self.active is not self.gst:
            return
        self._switch("qt")                  # play this track on the Qt engine instead
        if self._url.isValid():
            self.qt.play()
        self.notice.emit(f"GStreamer could not play this track ({msg}); used the standard engine for it.")

    def shutdown(self):
        if self.gst: self.gst.shutdown()
        self.qt.stop()
