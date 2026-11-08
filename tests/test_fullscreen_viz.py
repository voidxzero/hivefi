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
