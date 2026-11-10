"""Now Playing: configurable fields, audio properties, and clickable album / artist / genre ... that navigate."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import subprocess

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from hivefi import npfields, scanner
from hivefi.config import Config
from hivefi.db import Database
from hivefi.ui.np_dialog import NowPlayingDialog, SAMPLE
from hivefi.ui.panels import NowPlayingPanel
from tests.test_gui import win, wait  # noqa: F401  (fixture)

app = QApplication.instance() or QApplication([])
need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")

T = dict(title="Song", artist="A", album="B", album_artist="A", _aa="A", year=1999, genre="Rock", composer="C",
         track_no=3, disc_no=1, suffix="flac", sample_rate=96000, bit_depth=24, channels=2, bitrate=2300, duration=185,
         size=30_000_000, rating=4, play_count=7, skip_count=2, bpm=120, rg_gain=-6.5, source="local", path="/m/x.flac",
         date_added=1_700_000_000, last_played=0, comment="")


def test_field_text():
    c = {"out": {"rate": 48000, "format": "F32LE", "channels": 2}}
    got = {k: text for k, text, _l in npfields.lines(list(npfields.FIELDS), T, c)}
    assert got["format"] == "FLAC" and got["sample_rate"] == "96 kHz" and got["bit_depth"] == "24-bit"
    assert got["channels"] == "Stereo" and got["bitrate"] == "2300 kbps"
    assert got["tech"] == "FLAC · 96 kHz · 24-bit · Stereo · 2300 kbps"
    assert got["output"] == "48 kHz · F32 · Stereo" and got["duration"] == "3:05" and got["rating"] == "★★★★☆"
    assert got["track"] == "Track 3" and got["replaygain"] == "-6.50 dB" and got["source"] == "Local file"
    assert got["size"].endswith("MB") and got["plays"] == "7" and got["skips"] == "2" and got["bpm"] == "120"
    assert "comment" not in got and "last_played" not in got                           # empty fields are left out
    assert npfields.FIELDS["track"][1](dict(T, disc_no=2), {}) == "Disc 2 · Track 3"
    assert npfields.FIELDS["sample_rate"][1](dict(T, sample_rate=44100), {}) == "44.1 kHz"
    assert npfields.FIELDS["source"][1](dict(T, source="remote"), {}) == "Navidrome"
    # links: which fields lead somewhere
    links = {k: l for k, _t, l in npfields.lines(["title", "artist", "album", "genre", "year", "composer", "tech"], T)}
    assert links == {"title": "track", "artist": "artist", "album": "album", "genre": "genre", "year": "year",
                     "composer": "composer", "tech": None}
    labelled = npfields.lines(["artist", "tech"], T, labels=True)
    assert labelled[0][1] == "Artist: A" and labelled[1][1].startswith("Format summary: FLAC")
    assert npfields.lines(["nonsense", "title"], T)[0][0] == "title"
    assert npfields.lines([], T) == []


def test_html_escapes_and_links():
    h = npfields.html(["title", "artist"], dict(T, title="<b>Rock & Roll</b>"), labels=False, link_color="#ff0000")
    assert "&lt;b&gt;Rock &amp; Roll&lt;/b&gt;" in h and '<a href="track"' in h and "#ff0000" in h
    assert "font-weight:bold" in h.split("</div>")[0] and "font-weight" not in h.split("</div>")[1]
    h = npfields.html(["artist"], T, labels=True)
    assert 'Artist: <a href="artist">A</a>' in h
    h = npfields.html(["artist", "album"], T, lead="Live: Song - Band")
    assert h.startswith("<div style='font-size:large;font-weight:bold'>Live: Song - Band</div>")
    assert h.count("font-weight:bold") == 1


def test_panel_follows_the_configuration():
    cfg = Config()
    p = NowPlayingPanel(cfg)
    assert "Nothing playing" in p.plain_text()
    p.set_track(T, {})
    assert p.plain_text().splitlines() == ["Song", "A", "B"]                           # the default three
    cfg.np_fields = ["title", "tech", "year"]; p.render()
    assert p.plain_text().splitlines() == ["Song", "FLAC · 96 kHz · 24-bit · Stereo · 2300 kbps", "1999"]
    cfg.np_labels = True; p.render()
    assert p.plain_text().splitlines()[2] == "Year: 1999"
    p.set_stream_title("The Band - Live Song"); assert p.plain_text().splitlines()[0] == "The Band - Live Song"
    p.set_track(None); assert "Nothing playing" in p.plain_text()
    p.set_context({"out": {"rate": 44100, "format": "S16LE", "channels": 2}}); p.set_track(T, {})
    cfg.np_fields = ["output"]; p.set_context({"out": {"rate": 44100, "format": "S16LE", "channels": 2}})
    assert "44.1 kHz" in p.plain_text()
    assert NowPlayingPanel().fields() == ["title", "artist", "album"]                   # works without a config too


def test_clicks_emit_what_was_clicked():
    cfg = Config(); cfg.np_fields = ["title", "artist", "album", "genre", "tech"]
    p = NowPlayingPanel(cfg); p.resize(300, 400); p.set_track(T, {}); got = []
    p.navigate.connect(lambda kind, t: got.append((kind, t["title"])))
    for kind in ("artist", "album", "genre", "track"):
        p.info.linkActivated.emit(kind)
    QTest.mouseClick(p.art, Qt.MouseButton.LeftButton)                                  # the cover leads to the album
    assert got == [("artist", "Song"), ("album", "Song"), ("genre", "Song"), ("track", "Song"), ("album", "Song")]
    assert p.art.cursor().shape() == Qt.CursorShape.PointingHandCursor
    p.set_track(None); got.clear()
    QTest.mouseClick(p.art, Qt.MouseButton.LeftButton); p.info.linkActivated.emit("album")
    assert got == [] and p.art.cursor().shape() == Qt.CursorShape.ArrowCursor          # nothing playing: nowhere to go
    asked = []
    p.configureRequested.connect(lambda: asked.append(1)); p.configureRequested.emit(); assert asked


def test_configuration_dialog():
    d = NowPlayingDialog(["artist", "title"], False, SAMPLE)
    assert d.result_fields() == ["artist", "title"]
    assert d.list.item(0).text() == "Artist" and d.list.item(1).text() == "Title"      # the chosen ones, in order, first
    assert "Pink Floyd" in d.preview.text() and "Comfortably Numb" in d.preview.text()
    d.list.setCurrentRow(1); d._move(-1)
    assert d.result_fields() == ["title", "artist"]
    for i in range(d.list.count()):
        if d.list.item(i).data(Qt.ItemDataRole.UserRole) == "tech":
            d.list.item(i).setCheckState(Qt.CheckState.Checked)
    assert d.result_fields() == ["title", "artist", "tech"] and "96 kHz" in d.preview.text()
    d.labels.setChecked(True); assert "Format summary:" in d.preview.text()
    d.reset()
    assert d.result_fields() == ["title", "artist", "album"] and not d.labels.isChecked()
    for i in range(d.list.count()):
        d.list.item(i).setCheckState(Qt.CheckState.Unchecked)
    assert d.result_fields() == [] and "Nothing selected" in d.preview.text()


@need_ffmpeg
def test_audio_properties_are_read(tmp_path):
    for ext, args in (("flac", ["-ar", "96000", "-ac", "2", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]),
                      ("mp3", ["-ar", "44100", "-ac", "2"]), ("ogg", ["-ar", "48000", "-ac", "2"]),
                      ("wav", ["-ar", "22050", "-ac", "1"])):
        f = tmp_path / f"a.{ext}"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", *args, str(f)], check=True)
        t = scanner.read_tags(str(f))
        assert t["sample_rate"] == int(args[args.index("-ar") + 1]) and t["channels"] == (1 if ext == "wav" else 2), (ext, t)
    assert scanner.read_tags(str(tmp_path / "a.flac"))["bit_depth"] == 24
    assert scanner.read_tags(str(tmp_path / "a.wav"))["bit_depth"] == 16
    db = Database(tmp_path / "l.db"); scanner.scan(db, [str(tmp_path)])
    rows = {t["suffix"]: t for t in db.all_tracks("local")}
    assert rows["flac"]["sample_rate"] == 96000 and rows["flac"]["bit_depth"] == 24 and rows["ogg"]["sample_rate"] == 48000
    from hivefi.searchquery import Query
    assert Query("samplerate:>=96000 bits:24").match(rows["flac"], "") and not Query("samplerate:>=96000").match(rows["mp3"], "")
    assert Query("channels:1").match(rows["wav"], "")


def test_server_audio_properties_and_columns(tmp_path):
    from tests.mock_server import State, make_server
    from hivefi import sync
    from hivefi.subsonic import Subsonic
    st = State()
    for s in st.songs:
        s.update(samplingRate=48000, bitDepth=16, channelCount=2)
    srv = make_server(st)
    try:
        db = Database(tmp_path / "l.db")
        sync.full_sync(db, Subsonic(f"http://127.0.0.1:{srv.server_port}", "u", "pw"))
        t = db.all_tracks("remote")[0]
        assert (t["sample_rate"], t["bit_depth"], t["channels"]) == (48000, 16, 2)
    finally:
        srv.shutdown()
    from hivefi.ui.models import COLUMNS, DEFAULT_HIDDEN, TrackModel
    keys = [c[1] for c in COLUMNS]
    assert {"sample_rate", "bit_depth", "channels"} <= set(DEFAULT_HIDDEN) and keys[-3:] == ["sample_rate", "bit_depth", "channels"]
    m = TrackModel(); row = dict(T, id=1, starred=0); m.set_tracks([row])
    assert m.data(m.index(0, keys.index("sample_rate"))) == "96 kHz" and m.data(m.index(0, keys.index("bit_depth"))) == "24"
    assert m.data(m.index(0, keys.index("channels"))) == "Stereo"


def test_clicking_navigates_the_library(win):
    w, db = win, win.db
    w._set_view_mode("browser")                                         # the Genre / Artist / Album columns
    loc = db.all_tracks("local")
    assert len(loc) == 2
    # two local albums? the fixture has one album "LA" by "Local" and the server has albums of its own
    rem = db.all_tracks("remote")
    t = next(x for x in rem if x["album"] == "Album0")
    w.source = ("local", None); w.load_source(); w.search.setText("zzz-no-match")
    assert w._np_navigate("album", t) is True
    assert w.source == ("all", None) and w.search.text() == ""                          # leaves playlists / searches behind
    assert w.model.album == "Album0" and w.model.artist == t["_aa"]
    assert w.lists["album"].currentItem().text() == "Album0" and w.lists["artist"].currentItem().text() == t["_aa"]
    assert {x["album"] for x in w.model.tracks} == {"Album0"} and all(x["_aa"] == t["_aa"] for x in w.model.tracks)
    assert [x["id"] for x in w._selected()] == [t["id"]]                                # and the song is highlighted
    # artist: all of that artist's music
    assert w._np_navigate("artist", t) is True
    assert w.model.album is None and w.model.artist == t["_aa"] and {x["_aa"] for x in w.model.tracks} == {t["_aa"]}
    assert len(w.model.tracks) == 7
    # genre
    assert w._np_navigate("genre", t) is True
    assert w.model.genre == "Rock" and w.lists["genre"].currentItem().text() == "Rock"
    # year and composer use the search box
    assert w._np_navigate("year", t) is True and w.model.text == f"year:{t['year']}"
    assert [x["id"] for x in w.model.tracks] == [t["id"]]
    t2 = dict(t, composer="Some Composer")
    w._np_navigate("composer", t2); assert w.model.text == 'composer:"some composer"' and w.model.tracks == []
    # a track artist that is not the album artist is a search on the artist tag
    feat = dict(t, artist="Band feat. Guest"); w._np_navigate("artist", feat)
    assert w.model.text == 'artist:"band feat. guest"'
    # the title locates the song; unknown kinds, empty values and non-library tracks do nothing
    w._np_navigate("track", t); assert w.model.text == "" and w.model.row_of(t["id"]) is not None
    assert w._np_navigate("genre", dict(t, genre="")) is False and w._np_navigate("nonsense", t) is False
    assert w._np_navigate("album", dict(t, source="radio")) is False and w._np_navigate("album", None) is False
    # in the album grid the album is selected there
    w._set_view_mode("albums"); wait(300)
    w._np_navigate("album", t); wait(300)
    assert w.grid.selected_album() and {x["album"] for x in w.grid.selected_album()} == {"Album0"}
    assert {x["album"] for x in w.model.tracks} == {"Album0"}


def test_the_window_uses_the_configuration(win, monkeypatch):
    from hivefi.ui import main_window as mw
    w = win
    t = w.db.all_tracks("local")[0]
    w.player.play_list([t], 0); wait(600)
    assert w.np_panel.plain_text().splitlines()[0] == t["title"]
    class Fake(NowPlayingDialog):
        def exec(self):
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) in ("sample_rate", "format"):
                    self.list.item(i).setCheckState(Qt.CheckState.Checked)
            self.labels.setChecked(True)
            return True
    monkeypatch.setattr(mw, "NowPlayingDialog", Fake)
    w._configure_now_playing()
    assert "format" in w.cfg.np_fields and w.cfg.np_labels
    text = w.np_panel.plain_text()
    assert "File type: FLAC" in text and "Sampling rate: 44.1 kHz" in text and f"Title: {t['title']}" in text
    w.cfg.save()
    assert Config.load(w.cfg._path).np_fields == w.cfg.np_fields
    w.player.stop()
