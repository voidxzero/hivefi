from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QListWidgetItem, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget, QComboBox)

from ..subsonic import Subsonic, SubsonicError


_RUNNING: set = set()


class _Call(QThread):
    """Run a blocking network call off the UI thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent); self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001 - shown in the dialog
            self.failed.emit(str(e))


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._closed = False
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
        self.server_libs = QListWidget(); self.server_libs.setMaximumHeight(90)
        self.server_libs.setToolTip("Which of the server's libraries (music folders) to sync. All ticked = everything.")
        self._fill_server_libs(cfg.server_folder_names)
        load = QPushButton("Load libraries from server"); load.clicked.connect(self._load_libs)
        self.libs_status = QLabel("")
        for lbl, w in (("Server URL", self.url), ("Username", self.user), ("Password", self.pw),
                       ("Sync every", self.interval), ("Streaming quality", self.bitrate)):
            f.addRow(lbl, w)
        f.addRow(self.enabled)
        f.addRow("Libraries to sync", self.server_libs)
        f.addRow(load, self.libs_status)
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

    def _api(self):
        return Subsonic(self.url.text().strip(), self.user.text().strip(), self.pw.text(), timeout=10)

    def _async(self, fn, ok, fail):
        t = _Call(fn)                                    # no parent: it may outlive the dialog
        _RUNNING.add(t); t.finished.connect(lambda: _RUNNING.discard(t))
        t.done.connect(lambda r: self._closed or ok(r))  # ignore results that arrive after the dialog closed
        t.failed.connect(lambda e: self._closed or fail(e))
        t.start()

    def done(self, r):
        self._closed = True
        super().done(r)

    def _test(self):
        self.status.setText("Testing…")
        self._async(lambda: self._api().ping(), lambda _r: self.status.setText("✔ Connected"),
                    lambda e: self.status.setText(f"✘ {e}"))

    def _fill_server_libs(self, names: dict):
        """names: id -> name. Ticked = synced; the stored selection [] means all."""
        chosen = set(self.cfg.server_folders)
        self.server_libs.clear()
        for fid, name in names.items():
            it = QListWidgetItem(name); it.setData(Qt.ItemDataRole.UserRole, fid)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if not chosen or fid in chosen else Qt.CheckState.Unchecked)
            self.server_libs.addItem(it)
        self.server_libs.setEnabled(bool(names))

    def _load_libs(self):
        self.libs_status.setText("Loading…")
        def ok(folders):
            self.libs_status.setText(f"{len(folders)} librar{'y' if len(folders) == 1 else 'ies'}" if folders
                                     else "The server reports no separate libraries")
            self._fill_server_libs({f["id"]: f["name"] for f in folders})
        self._async(lambda: self._api().music_folders(), ok, lambda e: self.libs_status.setText(f"✘ {e}"))

    def chosen_server_folders(self):
        """(ids to sync — [] means all —, id -> name of every known library)."""
        items = [self.server_libs.item(i) for i in range(self.server_libs.count())]
        names = {it.data(Qt.ItemDataRole.UserRole): it.text() for it in items}
        ticked = [it.data(Qt.ItemDataRole.UserRole) for it in items if it.checkState() == Qt.CheckState.Checked]
        return ([] if len(ticked) in (0, len(items)) else ticked), names

    def apply(self):
        c = self.cfg
        c.library_dirs = self.folders()
        c.libraries_off = self.disabled_folders() + ([] if self.enabled.isChecked() else [c.NAVIDROME])
        c.server_url, c.username, c.password = self.url.text().strip(), self.user.text().strip(), self.pw.text()
        c.sync_interval_min, c.sync_on_start = self.interval.value(), self.on_start.isChecked()
        c.scrobble, c.max_bitrate = self.scrobble.isChecked(), self.bitrate.currentData()
        c.show_tray, c.close_to_tray = self.tray.isChecked(), self.close_tray.isChecked()
        c.server_folders, names = self.chosen_server_folders()
        if names:
            c.server_folder_names = names
        c.write_ratings = self.write_rt.isChecked()
        c.check_updates, c.github_token = self.updates.isChecked(), self.gh_token.text().strip()
        c.save()
