"""Composer, comment and BPM: read, written back, searched, shown."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import subprocess

import pytest

from hivefi import scanner, smart, tagedit, tagtools
from hivefi.db import Database
from hivefi.searchquery import Query

need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


@need_ffmpeg
@pytest.mark.parametrize("ext", ["flac", "mp3", "m4a", "ogg", "opus"])
def test_read_and_write_roundtrip(tmp_path, ext):
    f = tmp_path / f"a.{ext}"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-metadata", "composer=Bach",
                    "-metadata", "title=T", str(f)], check=True)
    t = scanner.read_tags(str(f))
    assert t["composer"] == "Bach" and t["bpm"] == 0 and t["comment"] == ""
    tagedit.write_tags(str(f), {"composer": "Mozart", "bpm": 128, "comment": "Live in Vienna"})
    t = scanner.read_tags(str(f))
    assert (t["composer"], t["bpm"], t["comment"]) == ("Mozart", 128, "Live in Vienna")
    assert t["title"] == "T"                                                           # other tags untouched
    tagedit.write_tags(str(f), {"comment": "Second edit"})
    assert scanner.read_tags(str(f))["comment"] == "Second edit"


@need_ffmpeg
def test_library_columns_and_batch_tools(tmp_path):
    m = tmp_path / "music"; m.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-metadata", "composer=j. s. bach",
                    "-metadata", "title=T", str(m / "a.flac")], check=True)
    db = Database(tmp_path / "l.db"); scanner.scan(db, [str(m)])
    t = db.all_tracks("local")[0]
    assert t["composer"] == "j. s. bach" and t["bpm"] == 0
    ch = tagtools.plan([t], "title_case", "composer")
    assert ch[0][1] == {"composer": "J. S. Bach"}
    done, errs = tagtools.apply_changes(db, ch)
    assert done == 1 and not errs and db.all_tracks("local")[0]["composer"] == "J. S. Bach"
    assert scanner.read_tags(str(m / "a.flac"))["composer"] == "J. S. Bach"
    assert tagtools.apply_operation(t, "title_case", "bpm") == {}                       # not a text tag


def test_search_and_smart_rules():
    t = dict(title="x", composer="Johann Sebastian Bach", comment="Live recording", bpm=120, album="a", artist="b", genre="")
    hay = "x b a "
    assert Query("composer:bach").match(t, hay) and not Query("composer:mozart").match(t, hay)
    assert Query("comment:live").match(t, hay) and Query("bpm:>=100").match(t, hay) and not Query("bpm:>130").match(t, hay)
    assert Query("bpm:100-130 composer:johann").match(t, hay)
    assert smart.match_rule(t, {"field": "composer", "op": "contains", "value": "sebastian"}, 0)
    assert smart.match_rule(t, {"field": "bpm", "op": ">", "value": "100"}, 0)
    assert not smart.match_rule(t, {"field": "comment", "op": "is", "value": "live"}, 0)


def test_columns_dialog_and_info_panel():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hivefi.ui.dialogs import TagDialog
    from hivefi.ui.models import COLUMNS, DEFAULT_HIDDEN, TrackModel
    from hivefi.ui.panels import TrackInfoPanel
    keys = [c[1] for c in COLUMNS]
    assert keys[-7:-3] == ["composer", "bpm", "comment", "skip_count"]
    assert {"composer", "bpm", "comment", "skip_count"} <= set(DEFAULT_HIDDEN)
    t = dict(id=1, title="T", artist="A", album_artist="A", album="B", genre="", year=0, track_no=1, disc_no=1,
             duration=60, bitrate=0, suffix="mp3", size=1, rating=0, starred=0, play_count=0, last_played=0, date_added=0,
             source="local", path="/x.mp3", remote_id=None, composer="Chopin", bpm=96, comment="Nocturne")
    m = TrackModel(); m.set_tracks([t])
    assert m.data(m.index(0, keys.index("composer"))) == "Chopin" and m.data(m.index(0, keys.index("bpm"))) == "96"
    assert m.data(m.index(0, keys.index("comment"))) == "Nocturne"
    panel = TrackInfoPanel(); panel.set_tracks([t])
    assert "Chopin" in panel.lbl.text() and "96" in panel.lbl.text() and "Nocturne" in panel.lbl.text()
    d = TagDialog([t])
    d.edits["composer"].setText("Liszt"); d.edits["bpm"].setText("140"); d.edits["comment"].setText("Études")
    v = d.values()
    assert v["composer"] == "Liszt" and v["bpm"] == 140 and v["comment"] == "Études"


def test_skip_counting(tmp_path):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtMultimedia import QMediaPlayer
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hivefi.config import Config
    from hivefi.player import Player
    PS = QMediaPlayer.PlaybackState
    p = Player(lambda t: QUrl(), Config())
    skipped = []
    p.skipped.connect(skipped.append)
    tracks = [dict(id=i, source="local", title=f"t{i}", artist="A", album="x", album_artist="A", duration=100, path=f"/x{i}.mp3")
              for i in range(1, 6)]
    state = {"s": PS.PlayingState, "p": 5000}
    p.mp.playbackState = lambda: state["s"]; p.mp.position = lambda: state["p"]; p.mp.duration = lambda: 100_000
    p.mp.setSource = lambda *a, **k: None; p.mp.play = lambda: None; p.mp.stop = lambda: None
    p.mp.preload_paused = lambda *a: None; p.mp.set_next = lambda *a: None
    p.play_list(tracks, 0)
    p.next(); assert [t["id"] for t in skipped] == [1]                      # left at 5% of the track: a skip
    state["p"] = 60_000; p.next(); assert [t["id"] for t in skipped] == [1]  # left after 60%: not a skip
    state["p"] = 10_000; p.jump(3); assert [t["id"] for t in skipped] == [1, 3]                     # jumping away counts too
    state["p"] = 10_000; p.jump(3); assert len(skipped) == 2                 # "jumping" to the same track does not
    state["p"] = 1000; p.mp.position = lambda: 1000; p.previous()          # (< 3 s: really goes back)
    assert [t["id"] for t in skipped][-1] == 4
    p._counted = True; state["p"] = 1000; n = len(skipped); p.next(); assert len(skipped) == n            # already counted as played
    state["s"] = PS.PausedState; p._counted = False; p.next(); assert len(skipped) == n                  # paused: not a skip
    state["s"] = PS.PlayingState; p.next(manual=False); assert len(skipped) == n                         # track ended by itself


def test_skips_in_the_library(tmp_path):
    from hivefi import smart
    db = Database(tmp_path / "l.db")
    db.upsert_track("k1", "local", path="/a.mp3", title="A", artist="X", album_artist="X", album="Al", duration=100.0)
    db.commit()
    tid = db.all_tracks()[0]["id"]
    for _ in range(3):
        db.bump_skip(tid)
    t = db.track(tid)
    assert t["skip_count"] == 3 and t["last_skipped"] > 0 and db.all_tracks()[0]["skip_count"] == 3
    assert smart.evaluate(db.all_tracks(), smart.PRESETS["Often Skipped"]) == db.all_tracks()
    assert Query("skips:>=3").match(t, "") and not Query("skips:>3").match(t, "")
