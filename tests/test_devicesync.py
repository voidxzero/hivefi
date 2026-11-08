"""Sync to a folder/device: copy, convert, skip what is current, delete extras, playlists, server downloads."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import subprocess
from pathlib import Path

import pytest

from hivefi import devicesync as ds, scanner
from hivefi.db import Database
from hivefi.subsonic import Subsonic
from tests.mock_server import State, make_server
from tests.test_gui import win, wait  # noqa: F401  (fixture)

need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def mk(path, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run(cmd + [str(path)], check=True)


@pytest.fixture
def lib(tmp_path):
    m = tmp_path / "music"
    mk(m / "a.flac", title="Lossless", artist="Band", album="Alb", track="1", album_artist="Band")
    mk(m / "b.mp3", title="Lossy", artist="Band", album="Alb", track="2", album_artist="Band")
    (m / "a.lrc").write_text("[00:01.00]x")
    db = Database(tmp_path / "l.db"); scanner.scan(db, [str(m)])
    return db, {t["title"]: t for t in db.all_tracks("local")}, tmp_path / "device"


@need_ffmpeg
def test_plan_and_copy(lib):
    db, t, dev = lib
    o = ds.Options(dest=str(dev))
    pl = ds.plan(list(t.values()), o)
    assert sorted(i.rel for i in pl.items) == ["Band/Alb/01 - Lossless.flac", "Band/Alb/02 - Lossy.mp3"]
    assert pl.count("copy") == 2 and pl.count("skip") == 0 and pl.bytes_to_write > 0
    st = ds.execute(pl, o)
    assert st["copied"] == 2 and not st["errors"]
    assert (dev / "Band/Alb/01 - Lossless.flac").exists() and (dev / "Band/Alb/01 - Lossless.lrc").exists()   # lyrics follow
    pl2 = ds.plan(list(t.values()), o)
    assert pl2.count("skip") == 2 and pl2.count("copy") == 0                                                # second run: nothing
    st2 = ds.execute(pl2, o)
    assert st2["copied"] == 0 and st2["skipped"] == 2
    (dev / "Band/Alb/02 - Lossy.mp3").write_bytes(b"short")                                                   # damaged copy is redone
    assert ds.plan(list(t.values()), o).count("copy") == 1


@need_ffmpeg
def test_convert_only_lossless(lib):
    db, t, dev = lib
    o = ds.Options(dest=str(dev), fmt="MP3 192 kbps", convert="lossless")
    pl = ds.plan(list(t.values()), o)
    acts = {Path(i.rel).name: i.action for i in pl.items}
    assert acts == {"01 - Lossless.mp3": "convert", "02 - Lossy.mp3": "copy"}                    # lossy files are never re-encoded
    st = ds.execute(pl, o)
    assert st["converted"] == 1 and st["copied"] == 1 and not st["errors"]
    assert scanner.read_tags(str(dev / "Band/Alb/01 - Lossless.mp3"))["title"] == "Lossless"
    assert ds.plan(list(t.values()), o).count("skip") == 2
    o2 = ds.Options(dest=str(dev / "all"), fmt="Opus 128 kbps", convert="all")
    assert {i.action for i in ds.plan(list(t.values()), o2).items} == {"convert"}
    o3 = ds.Options(dest=str(dev / "never"), fmt="Opus 128 kbps", convert="never")
    assert {i.action for i in ds.plan(list(t.values()), o3).items} == {"copy"}
    o4 = ds.Options(dest=str(dev / "same"), fmt="MP3 192 kbps", convert="all")
    assert {Path(i.rel).name: i.action for i in ds.plan(list(t.values()), o4).items}["02 - Lossy.mp3"] == "copy"   # already mp3


@need_ffmpeg
def test_delete_only_removes_what_an_earlier_sync_wrote(lib):
    db, t, dev = lib
    both = list(t.values())
    o = ds.Options(dest=str(dev))
    # things that were on the device before Hivefi: other people's audio, notes, and empty folders
    (dev / "WhatsApp/Media").mkdir(parents=True); (dev / "WhatsApp/Media/voice.opus").write_bytes(b"voice note")
    (dev / "Old Artist/Old Album").mkdir(parents=True); (dev / "Old Artist/Old Album/mine.mp3").write_bytes(b"x")
    (dev / "Ringtones").mkdir(); (dev / "Alarms/Empty").mkdir(parents=True)
    (dev / "notes.txt").write_text("keep me")
    pl = ds.plan(both, ds.Options(dest=str(dev), delete_extra=True))
    assert pl.extra == []                                                  # never synced here before: nothing is Hivefi's
    st = ds.execute(pl, o)
    assert st["copied"] == 2 and (dev / ds.MANIFEST).exists()
    assert (dev / "Ringtones").is_dir() and (dev / "Alarms/Empty").is_dir()   # unrelated empty folders survive a sync
    assert ds.read_manifest(dev) == {"Band/Alb/01 - Lossless.flac", "Band/Alb/02 - Lossy.mp3"}
    # now sync only one track with "delete" on: the other one, which we put there, goes - and nothing else
    o2 = ds.Options(dest=str(dev), delete_extra=True)
    pl = ds.plan([t["Lossless"]], o2)
    assert pl.extra == ["Band/Alb/02 - Lossy.mp3"]
    assert ds.plan([t["Lossless"]], ds.Options(dest=str(dev))).extra == []     # off by default
    st = ds.execute(pl, o2, playlists={"My: Mix/1": [t["Lossless"]]})
    assert st["deleted"] == 1 and not (dev / "Band/Alb/02 - Lossy.mp3").exists() and (dev / "Band/Alb/01 - Lossless.flac").exists()
    assert (dev / "WhatsApp/Media/voice.opus").read_bytes() == b"voice note"      # a stranger's audio file is untouched
    assert (dev / "Old Artist/Old Album/mine.mp3").exists() and (dev / "notes.txt").exists()
    assert (dev / "Ringtones").is_dir() and (dev / "Alarms/Empty").is_dir()
    assert ds.read_manifest(dev) == {"Band/Alb/01 - Lossless.flac"}
    m3u = (dev / "Playlists/My_ Mix_1.m3u8").read_text().splitlines()
    assert m3u == ["#EXTM3U", "../Band/Alb/01 - Lossless.flac"] and st["playlists"] == 1                       # relative paths
    # folders emptied by a deletion are removed, up to (not including) the device root
    pl = ds.plan([], o2); assert pl.extra == ["Band/Alb/01 - Lossless.flac"]
    ds.execute(pl, o2)
    assert not (dev / "Band").exists() and dev.is_dir() and (dev / "WhatsApp").is_dir()
    # a file the user already removed from the device is simply forgotten
    assert ds.read_manifest(dev) == set()


@need_ffmpeg
def test_failed_files_are_not_remembered(lib):
    db, t, dev = lib
    o = ds.Options(dest=str(dev), fmt="Opus 128 kbps", convert="all")
    bad = dict(t["Lossless"], path=t["Lossless"]["path"])
    Path(bad["path"]).write_bytes(b"garbage")                                  # ffmpeg cannot convert this
    st = ds.execute(ds.plan([bad], o), o)
    assert st["errors"] and ds.read_manifest(dev) == set()


@need_ffmpeg
def test_name_clashes_missing_files_and_cancel(lib):
    db, t, dev = lib
    twin = dict(t["Lossless"], id=999, path=t["Lossless"]["path"])
    pl = ds.plan([t["Lossless"], twin], ds.Options(dest=str(dev)))
    assert sorted(i.rel for i in pl.items) == ["Band/Alb/01 - Lossless (2).flac", "Band/Alb/01 - Lossless.flac"]
    gone = dict(t["Lossy"], path="/nonexistent/x.mp3")
    pl = ds.plan([gone, t["Lossy"]], ds.Options(dest=str(dev)))
    assert pl.missing_files == 1 and len(pl.items) == 1
    pl = ds.plan(list(t.values()), ds.Options(dest=str(dev / "c")))
    st = ds.execute(pl, ds.Options(dest=str(dev / "c")), cancelled=lambda: True)
    assert st["cancelled"] and st["copied"] == 0
    assert ds.plan([dict(source="radio", id=-1, url="x")], ds.Options(dest=str(dev))).items == []             # radio is skipped


def test_server_tracks_are_downloaded(tmp_path):
    st = State(); srv = make_server(st)
    try:
        api = Subsonic(f"http://127.0.0.1:{srv.server_port}", "u", "pw")
        tr = dict(source="remote", remote_id="5", title="Remote", artist="Band", album="Alb", album_artist="Band",
                  suffix="mp3", track_no=5, size=100, disc_no=1)
        o = ds.Options(dest=str(tmp_path / "dev"))
        pl = ds.plan([tr], o)
        assert [i.action for i in pl.items] == ["download"] and pl.items[0].rel == "Band/Alb/05 - Remote.mp3"
        res = ds.execute(pl, o, api=api)
        assert res["downloaded"] == 1 and not res["errors"]
        assert (tmp_path / "dev/Band/Alb/05 - Remote.mp3").read_bytes() == b"FAKE-AUDIO-5"
        assert not list((tmp_path / "dev").rglob("*.part"))
        assert ds.plan([tr], o).count("skip") == 1
        bad = ds.execute(ds.plan([dict(tr, remote_id="6", track_no=6)], o), o, api=Subsonic(api.base, "u", "wrong"))
        assert bad["errors"] and not list((tmp_path / "dev").rglob("*.part"))                               # failures are reported
    finally:
        srv.shutdown()


@need_ffmpeg
def test_dialog_and_menu(win, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    from hivefi.ui import main_window as mw
    from hivefi.ui.devicesync_dialog import DeviceSyncDialog
    w = win
    src = [("All music", w._tracks(), None), ("Playlist: Mix", w._tracks("local"), "Mix")]
    dlg = DeviceSyncDialog(src)
    assert dlg.source.count() == 2 and not dlg.mode.isEnabled()
    assert dlg.preview() is None and "destination" in dlg.report.text()
    dlg.dest.setText(str(tmp_path / "dev")); pl = dlg.preview()
    assert pl.count("copy") == 2 and "to copy" in dlg.report.text() and "to download" in dlg.report.text()
    dlg.fmt.setCurrentIndex(1); assert dlg.mode.isEnabled() and dlg.options().fmt == "FLAC (lossless)"
    dlg.fmt.setCurrentIndex(0); dlg.source.setCurrentIndex(1)
    assert dlg.playlist_name() == "Mix"; dlg.pl_box.setChecked(False); assert dlg.playlist_name() is None
    # through the window: sync the local playlist-less library to a folder
    dev = tmp_path / "phone"
    class Fake(DeviceSyncDialog):
        def exec(self):
            self.dest.setText(str(dev)); self.source.setCurrentIndex(self.source.count() - 1 if False else 1)
            return True
    monkeypatch.setattr(mw, "DeviceSyncDialog", Fake)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    w._sync_device(); wait(2500)
    assert len(list(dev.rglob("*.flac"))) == 2                                                              # the two local files
    assert "Synced: 2 copied" in w.statusBar().currentMessage() or "up to date" in w.statusBar().currentMessage()
