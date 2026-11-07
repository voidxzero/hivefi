"""Panels: seek bar (click to seek, waveform), visualizer, lock/layouts, session + layout persistence."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import math
import random
import shutil
import subprocess
import sys

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from hivefi import audio, session, waveform
from hivefi.config import Config
from hivefi.db import Database
from hivefi.ui import seekbar, visualizer
from tests.test_gui import win, wait  # noqa: F401  (fixture)

app = QApplication.instance() or QApplication([])
need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def click(w, x, y=5, kind=QEvent.Type.MouseButtonPress):
    btn = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    ev = QMouseEvent(kind, QPointF(x, y), QPointF(w.mapToGlobal(QPoint(int(x), int(y)))), Qt.MouseButton.LeftButton,
                     btn if kind != QEvent.Type.MouseMove else Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: w.mousePressEvent, QEvent.Type.MouseMove: w.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: w.mouseReleaseEvent}[kind](ev)


# ------------------------------------------------------------------ seek bar
@pytest.mark.parametrize("style", list(seekbar.STYLES))
def test_click_anywhere_seeks(style):
    s = seekbar.SeekBar(style); s.resize(500, 60); s.setRange(0, 200_000); got = []
    s.sliderMoved.connect(got.append)
    pad = 8 if style == "slider" else 0
    for frac in (0.0, 0.25, 0.5, 0.9, 1.0):
        got.clear()
        click(s, pad + frac * (500 - 2 * pad))
        assert got and abs(got[-1] - frac * 200_000) < 1500, (style, frac, got)
        click(s, pad + frac * (500 - 2 * pad), kind=QEvent.Type.MouseButtonRelease)
        assert not s.isSliderDown()
    click(s, -50); assert got[-1] == 0                   # outside the bar clamps instead of misbehaving
    click(s, 9999); assert got[-1] == 200_000
    s.grab()                                             # every style paints


def test_seek_drag_is_throttled_and_final_value_sent():
    s = seekbar.SeekBar("waveform"); s.resize(400, 54); s.setRange(0, 100_000); got = []
    s.sliderMoved.connect(got.append)
    click(s, 10)
    for x in range(11, 300):
        click(s, x, kind=QEvent.Type.MouseMove)
    assert len(got) < 60                                  # not one engine seek per pixel
    click(s, 300, kind=QEvent.Type.MouseButtonRelease)
    assert abs(got[-1] - 75_000) < 600 and s.value() == got[-1]


def test_waveform_paints_peaks_and_progress():
    s = seekbar.SeekBar("waveform"); s.resize(300, 54); s.setRange(0, 1000)
    s.set_peaks([255] * 100 + [20] * 100)
    s.setValue(500)
    img = s.grab().toImage()
    loud_top, quiet_top = img.pixelColor(50, 6), img.pixelColor(250, 6)
    assert loud_top != quiet_top                          # a tall bar reaches the top where the signal is loud
    assert img.pixelColor(250, 27) != quiet_top           # the quiet half still draws its short centre bar


@need_ffmpeg
def test_peaks_from_audio(tmp_path):
    f = tmp_path / "w.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=f=440:d=4,volume='if(lt(t,2),1,0.05)':eval=frame", str(f)], check=True)
    peaks = waveform.decode_peaks(str(f), 100)
    assert len(peaks) == 100 and max(peaks) == 255
    assert sum(peaks[:40]) / 40 > 4 * (sum(peaks[60:]) / 40)             # first half loud, second quiet
    t = {"source": "local", "path": str(f)}
    assert waveform.get_peaks(t) == waveform.get_peaks(t) and waveform.cache_file(waveform.track_key(t)).exists()
    assert waveform.get_peaks({"source": "remote", "remote_id": "1"}, api=None) is None


def test_reduce_peaks():
    from array import array
    assert waveform.reduce_peaks(array("h", [0, 0, 100, -200, 0, 50]), 3) == [0, 255, 84]
    assert waveform.reduce_peaks(array("h"), 10) == []


# ------------------------------------------------------------------ visualizer
def test_spectrum_to_bars_log_axis():
    mags = [-80.0] * 512
    mags[int(1000 / 22050 * 512)] = -10.0                 # a 1 kHz tone
    bars = visualizer.to_bars(mags, 48)
    assert len(bars) == 48 and max(bars) > 0.85
    peak = bars.index(max(bars))
    f = visualizer.F_LO * (visualizer.F_HI / visualizer.F_LO) ** ((peak + 0.5) / 48)
    assert 700 < f < 1400                                  # the peak lands on the 1 kHz bar
    assert visualizer.to_bars([], 8) == [0.0] * 8


@pytest.mark.parametrize("style", list(visualizer.STYLES))
def test_visualizer_styles_paint_and_move(style):
    v = visualizer.VisualizerPanel(style, 32); v.resize(300, 160); v.show()
    blank = v.grab().toImage()
    for _ in range(8):
        v.feed([random.uniform(-60, -5) for _ in range(512)], {"rms": [-18, -22], "peak": [-4, -8]}); v._tick()
    img = v.grab().toImage()
    assert img != blank                                    # data changes the picture
    v.feed([-80.0] * 512, {"rms": [-80, -80], "peak": [-80, -80]})
    for _ in range(60):
        v._tick()
    assert max(v._bars) < 0.05                             # decays back to silence
    v.available = False; v.note = "nope"; v.grab()


def test_parse_gst_messages():
    if not audio.GST_OK:
        pytest.skip("GStreamer needed")
    s = audio.Gst.Structure.from_string("spectrum, magnitude=(float){ -31.5, -40, -10 }")[0]
    assert audio.parse_vis(s) == ("spectrum", [-31.5, -40.0, -10.0])
    assert audio._floats("magnitude=(float){ -31.5, -inf, 1e-05, nan }", "magnitude") == [-31.5, -200.0, 1e-05, -200.0]
    assert audio._floats("rms=(double)< -20.5, -21 >, peak=(double)< -3 >", "rms") == [-20.5, -21.0]
    s = audio.Gst.Structure.from_string("level, rms=(double){ -20.5, -21 }, peak=(double){ -3, -4.5 };")[0]
    assert audio.parse_vis(s) == ("level", {"rms": [-20.5, -21.0], "peak": [-3.0, -4.5]})
    assert audio.parse_vis(audio.Gst.Structure.from_string("other, a=(int)1")[0]) == (None, None)


@need_ffmpeg
def test_engine_delivers_spectrum(tmp_path):
    if not audio.GST_OK:
        pytest.skip("GStreamer needed")
    f = tmp_path / "t.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=1000:d=3", str(f)], check=True)
    cfg = Config(); cfg.vis_active = True
    from PyQt6.QtCore import QUrl
    be = audio.Backend(cfg); got = []
    be.visData.connect(lambda m, l: got.append((m, l)))
    be.setSource(QUrl.fromLocalFile(str(f))); be.play()
    for _ in range(100):
        wait(50)
        if any(m for m, _l in got) and any(l for _m, l in got):
            break
    be.stop(); be.shutdown()
    mags = next(m for m, _l in got if m)
    assert len(mags) == audio.VIS_BANDS
    peak_bin = max(range(len(mags)), key=mags.__getitem__)
    assert abs(peak_bin * 22050 / len(mags) - 1000) < 120                  # the analyser sees the 1 kHz tone
    assert any(l and l["rms"] and l["rms"][0] > -40 for _m, l in got)       # and the level meter sees signal


def test_visualizer_chain_options():
    c = Config()
    assert "spectrum name=spec" in audio.chain_description(c)
    c.vis_enabled = False
    assert "spectrum" not in audio.chain_description(c)
    c.vis_enabled = True; c.bit_perfect = True
    assert "spectrum" not in audio.chain_description(c)                    # bit-perfect stays untouched
    c2 = Config(); c2.vis_active = True
    assert audio.chain_signature(c2) == audio.chain_signature(Config())    # toggling visibility must not rebuild


# ------------------------------------------------------------------ window: panels, layouts, session
def test_panels_layout_and_lock(win):
    w = win
    names = {tb.objectName() for tb in w.toolbars}
    assert {"tb_controls", "tb_seek", "tb_volume", "tb_search", "tb_mode"} <= names
    assert w.seek.parent() is not None and w.dock_vis.windowTitle() == "Visualizer"
    # panels can be moved: put the seek bar toolbar at the bottom and the controls on the left
    w.addToolBar(Qt.ToolBarArea.BottomToolBarArea, next(t for t in w.toolbars if t.objectName() == "tb_seek"))
    w.addToolBar(Qt.ToolBarArea.LeftToolBarArea, next(t for t in w.toolbars if t.objectName() == "tb_controls"))
    assert w.toolBarArea(next(t for t in w.toolbars if t.objectName() == "tb_seek")) == Qt.ToolBarArea.BottomToolBarArea
    # edit mode off (the default): nothing can be moved
    assert not w.edit_mode
    w._set_edit_mode(True)
    assert all(tb.isMovable() for tb in w.toolbars) and w.dock_sources.titleBarWidget() is None
    w._set_edit_mode(False)
    assert all(not tb.isMovable() for tb in w.toolbars) and w.dock_sources.titleBarWidget() is not None
    from PyQt6.QtWidgets import QDockWidget
    assert all(d.features() == QDockWidget.DockWidgetFeature.NoDockWidgetFeatures for d in w.findChildren(QDockWidget))
    assert not w.main_split.handle(1).isEnabled() and not w.browser_split.handle(1).isEnabled()
    w._set_edit_mode(True)
    # named layouts
    snap = w._layout_snapshot()
    w.cfg.layouts["mine"] = snap
    w._apply_builtin_layout("Minimal (controls only)")
    assert not w.dock_sources.isVisible()
    w._apply_layout_snapshot(w.cfg.layouts["mine"])
    assert w.toolBarArea(next(t for t in w.toolbars if t.objectName() == "tb_seek")) == Qt.ToolBarArea.BottomToolBarArea
    w._apply_builtin_layout("Everything")
    assert w.dock_vis.toggleViewAction().isChecked() and w.dock_sources.toggleViewAction().isChecked()


def test_seek_style_and_waveform_in_window(win):
    w = win
    local = w.db.all_tracks("local")
    w._set_seek_style("waveform")
    assert w.cfg.seek_style == "waveform" and w.seek.style_name() == "waveform"
    w.player.play_list(local, 0); wait(2500)
    assert w.seek._peaks and len(w.seek._peaks) > 10                       # computed in the background
    w._set_seek_style("slider")
    assert w.seek.style_name() == "slider" and w.cfg.seek_style == "slider"
    # remote tracks are not downloaded for analysis unless asked
    remote = w.db.all_tracks("remote")
    w._set_seek_style("waveform"); w.player.play_list(remote[:1], 0); wait(500)
    assert w.seek._peaks is None


def test_visualizer_dock_toggles_analysers(win):
    w = win
    w.dock_vis.show(); wait(100)
    assert w.cfg.vis_active
    w.dock_vis.hide(); wait(100)
    assert not w.cfg.vis_active
    w.cfg.bit_perfect = True; w._update_vis_availability(); assert not w.vis.available
    w.cfg.bit_perfect = False; w._update_vis_availability()


def test_session_roundtrip(win, tmp_path):
    w = win
    local = w.db.all_tracks("local")
    w.search.setText(""); w.source = ("local", None); w.refresh_playlists(); w.load_source()
    w.player.play_list(local, 1); wait(600)
    w.player.seek(800); wait(500)
    w.player.toggle(); wait(300)                                           # paused
    pos = w.player.mp.position()
    w._save_session()
    saved = session.load()
    assert saved["source"] == ["local", None] and saved["index"] == 1 and len(saved["queue"]) == 2
    assert saved["playing"] is False and abs(saved["pos"] - pos) < 100
    # a second window "starts up" and lands in the same place
    from hivefi.ui.main_window import MainWindow
    cfg2 = Config(); cfg2._path = w.cfg._path; cfg2.library_dirs = w.cfg.library_dirs
    cfg2.server_url = w.cfg.server_url; cfg2.username, cfg2.password = "u", "pw"; cfg2.show_tray = False
    cfg2.sync_on_start = False
    w.player.stop(); w.player.mp.shutdown()
    w2 = MainWindow(cfg2, Database(tmp_path / "l.db")); w2.show(); wait(1500)
    try:
        assert w2.source == ("local", None)
        assert [t["id"] for t in w2.player.queue] == saved["queue"] and w2.player.index == 1
        assert w2.player.current["id"] == saved["queue"][1]
        assert abs(w2.player.mp.position() - saved["pos"]) < 400 or w2.seek.value() >= saved["pos"] - 400
        assert w2.player.mp.playbackState().name != "PlayingState"        # restored paused, not blaring
    finally:
        w2.player.stop(); w2.player.mp.shutdown(); w2._closing = True


def test_layout_and_sizes_persist(win, tmp_path):
    w = win
    w.dock_sources.setMinimumWidth(10)
    w.main_split.setSizes([321, 579]); w.browser_split.setSizes([100, 200, 300])
    w.seek.set_style("waveform")
    w.cfg.seek_style = "waveform"
    w._save_session()
    from hivefi.ui.main_window import MainWindow
    cfg2 = Config.load(w.cfg._path)
    assert cfg2.split_state and cfg2.state and cfg2.browser_state
    w.player.stop(); w.player.mp.shutdown()
    w2 = MainWindow(cfg2, Database(tmp_path / "l.db")); w2.show(); wait(1200)
    try:
        a, b = w2.main_split.sizes(), w.main_split.sizes()
        assert abs(a[0] / sum(a) - 321 / 900) < 0.05, (a, b)
        c = w2.browser_split.sizes(); assert c[0] < c[1] < c[2]
        assert w2.seek.style_name() == "waveform"
    finally:
        w2.player.stop(); w2.player.mp.shutdown()


def _drag(w, x0, y, dx):
    from PyQt6.QtTest import QTest
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, y))
    for step in range(1, 9):
        QTest.mouseMove(w, QPoint(x0 + dx * step // 8, y)); wait(5)
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0 + dx, y))
    wait(50)


def test_edit_mode_locks_resizing(win):
    w = win
    w.show(); w.resize(1200, 700); w.dock_sources.show(); wait(300)
    assert not w.edit_mode and not w.act_edit.isChecked() and not w.btn_edit.isChecked()
    # locked: dragging the separator next to the Sources panel does nothing
    r = w.dock_sources.geometry()
    x, y = r.right() + 2, r.center().y()
    before = w.dock_sources.width()
    _drag(w, x, y, 90)
    assert w.dock_sources.width() == before
    # the splitters are dead too
    h = w.main_split.handle(1); sizes = w.main_split.sizes()
    from PyQt6.QtTest import QTest
    QTest.mousePress(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, 1))
    QTest.mouseMove(h, QPoint(2, 60)); QTest.mouseRelease(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, 60))
    wait(50)
    assert w.main_split.sizes() == sizes
    # edit mode on (menu action, status-bar button and shortcut share one state): the same drag resizes the panel
    w.act_edit.setChecked(True)
    assert w.edit_mode and w.btn_edit.isChecked() and "Editing" in w.btn_edit.text()
    r = w.dock_sources.geometry(); x, y = r.right() + 2, r.center().y()
    before = w.dock_sources.width()
    _drag(w, x, y, 90)
    assert w.dock_sources.width() > before + 40
    # and off again locks it again
    w.btn_edit.setChecked(False)
    assert not w.edit_mode and not w.act_edit.isChecked()
    r = w.dock_sources.geometry(); x, y = r.right() + 2, r.center().y()
    before = w.dock_sources.width()
    _drag(w, x, y, -60)
    assert w.dock_sources.width() == before
    # layouts keep the lock: applying one while locked leaves it locked
    w._apply_builtin_layout("Everything")
    assert w.dock_sources.titleBarWidget() is not None
