"""Option dialogs for the Tools menu: Organise Files, Convert, ReplayGain scan, Missing Files."""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .. import convert, organizer


def _buttons(dlg, ok_text):
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    return bb


class _FolderPick(QWidget):
    def __init__(self, text=""):
        super().__init__()
        self.edit = QLineEdit(text); b = QPushButton("Browse…")
        b.clicked.connect(self._pick)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.edit, 1); lay.addWidget(b)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.edit.text())
        if d:
            self.edit.setText(d)

    def text(self):
        return self.edit.text().strip()


PATTERN_HELP = ("Tokens: %albumartist% %artist% %album% %title% %track% %disc% %year% %genre%. "
                "Use / to make folders.")


class OrganizeDialog(QDialog):
    def __init__(self, tracks, default_root, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Organise Files"); self.resize(900, 520)
        self.tracks = tracks
        self.dest = _FolderPick(default_root)
        self.pattern = QLineEdit(organizer.DEFAULT_PATTERN)
        self.mode = QComboBox(); self.mode.addItem("Move (library follows the files)", "move")
        self.mode.addItem("Copy (originals stay put)", "copy")
        self.table = QTableWidget(0, 2); self.table.setHorizontalHeaderLabels(["From", "To"])
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(0, 400)
        self.count = QLabel("")
        form = QFormLayout()
        form.addRow("Destination folder", self.dest); form.addRow("Pattern", self.pattern); form.addRow("Action", self.mode)
        hint = QLabel(PATTERN_HELP); hint.setWordWrap(True)
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(hint); lay.addWidget(self.table, 1)
        lay.addWidget(self.count); lay.addWidget(_buttons(self, "Organise"))
        self.dest.edit.textChanged.connect(self._preview); self.pattern.textChanged.connect(self._preview)
        self._preview()

    def moves(self):
        return organizer.plan(self.tracks, self.dest.text(), self.pattern.text() or organizer.DEFAULT_PATTERN)

    def _preview(self):
        mv = self.moves() if self.dest.text() else []
        self.table.setRowCount(min(len(mv), 300))
        for i, (t, new) in enumerate(mv[:300]):
            self.table.setItem(i, 0, QTableWidgetItem(t["path"])); self.table.setItem(i, 1, QTableWidgetItem(new))
        self.count.setText(f"{len(mv)} of {len(self.tracks)} file(s) would change location"
                           + (" (first 300 shown)" if len(mv) > 300 else ""))

    def result_options(self):
        return self.moves(), self.mode.currentData()


class ConvertDialog(QDialog):
    def __init__(self, n, default_root, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Convert"); self.resize(560, 220)
        self.fmt = QComboBox(); self.fmt.addItems(list(convert.FORMATS))
        self.dest = _FolderPick(default_root)
        self.pattern = QLineEdit(organizer.DEFAULT_PATTERN)
        form = QFormLayout()
        form.addRow("Format", self.fmt); form.addRow("Output folder", self.dest); form.addRow("File names", self.pattern)
        note = QLabel(f"{n} local track(s) will be converted with ffmpeg. Originals are never changed; tags and "
                      "cover art are copied where the format allows. " + PATTERN_HELP)
        note.setWordWrap(True)
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(note); lay.addWidget(_buttons(self, "Convert"))

    def accept(self):
        if self.dest.text():
            super().accept()

    def result_options(self):
        return self.fmt.currentText(), self.dest.text(), self.pattern.text() or organizer.DEFAULT_PATTERN


class ReplayGainDialog(QDialog):
    def __init__(self, n, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan ReplayGain")
        self.album = QCheckBox("Also calculate album gain (tracks grouped by album)"); self.album.setChecked(True)
        note = QLabel(f"Measures loudness (EBU R128, reference -18 LUFS) of {n} local track(s) and writes "
                      "ReplayGain 2.0 tags into the files. Enable ReplayGain in Playback → ReplayGain to use them.")
        note.setWordWrap(True)
        lay = QVBoxLayout(self); lay.addWidget(note); lay.addWidget(self.album); lay.addWidget(_buttons(self, "Scan"))


class MissingDialog(QDialog):
    def __init__(self, missing, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Missing Files"); self.resize(760, 420)
        self.list = QListWidget()
        for t in missing:
            it = QListWidgetItem(f'{t["artist"]} – {t["title"]}   ({t["path"]})')
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable); it.setCheckState(Qt.CheckState.Checked)
            it.setData(Qt.ItemDataRole.UserRole, t["id"]); self.list.addItem(it)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(missing)} track(s) point to files that no longer exist. Checked ones will be "
                             "removed from the library (this also removes them from playlists)."))
        lay.addWidget(self.list, 1); lay.addWidget(_buttons(self, "Remove checked"))

    def checked_ids(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]
