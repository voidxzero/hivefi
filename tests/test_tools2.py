"""Export, batch tag tools and embedded covers."""
import csv
import json
import shutil
import subprocess

import pytest

from hivefi import art, libexport, scanner, tagedit, tagtools
from hivefi.db import Database

need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def test_title_case():
    tc = tagtools.title_case
    assert tc("the lord of the rings") == "The Lord of the Rings"
    assert tc("don't stop believin'") == "Don't Stop Believin'"
    assert tc("LIVE AT THE DJ BOOTH, VOL. II") == "Live at the Dj Booth, Vol. II"       # shouting is normalised
    assert tc("a tribute to the DJ and MC crew") == "A Tribute to the DJ and MC Crew"   # mixed case keeps acronyms
    assert tc("rock-and-roll (live) in paris") == "Rock-And-Roll (Live) in Paris"
    assert tc("  a song for  you  ") == "A Song for  You"
    assert tc("what is this song about") == "What Is This Song About"
    assert tc("") == ""


def test_find_replace_and_tidy():
    fr = tagtools.find_replace
    assert fr("Song (Remastered 2011)", " (Remastered 2011)", "") == "Song"
    assert fr("A.B.C", ".", " ") == "A B C"                                   # plain text, not a regex
    assert fr("Track 01 - Name", r"^Track \d+ - ", "", regex=True) == "Name"
    assert fr("abc", "B", "x") == "axc" and fr("abc", "B", "x", case=True) == "abc"
    assert fr("x", "", "y") == "x"
    assert fr("a\\b", "a", "c\\d") == "c\\d\\b"                                    # backslashes are plain text too
    assert tagtools.tidy("  too   many  spaces ") == "too many spaces"


def test_parse_filename():
    pf = tagtools.parse_filename
    assert pf("/m/01 - Hello World.mp3", "%track% - %title%") == {"track_no": 1, "title": "Hello World"}
    assert pf("/m/Artist - Album - 07 - Song.flac", "%artist% - %album% - %track% - %title%") == \
        {"artist": "Artist", "album": "Album", "track_no": 7, "title": "Song"}
    assert pf("/m/random name.mp3", "%track% - %title%") == {}
    assert pf("/m/2001 - Name.mp3", "%year% - %title%") == {"year": 2001, "title": "Name"}


def test_apply_operation():
    t = dict(title="the end (live)", artist="  x  ", path="/m/05 - New Name.mp3", track_no=0, album="Al")
    assert tagtools.apply_operation(t, "title_case") == {"title": "The End (Live)"}
    assert tagtools.apply_operation(t, "tidy", field="artist") == {"artist": "x"}
    assert tagtools.apply_operation(t, "upper", field="album") == {"album": "AL"}
    assert tagtools.apply_operation(t, "lower", field="album") == {"album": "al"}
    assert tagtools.apply_operation(t, "title_case", field="album") == {}                 # already fine: no change
    assert tagtools.apply_operation(t, "from_filename", pattern="%track% - %title%") == \
        {"track_no": 5, "title": "New Name"}
    assert tagtools.apply_operation(t, "find_replace", field="title", find="end", repl="start") == \
        {"title": "the start (live)"}
    assert tagtools.apply_operation(dict(title=5), "tidy") == {}


def test_export_formats(tmp_path):
    ts = [dict(title="Sön, \"quoted\"", artist="A", album="B", album_artist="A", genre="g", year=2000, track_no=1,
               disc_no=1, duration=61.26, bitrate=320, suffix="mp3", rating=4, play_count=2, last_played=0,
               date_added=86400 * 365, source="local", path="/m/a.mp3"),
          dict(title="Remote", artist="R", album="B", source="remote", path=None, rpath="Band/x.mp3", duration=10)]
    p = tmp_path / "lib.csv"
    assert libexport.export_any(ts, p) == 2
    rows = list(csv.reader(open(p, encoding="utf-8-sig")))
    head = rows[0]
    assert head[:3] == ["Title", "Artist", "Album"] and rows[1][0] == 'Sön, "quoted"'
    assert rows[1][head.index("Length (s)")] == "61.3" and rows[1][head.index("Last played")] == ""
    assert rows[2][-1] == "Band/x.mp3"                                              # server tracks show their server path
    assert {"Composer", "Comment", "BPM"} <= set(head)
    p = tmp_path / "lib.json"
    assert libexport.export_any(ts, p, ["title", "rating"]) == 2
    assert json.load(open(p))[0] == {"title": 'Sön, "quoted"', "rating": 4}
    p = tmp_path / "lib.m3u8"
    assert libexport.export_any(ts, p) == 1                                       # only local files are playable offline
    assert open(p, encoding="utf-8").read().splitlines() == ["#EXTM3U", "#EXTINF:61,A - Sön, \"quoted\"", "/m/a.mp3"]


@need_ffmpeg
@pytest.mark.parametrize("ext", ["flac", "mp3", "m4a", "ogg", "opus"])
def test_embedded_cover_roundtrip(tmp_path, ext):
    f = tmp_path / f"a.{ext}"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", str(f)], check=True)
    png = tmp_path / "c.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1", str(png)],
                   check=True)
    assert art._embedded(str(f)) is None
    tagedit.write_cover(str(f), png.read_bytes())
    got = art._embedded(str(f))
    assert got is not None and got[:8] == b"\x89PNG\r\n\x1a\n"
    tagedit.remove_cover(str(f))
    assert art._embedded(str(f)) is None
    with pytest.raises(ValueError):
        tagedit.write_cover(str(f), b"not an image")


@need_ffmpeg
def test_cover_cache_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    f = tmp_path / "a.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", str(f)], check=True)
    t = {"source": "local", "path": str(f)}
    assert art.get_art(t) is None
    png = tmp_path / "c.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=16x16", "-frames:v", "1", str(png)],
                   check=True)
    tagedit.write_cover(str(f), png.read_bytes())
    art.invalidate(t)
    assert art.get_art(t)[:4] == b"\x89PNG"


def test_playlist_formats(tmp_path):
    import xml.etree.ElementTree as ET
    ts = [dict(source="local", path="/m/a b&c.mp3", title="T & <x>", artist="A", album="B", duration=61.5),
          dict(source="remote", title="r", artist="x")]
    assert libexport.export_any(ts, tmp_path / "p.pls") == 1
    assert (tmp_path / "p.pls").read_text().splitlines() == ["[playlist]", "File1=/m/a b&c.mp3", "Title1=A - T & <x>",
                                                              "Length1=61", "NumberOfEntries=1", "Version=2"]
    assert libexport.export_any(ts, tmp_path / "p.xspf") == 1
    root = ET.parse(tmp_path / "p.xspf").getroot()
    ns = {"x": "http://xspf.org/ns/0/"}
    tr = root.find("x:trackList/x:track", ns)
    assert tr.find("x:location", ns).text == "file:///m/a%20b%26c.mp3" and tr.find("x:title", ns).text == "T & <x>"
    assert tr.find("x:duration", ns).text == "61500"
