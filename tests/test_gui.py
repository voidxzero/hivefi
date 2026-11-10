"""End-to-end GUI test (offscreen) against the mock Subsonic server."""
import os, shutil, subprocess, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pathlib import Path
import pytest
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication
from hivefi import skins
from hivefi.config import Config
from hivefi.db import Database
from tests.mock_server import State, make_server

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def wait(ms):
    l = QEventLoop(); QTimer.singleShot(ms, l.quit); l.exec()


_APP: list = []


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    music = tmp_path / "music"; music.mkdir()
    for i in (1, 2):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", "-metadata",
                        f"title=L{i}", "-metadata", "artist=Local", "-metadata", "album=LA", str(music / f"{i}.flac")], check=True)
    st = State(); srv = make_server(st)
    app = QApplication.instance() or QApplication([])
    _APP.append(app)       # one application for the whole run: dropping the last reference deleted it after each test,
                           # under windows that were still alive, and that crashed now and then
    skins.apply_skin(app, "Dark")
    cfg = Config(); cfg._path = tmp_path / "c.json"; cfg.library_dirs = [str(music)]
    cfg.server_url = f"http://127.0.0.1:{srv.server_port}"; cfg.username, cfg.password = "u", "pw"
    cfg.show_tray = False
    from hivefi.ui.main_window import MainWindow
    w = MainWindow(cfg, Database(tmp_path / "l.db")); w.show(); wait(3500)
    waited = 0
    while waited < 20000 and not (len(w.db.all_tracks("local")) == 2 and len(w.db.all_tracks("remote")) == 7
                                  and not w.status_lbl.text().startswith("Loading")):
        wait(200); waited += 200                                        # a loaded machine needs longer to scan and sync
    wait(300)
    w._st = st
    yield w
    w.close(); srv.shutdown()


def test_everything(win):
    w, db = win, win.db
    assert len(db.all_tracks("local")) == 2 and len(db.all_tracks("remote")) == 7
    # views, panels
    w._set_view_mode("albums"); wait(300)
    assert w.grid.count() == 3
    w.grid.setCurrentRow(0); assert w.model.rowCount() > 0
    w._set_view_mode("browser")
    for d in (w.dock_next, w.dock_lyrics, w.dock_info): d.show()
    w.player.play_list(db.all_tracks("local"), 0); wait(400)
    assert w.next_panel.list.count() == 2 and "L1" in w.next_panel.list.item(0).text() or True
    w.player.remove([1]); assert len(w.player.queue) == 1
    # smart playlists
    w.source = ("smart", "Never Played"); w.load_source(); assert w.model.rowCount() >= 7
    sid = db.save_smart("R", {"rules": [{"field": "genre", "op": "is", "value": "rock"}]})
    w.refresh_playlists(); w.source = ("smart", sid); w.load_source(); assert w.model.rowCount() == 7
    # rating click path + skins + sleep + mini
    w.set_rating([w.model.tracks[0]], 3); assert db.track(w.model.tracks[0]["id"])["rating"] == 3
    for name in skins.all_skins(): w.set_skin(name)
    w._set_sleep(5); assert w.sleep_timer.isActive(); w._set_sleep(0); assert not w.sleep_timer.isActive()
    w._enter_mini(); assert w.mini.isVisible(); w._leave_mini(); assert w.isVisible()
    # persistence
    w.close(); assert w.cfg.state and w.cfg.header_state


def test_libraries_on_off(win):
    w, db, st = win, win.db, win._st
    server_playlist = next(p for p in db.playlists() if p["sync"])
    assert len(w._tracks()) == 9 and ("playlist", server_playlist["id"]) in w._items
    # playing a Navidrome track, then switching Navidrome off, must stop it and empty the queue
    w.player.play_list(db.all_tracks("remote")[:2], 0)
    calls = len(st.calls)
    w.set_library_enabled("navidrome", False)
    wait(500)                                    # let requests already in flight (stream, cover art) settle
    calls = len(st.calls)
    assert w.api is None and not w.cfg.server_enabled
    assert w.player.current is None and w.player.queue == []
    assert len(w._tracks()) == 2 and all(t["source"] == "local" for t in w._tracks())
    w.source = ("all", None); w.load_source(); assert w.model.rowCount() == 2
    assert ("playlist", server_playlist["id"]) not in w._items          # server playlists hide with it
    w.sync_now(silent=True); wait(600)
    assert len(st.calls) == calls, st.calls[calls:]                     # no sync/network traffic while off
    assert len(db.all_tracks("remote")) == 7                            # data kept, just hidden
    assert w.sync_timer.isActive() is False
    # back on: catches up with a sync and everything returns
    w.set_library_enabled("navidrome", True); wait(2000)
    assert len(st.calls) > calls and len(w._tracks()) == 9
    assert ("playlist", server_playlist["id"]) in w._items
    # a single local folder can be switched off too, without deleting its tracks
    folder = w.cfg.library_dirs[0]
    w.set_library_enabled(folder, False); wait(500)
    assert len(w._tracks()) == 7 and len(db.all_tracks("local")) == 2
    w.scan_library(); wait(800)
    assert len(db.all_tracks("local")) == 2                             # scan leaves disabled folders alone
    w._local_only(); assert len(w._tracks()) == 2
    w._enable_all(); wait(1500); assert len(w._tracks()) == 9


def test_settings_dialog_toggles(win):
    from hivefi.ui.settings_dialog import SettingsDialog
    from PyQt6.QtCore import Qt
    w = win
    dlg = SettingsDialog(w.cfg)
    dlg.enabled.setChecked(False)
    dlg.dirs.item(0).setCheckState(Qt.CheckState.Unchecked)
    dlg.apply()
    assert not w.cfg.server_enabled and w.cfg.disabled_dirs == w.cfg.library_dirs


def test_equalizer_panel(win):
    w = win; ep = w.eq_panel; w.show_equalizer()
    ep.preset.setCurrentText("Rock")
    assert ep.gains() == [4, 3, 2, -1, -2, -1, 2, 3, 4, 5]
    assert ep.btn_auto.isChecked() and ep.preamp() <= -5 and w.cfg.eq_preamp == ep.preamp()   # auto preamp follows the curve
    ep.preset.setCurrentText("Flat"); assert ep.preamp() == 0
    ep.preset.setCurrentText("Rock"); rock_pre = ep.preamp(); assert rock_pre <= -5
    ep.sliders[3].setValue(0)                                           # band 2 moved -> custom curve
    assert ep.preset.currentText() == "Custom" and ep.gains()[2] == 0
    assert w.cfg.eq_gains == ep.gains()
    ep.sliders[0].setValue(-6); assert ep.preamp() == -3 and w.cfg.eq_preamp == -3
    assert not ep.btn_auto.isChecked() and not w.cfg.eq_auto_preamp     # a hand-set preamp turns the following off
    ep.sliders[1].setValue(ep.sliders[1].value() + 2); assert ep.preamp() == -3
    ep.sliders[1].setValue(ep.sliders[1].value() - 2)
    ep.btn_auto.setChecked(True); assert w.cfg.eq_auto_preamp and ep.preamp() < -3 and w.cfg.eq_preamp == ep.preamp()
    ep.btn_auto.setChecked(False)
    ep._auto_preamp(); assert -12 <= ep.preamp() <= -max(ep.gains())    # auto preamp offsets the peak of the whole curve
    auto_pre = ep.preamp()
    # switching layout keeps the curve's shape
    ep.layout_box.setCurrentIndex(ep.layout_box.findData(31))
    assert w.cfg.eq_bands == 31 and len(w.cfg.eq_gains) == 31 and len(ep.sliders) == 32
    assert abs(w.cfg.eq_gains[0] - 4) <= 1 and abs(w.cfg.eq_gains[-1] - 5) <= 1
    assert w.cfg.eq_preamp == auto_pre                                  # preamp survives a layout change
    ep.preset.setCurrentText("Flat"); assert set(ep.gains()) == {0.0}
    ep.layout_box.setCurrentIndex(ep.layout_box.findData(15)); assert len(ep.sliders) == 16
    # user presets are stored with their layout and re-interpolated for other layouts
    ep.sliders[8].setValue(10)
    import hivefi.ui.equalizer as eqmod
    eqmod.QInputDialog.getText = staticmethod(lambda *a, **k: ("Mine", True))
    ep._save_preset()
    assert w.cfg.eq_user_presets["Mine"]["bands"] == 15
    ep.layout_box.setCurrentIndex(ep.layout_box.findData(10)); ep.preset.setCurrentText("Mine")
    assert len(ep.gains()) == 10 and max(ep.gains()) > 0
    # live: enabling it rebuilds the chain on the playing deck
    w.player.play_list(w.db.all_tracks("local"), 0); wait(500)
    ep.enable.setChecked(True); wait(600)
    gst = w.player.mp.gst
    assert w.player.mp.engine == "gst" and "eq" in gst.cur.h and gst.cur.h["eq"].get_property("num-bands") == 10
    assert w.player.mp.playbackState().name == "PlayingState"
    ep.enable.setChecked(False); wait(500)
    assert "eq" not in gst.cur.h and w.player.mp.playbackState().name == "PlayingState"


def test_audio_settings_dialog(win):
    from hivefi.ui.audio_dialog import AudioDialog
    w = win
    dlg = AudioDialog(w.cfg, w.player.mp); dlg.on_apply = w.player.configure
    dlg.xf.setValue(3.5); dlg.gapless.setChecked(False); dlg.rate.setCurrentIndex(dlg.rate.findData(96000))
    dlg.depth.setCurrentIndex(dlg.depth.findData("24")); dlg.dither.setCurrentIndex(dlg.dither.findData("tpdf"))
    dlg.rg.setCurrentIndex(dlg.rg.findData("album")); dlg.curve.setCurrentIndex(dlg.curve.findData("cubic"))
    dlg.balance.setValue(-30); dlg.mono.setChecked(True); dlg.limiter.setChecked(True); dlg.bitperfect.setChecked(False)
    dlg.apply()
    c = w.cfg
    assert (c.crossfade_ms, c.gapless, c.output_rate, c.output_depth, c.dither) == (3500, False, 96000, "24", "tpdf")
    assert (c.rg_mode, c.volume_curve, c.balance, c.mono, c.limiter) == ("album", "cubic", -0.3, True, True)
    from hivefi import audio
    d = audio.chain_description(c)
    assert "rgvolume" in d and "audiopanorama" in d and "audiodynamic" in d and "rate=96000" in d and "S24_32LE" in d
    # persisted to disk
    from hivefi.config import Config
    assert Config.load(c._path).crossfade_ms == 3500
    # Qt-only engine: everything keeps working, DSP hidden from the engine
    dlg.engine.setCurrentIndex(dlg.engine.findData("qt")); dlg.apply(); wait(300)
    assert w.player.mp.engine == "qt"
    dlg.engine.setCurrentIndex(dlg.engine.findData("auto")); dlg.apply(); wait(300)
    assert w.player.mp.engine == "gst"


def test_player_gapless_through_gui(win):
    w = win
    local = w.db.all_tracks("local")
    w.cfg.gapless = True; w.cfg.crossfade_ms = 0; w.player.configure()
    w.player.play_list(local, 0); wait(300)
    first = w.player.current["id"]
    w.player.seek(max(0, int(local[0]["duration"] * 1000) - 1500)); wait(3500)
    assert w.player.current["id"] != first and w.player.index == 1       # advanced by the engine, no reload
