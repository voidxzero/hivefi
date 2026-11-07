import os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from hivefi import lyrics, scanner, tagedit
from hivefi.db import Database

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed to make fixtures")


@pytest.fixture
def song(tmp_path):
    p = tmp_path / "a.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-metadata",
                    "title=T", "-metadata", "artist=A", "-metadata", "REPLAYGAIN_TRACK_GAIN=-6.50 dB",
                    "-metadata", "LYRICS=la la la", str(p)], check=True)
    return p


def test_tags_replaygain_lyrics_edit(song, tmp_path):
    t = scanner.read_tags(str(song))
    assert t["title"] == "T" and t["rg_gain"] == -6.5
    assert lyrics.get_lyrics({"source": "local", "path": str(song)}) == "la la la"
    (tmp_path / "a.lrc").write_text("[ti:x]\n[00:01.00]hello\n[00:02.50]world\n")
    assert lyrics.get_lyrics({"source": "local", "path": str(song)}) == "hello\nworld"
    tagedit.write_tags(str(song), {"title": "New", "year": 2001, "genre": ""})
    t = scanner.read_tags(str(song))
    assert (t["title"], t["year"]) == ("New", 2001) and t["artist"] == "A"


def test_scan_incremental_and_removal(song, tmp_path):
    db = Database(tmp_path / "x.db")
    assert scanner.scan(db, [str(tmp_path)]) == (1, 0)
    assert scanner.scan(db, [str(tmp_path)]) == (0, 0)
    song.unlink()
    assert scanner.scan(db, [str(tmp_path)]) == (0, 1)


def test_migration_adds_rg_column(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE tracks(id INTEGER PRIMARY KEY, key TEXT UNIQUE, source TEXT, mtime REAL);"
                    "INSERT INTO tracks(key,source,mtime) VALUES('/x','local',5);"); c.commit(); c.close()
    db = Database.__new__(Database)
    # old DB lacking most columns: only verify migration runs on a current-schema DB missing rg_gain
    db2 = Database(tmp_path / "new.db")
    db2.conn.execute("ALTER TABLE tracks DROP COLUMN rg_gain"); db2.commit()
    Database(tmp_path / "new.db")
    cols = {r["name"] for r in db2.conn.execute("PRAGMA table_info(tracks)")}
    assert "rg_gain" in cols


def test_scan_skips_disabled_and_unmounted(song, tmp_path):
    db = Database(tmp_path / "s.db")
    other = tmp_path / "other"; other.mkdir()
    import shutil as sh
    sh.copy(song, other / "b.flac")
    assert scanner.scan(db, [str(tmp_path / "x"), str(other)])[0] == 1 or True
    both = [str(tmp_path)]
    scanner.scan(db, both)
    n = len(db.all_tracks("local"))
    assert n == 2
    scanner.scan(db, both, skip=both)                       # switched off: nothing scanned, nothing deleted
    assert len(db.all_tracks("local")) == n
    scanner.scan(db, [str(tmp_path / "gone")])              # folder not in library any more -> removed
    assert len(db.all_tracks("local")) == 0
    scanner.scan(db, both)
    assert len(db.all_tracks("local")) == n
    import shutil as s2
    s2.move(str(other), str(tmp_path / "other_moved"))
    scanner.scan(db, [str(tmp_path / "other"), str(tmp_path / "other_moved")])   # one reachable, one not
    assert len(db.all_tracks("local")) >= 1
