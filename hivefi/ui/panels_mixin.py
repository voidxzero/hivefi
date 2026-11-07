"""Panel system of the main window: movable toolbars (controls / seek bar / volume / search / view mode),
visualizer + waveform seek bar, lock layout, named layouts, and the session that is restored on start."""
import json

from PyQt6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup, QKeySequence
from PyQt6.QtWidgets import (QDockWidget, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
                             QSlider, QStyle, QTabBar, QToolBar, QVBoxLayout, QWidget)

from .. import session, waveform
from .seekbar import STYLES as SEEK_STYLES, SeekBar
from .visualizer import STYLES as VIS_STYLES, VisualizerPanel

BUILTIN_LAYOUTS = ("Default", "Minimal", "Now Playing focus", "Everything")
STATE_VERSION = 2          # bump when docks are added/removed: an older saved arrangement is then ignored


def _b64(ba: QByteArray) -> str:
    return bytes(ba.toBase64()).decode()


def _unb64(s: str) -> QByteArray:
    return QByteArray.fromBase64(s.encode())


class PaneDock(QDockWidget):
    """A dock that says so when the user closes it (a tab hidden behind another tab is not closed)."""
    closed = pyqtSignal()

    def closeEvent(self, e):
        super().closeEvent(e)
        self.closed.emit()


class PanelsMixin:
    # ------------------------------------------------------------------ toolbars
    def _make_toolbar(self, title, name):
        tb = QToolBar(title, self)
        tb.setObjectName(name); tb.setIconSize(QSize(24, 24))
        tb.setMovable(self.edit_mode)
        tb.setAllowedAreas(Qt.ToolBarArea.AllToolBarAreas)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbars.append(tb)
        return tb

    def _build_toolbars(self):
        S = QStyle.StandardPixmap
        self.toolbars: list[QToolBar] = []
        # --- controls
        tb = self._make_toolbar("Controls", "tb_controls")
        self.act_prev = tb.addAction(self._icon(S.SP_MediaSkipBackward), "Previous", self.player.previous)
        self.act_play = tb.addAction(self._icon(S.SP_MediaPlay), "Play/Pause", self.player.toggle)
        self.act_stop = tb.addAction(self._icon(S.SP_MediaStop), "Stop", self.player.stop)
        self.act_next = tb.addAction(self._icon(S.SP_MediaSkipForward), "Next", self.player.next)
        tb.addSeparator()
        self.btn_shuffle = QPushButton("Shuffle"); self.btn_shuffle.setCheckable(True)
        self.btn_shuffle.setChecked(self.cfg.shuffle); self.btn_shuffle.toggled.connect(self._set_shuffle)
        self.btn_repeat = QPushButton(); self.btn_repeat.clicked.connect(self._cycle_repeat)
        self._update_repeat_label()
        tb.addWidget(self.btn_shuffle); tb.addWidget(self.btn_repeat)
        # --- volume
        tv = self._make_toolbar("Volume", "tb_volume")
        vol_icon = QLabel(); vol_icon.setPixmap(self._icon(S.SP_MediaVolume).pixmap(18, 18))
        self.vol = QSlider(Qt.Orientation.Horizontal); self.vol.setRange(0, 100); self.vol.setFixedWidth(110)
        self.vol.setValue(self.cfg.volume); self.vol.valueChanged.connect(self._set_volume)
        tv.addWidget(vol_icon); tv.addWidget(self.vol)
        # --- search
        ts = self._make_toolbar("Search", "tb_search")
        self.search = QLineEdit(); self.search.setPlaceholderText("Search…"); self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(160); self.search.setMaximumWidth(320)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self._search_timer = QTimer(self, singleShot=True, interval=180,
                                    timeout=lambda: self._on_search(self.search.text()))
        ts.addWidget(self.search)
        # --- view mode
        tm = self._make_toolbar("View Mode", "tb_mode")
        self.mode_group = QActionGroup(self)
        self.act_browser = tm.addAction("Columns"); self.act_albums = tm.addAction("Albums")
        for act, mode in ((self.act_browser, "browser"), (self.act_albums, "albums")):
            act.setCheckable(True); self.mode_group.addAction(act)
            act.triggered.connect(lambda _c, m=mode: self._set_view_mode(m))
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        # --- seek bar (second row so it can stretch across the window)
        tk = self._make_toolbar("Seek Bar", "tb_seek")
        self.lbl_pos = QLabel("0:00"); self.lbl_pos.setMinimumWidth(44)
        self.seek = SeekBar(self.cfg.seek_style)
        self.seek.sliderMoved.connect(self.player.seek)
        self.lbl_dur = QLabel("0:00"); self.lbl_dur.setMinimumWidth(44)
        for w in (self.lbl_pos, self.seek, self.lbl_dur):
            tk.addWidget(w)
        self._wave_token = 0

    def _make_worker(self, fn):
        from .main_window import Worker
        return Worker(fn, self)

    # ------------------------------------------------------------------ track list as a movable panel
    def _build_track_list_slot(self):
        """Central area: the track list (plus a strip with a pop-out button in Edit Mode). Popping it out puts the
        very same table into a dock that can be moved, floated, tabbed and resized like every other panel."""
        self.central_box = QWidget()
        lay = self.central_layout = QVBoxLayout(self.central_box)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.tracks_strip = QWidget(); sl = QHBoxLayout(self.tracks_strip); sl.setContentsMargins(6, 2, 6, 2)
        sl.addWidget(QLabel("Track List")); sl.addStretch()
        pop = QPushButton("⇱ Pop out into a movable panel"); pop.setFlat(True)
        pop.clicked.connect(lambda: self._set_tracks_detached(True))
        sl.addWidget(pop)
        self.tracks_hint = QLabel("The track list is in its own panel.\nClose that panel (or untick View → Panels → "
                                  "Track List) to bring it back here.")
        self.tracks_hint.setAlignment(Qt.AlignmentFlag.AlignCenter); self.tracks_hint.hide()
        lay.addWidget(self.tracks_strip); lay.addWidget(self.table, 1); lay.addWidget(self.tracks_hint, 1)
        self.tracks_strip.hide()
        self.setCentralWidget(self.central_box)
        self.dock_tracks = PaneDock("Track List", self); self.dock_tracks.setObjectName("dock_tracks")
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_tracks)
        self.dock_tracks.hide()
        self.dock_tracks.closed.connect(lambda: self._set_tracks_detached(False))
        self.tracks_detached = False

    def _set_tracks_detached(self, on, show=True):
        on = bool(on)
        if on:
            if not self.tracks_detached:
                size, where = self.central_box.size(), self.central_box.mapToGlobal(self.central_box.rect().topLeft())
                self.dock_tracks.setWidget(self.table)
                self.tracks_hint.show(); self.tracks_strip.hide()
                if show:
                    self.dock_tracks.setFloating(True)
                    self.dock_tracks.resize(size); self.dock_tracks.move(where + QPoint(30, 30))
            if show:
                self.dock_tracks.show()
        elif self.tracks_detached:
            self.dock_tracks.hide()
            self.central_layout.insertWidget(1, self.table, 1)
            self.table.show(); self.tracks_hint.hide()
            self.tracks_strip.setVisible(self.edit_mode)
        self.tracks_detached = on
        self.cfg.tracks_detached = on
        if hasattr(self, "act_tracks_panel") and self.act_tracks_panel.isChecked() != on:
            self.act_tracks_panel.blockSignals(True); self.act_tracks_panel.setChecked(on); self.act_tracks_panel.blockSignals(False)

    def _sync_tracks_slot(self):
        """After a saved arrangement was restored: the table follows the track list panel's visibility."""
        want = not self.dock_tracks.isHidden()
        if want != self.tracks_detached:
            self._set_tracks_detached(want, show=False)

    # ------------------------------------------------------------------ waveform seek bar
    def _load_waveform(self, t):
        """Compute (or fetch from cache) the waveform of the playing track in the background."""
        self._wave_token += 1
        if not self.seek.wants_waveform() or not t:
            self.seek.set_peaks(None); return
        if t["source"] == "remote" and not self.cfg.waveform_remote:
            self.seek.set_peaks(None); return
        token, tc, api, cfg = self._wave_token, dict(t), self.api, self.cfg
        self.seek.set_peaks(None, busy=True)
        w = self._make_worker(lambda _p: waveform.get_peaks(tc, api, cfg))
        w.done.connect(lambda peaks, tk=token: tk == self._wave_token and self.seek.set_peaks(peaks))
        self._run(w)

    def _set_seek_style(self, name):
        self.cfg.seek_style = name
        self.seek.set_style(name)
        self._load_waveform(self.player.current)

    def _set_waveform_remote(self, on):
        self.cfg.waveform_remote = on
        self._load_waveform(self.player.current)

    # ------------------------------------------------------------------ visualizer
    def _build_visualizer(self):
        c = self.cfg
        self.vis = VisualizerPanel(c.vis_style, c.vis_bars, c.vis_peaks, c.vis_colors, c.vis_custom)
        self.dock_vis = self._dock("Visualizer", self.vis, "dock_vis", Qt.DockWidgetArea.RightDockWidgetArea)
        self.vis.styleChanged.connect(self._set_vis_style)
        self.vis.optionsChanged.connect(self._vis_options)
        self.vis.colorsChanged.connect(self._set_vis_colors)
        self.dock_vis.visibilityChanged.connect(self._vis_visible)
        self.player.mp.visData.connect(self.vis.feed)
        self._update_vis_availability()

    def _set_vis_style(self, name):
        self.cfg.vis_style = name
        self.vis.set_style(name)

    def _set_vis_colors(self, scheme, custom):
        self.cfg.vis_colors, self.cfg.vis_custom = scheme, list(custom)
        self.vis.set_colors(scheme, custom)

    def _vis_options(self):
        self.cfg.vis_bars, self.cfg.vis_peaks = self.vis.n_bars, self.vis.peaks_on

    def _vis_visible(self, visible):
        if self.cfg.vis_active != bool(visible):
            self.cfg.vis_active = bool(visible)
            self.player.configure(relook=False)         # switches the analysers' message posting on/off, no rebuild

    def _update_vis_availability(self):
        c, mp = self.cfg, self.player.mp
        self.vis.rate = int(c.output_rate or 44100)
        self.vis.available = bool(mp.engine == "gst" and c.vis_enabled and not c.bit_perfect)
        self.vis.note = ("Bit-perfect mode bypasses all processing, including the analysers." if c.bit_perfect else
                         "The visualizer is switched off in Preferences → General." if not c.vis_enabled else
                         "The visualizer needs the GStreamer audio engine (spectrum/level plugins).")
        self.vis.update()

    # ------------------------------------------------------------------ edit mode
    edit_mode = False                  # never persisted: the app always starts with the layout locked

    def _set_edit_mode(self, on):
        """On: panels can be dragged, floated, tabbed, resized and closed. Off: nothing about the layout can change
        (title bars are hidden, separators and splitters are dead, toolbars are fixed)."""
        self.edit_mode = on = bool(on)
        F = QDockWidget.DockWidgetFeature
        for d in self.findChildren(QDockWidget):
            d.setFeatures(F.DockWidgetMovable | F.DockWidgetFloatable | F.DockWidgetClosable if on
                          else F.NoDockWidgetFeatures)
            d.setTitleBarWidget(None if on else QWidget())      # an empty widget hides the title bar
        for tb in self.toolbars:
            tb.setMovable(on)
        for sp in (self.browser_split,):
            for i in range(1, sp.count()):
                sp.handle(i).setEnabled(on)
        for bar in self.findChildren(QTabBar):                  # tabs of stacked docks
            bar.setMovable(on)
        if hasattr(self, "tracks_strip"):
            self.tracks_strip.setVisible(on and not self.tracks_detached)
        if hasattr(self, "act_edit") and self.act_edit.isChecked() != on:
            self.act_edit.blockSignals(True); self.act_edit.setChecked(on); self.act_edit.blockSignals(False)
        if hasattr(self, "btn_edit"):
            self.btn_edit.blockSignals(True); self.btn_edit.setChecked(on); self.btn_edit.blockSignals(False)
            self.btn_edit.setText("✎ Editing layout — click to lock" if on else "✎ Edit layout")
        if self.isVisible():
            self.statusBar().showMessage("Edit mode: drag panels and toolbars, resize them, close or float them. "
                                         "Turn it off to lock the layout." if on else "Layout locked", 5000)

    def _apply_edit_state(self):
        self._set_edit_mode(self.edit_mode)

    def eventFilter(self, obj, ev):
        """Dock separators are handled by the main window itself: while the layout is locked they must not react."""
        if obj is self and not self.edit_mode and ev.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick):
            return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ layouts
    def _layout_snapshot(self):
        return {"state": _b64(self.saveState(STATE_VERSION)), "browser": self._splits_json(self.browser_split)}

    def _apply_layout_snapshot(self, snap):
        self.restoreState(_unb64(snap["state"]), STATE_VERSION)
        self._sync_tracks_slot()
        self._apply_edit_state()                                      # restoreState resets dock features
        self._apply_splits(self.browser_split, snap.get("browser", ""))

    def _build_layout_menu(self):
        m = self.layout_menu; m.clear()
        for name in BUILTIN_LAYOUTS:
            m.addAction(name, lambda n=name: self._apply_builtin_layout(n))
        saved = self.cfg.layouts
        if saved:
            m.addSeparator()
            for name in sorted(saved):
                m.addAction(name, lambda n=name: self._apply_layout_snapshot(self.cfg.layouts[n]))
        m.addSeparator()
        m.addAction("Save Current Layout…", self._save_layout)
        if saved:
            dm = m.addMenu("Delete Layout")
            for name in sorted(saved):
                dm.addAction(name, lambda n=name: (self.cfg.layouts.pop(n, None), self.cfg.save()))

    def _save_layout(self):
        name, ok = QInputDialog.getText(self, "Save layout", "Name:")
        name = name.strip()
        if not ok or not name or name in BUILTIN_LAYOUTS:
            return
        self.cfg.layouts[name] = self._layout_snapshot(); self.cfg.save()
        self.statusBar().showMessage(f"Layout '{name}' saved", 4000)

    def _apply_builtin_layout(self, name):
        self._set_tracks_detached(False)
        self.restoreState(self._default_state, STATE_VERSION)
        docks = (self.dock_sources, self.dock_browser, self.dock_np, self.dock_next,
                 self.dock_lyrics, self.dock_info, self.dock_eq, self.dock_vis)
        show = {"Default": (self.dock_sources, self.dock_browser, self.dock_np),
                "Minimal": (),
                "Now Playing focus": (self.dock_np, self.dock_lyrics, self.dock_vis),
                "Everything": docks}[name]
        for d in docks:
            d.setVisible(d in show)
        self._size_default_docks()
        for tb in self.toolbars:
            tb.setVisible(True)
        self._apply_edit_state()

    # ------------------------------------------------------------------ splitters
    @staticmethod
    def _splits_json(sp):
        return json.dumps({"sizes": sp.sizes()})

    @staticmethod
    def _apply_splits(sp, raw):
        """Restore splitter sizes proportionally (the window may have a different size than when they were saved)."""
        try:
            sizes = json.loads(raw)["sizes"]
        except (ValueError, KeyError, TypeError):
            return
        total_now = (sp.height() if sp.orientation() == Qt.Orientation.Vertical else sp.width()) or sum(sp.sizes())
        saved = sum(sizes) or 1
        if len(sizes) == sp.count() and total_now > 0:
            sp.setSizes([max(1, round(s * total_now / saved)) for s in sizes])

    def _restore_splits(self):
        self._apply_splits(self.browser_split, self.cfg.browser_state)

    # ------------------------------------------------------------------ session
    def _session_state(self):
        m, P = self.model, self.player
        pos = P.mp.position() if P.current else 0
        playing = P.mp.playbackState().name == "PlayingState" if P.current else False
        sel = [t["id"] for t in self._selected()[:50]]
        top = self.table.indexAt(self.table.viewport().rect().topLeft())
        return {
            "source": list(self.source), "genre": m.genre, "artist": m.artist, "album": m.album,
            "search": self.search.text(), "queue": [t["id"] for t in P.queue if t.get("id")], "index": P.index,
            "pos": pos, "playing": playing, "selected": sel,
            "top": m.tracks[top.row()]["id"] if top.isValid() and top.row() < len(m.tracks) else None,
            "sort": [self.table.horizontalHeader().sortIndicatorSection(),
                     int(self.table.horizontalHeader().sortIndicatorOrder().value)],
        }

    def _save_session(self):
        try:
            self.cfg.geometry = _b64(self.saveGeometry())
            self.cfg.state = _b64(self.saveState(STATE_VERSION))
            self.cfg.header_state = _b64(self.table.horizontalHeader().saveState())
            self.cfg.browser_state = self._splits_json(self.browser_split)
            self.cfg.save()
            session.save(self._session_state())
        except Exception:  # noqa: BLE001 - never let a failed autosave bother the user
            pass

    def _restore_session(self):
        """Put everything back the way it was when the app was closed. Always ends in a loaded track list."""
        s = session.load()
        try:
            src = tuple(s.get("source") or ())
            if len(src) == 2 and (src[0], src[1]) in self._items:
                self.source = src
                self.refresh_playlists()                           # highlights the saved source in the tree
            self.search.blockSignals(True); self.search.setText(s.get("search", "")); self.search.blockSignals(False)
            self.model.text = (s.get("search") or "").lower()
        except Exception:  # noqa: BLE001
            s = {}
        self.load_source()
        if not s:
            return
        try:
            saved = (s.get("genre"), s.get("artist"), s.get("album"))
            if any(saved):
                self._restore_browser(saved); self._refresh_grid(); self._update_status()
            ids = s.get("selected") or []
            row = next((r for i in ids if (r := self.model.row_of(i)) is not None), None)
            if row is None and s.get("top") is not None:
                row = self.model.row_of(s["top"])
            if row is not None:
                idx = self.model.index(row, 2)
                if ids and self.model.row_of(ids[0]) is not None:
                    self.table.selectRow(row)
                self.table.scrollTo(idx, self.table.ScrollHint.PositionAtTop if not ids else
                                    self.table.ScrollHint.PositionAtCenter)
            mode = self.cfg.resume_mode
            if mode != "off" and s.get("queue"):
                by_id = {t["id"]: t for t in self._tracks()}
                queue = [by_id[i] for i in s["queue"] if i in by_id]
                cur = s.get("index", -1)
                cur_id = s["queue"][cur] if 0 <= cur < len(s["queue"]) else None
                idx = next((k for k, t in enumerate(queue) if t["id"] == cur_id), 0 if queue else -1)
                if queue and idx >= 0 and (queue[idx]["source"] == "local" or self.api):
                    self.player.restore(queue, idx, int(s.get("pos") or 0),
                                        play=(mode == "play" and bool(s.get("playing"))))
        except Exception as e:  # noqa: BLE001 - a stale session must never prevent the app from starting
            self.statusBar().showMessage(f"Could not restore the last session: {e}", 6000)

    def _start_autosave(self):
        self._autosave = QTimer(self, interval=30_000, timeout=self._save_session)
        self._autosave.start()
