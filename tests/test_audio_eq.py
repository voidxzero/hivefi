"""Audio engine: DSP maths, chain building, gapless, crossfade, live rebuilds, fallbacks."""
import math, os, shutil, subprocess, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from PyQt6.QtCore import QEventLoop, QTimer, QUrl
from PyQt6.QtWidgets import QApplication
from hivefi import audio
from hivefi.audio import GST_OK, Gst
from hivefi.config import Config

pytestmark = pytest.mark.skipif(not GST_OK or not shutil.which("ffmpeg"), reason="GStreamer + ffmpeg needed")
app = QApplication.instance() or QApplication([])


def wait(ms):
    l = QEventLoop(); QTimer.singleShot(ms, l.quit); l.exec()


def wait_for(cond, timeout_ms=8000):
    t = 0
    while not cond() and t < timeout_ms:
        wait(40); t += 40
    return cond()


# ------------------------------------------------------------------ pure maths
def test_eq_layouts_and_resampling():
    for n, freqs in audio.EQ_LAYOUTS.items():
        assert len(freqs) == n and freqs == sorted(freqs)
        assert audio.eq_bandwidth_hz(n, 1000) > 0
    assert audio.eq_bandwidth_hz(31, 1000) < audio.eq_bandwidth_hz(15, 1000) < audio.eq_bandwidth_hz(10, 1000)
    g = [6, 5, 4, 2, 0, 0, 0, 0, 0, -3]
    assert audio.resample_gains(g, 10, 10) == g
    g31 = audio.resample_gains(g, 10, 31)
    assert len(g31) == 31 and g31[0] == 6 and g31[-1] == -3 and max(g31) <= 6 and min(g31) >= -3
    back = audio.resample_gains(g31, 31, 10)
    assert all(abs(a - b) <= 1.0 for a, b in zip(back, g))             # round trip stays close


def test_chain_description_and_signature():
    c = Config()
    base = audio.chain_signature(c)
    for mut in (dict(eq_enabled=True), dict(eq_enabled=True, eq_bands=31), dict(mono=True), dict(swap_channels=True),
                dict(crossfeed="cmoy"), dict(limiter=True), dict(balance=0.5), dict(bit_perfect=True),
                dict(output_rate=96000), dict(output_depth="24"), dict(dither="tpdf"), dict(rg_mode="off"),
                dict(output_device="alsa:x"), dict(buffer_ms=300)):
        c2 = Config(); [setattr(c2, k, v) for k, v in mut.items()]
        assert audio.chain_signature(c2) != base, mut                    # structural -> rebuild
        Gst.parse_bin_from_description(audio.chain_description(c2), True)  # and it must be a valid pipeline
    for mut in (dict(eq_gains=[3.0] * 10), dict(rg_preamp=3.0), dict(resample_quality=10), dict(crossfade_ms=3000)):
        c2 = Config(); [setattr(c2, k, v) for k, v in mut.items()]
        assert audio.chain_signature(c2) == base, mut                    # live property change, no rebuild
    bp = Config(); bp.bit_perfect = True; bp.eq_enabled = True; bp.mono = True
    d = audio.chain_description(bp)
    assert "equalizer" not in d and "rgvolume" not in d and "volume name=fade" not in d and "audioconvert" in d


# ------------------------------------------------------------------ real DSP, measured
def measure(chain_cfg=None, eq=None, freq=1000, left_only=True, extra=""):
    """Per-channel RMS (dB) of a tone passed through the real chain description."""
    cfg = chain_cfg or Config()
    pre = "audiopanorama method=simple panorama=-1 ! " if left_only else ""
    p = Gst.parse_launch(f"audiotestsrc freq={freq} volume=0.1 num-buffers=40 samplesperbuffer=4410 ! "
                         f"audio/x-raw,rate=44100,channels=1 ! audioconvert ! audio/x-raw,channels=2 ! {pre}"
                         f"{audio.chain_description(cfg)} ! level interval=100000000 ! fakesink sync=false")
    if eq:
        cfg.eq_gains = eq
    handles = {n: p.get_by_name(n) for n in ("rg", "eq", "swap", "bs2b", "pan", "dyn", "resamp") if p.get_by_name(n)}
    audio.apply_chain_settings(handles, cfg)          # exactly what a live deck does
    p.set_state(Gst.State.PLAYING); bus = p.get_bus(); vals = []
    while True:
        m = bus.timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.ELEMENT | Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if m is None or m.type != Gst.MessageType.ELEMENT:
            assert m is None or m.type == Gst.MessageType.EOS, m.parse_error()
            break
        st = m.get_structure()
        if st.get_name() == "level":
            vals.append(st.get_value("rms"))
    p.set_state(Gst.State.NULL)
    mid = vals[10:30]
    out = []
    for ch in (0, 1):
        good = [v[ch] for v in mid if math.isfinite(v[ch]) and v[ch] > -200]
        out.append(sum(good) / len(good) if good else -200.0)
    return out


def test_dsp_mono_swap_balance():
    flat = measure()
    assert flat[0] > -30 and flat[1] < -100                              # left-only source is really left only
    c = Config(); c.mono = True
    m = measure(c); assert abs(m[0] - m[1]) < 0.5 and m[1] > -35         # mono: signal in both channels
    c = Config(); c.swap_channels = True
    s = measure(c); assert s[0] < -100 and s[1] > -30                    # swapped: right only
    c = Config(); c.balance = 1.0
    b = measure(c); assert b[0] < -100                                   # full right balance kills left
    c = Config(); c.balance = 0.5
    h = measure(c, left_only=False); assert h[0] < h[1] - 3              # half right: left clearly quieter
    c = Config(); c.balance = 0.0
    z = measure(c, left_only=False); assert abs(z[0] - z[1]) < 0.1       # centred: untouched


def test_dsp_equalizer_all_layouts():
    flat = measure(left_only=False)[0]
    for n in (10, 15, 31):
        c = Config(); c.eq_enabled = True; c.eq_bands = n
        idx = audio.EQ_LAYOUTS[n].index(1000)
        boost = [0.0] * n; boost[idx] = 12.0
        cut = [0.0] * n; cut[idx] = -12.0
        far = [0.0] * n; far[0] = 12.0
        assert measure(c, boost, left_only=False)[0] > flat + 9, n       # +12 dB at the tone's own band
        assert measure(c, cut, left_only=False)[0] < flat - 9, n
        assert abs(measure(c, far, left_only=False)[0] - flat) < 1.0, n  # a far-away band leaves it alone


def test_dsp_limiter_and_shelf_types():
    c = Config(); c.eq_enabled = True; c.eq_bands = 10; c.eq_shelf_ends = True
    top = [0.0] * 10; top[9] = 12.0
    hi = measure(c, top, freq=19000, left_only=False)[0]
    flat = measure(freq=19000, left_only=False)[0]
    assert hi > flat + 6                                                 # high shelf lifts everything above it
    base = measure(left_only=False)[0]
    c = Config(); c.limiter = True; c.limiter_threshold_db = -30         # tone peaks near -20 dBFS -> must be held down
    assert measure(c, left_only=False)[0] < base - 5
    c = Config(); c.limiter = True; c.limiter_threshold_db = -6          # far above the peaks -> transparent
    assert abs(measure(c, left_only=False)[0] - base) < 1.0


# ------------------------------------------------------------------ engine behaviour
@pytest.fixture(scope="module")
def tracks(tmp_path_factory):
    d = tmp_path_factory.mktemp("aud")
    out = []
    for i, (name, album) in enumerate((("a", "X"), ("b", "X"), ("c", "Y"))):
        p = d / f"{name}.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 * (i + 1)}:duration=3",
                        "-metadata", f"album={album}", str(p)], check=True)
        out.append(dict(id=i + 1, source="local", path=str(p), title=name, artist="A", album=album, album_artist="A",
                        duration=3.0, rg_gain=None, remote_id=None, genre="", year=0, track_no=i + 1, disc_no=1))
    return out


class Rec:
    def __init__(self, be):
        self.adv, self.eom, self.err, self.notes, self.pos = [], [], [], [], []
        be.advanced.connect(lambda: self.adv.append((len(self.pos), self.pos[-1] if self.pos else 0)))
        be.mediaStatusChanged.connect(lambda s: self.eom.append(s))
        be.errorOccurred.connect(self.err.append); be.notice.connect(self.notes.append)
        be.positionChanged.connect(self.pos.append)


def url(t): return QUrl.fromLocalFile(t["path"])


def new_backend(**cfg_over):
    cfg = Config()
    for k, v in cfg_over.items(): setattr(cfg, k, v)
    be = audio.Backend(cfg); be.out.setVolume(0.2)
    return cfg, be, Rec(be)


def test_gapless_transition(tracks):
    cfg, be, rec = new_backend(gapless=True, crossfade_ms=0)
    a, b = tracks[:2]
    be.setSource(url(a)); be.set_next(url(b), True); be.play()
    assert wait_for(lambda: rec.adv, 6000), "engine never advanced to the queued track"
    assert not rec.eom, "playback hit end-of-media: that would be an audible gap"
    wait(300)
    assert rec.pos[-1] < 1500                                            # position restarted for track B
    assert be.gst._url.toString().endswith("b.flac")
    be.shutdown()


def test_no_gapless_when_disabled(tracks):
    cfg, be, rec = new_backend(gapless=False, crossfade_ms=0)
    be.setSource(url(tracks[0])); be.set_next(url(tracks[1]), True); be.play()
    assert wait_for(lambda: rec.eom, 6000) and not rec.adv               # plain end-of-media; player loads the next
    be.shutdown()


def test_auto_crossfade_timing(tracks):
    cfg, be, rec = new_backend(crossfade_ms=1000, crossfade_same_album=True)
    be.setSource(url(tracks[0])); be.set_next(url(tracks[1]), True); be.play()
    assert be.gst._xf_planned and wait_for(lambda: be.gst._preloaded, 2000)   # next track is pre-rolled
    assert wait_for(lambda: rec.adv, 6000)
    last_pos = rec.adv[0][1]
    assert 1500 <= last_pos <= 2600, f"crossfade started at {last_pos} ms of a 3000 ms track (fade length 1000)"
    assert be.gst._xfading
    assert wait_for(lambda: not be.gst._xfading, 3000)
    assert be.gst.cur.level == 1.0 and not be.gst.alt.started()          # old deck released, new one at full level
    assert not rec.eom and not rec.err
    be.shutdown()


def test_same_album_stays_gapless_unless_asked(tracks):
    cfg, be, rec = new_backend(crossfade_ms=1000, crossfade_same_album=False)
    be.setSource(url(tracks[0])); be.set_next(url(tracks[1]), True)
    assert not be.gst._xf_planned                                        # gapless instead of crossfade
    be.set_next(url(tracks[2]), False)
    assert be.gst._xf_planned                                            # different album -> crossfade
    be.shutdown()


def test_manual_skip_crossfade(tracks):
    cfg, be, rec = new_backend(crossfade_ms=800, crossfade_manual=True)
    be.setSource(url(tracks[0])); be.play(); wait(500)
    be.setSource(url(tracks[2]), crossfade=True)
    assert be.gst._xfading and be.playbackState().name == "PlayingState"
    assert wait_for(lambda: not be.gst._xfading, 3000)
    assert be.gst.cur.uri.endswith("c.flac")
    cfg.crossfade_manual = False; be.apply_settings()
    be.setSource(url(tracks[1]), crossfade=True)
    assert not be.gst._xfading                                           # disabled -> hard cut
    be.shutdown()


def test_settings_rebuild_keeps_playing(tracks):
    cfg, be, rec = new_backend()
    be.setSource(url(tracks[0])); be.play(); wait(900)
    before = be.position(); assert before > 300
    cfg.eq_enabled = True; cfg.eq_bands = 31; cfg.eq_gains = [1.0] * 31; be.apply_settings(); wait(600)
    assert "eq" in be.gst.cur.h and be.gst.cur.h["eq"].get_property("num-bands") == 31
    assert be.playbackState().name == "PlayingState" and be.position() >= before - 100
    cfg.bit_perfect = True; be.apply_settings(); wait(500)
    assert "eq" not in be.gst.cur.h and "fade" not in be.gst.cur.h
    assert be.playbackState().name == "PlayingState" and not rec.err
    cfg.bit_perfect = False; cfg.eq_enabled = False; be.apply_settings(); wait(300)
    assert be.playbackState().name == "PlayingState"
    be.shutdown()


def test_every_dsp_option_plays(tracks):
    for over in (dict(mono=True), dict(swap_channels=True), dict(crossfeed="jmeier"), dict(limiter=True),
                 dict(balance=-0.4), dict(rg_mode="album", rg_preamp=2.0), dict(resample_quality=10, dither="tpdf",
                 noise_shaping="high", output_depth="16"), dict(eq_enabled=True, eq_bands=15, eq_shelf_ends=True)):
        cfg, be, rec = new_backend(**over)
        be.setSource(url(tracks[0])); be.play(); wait(600)
        assert be.playbackState().name == "PlayingState" and not rec.err and not rec.notes, over
        assert be.position() > 100, over
        be.shutdown()


def test_output_format_conversion(tracks):
    cfg, be, rec = new_backend(output_rate=48000, output_depth="float")
    be.setSource(url(tracks[0])); be.play(); wait(900)
    caps = be.output_caps()
    assert caps and caps["rate"] == 48000 and caps["format"] == "F32LE", caps
    be.shutdown()


def test_pause_fade_and_volume_curve(tracks):
    cfg, be, rec = new_backend(fade_pause_ms=300)
    be.setSource(url(tracks[0])); be.play(); wait(500)
    be.pause(); assert be.playbackState().name == "PausedState"          # UI state flips immediately
    assert wait_for(lambda: be.gst.cur.pipe.get_state(0)[1] == Gst.State.PAUSED, 1500)
    assert be.gst.cur.level < 0.05                                       # faded to silence before pausing
    be.play(); assert wait_for(lambda: be.gst.cur.level > 0.99, 1500)
    be.shutdown()
    from hivefi.player import curve_gain
    assert curve_gain(0.5, "linear") == 0.5 and curve_gain(0.5, "cubic") == 0.125
    assert curve_gain(0, "db") == 0 and abs(curve_gain(1, "db") - 1) < 1e-9 and 0.0009 < curve_gain(0.01, "db") < 0.0011


def test_fallback_to_qt_on_gstreamer_failure(tracks, tmp_path):
    cfg, be, rec = new_backend()
    bad = tmp_path / "broken.flac"; bad.write_bytes(b"not audio at all")
    be.setSource(QUrl.fromLocalFile(str(bad))); be.play()
    assert wait_for(lambda: rec.notes, 4000)
    assert "could not play" in rec.notes[0] and be.engine == "qt"
    be.setSource(url(tracks[0])); be.play()                              # next good track returns to GStreamer
    assert be.engine == "gst"
    wait(400); assert be.playbackState().name == "PlayingState"
    be.shutdown()


def test_player_lookahead_and_keyboard_flag(tracks):
    from hivefi.player import Player
    cfg = Config(); cfg.crossfade_ms = 1000
    pl = Player(lambda t: QUrl.fromLocalFile(t["path"]), cfg)
    pl.out.setVolume(0.1)
    pl.play_list(tracks, 0); wait(300)
    g = pl.mp.gst
    assert g._next.toString().endswith("b.flac") and not g._xf_planned   # same album -> gapless
    pl.next(); wait(300)
    assert pl.index == 1 and g._next.toString().endswith("c.flac") and g._xf_planned   # album change -> crossfade
    pl.set_repeat("one"); assert g._next.toString().endswith("b.flac")
    pl.set_repeat("off"); pl.remove([2]); assert g._next is None
    pl.enqueue([tracks[0]]); assert g._next.toString().endswith("a.flac")
    pl.mp.shutdown()


def test_config_migrations():
    import json, tempfile, pathlib
    p = pathlib.Path(tempfile.mktemp())
    p.write_text(json.dumps({"replaygain": False, "eq_user_presets": {"old": [1] * 10}}))
    c = Config.load(p)
    assert c.rg_mode == "off" and c.eq_user_presets["old"] == {"bands": 10, "gains": [1] * 10}
    p.write_text(json.dumps({"replaygain": True, "eq_bands": 31}))
    c = Config.load(p); assert c.rg_mode == "track" and len(c.eq_gains) == 31


@pytest.mark.skipif(not GST_OK, reason="GStreamer needed")
def test_analyser_results_wait_until_their_audio_is_heard():
    """The analysers run before the sound card's buffer (over a second of it on some setups): their results are held
    until the pipeline clock reaches each message's end time, so the visualizers are in step with the sound."""
    from types import SimpleNamespace
    S = Gst.SECOND // 1000
    vals = ", ".join("(float)-20.0" for _ in range(4))

    def msg(end):
        st = Gst.Structure.new_from_string(f"spectrum, endtime=(guint64){end}, magnitude=(float){{ {vals} }}")
        return Gst.Message.new_element(None, st)

    class Clock:
        t = 0
        def get_time(self): return self.t
    clock = Clock()
    pipe = SimpleNamespace(get_clock=lambda: clock, get_base_time=lambda: 0)
    cur, other = SimpleNamespace(pipe=pipe), SimpleNamespace(pipe=pipe)
    eng = SimpleNamespace(_state=audio.PS.PlayingState, _vis_q=[])
    due = lambda: audio.GstEngine._due_vis(eng, cur)
    eng._vis_q = [(cur, e * S, "spectrum", msg(e * S)) for e in (100, 500, 1200)] + [(other, 50 * S, "spectrum", msg(50 * S))]
    clock.t = 90 * S
    mags, lv = due()
    assert mags == [-20.0] * 4 and lv is None                 # the 100 ms one is due (10 ms early is fine)
    assert [e[1] // S for e in eng._vis_q] == [500, 1200]     # later ones wait; the other deck's are dropped
    clock.t = 400 * S
    assert due() == (None, None) and len(eng._vis_q) == 2
    clock.t = 1300 * S
    assert due()[0] is not None and eng._vis_q == []
    eng._vis_q = [(cur, 20000 * S, "spectrum", msg(20000 * S))]  # impossible (stale after a seek): dropped, not shown
    assert due() == (None, None) and eng._vis_q == []
    eng._vis_q = [(cur, 4000 * S, "spectrum", msg(4000 * S))]    # a big output buffer (2.7 s ahead): still waits
    assert due() == (None, None) and len(eng._vis_q) == 1
    eng._state = audio.PS.PausedState                          # paused: the sink keeps its buffer, so does the queue
    assert due() == (None, None) and len(eng._vis_q) == 1
    eng._state = audio.PS.PlayingState
    pipe.get_clock = lambda: None                              # no clock at all: passed on at once, as before
    assert due()[0] is not None
