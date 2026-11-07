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


def test_batch_tags_export_and_cover(win, monkeypatch, tmp_path):
    import subprocess
    from PyQt6.QtWidgets import QFileDialog
    from hivefi import art, tagedit
    w, db = win, win.db
    local = db.all_tracks("local")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    # batch tag tools: lower-case the titles of two local files, then Title Case them back through the dialog
    from hivefi.ui import main_window as mw
    from hivefi.ui.tagtools_dialog import TagToolsDialog
    dlg = TagToolsDialog(local)
    dlg.op.setCurrentIndex(dlg.op.findData("lower")); dlg.field.setCurrentIndex(dlg.field.findData("title"))
    assert dlg.count.text().startswith("2 of 2") and dlg.ok.isEnabled()                 # "L1", "L2" -> "l1", "l2"
    dlg.op.setCurrentIndex(dlg.op.findData("find_replace")); dlg.find.setText("L"); dlg.repl.setText("song ")
    assert dlg.table.rowCount() == 2 and dlg.ok.isEnabled()
    dlg.regex.setChecked(True); dlg.find.setText("(")                       # a half-typed regex must not crash
    assert dlg.table.rowCount() == 0 and not dlg.ok.isEnabled()
    dlg.regex.setChecked(False); dlg.find.setText("L")
    dlg.exec = lambda: True
    monkeypatch.setattr(mw, "TagToolsDialog", lambda tracks, parent=None: dlg)          # use the configured dialog
    w._tag_tools(); wait(1500)
    assert sorted(t["title"] for t in db.all_tracks("local")) == ["song 1", "song 2"]
    import mutagen
    assert sorted(mutagen.File(t["path"], easy=True)["title"][0] for t in db.all_tracks("local")) == ["song 1", "song 2"]
    # export the current view
    out = tmp_path / "export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    w.source = ("local", None); w.load_source()
    w._export_tracks(); wait(500)
    assert out.exists() and out.read_text(encoding="utf-8-sig").count("\n") == 3
    # embedded cover
    png = tmp_path / "c.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=16x16", "-frames:v", "1", str(png)], check=True)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(png), ""))
    w._set_cover(db.all_tracks("local")); wait(1500)
    assert all(art._embedded(t["path"])[:4] == b"\x89PNG" for t in db.all_tracks("local"))
    w._set_cover(db.all_tracks("local"), remove=True); wait(1500)
    assert all(art._embedded(t["path"]) is None for t in db.all_tracks("local"))
