"""Play history, the play log and library statistics."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import time

from hivefi import stats
from hivefi.db import Database
from tests.test_gui import win, wait  # noqa: F401  (fixture)


def add(db, i, **kw):
    f = dict(title=f"T{i}", artist="A", album_artist="A", album="Al", genre="Rock", year=1995, duration=100.0, size=1000,
             suffix="flac", rating=0)
    f.update(kw)
    db.upsert_track(f"k{i}", "local", path=f"/m/{i}.flac", **f)


def test_play_log_and_history(tmp_path):
    db = Database(tmp_path / "l.db")
    for i in range(3):
        add(db, i)
    db.commit()
    ids = [t["id"] for t in sorted(db.all_tracks(), key=lambda t: t["key"])]
    for tid in (ids[0], ids[1], ids[0]):                                   # played: 0, 1, 0
        db.bump_play(tid); time.sleep(0.01)
    assert [t["id"] for t in db.play_history()] == [ids[0], ids[1], ids[0]]   # newest first, once per play
    assert len(db.plays_since(0)) == 3 and db.plays_since(time.time() + 10) == []
    assert db.track(ids[0])["play_count"] == 2
    assert [t["id"] for t in db.play_history(limit=1)] == [ids[0]]
    db.delete_tracks([ids[0]])                                             # removing a track removes its plays too
    assert len(db.plays_since(0)) == 1 and [t["id"] for t in db.play_history()] == [ids[1]]


def test_statistics():
    now = 1_000_000_000
    ts = [dict(id=1, title="a", artist="X", album_artist="X", album="A1", genre="Rock", year=1994, duration=3600,
               size=2048, suffix="flac", rating=5, play_count=5, _ak="1", _aa="X"),
          dict(id=2, title="b", artist="X", album_artist="X", album="A1", genre="Rock", year=1999, duration=1800,
               size=1024, suffix="mp3", rating=3, play_count=1, _ak="1", _aa="X"),
          dict(id=3, title="c", artist="Y", album_artist="Y", album="B", genre="", year=2011, duration=60, size=10,
               suffix="mp3", rating=0, play_count=0, _ak="2", _aa="Y")]
    plays = [(1, now - 3600), (1, now - 86400 * 3), (2, now - 86400 * 20), (99, now - 10)]     # 99: a track no longer in the library
    s = stats.compute(ts, plays, now)
    assert (s["tracks"], s["albums"], s["artists"], s["genres"]) == (3, 2, 2, 1)
    assert s["duration"] == 5460 and s["size"] == 3082
    assert s["formats"] == [("MP3", 2), ("FLAC", 1)] and s["decades"] == [("1990s", 2), ("2010s", 1)]
    assert s["top_artists"][0] == ("X", 2) and s["top_played_artists"] == [("X", 6)]
    assert s["top_tracks"][0] == ("X – a", 5) and s["never_played"] == 1
    assert s["rated"] == 2 and s["avg_rating"] == 4.0 and s["total_plays"] == 6
    assert s["plays_7"] == 2 and s["listened_7"] == 7200 and s["plays_30"] == 3 and s["listened_30"] == 9000
    html = stats.render_html(s)
    assert "<b>3</b> tracks" in html and "X – a" in html and "FLAC" in html and "1990s" in html
    assert stats.render_html(stats.compute([], [], now))                   # an empty library renders too
    assert stats.fmt_duration(90061) == "1d 1h 1m" and stats.fmt_duration(59) == "0m" and stats.fmt_size(1536) == "1.5 KB"


def test_history_and_stats_in_the_window(win, monkeypatch):
    from hivefi.ui import main_window as mw
    w, db = win, win.db
    assert ("history", None) in w._items
    tr = db.all_tracks("local")
    w._on_played(tr[0]); w._on_played(tr[1]); w._on_played(tr[0]); wait(200)
    w.source = ("history", None); w.load_source()
    assert [t["id"] for t in w.model.tracks] == [tr[0]["id"], tr[1]["id"], tr[0]["id"]]
    assert w.model.sort_col is None                                         # history keeps its own order
    shown = {}
    monkeypatch.setattr(mw.StatsDialog, "exec", lambda self: shown.setdefault("html", self.view.toPlainText()) and 0)
    w._show_stats()
    assert "tracks" in shown["html"] and "Library" in shown["html"] and "plays in total" in shown["html"]
