import shutil
import subprocess

import pytest

from hivefi import convert, organizer, rgscan, scanner, tagedit
from hivefi.db import Database

need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def make(path, vol=0.5, freq=440, secs=3, **tags):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={secs}",
           "-af", f"volume={vol}"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run(cmd + [str(path)], check=True)


def lib(tmp_path, files):
    db = Database(tmp_path / "db.sqlite")
    scanner.scan(db, [str(tmp_path / "music")])
    return db, db.all_tracks("local")


def test_render_and_clean():
    t = dict(album_artist="AC/DC", artist="AC/DC", album="Back: In Black?", title="Hells Bells", track_no=1,
             disc_no=1, year=1980)
    assert organizer.render(t) == "AC_DC/Back_ In Black/01 - Hells Bells"
    assert organizer.render(t, "%year% - %album%/%title%") == "1980 - Back_ In Black/Hells Bells"
    t.update(disc_no=2, track_no=3)
    assert organizer.render(t) == "AC_DC/Back_ In Black/2-03 - Hells Bells"
    assert organizer.render({}) == "Unknown Artist/Unknown Album/Untitled"


@need_ffmpeg
def test_organize_move_and_collisions(tmp_path):
    m = tmp_path / "music"; (m / "junk").mkdir(parents=True)
    make(m / "junk/a.flac", title="Song", artist="Art", album="Alb", track="2")
    make(m / "junk/b.flac", title="Song", artist="Art", album="Alb", track="2")     # same target name
    (m / "junk/a.lrc").write_text("[00:01.00]x")
    db, tracks = lib(tmp_path, None)
    ids = {t["path"]: t["id"] for t in tracks}
    moves = organizer.plan(tracks, m / "sorted")
    assert sorted(p.split("/")[-1] for _, p in moves) == ["02 - Song (2).flac", "02 - Song.flac"]
    done, errs = organizer.execute(db, moves, "move", prune_to=m)
    assert done == 2 and not errs
    assert not (m / "junk").exists()                                   # emptied folder removed
    assert (m / "sorted/Art/Alb").is_dir()
    now = db.all_tracks("local")
    assert {t["id"] for t in now} == set(ids.values())                 # same rows, new paths
    assert all(t["path"].startswith(str(m / "sorted")) for t in now)
    assert any((m / "sorted/Art/Alb").glob("*.lrc"))                   # sidecar followed
    assert organizer.plan(now, m / "sorted") == []                     # already organised


@need_ffmpeg
def test_organize_copy_keeps_library(tmp_path):
    m = tmp_path / "music"; m.mkdir()
    make(m / "a.flac", title="S", artist="A", album="B")
    db, tracks = lib(tmp_path, None)
    organizer.execute(db, organizer.plan(tracks, tmp_path / "out"), "copy")
    assert (m / "a.flac").exists() and list((tmp_path / "out").rglob("*.flac"))
    assert db.all_tracks("local")[0]["path"] == str(m / "a.flac")


@need_ffmpeg
@pytest.mark.parametrize("label,ext", [("MP3 192 kbps", "mp3"), ("Opus 128 kbps", "opus"),
                                       ("FLAC (lossless)", "flac"), ("AAC 256 kbps (m4a)", "m4a")])
def test_convert(tmp_path, label, ext):
    m = tmp_path / "music"; m.mkdir()
    make(m / "a.flac", title="Hello", artist="Me", album="Disc", track="4")
    db, tracks = lib(tmp_path, None)
    done, errs = convert.convert_tracks(tracks, tmp_path / "out", label)
    assert done == 1 and not errs, errs
    out = tmp_path / "out/Me/Disc" / f"04 - Hello.{ext}"
    assert out.exists()
    tags = scanner.read_tags(str(out))
    assert tags["title"] == "Hello" and tags["artist"] == "Me"
    done, errs = convert.convert_tracks(tracks, tmp_path / "out", label)      # second run skips
    assert done == 0 and "already exists" in errs[0]


@need_ffmpeg
@pytest.mark.parametrize("ext", ["flac", "mp3", "m4a", "ogg"])
def test_replaygain_scan(tmp_path, ext):
    m = tmp_path / "music"; m.mkdir()
    make(m / f"loud.{ext}", vol=0.7, title="L", artist="A", album="X", track="1")
    make(m / f"quiet.{ext}", vol=0.07, title="Q", artist="A", album="X", track="2")
    db, tracks = lib(tmp_path, None)
    done, errs = rgscan.scan_tracks(db, tracks)
    assert done == 2 and not errs, errs
    by = {t["title"]: t for t in db.all_tracks("local")}
    assert by["Q"]["rg_gain"] - by["L"]["rg_gain"] == pytest.approx(20, abs=1.0)    # 20 dB apart
    assert by["L"]["rg_gain"] == pytest.approx(6.9, abs=1.0)      # sine of amplitude 0.0875 is about -24.9 LUFS
    # tags are on disk and a rescan reads the same value
    assert scanner.read_tags(str(m / f"loud.{ext}"))["rg_gain"] == pytest.approx(by["L"]["rg_gain"], abs=0.01)
    import mutagen
    f = mutagen.File(str(m / f"quiet.{ext}"))
    assert any("album_gain" in str(k).lower() for k in (f.tags.keys() if f.tags else []))


def test_album_loudness_weighting():
    assert rgscan.album_loudness([(-20, 100), (-20, 50)]) == pytest.approx(-20)
    # a long loud track dominates a short quiet one
    assert rgscan.album_loudness([(-10, 300), (-30, 30)]) > -11
