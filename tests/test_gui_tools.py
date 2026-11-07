"""GUI wiring of the Tools menu (offscreen, dialogs stubbed)."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from hivefi.ui import main_window as mw
from tests.test_gui import win, wait  # noqa: F401  (fixture)


def test_tools(win, monkeypatch, tmp_path):
    w, db = win, win.db
    local = db.all_tracks("local")
    assert len(local) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    # save queue as playlist
    w.player.play_list(local, 0); wait(200)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Queue copy", True))
    w._save_queue_playlist()
    pl = next(p for p in db.playlists() if p["name"] == "Queue copy")
    assert len(db.playlist_tracks(pl["id"])) == 2

    # editable copy of a preset auto playlist
    monkeypatch.setattr(mw.SmartDialog, "exec", lambda self: True)
    n = len(db.smart_playlists())
    w._copy_smart("Top Rated")
    sp = db.smart_playlists()
    assert len(sp) == n + 1 and sp[-1]["name"] == "Top Rated (copy)"
    assert sp[-1]["spec"]["rules"][0]["field"] == "rating" and w.source == ("smart", sp[-1]["id"])

    # organise (move) through the dialog wiring; the queued track's path follows the move
    dest = tmp_path / "sorted"
    monkeypatch.setattr(mw.OrganizeDialog, "exec", lambda self: self.dest.edit.setText(str(dest)) or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w._organize(); wait(1500)
    assert len(list(dest.rglob("*.flac"))) == 2
    assert all(t["path"].startswith(str(dest)) for t in db.all_tracks("local"))
    assert all(t["path"].startswith(str(dest)) for t in w.player.queue)

    # replaygain scan
    monkeypatch.setattr(mw.ReplayGainDialog, "exec", lambda self: True)
    w._scan_rg(); wait(2500)
    assert all(t["rg_gain"] is not None for t in db.all_tracks("local"))

    # convert
    out = tmp_path / "conv"
    monkeypatch.setattr(mw.ConvertDialog, "exec",
                        lambda self: self.dest.edit.setText(str(out)) or self.fmt.setCurrentText("MP3 192 kbps") or True)
    w._convert(); wait(2500)
    assert len(list(out.rglob("*.mp3"))) == 2

    # missing files
    gone = Path(db.all_tracks("local")[0]["path"]); gone.unlink()
    monkeypatch.setattr(mw.MissingDialog, "exec", lambda self: True)
    w._find_missing()
    assert len(db.all_tracks("local")) == 1
