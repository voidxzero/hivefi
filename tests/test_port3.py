import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import shutil
import subprocess

import pytest
from PyQt6.QtWidgets import QApplication

from hivefi import duplicates, lyrics, scanner, tagedit
from hivefi.ui.panels import LyricsPanel

APP = None
LRC = "[ar:x]\n[00:01.00]one\n[00:03.50][00:10.00]two\n[00:05.00]\n[offset:0]\n"


def test_parse_lrc():
    assert lyrics.is_synced(LRC)
    assert lyrics.parse_lrc(LRC) == [(1000, "one"), (3500, "two"), (5000, ""), (10000, "two")]
    assert lyrics.parse_lrc("[offset:500]\n[00:01.00]a\n[00:02.00]b")[0] == (500, "a")
    assert not lyrics.is_synced("plain\ntext")


def test_synced_file_and_plain_view(tmp_path):
    s = tmp_path / "a.flac"; s.write_bytes(b"")
    (tmp_path / "a.lrc").write_text(LRC)
    t = {"source": "local", "path": str(s)}
    assert lyrics.is_synced(lyrics.find_lyrics(t))
    assert lyrics.get_lyrics(t).splitlines()[:2] == ["one", "two"]


def test_panel_follows_position():
    global APP
    APP = QApplication.instance() or QApplication([])
    p = LyricsPanel(); got = []
    p.seekRequested.connect(got.append)
    p.show_lyrics("T", LRC)
    assert p.synced and p.lines.count() == 4
    p.set_position(4000); assert p._cur == 1
    p.set_position(500); assert p._cur == -1
    p.set_position(11000); assert p._cur == 3
    p.lines.itemDoubleClicked.emit(p.lines.item(1)); assert got == [3500]
    p.show_lyrics("T", "just words"); assert not p.synced


def mk(i, title, artist, dur, suffix="mp3", br=128, src="local"):
    return dict(id=i, title=title, artist=artist, duration=dur, suffix=suffix, bitrate=br, source=src, size=1)


def test_duplicates():
    tr = [mk(1, "Song (Remastered)", "A", 200), mk(2, "Song", "A", 201, "flac", 900),
          mk(3, "Song", "A", 300), mk(4, "Other", "A", 200), mk(5, "Song", "B", 200),
          mk(6, "Song feat. X", "A", 200, "mp3", 320, "remote")]
    g = duplicates.find_duplicates(tr)
    assert len(g) == 1
    assert [t["id"] for t in g[0]] == [2, 6, 1]          # flac first, then 320 kbps, then 128


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
@pytest.mark.parametrize("ext", ["flac", "mp3", "m4a", "ogg"])
def test_rating_roundtrip(tmp_path, ext):
    f = tmp_path / f"a.{ext}"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", str(f)], check=True)
    for r in (4, 2, 0):
        tagedit.write_rating(str(f), r)
        assert scanner.read_tags(str(f))["rating"] == r


def test_legacy_folder_migration(tmp_path, monkeypatch):
    from hivefi import config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = tmp_path / config.LEGACY_APP; old.mkdir()
    (old / "config.json").write_text('{"skin": "MusicBee Classic", "server_url": "http://x"}')
    assert config.config_dir() == tmp_path / "hivefi" and not old.exists()
    c = config.Config.load()
    assert c.server_url == "http://x" and c.skin == "Classic"
