"""Panel system of the main window: movable toolbars (controls / seek bar / volume / search / view mode),
visualizer + waveform seek bar, lock layout, named layouts, and the session that is restored on start."""
import json

from PyQt6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QDockWidget, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                             QPushButton, QSizePolicy, QSlider, QStyle, QTabBar, QToolBar, QVBoxLayout, QWidget)

from .. import session, waveform
from .seekbar import STYLES as SEEK_STYLES, SeekBar
from .visualizer import STYLES as VIS_STYLES, VisualizerPanel

BUILTIN_LAYOUTS = ("Default", "Minimal", "Now Playing focus", "Everything")
STATE_VERSION = 3          # bump when docks are added/removed: an older saved arrangement is then ignored


def _b64(ba: QByteArray) -> str:
    return bytes(ba.toBase64()).decode()


def _unb64(s: str) -> QByteArray:
    return QByteArray.fromBase64(s.encode())


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
        vol_icon = self.vol_icon = QLabel(); vol_icon.setPixmap(self._icon(S.SP_MediaVolume).pixmap(18, 18))
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
        self.vis.fullscreenRequested.connect(self.open_fullscreen_vis)
        self.dock_vis.visibilityChanged.connect(self._vis_visible)
        self.player.mp.visData.connect(self.vis.feed)
        self._update_vis_availability()

    def _set_vis_style(self, name):
        self.cfg.vis_style = name
        self.vis.set_style(name)

    def open_fullscreen_vis(self, _checked=False):
        """The full-screen visualizer: MilkDrop-style feedback effects and classic media-player effects."""
        from .viz_fullscreen import FullscreenVisualizer
        if getattr(self, "fs_vis", None) is not None:
            self.fs_vis.activateWindow(); return self.fs_vis
        c, mp = self.cfg, self.player.mp
        if mp.engine != "gst" or not c.vis_enabled or c.bit_perfect:
            self.statusBar().showMessage("The visualizer needs the GStreamer engine with the analysers on and "
                                         "bit-perfect mode off.", 8000)
            return None
        fs = self.fs_vis = FullscreenVisualizer(c, self)
        mp.visData.connect(fs.feed)
        self.player.trackChanged.connect(fs.set_track)
        fs.set_track(self.player.current)
        was_active = c.vis_active
        c.vis_active = True
        self.player.configure(relook=False)                    # the analysers must be posting while it is shown
        def closed():
            try:
                mp.visData.disconnect(fs.feed); self.player.trackChanged.disconnect(fs.set_track)
            except TypeError:
                pass
            self.fs_vis = None
            c.vis_active = bool(was_active or self.dock_vis.toggleViewAction().isChecked())
            self.player.configure(relook=False)
            c.save()
            fs.deleteLater()
        fs.closed.connect(closed)
        fs.effectFailed.connect(lambda name, msg: self.statusBar().showMessage(
            f"Visualizer effect '{name}' hit a problem and was skipped ({msg})", 10000))
        fs.start()
        return fs

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
        was, self.edit_mode = self.edit_mode, bool(on)
        on = self.edit_mode
        if on and not was and not getattr(self, "_in_revert", False):
            self._edit_snapshot = self._layout_snapshot()           # what "Revert" goes back to
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
        if hasattr(self, "edit_toolbar"):
            self.edit_toolbar.setVisible(on)
        self._style_edit_mode(on)
        for d in self.findChildren(QDockWidget):                   # right-click a panel's title bar for its menu
            if not d.property("hf_menu"):
                d.setProperty("hf_menu", True)
                d.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                d.customContextMenuRequested.connect(lambda pos, d=d: self._dock_menu(d, pos))
        if hasattr(self, "act_edit") and self.act_edit.isChecked() != on:
            self.act_edit.blockSignals(True); self.act_edit.setChecked(on); self.act_edit.blockSignals(False)
        if hasattr(self, "btn_edit"):
            self.btn_edit.blockSignals(True); self.btn_edit.setChecked(on); self.btn_edit.blockSignals(False)
            self.btn_edit.setText("✎ Editing layout — click to lock" if on else "✎ Edit layout")
        if self.isVisible() and on != was:
            self.statusBar().showMessage("Edit mode: drag panels and toolbars by their grips and title bars, resize "
                                         "them, close or float them. Esc or Done locks the layout." if on
                                         else "Layout locked", 6000)

    def _style_edit_mode(self, on):
        """Editable panels get a dashed accent frame so it is obvious what can be moved."""
        accent = getattr(self, "skin_tokens", {}).get("accent", "#4fc3f7")
        self.setStyleSheet(f"QDockWidget {{ border: 1px dashed {accent}; }}"
                           f"QToolBar {{ border: 1px dashed {accent}; }}" if on else "")

    def _build_edit_bar(self):
        """A strip across the top of the central area while Edit Mode is on."""
        bar = self.edit_bar = QFrame()
        lay = QHBoxLayout(bar); lay.setContentsMargins(8, 2, 8, 2)
        hint = QLabel("<b>✎ Editing layout</b> — drag panels by their title bars and toolbars by their grips; "
                      "right-click a panel title for more")
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # never widen the window
        hint.setMinimumWidth(0)                                 # no word wrap: height-for-width made the bar huge
        lay.addWidget(hint, 1)
        pb = QPushButton("Panels ▾"); pm = QMenu(pb); pb.setMenu(pm)
        pm.aboutToShow.connect(lambda: self._fill_panels_menu(pm))
        buttons = [pb]
        for text, tip, fn in (("Save layout…", "Save this arrangement under a name", self._save_layout),
                              ("Reset", "Back to the default arrangement", self._reset_layout),
                              ("Revert", "Undo everything done since Edit Mode was turned on", self._revert_layout),
                              ("Done", "Lock the layout (Esc)", lambda: self._set_edit_mode(False))):
            b = QPushButton(text); b.setToolTip(tip); b.clicked.connect(lambda _c=False, f=fn: f()); buttons.append(b)
        for b in buttons:
            lay.addWidget(b)
        tb = self.edit_toolbar = QToolBar("Edit Layout", self)      # a full-width row under the other toolbars
        tb.setObjectName("tb_editbar"); tb.setMovable(False); tb.setFloatable(False)
        tb.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(bar)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        tb.hide()
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, lambda: self.edit_mode and self._set_edit_mode(False))
        return bar

    def _fill_panels_menu(self, menu):
        menu.clear()
        for d in (self.dock_sources, self.dock_browser, self.dock_tracks, self.dock_np, self.dock_next, self.dock_lyrics,
                  self.dock_info, self.dock_eq, self.dock_vis):
            menu.addAction(d.toggleViewAction())
        menu.addSeparator()
        for tb in self.toolbars:
            menu.addAction(tb.toggleViewAction())

    def _revert_layout(self):
        snap = getattr(self, "_edit_snapshot", None)
        if not snap:
            return
        self._in_revert = True
        try:
            self._apply_layout_snapshot(snap)
        finally:
            self._in_revert = False
        self.statusBar().showMessage("Layout reverted to how it was when editing started", 5000)

    def _dock_menu(self, dock, pos):
        """Right-click on a panel's title bar (Edit Mode)."""
        if not self.edit_mode or (not dock.isFloating() and pos.y() > 28):
            return
        A = Qt.DockWidgetArea
        m = QMenu(self)
        if dock.isFloating():
            m.addAction("Dock it", lambda: dock.setFloating(False))
        else:
            m.addAction("Float", lambda: dock.setFloating(True))
            mv = m.addMenu("Move to")
            for label, area in (("Left", A.LeftDockWidgetArea), ("Right", A.RightDockWidgetArea),
                                ("Top", A.TopDockWidgetArea), ("Bottom", A.BottomDockWidgetArea)):
                a = mv.addAction(label); a.setEnabled(self.dockWidgetArea(dock) != area)
                a.triggered.connect(lambda _c, ar=area: self.addDockWidget(ar, dock))
            others = [d for d in self.findChildren(QDockWidget) if d is not dock and d.isVisible()]
            if others:
                tm = m.addMenu("Stack as a tab with")
                for o in others:
                    tm.addAction(o.windowTitle(), lambda o=o: self.tabifyDockWidget(o, dock))
        m.addSeparator()
        m.addAction("Close", dock.close)
        m.exec(dock.mapToGlobal(pos))

    def _apply_edit_state(self):
        self._set_edit_mode(self.edit_mode)

    def eventFilter(self, obj, ev):
        """Dock separators are handled by the main window itself: while the layout is locked they must not react."""
        if obj is self and not self.edit_mode and ev.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick):
            return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ panel editor (Preferences → Panels)
    AREAS = {"Left": Qt.DockWidgetArea.LeftDockWidgetArea, "Right": Qt.DockWidgetArea.RightDockWidgetArea,
             "Top": Qt.DockWidgetArea.TopDockWidgetArea, "Bottom": Qt.DockWidgetArea.BottomDockWidgetArea}
    TB_AREAS = {"Top": Qt.ToolBarArea.TopToolBarArea, "Bottom": Qt.ToolBarArea.BottomToolBarArea,
                "Left": Qt.ToolBarArea.LeftToolBarArea, "Right": Qt.ToolBarArea.RightToolBarArea}

    def panel_specs(self) -> list[dict]:
        """Every panel and toolbar with where it is now: {key, title, kind, visible, location, tab_with}."""
        out = []
        names = {v: k for k, v in self.AREAS.items()}
        for d in (self.dock_sources, self.dock_browser, self.dock_tracks, self.dock_np, self.dock_next, self.dock_lyrics,
                  self.dock_info, self.dock_eq, self.dock_vis):
            tabbed = [o for o in self.tabifiedDockWidgets(d)]
            out.append({"key": d.objectName(), "title": d.windowTitle(), "kind": "dock", "obj": d,
                        "visible": d.toggleViewAction().isChecked(),
                        "location": "Floating" if d.isFloating() else names.get(self.dockWidgetArea(d), "Right"),
                        "tab_with": tabbed[0].objectName() if tabbed and not d.isFloating() else ""})
        tnames = {v: k for k, v in self.TB_AREAS.items()}
        for tb in self.toolbars:
            out.append({"key": tb.objectName(), "title": tb.windowTitle(), "kind": "toolbar", "obj": tb,
                        "visible": tb.toggleViewAction().isChecked(),
                        "location": tnames.get(self.toolBarArea(tb), "Top"), "tab_with": ""})
        return out

    def apply_panel_specs(self, rows: list[dict]):
        """Apply {key, visible, location, tab_with} rows (only the ones given)."""
        by_key = {s["key"]: s["obj"] for s in self.panel_specs()}
        docks = []
        for r in rows:
            obj = by_key.get(r["key"])
            if obj is None:
                continue
            if isinstance(obj, QToolBar):
                if r["location"] in self.TB_AREAS:
                    self.addToolBar(self.TB_AREAS[r["location"]], obj)
                obj.setVisible(bool(r["visible"]))
                continue
            if r["location"] == "Floating":
                obj.setFloating(True)
            else:
                obj.setFloating(False)
                self.addDockWidget(self.AREAS.get(r["location"], Qt.DockWidgetArea.RightDockWidgetArea), obj)
            obj.setVisible(bool(r["visible"]))
            docks.append((obj, r.get("tab_with") or ""))
        for obj, other in docks:                                   # stacking needs both docks to be in place first
            o = by_key.get(other)
            if o is not None and o is not obj and not obj.isFloating() and not o.isFloating() \
                    and self.dockWidgetArea(o) == self.dockWidgetArea(obj):
                self.tabifyDockWidget(o, obj)
        self._apply_edit_state()

    # ------------------------------------------------------------------ layouts
    def _layout_snapshot(self):
        return {"state": _b64(self.saveState(STATE_VERSION)), "browser": self._splits_json(self.browser_split)}

    def _apply_layout_snapshot(self, snap):
        self.restoreState(_unb64(snap["state"]), STATE_VERSION)
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
        self.restoreState(self._default_state, STATE_VERSION)
        docks = (self.dock_sources, self.dock_browser, self.dock_tracks, self.dock_np, self.dock_next,
                 self.dock_lyrics, self.dock_info, self.dock_eq, self.dock_vis)
        show = {"Default": (self.dock_sources, self.dock_browser, self.dock_tracks, self.dock_np),
                "Minimal": (self.dock_tracks,),
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
            from .models import COLUMNS
            self.cfg.header_columns = [c[1] for c in COLUMNS]
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
            if len(src) == 2 and src[0] == "folder" and isinstance(src[1], (list, tuple)):
                src = ("folder", tuple(src[1]))                    # JSON turned the folder tuple into a list
            if len(src) == 2 and ((src[0], src[1]) in self._items or src[0] == "folder"):
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
