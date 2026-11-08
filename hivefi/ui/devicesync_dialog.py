"""Sync to Device dialog: what to send, where, in which format, and a preview of what would change."""
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QVBoxLayout)

from .. import convert, devicesync, organizer
from ..stats import fmt_size


class DeviceSyncDialog(QDialog):
    def __init__(self, sources, parent=None, api=None):
        """sources: [(label, [tracks], playlist name or None)]"""
        super().__init__(parent)
        self.setWindowTitle("Sync to Device"); self.resize(620, 0)
        self.sources, self.api = sources, api
        self.source = QComboBox()
        for label, tracks, _pl in sources:
            self.source.addItem(f"{label}  ({len(tracks)} tracks)")
        self.dest = QLineEdit(); b = QPushButton("Browse…"); b.clicked.connect(self._pick)
        row = QHBoxLayout(); row.addWidget(self.dest, 1); row.addWidget(b)
        self.fmt = QComboBox(); self.fmt.addItem("Keep files as they are", None)
        for label in convert.FORMATS:
            self.fmt.addItem(f"Convert to {label}", label)
        self.mode = QComboBox()
        self.mode.addItem("only lossless files (FLAC, WAV…)", "lossless"); self.mode.addItem("every file", "all")
        self.pattern = QLineEdit(organizer.DEFAULT_PATTERN)
        self.delete = QCheckBox("Remove music that an earlier sync put there and that is no longer part of it")
        self.delete.setToolTip("Only files Hivefi itself copied are ever removed; other music and recordings on the "
                               "device are left alone")
        self.pl_box = QCheckBox("Write the playlist to a Playlists folder on the device"); self.pl_box.setChecked(True)
        self.report = QLabel("")
        self.report.setWordWrap(True)
        prev = QPushButton("Preview"); prev.clicked.connect(self.preview)
        form = QFormLayout()
        form.addRow("Send", self.source); form.addRow("To", row); form.addRow("Format", self.fmt)
        form.addRow("Convert", self.mode); form.addRow("File names", self.pattern)
        form.addRow(self.pl_box); form.addRow(self.delete)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.bb.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText("Sync")
        self.bb.accepted.connect(self._accept); self.bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(form)
        lay.addWidget(QLabel("Files that are already on the device and up to date are skipped.\n"
                             "Server (Navidrome) tracks are downloaded in their original format."))
        lay.addWidget(prev); lay.addWidget(self.report); lay.addWidget(self.bb)
        self.fmt.currentIndexChanged.connect(lambda _i: self.mode.setEnabled(self.fmt.currentData() is not None))
        self.mode.setEnabled(False)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the device or folder", self.dest.text())
        if d:
            self.dest.setText(d)

    def options(self) -> devicesync.Options:
        return devicesync.Options(dest=self.dest.text().strip(), pattern=self.pattern.text() or organizer.DEFAULT_PATTERN,
                                  fmt=self.fmt.currentData(), convert=self.mode.currentData(),
                                  delete_extra=self.delete.isChecked())

    def tracks(self):
        return self.sources[self.source.currentIndex()][1]

    def playlist_name(self):
        return self.sources[self.source.currentIndex()][2] if self.pl_box.isChecked() else None

    def plan(self):
        return devicesync.plan(self.tracks(), self.options())

    def preview(self):
        if not self.dest.text().strip():
            self.report.setText("Choose a destination first."); return None
        pl = self.plan()
        parts = [f"{pl.count('copy')} to copy", f"{pl.count('convert')} to convert",
                 f"{pl.count('download')} to download", f"{pl.count('skip')} already there"]
        if self.delete.isChecked():
            parts.append(f"{len(pl.extra)} to delete")
        if pl.missing_files:
            parts.append(f"{pl.missing_files} missing on this computer")
        self.report.setText("  ·  ".join(parts) + f"  ·  about {fmt_size(pl.bytes_to_write)} to write")
        return pl

    def _accept(self):
        if self.dest.text().strip():
            self.accept()
        else:
            self.report.setText("Choose a destination first.")
