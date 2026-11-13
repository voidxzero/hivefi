"""Albums are title + album artist: compilations stay whole, same-named albums stay apart."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import sqlite3
import subprocess

import pytest

from hivefi import albums, scanner, sync
from hivefi.db import Database
from hivefi.subsonic import Subsonic
from tests.mock_server import State, make_server


def T(i, **kw):
    base = dict(id=i, source="local", album="Hits", artist="A", album_artist="A", aa_tag="", title=f"t{i}",
                path=f"/m/x/{i}.mp3", album_id=None, disc_no=1, track_no=i, genre="", duration=1.0, starred=0)
    base.update(kw)
    return base


def groups(ts):
    albums.annotate(ts)
    out = {}
    for t in ts:
        out.setdefault(t["_ak"], []).append(t)
    return out


def test_compilation_without_album_artist_stays_one_album():
    ts = [T(1, artist="X", album_artist="X"), T(2, artist="Y", album_artist="Y"), T(3, artist="Z", album_artist="Z")]
    g = groups(ts)
    assert len(g) == 1 and {t["_aa"] for t in ts} == {albums.VARIOUS}


def test_single_artist_album_without_tag_uses_that_artist():
    ts = [T(1, artist="Solo"), T(2, artist="Solo"), T(3, artist="solo ")]
    assert len(groups(ts)) == 1 and {t["_aa"] for t in ts} == {"Solo"}


def test_same_title_in_different_folders_is_two_albums():
    ts = [T(1, artist="One", path="/m/one/1.mp3"), T(2, artist="Two", path="/m/two/2.mp3")]
    g = groups(ts)
    assert len(g) == 2 and {t["_aa"] for t in ts} == {"One", "Two"}


def test_disc_folders_and_titles_merge():
    ts = [T(1, path="/m/alb/CD 1/1.mp3"), T(2, path="/m/alb/Disc 2/2.mp3"), T(3, path="/m/alb/cd3/3.mp3")]
    assert len(groups(ts)) == 1
    ts = [T(1, album="Epic (Disc 1)", path="/m/a/1.mp3"), T(2, album="Epic [CD2]", path="/m/a/2.mp3"),
          T(3, album="Epic", path="/m/a/3.mp3")]
    assert len(groups(ts)) == 1
    assert albums.title_of(ts) in ("Epic (Disc 1)", "Epic [CD2]", "Epic")


def test_album_artist_tag_wins_and_pulls_in_untagged_tracks():
    ts = [T(1, aa_tag="The Band", artist="The Band"), T(2, aa_tag="", artist="The Band feat. Guest"),
          T(3, aa_tag="The Band", artist="The Band")]
    g = groups(ts)
    assert len(g) == 1 and {t["_aa"] for t in ts} == {"The Band"}


def test_tag_groups_span_folders_but_different_tags_split():
    ts = [T(1, aa_tag="Band", path="/m/a/1.mp3"), T(2, aa_tag="band", path="/m/b/2.mp3")]
    assert len(groups(ts)) == 1                               # same title + same album artist, wherever the files are
    ts = [T(1, aa_tag="Alpha"), T(2, aa_tag="Beta"), T(3, aa_tag="Alpha")]
    g = groups(ts)
    assert len(g) == 2 and sorted(len(v) for v in g.values()) == [1, 2]    # one folder, two artists' "Hits"


def test_server_albums_follow_album_id():
    ts = [T(1, source="remote", path=None, album_id="9", artist="X"), T(2, source="remote", path=None, album_id="9", artist="Y"),
          T(3, source="remote", path=None, album_id="10", artist="X")]
    g = groups(ts)
    assert len(g) == 2
    assert {t["_aa"] for t in ts if t["album_id"] == "9"} == {albums.VARIOUS}
    ts = [T(1, source="remote", path=None, album_id="9", artist="X", aa_tag="Host"),
          T(2, source="remote", path=None, album_id="9", artist="Y", aa_tag="Host")]
    groups(ts); assert {t["_aa"] for t in ts} == {"Host"}


def test_annotate_is_repeatable():
    ts = [T(1, artist="X"), T(2, artist="Y")]
    albums.annotate(ts); first = [(t["_ak"], t["_aa"]) for t in ts]
    albums.annotate(ts); assert [(t["_ak"], t["_aa"]) for t in ts] == first
    assert all("_tag" not in t for t in ts)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
def test_scan_keeps_compilations_together(tmp_path):
    m = tmp_path / "music"; (m / "Mix").mkdir(parents=True); (m / "Solo").mkdir()
    def mk(path, **tags):
        cmd = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1"]
        for k, v in tags.items():
            cmd += ["-metadata", f"{k}={v}"]
        subprocess.run(cmd + [str(path)], check=True)
    for i, a in enumerate("XYZ"):
        mk(m / "Mix" / f"{i}.flac", title=f"s{i}", artist=a, album="Mixtape")
    for i in range(2):
        mk(m / "Solo" / f"{i}.flac", title=f"o{i}", artist="Band" if i == 0 else "Band feat. Guest", album="Solo",
           album_artist="Band")
    db = Database(tmp_path / "l.db")
    scanner.scan(db, [str(m)])
    ts = db.all_tracks("local")
    by_album = {}
    for t in ts:
        by_album.setdefault(t["_ak"], []).append(t)
    assert len(by_album) == 2
    assert sorted(len(v) for v in by_album.values()) == [2, 3]
    mix = next(v for v in by_album.values() if len(v) == 3)
    assert {t["_aa"] for t in mix} == {albums.VARIOUS} and all(t["aa_tag"] == "" for t in mix)
    solo = next(v for v in by_album.values() if len(v) == 2)
    assert {t["_aa"] for t in solo} == {"Band"} and {t["aa_tag"] for t in solo} == {"Band"}


def test_sync_records_album_id_and_groups_by_it(tmp_path):
    st = State(); srv = make_server(st)
    try:
        api = Subsonic(f"http://127.0.0.1:{srv.server_port}", "u", "pw")
        db = Database(tmp_path / "l.db")
        sync.full_sync(db, api)
        ts = db.all_tracks("remote")
        assert {t["album_id"] for t in ts} == {"alb0", "alb1"} and {t["aa_tag"] for t in ts} == {"Band"}
        assert len({t["_ak"] for t in ts}) == 2 and {t["_aa"] for t in ts} == {"Band"}
    finally:
        srv.shutdown()


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE tracks(id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
        remote_id TEXT, path TEXT, title TEXT, artist TEXT, album_artist TEXT, album TEXT, genre TEXT, year INTEGER,
        track_no INTEGER, disc_no INTEGER, duration REAL DEFAULT 0, bitrate INTEGER DEFAULT 0, suffix TEXT,
        size INTEGER DEFAULT 0, rating INTEGER DEFAULT 0, starred INTEGER DEFAULT 0, play_count INTEGER DEFAULT 0,
        last_played REAL DEFAULT 0, date_added REAL DEFAULT 0, mtime REAL DEFAULT 0, cover_id TEXT,
        seen INTEGER DEFAULT 1, rg_gain REAL, folder_id TEXT);
        INSERT INTO tracks(key, source, path, mtime, title, artist, album_artist, album) VALUES
        ('/a.mp3', 'local', '/a.mp3', 123.0, 't', 'a', 'a', 'x');""")
    c.commit(); c.close()
    db = Database(path)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(tracks)")}
    assert {"album_id", "aa_tag"} <= cols
    assert db.conn.execute("SELECT mtime FROM tracks").fetchone()[0] == 0           # local tags get re-read once
    assert db.all_tracks("local")[0]["_aa"] == "a"


def test_grid_and_browser_use_the_same_albums(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hivefi.ui.album_grid import AlbumGrid
    from hivefi.ui.models import TrackModel
    ts = [T(1, artist="X", genre="g"), T(2, artist="Y", genre="g"), T(3, artist="Z", genre="g"),
          T(4, album="Other", artist="Solo", genre="g", path="/m/o/4.mp3")]
    albums.annotate(ts)
    g = AlbumGrid(lambda: None); g.set_tracks(ts)
    texts = sorted(g.item(i).text().replace("\n", " | ") for i in range(g.count()))
    assert texts == ["Hits | Various Artists", "Other | Solo"]
    got = []
    g.albumSelected.connect(got.append)
    g.setCurrentRow(0); g.item(0).setSelected(True)
    assert got and {t["id"] for t in got[-1]} in ({1, 2, 3}, {4})
    m = TrackModel(); m.set_tracks(ts, sort_col="title")
    assert {m.artist_of(t) for t in ts} == {albums.VARIOUS, "Solo"}
    m.set_filters(artist=albums.VARIOUS)
    assert {t["id"] for t in m.tracks} == {1, 2, 3}                           # the browser shows the whole compilation
    m.set_filters(genre=None, artist=None, album=None, ids={4})
    assert [t["id"] for t in m.tracks] == [4]
    m.set_filters(text="hits")                                                # any other filter clears "one album"
    assert m.ids is None and len(m.tracks) == 3


def R(i, **kw):
    """A server track synced before album ids were stored (no album_id, no path)."""
    return T(i, source="remote", path=None, **kw)


def test_server_tracks_without_album_id_do_not_merge_different_artists():
    ts = [R(1, album="Trilogy", artist="ATB", album_artist="ATB"),
          R(2, album="Trilogy", artist="Carpenter Brut", album_artist="Carpenter Brut"),
          R(3, album="Trilogy", artist="ATB", album_artist="ATB")]
    g = groups(ts)
    assert len(g) == 2 and sorted(len(v) for v in g.values()) == [1, 2]


def test_server_tracks_without_album_id_join_their_local_twins_album():
    """An untagged local compilation is one album (its folder); the server reports each track's artist as album
    artist, which alone would split it per artist. The server copies follow their local twins instead."""
    loc = [T(1, album="Comp", artist="X", album_artist="X", title="a", path="/m/comp/1.mp3"),
           T(2, album="Comp", artist="Y", album_artist="Y", title="b", path="/m/comp/2.mp3")]
    rem = [R(11, album="Comp", artist="X", album_artist="X", title="a", track_no=1),
           R(12, album="Comp", artist="Y", album_artist="Y", title="b", track_no=2)]
    g = groups(loc + rem)
    assert len(g) == 1 and {t["_aa"] for t in loc + rem} == {albums.VARIOUS}


def test_guest_artists_stay_with_the_lead_artist():
    ts = [R(1, album="Platinum", artist="Severina", album_artist="Severina"),
          R(2, album="Platinum", artist="Severina & Boris", album_artist="Severina & Boris"),
          R(3, album="Platinum", artist="Severina feat. X", album_artist="Severina feat. X")]
    g = groups(ts)
    assert len(g) == 1 and {t["_aa"] for t in ts} == {"Severina"}
    assert albums.main_artist("Simon, Garfunkel") == "simon" and albums.main_artist("Severina") == "severina"


def test_copies_of_an_album_are_one_album_with_every_copy():
    m4a = [T(i, album="Ambient", artist="Moby", album_artist="Moby", path=f"/m/marko/Ambient/{i}.m4a") for i in (1, 2)]
    mp3 = [T(i + 10, album="Ambient", artist="Moby", album_artist="Moby", path=f"/m/mine/Ambient/{i}.mp3", track_no=i)
           for i in (1, 2)]
    srv = [R(20 + i, album="Ambient", artist="Moby", album_artist="Moby", album_id=f"al-{k}", title=f"s{i}")
           for k in (1, 2) for i in (1, 2)]
    g = groups(m4a + mp3 + srv)
    assert len(g) == 1 and len(next(iter(g.values()))) == 8
    assert next(iter(g))[0] == "l"                            # the local album is the one kept


def test_unknown_album_groups_are_never_merged():
    ts = [T(1, album="", artist="Unknown Artist", path="/m/a/1.mp3"),
          T(2, album="", artist="Unknown Artist", path="/m/b/2.mp3")]
    assert len(groups(ts)) == 2


def test_disc_suffixes_without_brackets_meet():
    for a, b in (("Alive 2007 CD1", "Alive 2007 CD2"), ("Music Bank Disc 1", "Music Bank Disc 3"),
                 ("Garage Inc Cd 1", "Garage Inc Cd 2"), ("Hits (Golden Edition CD1)", "Hits (Golden Edition CD2)")):
        assert albums.norm_album(a) == albums.norm_album(b), (a, b)
    assert albums.norm_album("Hits (Golden Edition CD2)") == "hits (golden edition)"
    assert albums.norm_album("Disc 1") == "disc 1"             # nothing but a disc number: kept
    assert albums.norm_album("1999") == "1999" and albums.norm_album("CD") == "cd"


def test_clicked_album_opens_under_its_row():
    """Albums view: the selected album's tracks show in a band across the grid, right under that album's row;
    the rows after it move down, another album moves the band, a click on the open album closes it."""
    from PyQt6.QtCore import QEventLoop, QTimer, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hivefi.ui.album_grid import AlbumGrid

    def wait(ms):
        loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()
    ts = []
    for a in range(12):                                      # 12 albums of 3 tracks, A00 .. A11
        ts += [T(a * 10 + n, album=f"A{a:02d}", artist="X", album_artist="X", track_no=n, year=1990 + a,
                 path=f"/m/{a}/{n}.mp3") for n in (1, 2, 3)]
    albums.annotate(ts)
    g = AlbumGrid(lambda: None); g.resize(4 * g._cell.width() + 30, 900); g.set_tracks(ts); g.show(); wait(50)
    tiles = [g.item(i) for i in range(12)]
    ys = sorted({g.visualItemRect(it).y() for it in tiles})
    assert len(ys) == 3                                      # 4 columns, 3 rows
    got = []; g.albumSelected.connect(got.append)
    QTest.mouseClick(g.viewport(), Qt.MouseButton.LeftButton, pos=g.visualItemRect(tiles[1]).center()); wait(50)
    assert {t["album"] for t in got[-1]} == {"A01"} and g.count() == 13
    band = g.panel.geometry()
    assert g.panel.isVisible() and [t["track_no"] for t in g.panel.tracks] == [1, 2, 3]
    assert band.width() == g.viewport().width() and band.y() == g.visualItemRect(tiles[0]).bottom() + 1
    assert g.visualItemRect(tiles[4]).y() == band.bottom() + 1          # the second row starts below the band
    assert g.visualItemRect(tiles[3]).y() == g.visualItemRect(tiles[1]).y()
    played = []; g.playFrom.connect(lambda tracks, i: played.append((len(tracks), tracks[i]["track_no"])))
    QTest.mouseDClick(g.panel, Qt.MouseButton.LeftButton, pos=g.panel._cell_rect(2).center())
    assert played == [(3, 3)]
    # an album of the last row: the band moves there, nothing is left behind
    QTest.mouseClick(g.viewport(), Qt.MouseButton.LeftButton, pos=g.visualItemRect(tiles[9]).center()); wait(50)
    assert g.count() == 13 and {t["album"] for t in g.panel.tracks} == {"A09"}
    assert g.visualItemRect(tiles[4]).y() == g.visualItemRect(tiles[0]).bottom() + 1
    assert g.panel.geometry().y() == g.visualItemRect(tiles[8]).bottom() + 1
    # narrower: 3 columns now, the band follows the album's new row
    g.resize(3 * g._cell.width() + 30, 900); wait(100)
    assert g.count() == 13 and g.panel.geometry().width() == g.viewport().width()
    assert g.panel.geometry().y() == g.visualItemRect(tiles[9]).bottom() + 1
    assert g.visualItemRect(tiles[11]).y() == g.visualItemRect(tiles[9]).y()
    # a click on the open album closes it and clears the selection
    QTest.mouseClick(g.viewport(), Qt.MouseButton.LeftButton, pos=g.visualItemRect(tiles[9]).center()); wait(50)
    assert g.count() == 12 and not g.panel.isVisible() and got[-1] is None
    # switched off: selecting only selects
    g.set_expand(False)
    QTest.mouseClick(g.viewport(), Qt.MouseButton.LeftButton, pos=g.visualItemRect(tiles[2]).center()); wait(50)
    assert g.count() == 12 and not g.panel.isVisible() and {t["album"] for t in got[-1]} == {"A02"}
    g.set_expand(True); wait(50)
    assert g.count() == 13 and g.panel.isVisible()
    g.set_tracks(ts[:6])                                      # refilled: closed, no stale band
    assert g.count() == 2 and not g.panel.isVisible()
    g.pool.waitForDone(2000); g.close()
