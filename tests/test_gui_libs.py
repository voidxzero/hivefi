"""Server libraries in the GUI + responsiveness guard on a large library (offscreen)."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import time

from PyQt6.QtCore import Qt

from hivefi.config import Config
from hivefi.ui.settings_dialog import SettingsDialog
from tests.test_gui import win, wait  # noqa: F401  (fixture)


def test_server_libraries_gui(win):
    w, db, st = win, win.db, win._st
    st.folders = [{"id": "1", "name": "Music"}, {"id": "2", "name": "Audiobooks"}]
    for s in st.songs:
        s["folder"] = "1" if int(s["id"]) <= 4 else "2"
    w.sync_now(silent=True); wait(1500)
    assert w.cfg.server_folder_names == {"1": "Music", "2": "Audiobooks"}
    assert ("remote", "1") in w._items and ("remote", "2") in w._items          # one source per library
    w.source = ("remote", "2"); w.load_source()
    assert w.model.rowCount() == 3
    # hide one library (view-only: no resync, data stays)
    w.set_library_enabled(Config.server_lib_key("2"), False)
    assert len(db.all_tracks("remote")) == 7 and len(w._tracks("remote")) == 4
    w.source = ("all", None); w.load_source()
    assert w.model.rowCount() == 4 + 2
    w.set_library_enabled(Config.server_lib_key("2"), True)
    assert len(w._tracks("remote")) == 7
    # choose which libraries to sync from Preferences
    dlg = SettingsDialog(w.cfg)
    assert dlg.server_libs.count() == 2
    assert dlg.chosen_server_folders()[0] == []                                  # nothing unticked = all
    dlg.server_libs.item(0).setCheckState(Qt.CheckState.Unchecked)
    ids, names = dlg.chosen_server_folders()
    assert ids == ["2"] and names == {"1": "Music", "2": "Audiobooks"}
    dlg.apply()
    assert w.cfg.server_folders == ["2"]
    w.sync_now(silent=True); wait(1500)
    assert len(db.all_tracks("remote")) == 3


def test_large_library_stays_responsive(win):
    """60k tracks: the actions that used to freeze the window (seconds each) must stay well under a second."""
    w, db = win, win.db
    for i in range(30000):
        db.upsert_track(f"x:{i}", "remote", remote_id=f"x{i}", title=f"Song {i}", artist=f"Artist {i % 300}",
                        album_artist=f"Artist {i % 300}", album=f"Album {i // 12}", genre=f"G{i % 20}", year=2000,
                        track_no=i % 12, disc_no=1, duration=200.0, bitrate=320, suffix="mp3", size=1,
                        cover_id="c")
    db.commit()
    def took(fn):
        t = time.perf_counter(); fn(); wait(1); return time.perf_counter() - t
    w.source = ("all", None)
    assert took(w.load_source) < 3.0
    assert w.model.rowCount() > 30000
    assert took(lambda: w._header_sort(2)) < 1.0
    titles = [t["title"].lower() for t in w.model.tracks[:50]]; assert titles == sorted(titles)
    assert took(lambda: w._on_search("song 12")) < 1.0
    assert took(lambda: w._on_search("")) < 1.0
    assert took(lambda: w.lists["genre"].setCurrentRow(2)) < 1.0
    w.table.selectAll()
    sel = w._selected(); assert len(sel) == w.model.rowCount()
    assert took(lambda: w.set_rating(sel[:5000], 4)) < 3.0
    assert db.track(sel[0]["id"])["rating"] == 4
    assert took(lambda: w._set_view_mode("albums")) < 3.0
