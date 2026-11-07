from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget, QComboBox)

from ..subsonic import Subsonic, SubsonicError


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Preferences")
        self.resize(520, 420)
        tabs = QTabWidget()

        # Library
        lib = QWidget(); lv = QVBoxLayout(lib)
        lv.addWidget(QLabel("Music folders (untick to switch a library off without removing it):"))
        self.dirs = QListWidget()
        for d in cfg.library_dirs: self._add_item(d, cfg.lib_enabled(d))
        lv.addWidget(self.dirs)
        row = QHBoxLayout()
        add = QPushButton("Add…"); rem = QPushButton("Remove")
        add.clicked.connect(self._add_dir)
        rem.clicked.connect(lambda: [self.dirs.takeItem(self.dirs.row(i)) for i in self.dirs.selectedItems()])
        row.addWidget(add); row.addWidget(rem); row.addStretch(); lv.addLayout(row)
        tabs.addTab(lib, "Library")

        # Server
        srv = QWidget(); f = QFormLayout(srv)
        self.enabled = QCheckBox("Enable this library (untick to hide it and pause syncing)")
        self.enabled.setChecked(cfg.lib_enabled(cfg.NAVIDROME))
        self.url = QLineEdit(cfg.server_url); self.url.setPlaceholderText("https://navidrome.example.com")
        self.user = QLineEdit(cfg.username)
        self.pw = QLineEdit(cfg.password); self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.interval = QSpinBox(); self.interval.setRange(0, 1440); self.interval.setSuffix(" min (0 = manual)")
        self.interval.setValue(cfg.sync_interval_min)
        self.on_start = QCheckBox("Sync when the app starts"); self.on_start.setChecked(cfg.sync_on_start)
        self.scrobble = QCheckBox("Scrobble plays to the server"); self.scrobble.setChecked(cfg.scrobble)
        self.bitrate = QComboBox()
        for label, v in (("Original (no transcoding)", 0), ("320 kbps", 320), ("192 kbps", 192), ("128 kbps", 128)):
            self.bitrate.addItem(label, v)
        self.bitrate.setCurrentIndex(max(0, self.bitrate.findData(cfg.max_bitrate)))
        self.status = QLabel("")
        test = QPushButton("Test connection"); test.clicked.connect(self._test)
        for lbl, w in (("Server URL", self.url), ("Username", self.user), ("Password", self.pw),
                       ("Sync every", self.interval), ("Streaming quality", self.bitrate)):
            f.addRow(lbl, w)
        f.addRow(self.enabled)
        f.addRow(self.on_start); f.addRow(self.scrobble); f.addRow(test, self.status)
        tabs.addTab(srv, "Navidrome / Subsonic")

        gen = QWidget(); gl = QVBoxLayout(gen)
        self.tray = QCheckBox("Show system tray icon"); self.tray.setChecked(cfg.show_tray)
        self.close_tray = QCheckBox("Keep playing in the tray when the window is closed")
        self.close_tray.setChecked(cfg.close_to_tray)
        self.write_rt = QCheckBox("Write star ratings into local files' tags"); self.write_rt.setChecked(cfg.write_ratings)
        gl.addWidget(self.write_rt)
        self.updates = QCheckBox("Check for updates on startup"); self.updates.setChecked(cfg.check_updates)
        self.gh_token = QLineEdit(cfg.github_token); self.gh_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.gh_token.setPlaceholderText("GitHub token (optional; defaults to the gh CLI login)")
        gl.addWidget(self.tray); gl.addWidget(self.close_tray); gl.addWidget(self.updates); gl.addWidget(self.gh_token)
        gl.addStretch()
        tabs.addTab(gen, "General")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs); lay.addWidget(bb)

    def _add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Add music folder")
        if d and d not in self.folders():
            self._add_item(d, True)

    def _add_item(self, path, enabled):
        from PyQt6.QtWidgets import QListWidgetItem
        it = QListWidgetItem(path)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self.dirs.addItem(it)

    def disabled_folders(self):
        return [self.dirs.item(i).text() for i in range(self.dirs.count())
                if self.dirs.item(i).checkState() != Qt.CheckState.Checked]

    def folders(self):
        return [self.dirs.item(i).text() for i in range(self.dirs.count())]

    def _test(self):
        self.status.setText("Testing…"); self.status.repaint()
        try:
            Subsonic(self.url.text(), self.user.text(), self.pw.text(), timeout=10).ping()
            self.status.setText("✔ Connected")
        except SubsonicError as e:
            self.status.setText(f"✘ {e}")

    def apply(self):
        c = self.cfg
        c.library_dirs = self.folders()
        c.libraries_off = self.disabled_folders() + ([] if self.enabled.isChecked() else [c.NAVIDROME])
        c.server_url, c.username, c.password = self.url.text().strip(), self.user.text().strip(), self.pw.text()
        c.sync_interval_min, c.sync_on_start = self.interval.value(), self.on_start.isChecked()
        c.scrobble, c.max_bitrate = self.scrobble.isChecked(), self.bitrate.currentData()
        c.show_tray, c.close_to_tray = self.tray.isChecked(), self.close_tray.isChecked()
        c.write_ratings = self.write_rt.isChecked()
        c.check_updates, c.github_token = self.updates.isChecked(), self.gh_token.text().strip()
        c.save()
