"""Inbox processing and folder monitoring."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from hivefi import inbox
from hivefi.ui.watcher import FolderWatcher
from tests.test_gui import win, wait  # noqa: F401  (fixture)

need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
app = QApplication.instance() or QApplication([])


def mk(path, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run(cmd + [str(path)], check=True)


@need_ffmpeg
def test_inbox_moves_named_files(tmp_path):
    ib, lib = tmp_path / "inbox", tmp_path / "lib"
    lib.mkdir()
    mk(ib / "x/new1.flac", title="First", artist="Band", album="Debut", track="1", album_artist="Band")
    mk(ib / "new2.flac", title="Second", artist="Band", album="Debut", track="2", album_artist="Band")
    mk(ib / "twin.flac", title="First", artist="Band", album="Debut", track="1", album_artist="Band")
    (ib / "x/new1.lrc").write_text("[00:01.00]hi")
    (ib / "broken.mp3").write_bytes(b"not audio at all")
    (ib / "notes.txt").write_text("not music")
    moved, left, errors = inbox.process(ib, lib, min_age=0)
    assert len(moved) == 3 and not errors
    assert (lib / "Band/Debut/01 - First.flac").exists() and (lib / "Band/Debut/02 - Second.flac").exists()
    assert (lib / "Band/Debut/01 - First (2).flac").exists()                                  # a clash gets a number
    assert (lib / "Band/Debut/01 - First (2).lrc").read_text() == "[00:01.00]hi"             # the lyrics came along with their song
    assert (ib / "broken.mp3").exists() and (ib / "notes.txt").exists() and str(ib / "broken.mp3") in left
    assert not (ib / "x").exists()                                                            # emptied folders are removed
    assert ib.exists()


@need_ffmpeg
def test_inbox_waits_for_files_that_are_still_arriving(tmp_path):
    ib, lib = tmp_path / "inbox", tmp_path / "lib"
    lib.mkdir()
    mk(ib / "a.flac", title="A", artist="X", album="Y", album_artist="X")
    moved, left, _ = inbox.process(ib, lib, min_age=60)                                       # modified a second ago
    assert moved == [] and left == [str(ib / "a.flac")] and (ib / "a.flac").exists()
    moved, _l, _e = inbox.process(ib, lib, min_age=60, now=time.time() + 120)
    assert len(moved) == 1


def test_inbox_refuses_nonsense(tmp_path):
    (tmp_path / "a").mkdir()
    assert inbox.process(tmp_path / "missing", tmp_path / "a") == ([], [], [])
    assert inbox.process(tmp_path / "a", tmp_path / "nolib") == ([], [], [])
    assert inbox.process(tmp_path / "a", tmp_path / "a") == ([], [], [])                      # inbox = library
    (tmp_path / "a/sub").mkdir()
    assert inbox.process(tmp_path / "a", tmp_path / "a/sub") == ([], [], [])                  # library inside the inbox


def test_watcher_reports_new_files_and_new_folders(tmp_path):
    root = tmp_path / "lib"; (root / "old").mkdir(parents=True)
    w = FolderWatcher(delay_ms=150); hits = []
    w.changed.connect(lambda: hits.append(1))
    assert w.watch([str(root)]) == 2
    (root / "old/song.flac").write_bytes(b"x"); wait(700)
    assert len(hits) == 1                                                                      # one merged notification
    (root / "old/two.flac").write_bytes(b"x"); (root / "old/three.flac").write_bytes(b"x"); wait(700)
    assert len(hits) == 2
    (root / "newdir").mkdir(); wait(700)
    assert len(hits) == 3
    (root / "newdir/deep.flac").write_bytes(b"x"); wait(700)                                   # the new folder is watched too
    assert len(hits) == 4
    w.stop(); (root / "late.flac").write_bytes(b"x"); wait(500)
    assert len(hits) == 4
    small = FolderWatcher(limit=2)
    (root / "a" / "b" / "c").mkdir(parents=True)
    small.watch([str(root)]); assert small.overflow and len(small.fsw.directories()) == 2


@need_ffmpeg
def test_window_picks_up_new_files_and_the_inbox(win, tmp_path):
    w, db = win, win.db
    lib = Path(w.cfg.library_dirs[0])
    assert len(db.all_tracks("local")) == 2
    w.cfg.watch_folders = True
    assert w._setup_watch() >= 1
    w._watcher.delay_ms = 300
    mk(lib / "added.flac", title="Added", artist="New", album="Fresh", album_artist="New")
    for _ in range(40):
        wait(250)
        if len(db.all_tracks("local")) == 3:
            break
    assert len(db.all_tracks("local")) == 3 and any(t["title"] == "Added" for t in db.all_tracks("local"))
    # inbox: a dropped file is organised into the library and shows up
    w.cfg.watch_folders = False
    w.cfg.inbox_dir = str(tmp_path / "inbox"); w.cfg.inbox_target = str(lib)
    mk(tmp_path / "inbox/dropped.flac", title="Dropped", artist="Drop", album="Box", track="3", album_artist="Drop")
    w._process_inbox(min_age=0)
    for _ in range(40):
        wait(250)
        if any(t["title"] == "Dropped" for t in db.all_tracks("local")):
            break
    row = next(t for t in db.all_tracks("local") if t["title"] == "Dropped")
    assert row["path"] == str(lib / "Drop/Box/03 - Dropped.flac") and not (tmp_path / "inbox/dropped.flac").exists()
    assert w._inbox_target() == str(lib)
    w.cfg.inbox_target = "/nonexistent"; assert w._inbox_target() == w.cfg.library_dirs[0]
    # preferences keep the new settings
    from hivefi.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(w.cfg, w)
    dlg.watch.setChecked(True); dlg.inbox.setText("/some/inbox"); dlg.inbox_pattern.setText("%artist%/%title%")
    dlg.apply()
    assert w.cfg.watch_folders and w.cfg.inbox_dir == "/some/inbox" and w.cfg.inbox_pattern == "%artist%/%title%"


@need_ffmpeg
def test_inbox_keeps_an_untagged_compilation_together(tmp_path):
    ib, lib = tmp_path / "inbox", tmp_path / "lib"
    lib.mkdir()
    for i, artist in enumerate(("Artist X", "Artist Y", "Artist Z"), 1):
        mk(ib / "Mix Tape" / f"{i}.flac", title=f"Song {i}", artist=artist, album="Mix Tape", track=str(i))     # no album artist
    mk(ib / "Solo" / "1.flac", title="One", artist="Band", album="Solo", track="1", album_artist="Band")
    moved, left, errors = inbox.process(ib, lib, min_age=0)
    assert len(moved) == 4 and not errors
    assert sorted(p.name for p in (lib / "Various Artists/Mix Tape").iterdir()) == ["01 - Song 1.flac", "02 - Song 2.flac",
                                                                                    "03 - Song 3.flac"]
    assert not (lib / "Artist X").exists() and (lib / "Band/Solo/01 - One.flac").exists()
    # and the library then sees exactly one album, whatever the scan order
    from hivefi import scanner
    from hivefi.db import Database
    db = Database(tmp_path / "l.db"); scanner.scan(db, [str(lib)])
    mix = [t for t in db.all_tracks("local") if t["album"] == "Mix Tape"]
    assert len({t["_ak"] for t in mix}) == 1 and {t["_aa"] for t in mix} == {"Various Artists"}


@need_ffmpeg
def test_folder_changes_do_both_inbox_and_rescan(win, monkeypatch, tmp_path):
    w = win
    calls = []
    monkeypatch.setattr(w, "_process_inbox", lambda *a, **k: calls.append("inbox"))
    monkeypatch.setattr(w, "scan_library", lambda *a, **k: calls.append("scan"))
    w.cfg.inbox_dir = ""; w.cfg.watch_folders = True; w._folders_changed()
    assert calls == ["scan"]
    calls.clear(); w.cfg.inbox_dir = str(tmp_path / "ib"); w._folders_changed()
    assert calls == ["inbox", "scan"]                                  # files dropped straight into the library count too
    calls.clear(); w.cfg.watch_folders = False; w._folders_changed()
    assert calls == ["inbox"]
