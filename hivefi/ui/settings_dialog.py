from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QListWidgetItem, QTableWidget, QTableWidgetItem, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget, QComboBox)

from ..subsonic import Subsonic, SubsonicError


class PanelsTab(QWidget):
    """Which panels there are, whether they are shown, and where they live (like MusicBee's panel layout editor)."""
    LOCATIONS = ["Left", "Right", "Top", "Bottom", "Floating"]

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Panel", "Show", "Location", "Stacked as a tab with"])
        self.table.verticalHeader().hide(); self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 50); self.table.setColumnWidth(2, 110)
        self.edit = QCheckBox("Allow moving and resizing panels on screen (Edit Mode)")
        self.edit.setChecked(win.edit_mode)
        self.layouts = QComboBox()
        self.btn_apply = QPushButton("Apply now"); self.btn_apply.setToolTip("Apply the changes without closing Preferences")
        self.btn_apply.clicked.connect(self.apply_to_window)
        load, save, delete = QPushButton("Load"), QPushButton("Save current as…"), QPushButton("Delete")
        reset = QPushButton("Reset to default")
        load.clicked.connect(self._load_layout); save.clicked.connect(self._save_layout)
        delete.clicked.connect(self._delete_layout); reset.clicked.connect(lambda: self._run_layout("Default"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Choose which panels are shown and where they sit. Toolbars (controls, seek bar, volume, "
                             "search, view mode) can go to any edge; panels can also float or be stacked as tabs."))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout(); row.addWidget(self.btn_apply); row.addStretch(); row.addWidget(reset); lay.addLayout(row)
        lay.addWidget(self.edit)
        lay.addWidget(QLabel("Layouts:"))
        row2 = QHBoxLayout()
        for w in (self.layouts, load, save, delete):
            row2.addWidget(w)
        row2.setStretch(0, 1); lay.addLayout(row2)
        self.refresh()

    # ---- table <-> window
    def refresh(self):
        self.specs = self.win.panel_specs()
        self._initial = {s["key"]: (s["visible"], s["location"], s["tab_with"]) for s in self.specs}
        titles = {s["key"]: s["title"] for s in self.specs if s["kind"] == "dock"}
        self.table.setRowCount(len(self.specs))
        for r, s in enumerate(self.specs):
            name = QTableWidgetItem(s["title"] + ("   (toolbar)" if s["kind"] == "toolbar" else ""))
            name.setFlags(Qt.ItemFlag.ItemIsEnabled); self.table.setItem(r, 0, name)
            show = QCheckBox(); show.setChecked(s["visible"])
            cell = QWidget(); cl = QHBoxLayout(cell); cl.setContentsMargins(12, 0, 0, 0); cl.addWidget(show)
            self.table.setCellWidget(r, 1, cell)
            loc = QComboBox()
            locs = self.LOCATIONS if s["kind"] == "dock" else ["Top", "Bottom", "Left", "Right"]
            loc.addItems(locs); loc.setCurrentText(s["location"] if s["location"] in locs else locs[0])
            self.table.setCellWidget(r, 2, loc)
            tab = QComboBox(); tab.addItem("—", "")
            if s["kind"] == "dock":
                for key, title in titles.items():
                    if key != s["key"]:
                        tab.addItem(title, key)
                tab.setCurrentIndex(max(0, tab.findData(s["tab_with"])))
            else:
                tab.setEnabled(False)
            self.table.setCellWidget(r, 3, tab)
            loc.currentTextChanged.connect(lambda text, t=tab: t.setEnabled(text != "Floating"))
            tab.setEnabled(s["kind"] == "dock" and loc.currentText() != "Floating")
        self.layouts.clear()
        from .panels_mixin import BUILTIN_LAYOUTS
        for n in BUILTIN_LAYOUTS:
            self.layouts.addItem(n, ("builtin", n))
        for n in sorted(self.win.cfg.layouts):
            self.layouts.addItem(n, ("saved", n))

    def rows(self):
        out = []
        for r, s in enumerate(self.specs):
            out.append({"key": s["key"], "visible": self.table.cellWidget(r, 1).findChild(QCheckBox).isChecked(),
                        "location": self.table.cellWidget(r, 2).currentText(),
                        "tab_with": self.table.cellWidget(r, 3).currentData() or ""})
        return out

    def apply_to_window(self):
        changed = [r for r in self.rows()
                   if (r["visible"], r["location"], r["tab_with"]) != self._initial[r["key"]]]
        if changed:                                              # panels that were not touched keep their sizes
            self.win.apply_panel_specs(changed)
        if self.edit.isChecked() != self.win.edit_mode:
            self.win._set_edit_mode(self.edit.isChecked())
        self.refresh()

    # ---- layouts
    def _run_layout(self, name):
        self.win._apply_builtin_layout(name); self.refresh()

    def _load_layout(self):
        kind, name = self.layouts.currentData() or (None, None)
        if kind == "builtin":
            self._run_layout(name)
        elif kind == "saved":
            self.win._apply_layout_snapshot(self.win.cfg.layouts[name]); self.refresh()

    def _save_layout(self):
        self.win._save_layout(); self.refresh()

    def _delete_layout(self):
        kind, name = self.layouts.currentData() or (None, None)
        if kind == "saved":
            self.win.cfg.layouts.pop(name, None); self.win.cfg.save(); self.refresh()


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
        self.resume = QComboBox()
        for label, v in (("Reopen the last queue, paused at the saved position", "paused"),
                         ("Reopen the last queue and keep playing if it was playing", "play"),
                         ("Don't restore the queue", "off")):
            self.resume.addItem(label, v)
        self.resume.setCurrentIndex(max(0, self.resume.findData(cfg.resume_mode)))
        self.vis_on = QCheckBox("Visualizer analysers in the audio chain (about 1% CPU)")
        self.vis_on.setChecked(cfg.vis_enabled)
        gl.addWidget(QLabel("On start:")); gl.addWidget(self.resume); gl.addWidget(self.vis_on)
        gl.addWidget(self.tray); gl.addWidget(self.close_tray); gl.addWidget(self.updates); gl.addWidget(self.gh_token)
        gl.addStretch()
        tabs.addTab(gen, "General")

        self.panels_tab = PanelsTab(parent) if hasattr(parent, "panel_specs") else None
        if self.panels_tab:
            tabs.addTab(self.panels_tab, "Panels")

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
        if self.panels_tab:
            self.panels_tab.apply_to_window()
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
        c.resume_mode, c.vis_enabled = self.resume.currentData(), self.vis_on.isChecked()
        c.write_ratings = self.write_rt.isChecked()
        c.check_updates, c.github_token = self.updates.isChecked(), self.gh_token.text().strip()
        c.save()
