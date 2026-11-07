"""Internet radio: stations, playing them like tracks, stream titles, server stations."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

from hivefi import audio, radio, sync
from hivefi.db import Database
from hivefi.subsonic import Subsonic
from hivefi.ui import main_window as mw
from hivefi.ui.station_dialog import StationDialog
from tests.mock_server import State, make_server
from tests.test_gui import win, wait  # noqa: F401  (fixture)

app = QApplication.instance() or QApplication([])


def test_url_validation_and_track_shape():
    assert radio.valid_url("http://a.b/stream") and radio.valid_url("https://x.y:8000/live.mp3")
    assert not radio.valid_url("ftp://x/y") and not radio.valid_url("stream") and not radio.valid_url("")
    t = radio.radio_track({"id": 3, "name": "FM", "url": "http://x/s", "genre": "Rock", "homepage": ""})
    assert t["id"] == -3 and t["source"] == "radio" and t["duration"] == 0 and t["title"] == "FM"
    assert t["album"] == "Rock" and t["_aa"] == radio.LABEL


def test_db_stations(tmp_path):
    db = Database(tmp_path / "l.db")
    a = db.add_radio("One", "http://x/1", genre="Jazz")
    b = db.add_radio("Two", "http://x/2")
    assert [s["name"] for s in db.radio_stations()] == ["One", "Two"]
    assert db.add_radio("One (renamed)", "http://x/1") == a                    # same stream: updated, not duplicated
    assert db.radio_station(a)["name"] == "One (renamed)" and db.radio_station(a)["genre"] == "Jazz"
    db.update_radio(b, "Two!", "http://x/22", "Pop", "http://home")
    assert db.radio_station(b)["url"] == "http://x/22" and db.radio_station(b)["homepage"] == "http://home"
    db.delete_radio(a)
    assert [s["id"] for s in db.radio_stations()] == [b]


def test_server_stations_are_synced(tmp_path):
    st = State(); st.radio = [{"id": "r1", "name": "Server FM", "streamUrl": "http://srv/fm", "homePageUrl": "http://srv"},
                              {"id": "r2", "name": "No URL", "streamUrl": ""}]
    srv = make_server(st)
    try:
        api = Subsonic(f"http://127.0.0.1:{srv.server_port}", "u", "pw")
        db = Database(tmp_path / "l.db")
        db.add_radio("Mine", "http://mine/s")
        sync.full_sync(db, api)
        names = {s["name"]: s for s in db.radio_stations()}
        assert set(names) == {"Mine", "Server FM"} and names["Server FM"]["remote_id"] == "r1"
        assert names["Server FM"]["homepage"] == "http://srv"
        sync.full_sync(db, api)
        assert len(db.radio_stations()) == 2                                  # no duplicates on every sync
        st.radio = None                                                       # a server without radio support
        sync.full_sync(db, api); assert len(db.radio_stations()) == 2
    finally:
        srv.shutdown()


def test_station_dialog_validates():
    d = StationDialog()
    d.name.setText("X"); d.url.setText("not a url"); d._ok()
    assert d.result() == 0 and "http" in d.msg.text()
    d.url.setText("http://ok.example/stream"); d.name.setText("")
    d._ok()
    assert d.result() == 1 and d.values()["name"] == "http://ok.example/stream"


@pytest.mark.skipif(not audio.GST_OK, reason="GStreamer needed")
def test_stream_title_from_tags():
    s = audio.Gst.TagList.new_from_string('taglist, title=(string)"Artist - Song"')
    msg = audio.Gst.Message.new_tag(None, s)
    ok, name = msg.parse_tag().get_string("title")
    assert ok and name == "Artist - Song"


def test_radio_in_the_window(win, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    w, db = win, win.db
    assert ("radio", None) in w._items
    monkeypatch.setattr(mw.StationDialog, "exec", lambda self: True)
    monkeypatch.setattr(mw.StationDialog, "values", lambda self: dict(name="Jazz FM", url="http://127.0.0.1:9/jazz",
                                                                       genre="Jazz", homepage=""))
    w._add_station(); wait(200)
    sid = db.radio_stations()[0]["id"]
    assert ("radio", sid) in w._items and w.source == ("radio", sid)
    assert w.model.rowCount() == 1 and w.model.tracks[0]["title"] == "Jazz FM"
    assert [c for c in range(w.model.columnCount())]                            # model renders the row
    assert w.model.data(w.model.index(0, 7)) == "live" and w.model.data(w.model.index(0, 10)) == "Radio"
    w.source = ("radio", None); w.load_source(); assert w.model.rowCount() == 1
    t = w.model.tracks[0]
    assert w._resolve(t) == QUrl("http://127.0.0.1:9/jazz")
    w.set_rating([t], 5); w.set_star([t], True)                                  # no-ops for stations, no crash
    assert db.radio_stations()[0]["id"] == sid
    # a stream title announced by the stream shows up
    w.player.play_list([t], 0); wait(300)
    w.player.streamTitle.emit("The Band - A Song")
    assert "A Song" in w.np_panel.sub.text() and "A Song" in w.windowTitle()
    w.player.stop()
    # edit and delete
    monkeypatch.setattr(mw.StationDialog, "values", lambda self: dict(name="Jazz FM 2", url="http://127.0.0.1:9/jazz",
                                                                       genre="Jazz", homepage=""))
    w._edit_station(sid); assert db.radio_station(sid)["name"] == "Jazz FM 2"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w._delete_station(sid); wait(100)
    assert db.radio_stations() == [] and w.source == ("radio", None) and w.model.rowCount() == 0
    # radio is not part of the library: never in All Music, searches or Auto-DJ
    assert all(t["source"] != "radio" for t in w._tracks())
