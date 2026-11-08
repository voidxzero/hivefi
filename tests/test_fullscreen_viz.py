"""Full-screen visualizer: effects, beat detection, feedback warp, window controls and integration."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import random
import time

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from hivefi import audio, vizfx
from hivefi.config import Config
from tests.test_gui import win, wait  # noqa: F401  (fixture)

app = QApplication.instance() or QApplication([])
np = vizfx.np


def loud(a, level=-20.0, t=0.033):
    a.feed([random.uniform(-60, -10) for _ in range(512)], {"rms": [level, level], "peak": [-5, -5]})
    a.update(t)


def test_audio_bands_and_beat_detection():
    a = vizfx.Audio()
    for _ in range(40):                                   # silence: nothing moves, no beats
        a.feed([-80.0] * 512, {"rms": [-80, -80], "peak": [-80, -80]}); a.update(0.033)
    assert a.bass < 0.02 and not a.beat and a.beat_count == 0
    beats = 0
    for i in range(120):                                  # a kick drum every 15 frames (~0.5 s)
        mags = [-80.0] * 512
        if i % 15 == 0:
            for k in range(0, 30):
                mags[k] = -8.0
        a.feed(mags, {"rms": [-15, -15], "peak": [-5, -5]}); a.update(0.033)
        beats += a.beat
    assert 5 <= beats <= 9                                # one per kick, not one per frame
    assert a.level > 0.5
    for _ in range(60):                                   # no data for a while: relaxes to silence
        a.update(0.033)
    assert a.bass < 0.05 and a.level < 0.1
    assert len(a.wave(50)) == 50 and all(-1 <= v <= 1 for v in a.wave(50))


@pytest.mark.skipif(np is None, reason="numpy needed")
def test_feedback_warp_geometry():
    fb = vizfx.Feedback(64, 48)
    cx, cy = 31, 23
    fb.buf[cy, cx + 10, :3] = 200                         # one bright dot right of the centre
    fb.warp(zoom=2.0, decay=1.0)                          # zoom > 1 grows the picture outward
    ys, xs = np.nonzero(fb.buf[..., 0])
    assert len(xs) and abs(xs.mean() - (cx + 20)) <= 2 and abs(ys.mean() - cy) <= 2
    fb.clear(); fb.buf[cy, cx + 10, :3] = 200
    fb.warp(zoom=1.0, decay=0.5)                          # decay halves the brightness, nothing moves
    assert fb.buf[cy, cx + 10, 0] == 100
    fb.clear(); fb.buf[cy, cx + 10, :3] = 200
    fb.warp(rot=3.14159, decay=1.0)                       # a half turn puts it on the other side of the centre
    ys, xs = np.nonzero(fb.buf[..., 0])
    assert abs(xs.mean() - (cx - 10)) <= 2
    fb.clear(); fb.buf[cy, cx + 10, :3] = 200
    fb.warp(mirror=True, decay=1.0)
    ys, xs = np.nonzero(fb.buf[..., 0])
    assert len(set(xs.tolist())) >= 2                     # mirrored: the dot appears on both sides
    fb.clear(); fb.buf[10:20, 10:20, :3] = 255
    fb.warp(decay=0.0); assert fb.buf[..., :3].max() == 0


@pytest.mark.parametrize("cls", vizfx.effect_classes())
def test_every_effect_draws_and_stays_fast(cls):
    a = vizfx.Audio()
    e = cls()
    img = QImage(640, 360, QImage.Format.Format_RGB32); img.fill(0)
    fb = vizfx.Feedback(320, 180) if e.family == "milkdrop" else None
    blank = img.copy(); times = []
    for _ in range(45):
        loud(a)
        t0 = time.perf_counter()
        if fb:
            e.paint_into(fb, a)
            p = QPainter(img); p.drawImage(img.rect(), fb.image); p.end()
        else:
            img.fill(0); p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing); e.paint(p, 640, 360, a); p.end()
        times.append(time.perf_counter() - t0)
    assert img != blank, e.name                           # it drew something
    assert sorted(times)[len(times) // 2] < 0.08, (e.name, times)         # median frame well under 80 ms
    for _ in range(80):                                   # and silence does not crash or leave the screen full
        a.feed([-80.0] * 512, {"rms": [-80, -80], "peak": [-80, -80]}); a.update(0.033)
        if fb:
            e.paint_into(fb, a)
        else:
            img.fill(0); p = QPainter(img); e.paint(p, 640, 360, a); p.end()


def test_effect_names_and_families():
    classes = vizfx.effect_classes()
    names = [c.name for c in classes]
    assert len(names) == len(set(names)) and {"Bars", "Scope", "Ambience", "Battery", "Particles", "Spikes",
                                              "Starfield"} <= set(names)
    if np is not None:
        assert len([c for c in classes if c.family == "milkdrop"]) >= 8 and "Tunnel Vision" in names
    assert [c.name for c in vizfx.effect_classes(with_numpy=False)] == [c.name for c in vizfx.CLASSIC]


def test_window_controls():
    cfg = Config()
    from hivefi.ui.viz_fullscreen import FullscreenVisualizer
    fs = FullscreenVisualizer(cfg); closed = []
    fs.closed.connect(lambda: closed.append(1)); changed = []
    fs.effectChanged.connect(changed.append)
    fs.resize(800, 450); fs.start(); wait(150)
    assert fs.auto and fs.cycle == 30 and fs.timer.isActive()
    first = fs.effect.name
    QTest.keyClick(fs, Qt.Key.Key_Right); assert fs.effect.name != first and not fs.auto and cfg.vis_full_effect == fs.effect.name
    QTest.keyClick(fs, Qt.Key.Key_Left); assert fs.effect.name == first
    QTest.keyClick(fs, Qt.Key.Key_Space); assert fs.auto and cfg.vis_full_effect == "auto"
    QTest.keyClick(fs, Qt.Key.Key_Up); assert fs.cycle == 45 and cfg.vis_full_cycle == 45
    QTest.keyClick(fs, Qt.Key.Key_Down); QTest.keyClick(fs, Qt.Key.Key_Down); assert fs.cycle == 15
    QTest.keyClick(fs, Qt.Key.Key_O); assert not fs.overlay
    assert fs.select("Bars") and fs.effect.name == "Bars" and not fs.auto and not fs.select("Nope")
    assert changed
    # automatic cycling: every effect gets a turn before any repeats
    fs.set_auto(True, 5); seen = set()
    for _ in range(len(fs.effects)):
        fs._since_switch = 6; fs._tick(); seen.add(fs.effect.name)
    assert len(seen) >= len(fs.effects) - 1
    fs.set_track({"artist": "A", "title": "T"}); assert fs.track_text == "A – T"
    for name in fs.names():                               # paint every effect through the real widget
        fs.select(name); loud(fs.audio); fs.audio.feed([-20.0] * 512, {"rms": [-20, -20]}); fs.grab()
    QTest.keyClick(fs, Qt.Key.Key_Escape); wait(50)
    assert closed == [1] and not fs.timer.isActive()


def test_double_click_closes_and_cursor_hides():
    from hivefi.ui.viz_fullscreen import FullscreenVisualizer
    fs = FullscreenVisualizer(Config()); fs.resize(400, 300); fs.start(); wait(100)
    fs._cursor_at -= 5; fs._tick()
    assert fs.cursor().shape() == Qt.CursorShape.BlankCursor
    QTest.mouseMove(fs, QPoint(20, 20)); fs.mouseMoveEvent(type("E", (), {})())
    assert fs.cursor().shape() == Qt.CursorShape.ArrowCursor
    QTest.mouseDClick(fs, Qt.MouseButton.LeftButton); wait(50)
    assert not fs.isVisible()


def test_integration_with_the_window(win):
    w = win
    fs = w.open_fullscreen_vis(); wait(300)
    assert fs is not None and fs.isVisible() and w.cfg.vis_active                       # analysers switched on
    assert w.open_fullscreen_vis() is fs                                                # a second request reuses it
    w.player.mp.visData.emit([-20.0] * 512, {"rms": [-12, -12], "peak": [-3, -3]}); wait(100)
    assert max(fs.audio.raw) > 0.5 and fs.audio.level > 0.3                             # the engine's data reaches it
    w.player.play_list(w.db.all_tracks("local"), 0); wait(300)
    assert fs.track_text and " – " in fs.track_text
    w._set_full("vis_full_effect", "Bars"); assert fs.effect.name == "Bars" and not fs.auto
    w._set_full("vis_full_effect", "auto"); assert fs.auto
    w._set_full("vis_full_cycle", 60); assert fs.cycle == 60 and w.cfg.vis_full_cycle == 60
    fs.close(); wait(200)
    assert w.fs_vis is None and not w.cfg.vis_active                                    # back to off when no panel is open
    # Fullscreen menu lists every effect and the options
    from PyQt6.QtWidgets import QMenu
    m = QMenu(); w._fill_fullscreen_menu(m)
    texts = [a.text() for a in m.actions()]
    assert "Auto (cycle through all)" in texts and any("Bars" in t for t in texts) and any("Tunnel Vision" in t or np is None for t in texts)
    # it refuses politely when the analysers are not available
    w.cfg.bit_perfect = True
    assert w.open_fullscreen_vis() is None and "GStreamer" in w.statusBar().currentMessage()
    w.cfg.bit_perfect = False
    # the visualizer panel can ask for it too
    got = []
    w.vis.fullscreenRequested.connect(lambda: got.append(1)); w.vis.mouseDoubleClickEvent(None)
    assert got == [1]
    # leaving with the dock open keeps the analysers on
    w.dock_vis.show(); wait(100)
    fs2 = w.open_fullscreen_vis(); wait(200); fs2.close(); wait(200)
    assert w.cfg.vis_active


@pytest.mark.skipif(np is None, reason="numpy needed")
def test_switching_effect_clears_the_screen():
    from hivefi.ui.viz_fullscreen import FullscreenVisualizer
    fs = FullscreenVisualizer(Config()); fs.resize(640, 360); fs.show()
    def run(frames=60):
        for _ in range(frames):
            fs.audio.feed([-12.0] * 512, {"rms": [-12, -12], "peak": [-3, -3]}); fs.audio.update(0.033); fs.grab()
    fs.select("Tunnel Vision"); run()
    assert fs._fb is not None and fs._fb.buf[..., :3].max() > 40                       # the feedback buffer is full of picture
    fs.select("Spiral Galaxy")                                                         # another feedback effect
    assert fs._fb.buf[..., :3].max() == 0                                              # ... starts from black, not a faded echo
    run(); assert fs._fb.buf[..., :3].max() > 40
    fs.select("Bars"); fs.select("Tunnel Vision")                                      # via a classic one and back
    assert fs._fb.buf[..., :3].max() == 0
    # classic effects forget their particles / rings / sparks / stars when they are chosen again
    fs.select("Particles"); run()
    assert fs.effect.ps
    fs.select("Battery"); run(); assert fs.effect.rings
    fs.select("Particles"); assert fs.effect.ps == []
    fs.select("Battery"); assert fs.effect.rings == []
    fs.select("Fireworks")
    for i in range(90):                                                                # a kick every 15 frames makes beats
        mags = [-80.0] * 512
        if i % 15 == 0:
            mags[:40] = [-8.0] * 40
        fs.audio.feed(mags, {"rms": [-15, -15], "peak": [-4, -4]}); fs.audio.update(0.033); fs.grab()
    assert fs.effect.sparks
    fs.next_effect(1); fs.next_effect(-1); assert fs.effect.name == "Fireworks" and fs.effect.sparks == []
    # automatic cycling clears too
    fs.set_auto(True, 5); fs.select("Tunnel Vision"); fs.set_auto(True, 5); run()
    fs._since_switch = 6; fs._tick()
    assert fs._fb.buf[..., :3].max() == 0 or fs.effect.family != "milkdrop"
    names = fs.names(); fs.select(names[0]); assert len(fs.effects) == len(names)       # the list itself is unchanged
    fs.close()


def test_hostile_numbers_never_reach_the_painters():
    """NaN / infinite / absurd values from the analysers or a zero-sized window used to raise inside the effects."""
    nan, inf = float("nan"), float("inf")
    a = vizfx.Audio()
    a.feed([nan, inf, -inf] * 170 + [0.0, 1e30], {"rms": [nan, inf], "peak": [nan, nan]}); a.update(nan)
    assert all(0.0 <= v <= 1.0 for v in a.raw) and 0.0 <= a.level <= 1.0 and a.dt == 0.033
    for dt in (nan, inf, -5.0, 1e9, 0.0):
        a.update(dt); assert 0.0 <= a.dt <= 0.25 and a.t == a.t and abs(a.t) < 1e6
    assert vizfx.hsv(nan, nan, nan, nan).isValid() and vizfx.hsv(inf, -inf, inf, inf).alpha() == 255
    assert vizfx.hsv(0.5, 0.5, 0.5, -20).alpha() == 0 and vizfx.hsv(0.5, 0.5, 0.5, 9999).alpha() == 255
    img = QImage(64, 64, QImage.Format.Format_RGB32)
    for cls in vizfx.CLASSIC:
        for w, h in ((0, 0), (1, 1), (200, 0), (0, 100), (320, 180)):
            e = cls()
            for beat in (False, True):
                a.beat = beat
                p = QPainter(img)
                try:
                    e.paint(p, w, h, a); e.paint(p, w, h, a)
                finally:
                    p.end()


def test_a_broken_effect_cannot_take_the_program_down(capsys, monkeypatch):
    from hivefi.ui.viz_fullscreen import FullscreenVisualizer
    fs = FullscreenVisualizer(Config()); fs.resize(400, 300); failed = []
    fs.effectFailed.connect(lambda n, m: failed.append((n, m)))
    fs.auto = False
    fs.select("Battery")
    def boom(*a, **k):
        raise ZeroDivisionError("division by zero")
    fs.effect.paint = boom
    fs.grab()                                                  # used to abort the whole application
    assert failed and failed[0][0] == "Battery" and "ZeroDivisionError" in failed[0][1]
    assert "Battery" in fs.broken and fs.effect.name != "Battery"
    assert "ZeroDivisionError" in capsys.readouterr().err        # the traceback is still printed for the bug report
    n = len(failed)
    for _ in range(5):
        fs.grab()                                              # the next effect keeps painting
    assert len(failed) == n
    fs.set_auto(True, 5); seen = set()
    for _ in range(3 * len(fs.effects)):
        fs._since_switch = 6; fs._tick(); seen.add(fs.effect.name)
    assert "Battery" not in seen and len(seen) >= 3            # automatic cycling avoids the broken effect
    # everything broken: no crash, no endless switching
    for cls in vizfx.effect_classes():                         # (effects are re-created when chosen: patch the classes)
        monkeypatch.setattr(cls, "paint", boom, raising=False)
        if cls.family == "milkdrop":
            monkeypatch.setattr(cls, "paint_into", boom)
    for _ in range(len(fs.effects) + 3):
        fs.grab()
    assert len(fs.broken) == len(fs.effects)
    fs.grab()
    # a window with no size paints nothing and does not fail
    z = FullscreenVisualizer(Config()); z.resize(0, 0); z.grab(); assert not z.broken


@pytest.mark.skipif(np is None, reason="numpy needed")
def test_feedback_buffer_is_the_images_own_memory():
    """Painting with QPainter and warping with numpy must act on the same pixels (a copy broke the feedback loop)."""
    from PyQt6.QtGui import QColor
    fb = vizfx.Feedback(64, 48)
    p = QPainter(fb.image); p.fillRect(40, 23, 2, 2, QColor(200, 100, 50)); p.end()
    assert tuple(fb.buf[23, 40][:3]) == (50, 100, 200)                                 # numpy sees what QPainter drew (BGR)
    fb.warp(zoom=2.0, decay=1.0)
    assert fb.image.pixelColor(40, 23).name() == "#000000"                             # the drawn dot moved away ...
    assert fb.image.pixelColor(50, 23).name() == "#c86432"                             # ... and the image shows where it went
    fb.clear(); assert fb.image.pixelColor(50, 23).name() == "#000000"
    p = QPainter(fb.image); p.fillRect(0, 0, 5, 5, QColor(255, 255, 255)); p.end()
    for _ in range(3):
        fb.warp(decay=0.5)
    assert fb.image.pixelColor(2, 2).red() in (31, 32)                                  # three halvings of 255
    assert fb.image.pixelColor(2, 2).alpha() == 255


@pytest.mark.skipif(np is None, reason="numpy needed")
def test_feedback_effects_really_feed_back(tmp_path):
    """A shape drawn once leaves a trail that keeps moving (zoom) and fading after the music stops."""
    a = vizfx.Audio()
    e = vizfx.TunnelVision(); fb = vizfx.Feedback(160, 90)
    for _ in range(20):
        a.feed([-10.0] * 512, {"rms": [-10, -10], "peak": [-3, -3]}); a.update(0.033); e.paint_into(fb, a)
    lit = int((fb.buf[..., :3].max(axis=2) > 20).sum())
    assert lit > 500
    for _ in range(3):                                   # silence: nothing new is drawn, the old picture keeps warping
        a.update(0.033)                                  # (no data arrives, so the bars relax)
    before = fb.buf[..., :3].copy()
    fb.warp(**e.motion(a.t, a))
    assert not np.array_equal(before, fb.buf[..., :3])   # it moved
    for _ in range(80):
        a.update(0.033); fb.warp(**e.motion(a.t, a))
    assert fb.buf[..., :3].max() < 25                    # and faded away
