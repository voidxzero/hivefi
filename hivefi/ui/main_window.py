import json
import os
import random
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QByteArray, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QCursor, QDesktopServices, QIcon, QKeySequence, QPixmap,
                         QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QPushButton, QSlider,
                             QSplitter, QStackedWidget, QStyle, QSystemTrayIcon, QTableView, QToolBar,
                             QTreeWidget, QTreeWidgetItem)

from .. import art, scanner, sync
from ..config import Config, data_dir
from ..db import Database
from ..mpris import Mpris
from ..player import Player
from ..subsonic import Subsonic, SubsonicError
from .. import convert, lyrics, organizer, rgscan, skins, smart, tagedit, updater
from .album_grid import AlbumGrid
from .dialogs import TagDialog
from .audio_dialog import AudioDialog
from .duplicates_dialog import DuplicatesDialog
from .tools_dialogs import ConvertDialog, MissingDialog, OrganizeDialog, ReplayGainDialog
from .equalizer import EqualizerPanel
from .mini import MiniPlayer
from .panels_mixin import PanelsMixin
from .seekbar import STYLES as SEEK_STYLES
from .visualizer import STYLES as VIS_STYLES
from .models import COLUMNS, DEFAULT_HIDDEN, MIME, TrackModel, fmt_time
from .panels import LyricsPanel, NowPlayingPanel, TrackInfoPanel, UpNextPanel
from .smart_dialog import SmartDialog
from .settings_dialog import SettingsDialog

ALL = "[All]"


class Worker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as e:  # noqa: BLE001 - surfaced to UI
            self.failed.emit(str(e))


class SourceTree(QTreeWidget):
    """Source tree that accepts track drops onto playlists."""
    tracksDropped = pyqtSignal(int, list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _playlist_at(self, pos):
        it = self.itemAt(pos)
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return d[1] if d and d[0] == "playlist" else None

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME): e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME) and self._playlist_at(e.position().toPoint()) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        pid = self._playlist_at(e.position().toPoint())
        if pid is not None and e.mimeData().hasFormat(MIME):
            self.tracksDropped.emit(pid, json.loads(bytes(e.mimeData().data(MIME)).decode()))
            e.acceptProposedAction()


class MainWindow(PanelsMixin, QMainWindow):
    def __init__(self, cfg: Config, db: Database):
        super().__init__()
        self.cfg, self.db = cfg, db
        self.api: Subsonic | None = None
        self._rebuild_api()
        self.setWindowTitle("Hivefi")
        self.resize(1280, 780)
        self.player = Player(self._resolve, cfg, self)
        self.player.set_volume(cfg.volume)
        self.player.shuffle, self.player.repeat = cfg.shuffle, cfg.repeat
        self.player.auto_dj = self._auto_dj_tracks if cfg.auto_dj else None
        self.skin_tokens = skins.all_skins().get(cfg.skin) or skins.BUILTIN[skins.DEFAULT]
        self._quitting = False
        self.restart_requested = False
        self._closing = False
        self.source = ("all", None)
        self._workers: set[Worker] = set()
        self._sync_worker = self._scan_worker = self._flush_worker = None
        self._art_token = 0
        self._sorted_once = False

        self._build_ui()
        self._build_menu()
        self._connect_player()
        self.mpris = Mpris(self, self.player)
        self.mini = MiniPlayer(self.player)
        self.mini.restoreRequested.connect(self._leave_mini)
        self._build_tray()
        self._init_equalizer()
        self._default_state = self.saveState()
        if cfg.geometry:
            self.restoreGeometry(QByteArray.fromBase64(cfg.geometry.encode()))
        if cfg.state:
            self.restoreState(QByteArray.fromBase64(cfg.state.encode()))
        if cfg.header_state:
            self.table.horizontalHeader().restoreState(QByteArray.fromBase64(cfg.header_state.encode()))
            self._sorted_once = True
        self.installEventFilter(self)
        self._set_edit_mode(False)                               # always starts locked; View → Edit Mode unlocks
        QTimer.singleShot(0, self._restore_splits)               # needs the window laid out at its final size
        self._start_autosave()
        self._set_view_mode(cfg.view_mode, initial=True)
        self._apply_skin_visuals()
        self.sleep_timer = QTimer(self, singleShot=True, timeout=self._sleep_fire)

        self.sync_timer = QTimer(self, timeout=lambda: self.sync_now(silent=True))
        self._apply_sync_timer()
        self.refresh_playlists()
        self.status_lbl.setText("Loading library…")
        w = Worker(lambda _p: len(self.db.all_tracks()), self)       # fill the track cache off the UI thread
        w.done.connect(lambda _n: (self._restore_session(), self._startup()))
        w.failed.connect(lambda _e: (self.load_source(), self._startup()))
        self._run(w)

    # ------------------------------------------------------------ setup
    def _rebuild_api(self):
        c = self.cfg
        self.api = Subsonic(c.server_url, c.username, c.password) if c.server_enabled else None

    def _resolve(self, t):
        if t["source"] == "local":
            return QUrl.fromLocalFile(t["path"])
        if self.api is None:
            return QUrl()
        return QUrl(self.api.stream_url(t["remote_id"], self.cfg.max_bitrate))

    # tracks from libraries that are switched off never show up anywhere
    def _tracks(self, source=None):
        return self.cfg.enabled_tracks(self.db.all_tracks(source))

    def _playlist_tracks(self, pid):
        return [t for t in self.db.playlist_tracks(pid) if self.cfg.track_enabled(t)]

    def _icon(self, sp):
        return self.style().standardIcon(sp)

    def _build_ui(self):
        self._build_toolbars()

        # sources tree
        self.tree = SourceTree(); self.tree.setHeaderHidden(True)
        self.tree.tracksDropped.connect(self._drop_to_playlist); self.tree.setMinimumWidth(170)
        self.tree.itemSelectionChanged.connect(self._on_source)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)

        # column browser
        self.lists = {}
        browser = self.browser_split = QSplitter(Qt.Orientation.Horizontal)
        for name in ("genre", "artist", "album"):
            lw = QListWidget(); lw.setObjectName(name); lw.setUniformItemSizes(True)
            lw.currentRowChanged.connect(lambda _r, n=name: self._on_browser(n))
            self.lists[name] = lw; browser.addWidget(lw)

        # album grid
        self.grid = AlbumGrid(lambda: self.api)
        self.grid.albumSelected.connect(self._on_grid_album)
        self.grid.playRequested.connect(lambda ts: self.player.play_list(ts, 0))
        self.grid.contextRequested.connect(self._grid_menu)
        self.top_stack = QStackedWidget(); self.top_stack.addWidget(browser); self.top_stack.addWidget(self.grid)

        # track table
        self.model = TrackModel()
        self.table = QTableView(); self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide(); self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(True); hh.setSectionsMovable(True)
        hh.setSectionsClickable(True); hh.setSortIndicatorShown(True)
        hh.sectionClicked.connect(self._header_sort)             # sorting is done by the model, not Qt
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        for i, (_, key, w) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, w)
            self.table.setColumnHidden(i, key in DEFAULT_HIDDEN)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.doubleClicked.connect(self._on_double)
        self.table.clicked.connect(self._on_table_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._track_menu)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._sel_changed())
        QShortcut(QKeySequence(Qt.Key.Key_Space), self.table, self.player.toggle,
                  context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self.table, self._play_selected,
                  context=Qt.ShortcutContext.WidgetShortcut)

        self.main_split = main = QSplitter(Qt.Orientation.Vertical)
        main.addWidget(self.top_stack); main.addWidget(self.table); main.setSizes([190, 600])
        self.setCentralWidget(main)

        # dockable panels
        self.np_panel = NowPlayingPanel(); self.next_panel = UpNextPanel()
        self.lyrics_panel = LyricsPanel(); self.info_panel = TrackInfoPanel()
        L, R = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        self.dock_sources = self._dock("Sources", self.tree, "dock_sources", L)
        self.dock_np = self._dock("Now Playing", self.np_panel, "dock_np", L)
        self.dock_next = self._dock("Up Next", self.next_panel, "dock_next", R)
        self.dock_lyrics = self._dock("Lyrics", self.lyrics_panel, "dock_lyrics", R)
        self.dock_info = self._dock("Track Info", self.info_panel, "dock_info", R)
        self.eq_panel = EqualizerPanel(self.cfg, self.player.mp.eq_available)
        self.dock_eq = self._dock("Equalizer", self.eq_panel, "dock_eq", R)
        self.tabifyDockWidget(self.dock_next, self.dock_lyrics); self.tabifyDockWidget(self.dock_lyrics, self.dock_info)
        self._build_visualizer()
        self.tabifyDockWidget(self.dock_info, self.dock_eq); self.tabifyDockWidget(self.dock_eq, self.dock_vis)
        for d in (self.dock_next, self.dock_lyrics, self.dock_info, self.dock_eq, self.dock_vis):
            d.hide()
        self.dock_np.setMinimumHeight(200)
        self.dock_lyrics.visibilityChanged.connect(lambda v: v and self._refresh_lyrics())
        self.dock_next.visibilityChanged.connect(lambda v: v and self._refresh_next())
        self.dock_info.visibilityChanged.connect(lambda v: v and self._sel_changed())
        self.next_panel.jumpRequested.connect(self.player.jump)
        self.next_panel.removeRequested.connect(self.player.remove)
        self.status_lbl = QLabel(""); self.statusBar().addWidget(self.status_lbl, 1)
        self.sync_lbl = QLabel(""); self.statusBar().addPermanentWidget(self.sync_lbl)
        self.btn_edit = QPushButton("✎ Edit layout"); self.btn_edit.setCheckable(True); self.btn_edit.setFlat(True)
        self.btn_edit.setToolTip("Unlock the layout to move, resize, float or close panels (Ctrl+Shift+E)")
        self.btn_edit.toggled.connect(self._set_edit_mode)
        self.statusBar().addPermanentWidget(self.btn_edit)

        for key in (Qt.Key.Key_MediaTogglePlayPause, Qt.Key.Key_MediaPlay):
            QShortcut(QKeySequence(key), self, self.player.toggle)
        QShortcut(QKeySequence(Qt.Key.Key_MediaNext), self, self.player.next)
        QShortcut(QKeySequence(Qt.Key.Key_MediaPrevious), self, self.player.previous)
        QShortcut(QKeySequence(Qt.Key.Key_MediaStop), self, self.player.stop)

    def _build_menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("Add &Folder to Library…", self._add_folder)
        f.addAction("&Rescan Library", QKeySequence("F5"), self.scan_library)
        f.addAction("New &Auto Playlist…", self._new_smart)
        f.addAction("&Import Playlist (M3U)…", self._import_m3u)
        f.addAction("&Save Queue as Playlist…", self._save_queue_playlist)
        f.addSeparator()
        f.addAction("&Sync with Navidrome", QKeySequence("Ctrl+Shift+S"), self.sync_now)
        f.addAction("&Preferences…", QKeySequence("Ctrl+,"), self.open_settings)
        f.addSeparator()
        f.addAction("&Quit", QKeySequence("Ctrl+Q"), self._quit)
        p = mb.addMenu("&Playback")
        p.addAction("Play/Pause", QKeySequence("Ctrl+Space"), self.player.toggle)
        p.addAction("Next", QKeySequence("Ctrl+Right"), self.player.next)
        p.addAction("Previous", QKeySequence("Ctrl+Left"), self.player.previous)
        p.addAction("Clear Queue", self.player.clear)
        p.addSeparator()
        sm = p.addMenu("Sleep Timer"); self.sleep_group = QActionGroup(self)
        for label, mins in (("Off", 0), ("15 minutes", 15), ("30 minutes", 30), ("45 minutes", 45),
                            ("1 hour", 60), ("90 minutes", 90)):
            a = sm.addAction(label); a.setCheckable(True); a.setChecked(mins == 0); self.sleep_group.addAction(a)
            a.triggered.connect(lambda _c, m=mins: self._set_sleep(m))
        p.addAction("Equalizer", QKeySequence("Ctrl+E"), self.show_equalizer)
        p.addSeparator()
        a = p.addAction("Auto-DJ (keep playing similar-random tracks)"); a.setCheckable(True)
        a.setChecked(self.cfg.auto_dj); a.toggled.connect(self._toggle_auto_dj)
        rm = p.addMenu("ReplayGain"); rg_group = QActionGroup(self)
        for label, mode in (("Off", "off"), ("Track gain", "track"), ("Album gain", "album")):
            a = rm.addAction(label); a.setCheckable(True); a.setChecked(self.cfg.rg_mode == mode); rg_group.addAction(a)
            a.triggered.connect(lambda _c, m=mode: self._set_rg_mode(m))
        p.addAction("Audio Settings…", QKeySequence("Ctrl+Shift+A"), self.open_audio_settings)
        v = mb.addMenu("&View")
        v.addAction("Focus Search", QKeySequence("Ctrl+F"), self.search.setFocus)
        v.addAction("Lyrics", QKeySequence("Ctrl+L"), self.show_lyrics)
        v.addSeparator()
        self.lib_menu = v.addMenu("Libraries"); self.lib_menu.aboutToShow.connect(self._build_lib_menu)
        pm = v.addMenu("Panels")
        for d in (self.dock_sources, self.dock_np, self.dock_next, self.dock_lyrics, self.dock_info, self.dock_eq,
                  self.dock_vis):
            pm.addAction(d.toggleViewAction())
        pm.addSeparator()
        for tb in self.toolbars:
            pm.addAction(tb.toggleViewAction())
        pm.addSeparator()
        self.act_edit = pm.addAction("Edit Mode", QKeySequence("Ctrl+Shift+E")); self.act_edit.setCheckable(True)
        self.act_edit.setChecked(self.edit_mode); self.act_edit.toggled.connect(self._set_edit_mode)
        pm.addAction("Reset Layout", self._reset_layout)
        self.layout_menu = v.addMenu("Layouts"); self.layout_menu.aboutToShow.connect(self._build_layout_menu)
        sm = v.addMenu("Seek Bar Style"); sg = QActionGroup(self)
        for key, label in SEEK_STYLES.items():
            a = sm.addAction(label); a.setCheckable(True); a.setChecked(key == self.cfg.seek_style); sg.addAction(a)
            a.triggered.connect(lambda _c, k=key: self._set_seek_style(k))
        sm.addSeparator()
        a = sm.addAction("Analyse streamed tracks too (downloads them)"); a.setCheckable(True)
        a.setChecked(self.cfg.waveform_remote); a.toggled.connect(self._set_waveform_remote)
        vm = v.addMenu("Visualizer Style"); vg = QActionGroup(self)
        for key, label in VIS_STYLES.items():
            a = vm.addAction(label); a.setCheckable(True); a.setChecked(key == self.cfg.vis_style); vg.addAction(a)
            a.triggered.connect(lambda _c, k=key: self._set_vis_style(k))
        vm.addSeparator(); vm.addAction("Show Visualizer", self.dock_vis.show)
        v.addAction("Columns View", lambda: self._set_view_mode("browser"))
        v.addAction("Albums View", lambda: self._set_view_mode("albums"))
        self.skin_menu = v.addMenu("Skin"); self._build_skin_menu()
        v.addSeparator()
        v.addAction("Mini Player", QKeySequence("Ctrl+M"), self._enter_mini)
        v.addAction("Full Screen", QKeySequence("F11"),
                    lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        tl = mb.addMenu("&Tools")
        tl.addAction("&Organise Files…", self._organize)
        tl.addAction("&Convert…", self._convert)
        tl.addAction("Scan &ReplayGain…", self._scan_rg)
        tl.addSeparator()
        tl.addAction("Find &Duplicates…", self._find_duplicates)
        tl.addAction("Find &Missing Files…", self._find_missing)
        h = mb.addMenu("&Help")
        h.addAction("Check for &Updates…", lambda: self.check_updates(manual=True))

    def _connect_player(self):
        P = self.player
        P.positionChanged.connect(self.lyrics_panel.set_position)
        self.lyrics_panel.seekRequested.connect(P.seek)
        P.trackChanged.connect(self._on_track)
        P.stateChanged.connect(lambda pl: self.act_play.setIcon(
            self._icon(QStyle.StandardPixmap.SP_MediaPause if pl else QStyle.StandardPixmap.SP_MediaPlay)))
        P.positionChanged.connect(self._on_pos)
        P.durationChanged.connect(self._on_dur)
        P.queueChanged.connect(self._on_queue_changed)
        P.played.connect(self._on_played)
        P.nowPlaying.connect(self._on_now_playing)
        P.error.connect(lambda m: self.statusBar().showMessage(f"Playback error: {m}", 8000))

    # ------------------------------------------------------- sources
    def refresh_playlists(self):
        sel = self.source
        self.tree.blockSignals(True)
        self.tree.clear()
        def item(parent, text, data):
            it = QTreeWidgetItem(parent, [text]); it.setData(0, Qt.ItemDataRole.UserRole, data); return it
        lib = item(self.tree, "Library", None)
        self._items = {("all", None): item(lib, "All Music", ("all", None)),
                       ("local", None): item(lib, "This Computer", ("local", None))}
        if self.cfg.server_configured:
            nav = item(lib, "Navidrome" if self.cfg.server_enabled else "Navidrome (off)", ("remote", None))
            if not self.cfg.server_enabled:
                nav.setForeground(0, self.palette().placeholderText())
            self._items[("remote", None)] = nav
            names = self.cfg.server_folder_names
            if len(names) > 1:                                    # several libraries on the server: list each
                for fid, name in names.items():
                    on = self.cfg.server_enabled and self.cfg.lib_enabled(Config.server_lib_key(fid))
                    child = item(nav, name if on else f"{name} (off)", ("remote", fid))
                    if not on:
                        child.setForeground(0, self.palette().placeholderText())
                    self._items[("remote", fid)] = child
        self._items[("queue", None)] = item(self.tree, "Now Playing", ("queue", None))
        pls = item(self.tree, "Playlists", None)
        for p in self.db.playlists():
            if p["sync"] and not self.cfg.server_enabled:      # server-backed playlists hide with Navidrome
                continue
            self._items[("playlist", p["id"])] = item(pls, p["name"] + ("  ☁" if p["sync"] else ""),
                                                    ("playlist", p["id"]))
        auto = item(self.tree, "Auto Playlists", None)
        for name in smart.PRESETS:
            self._items[("smart", name)] = item(auto, name, ("smart", name))
        for sp in self.db.smart_playlists():
            self._items[("smart", sp["id"])] = item(auto, "★ " + sp["name"], ("smart", sp["id"]))
        self.tree.expandAll()
        self.tree.setCurrentItem(self._items.get(sel, self._items[("all", None)]))
        self.tree.blockSignals(False)
        if sel not in self._items:
            self.source = ("all", None); self.load_source()

    def _on_source(self):
        it = self.tree.currentItem()
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if d:
            self.source = tuple(d); self.load_source()

    def load_source(self, keep=False):
        """Reload the track list of the current source. keep=True preserves the Genre/Artist/Album selection."""
        kind, arg = self.source
        if kind == "all": tracks = self._tracks()
        elif kind == "remote" and arg is not None:
            tracks = [t for t in self._tracks("remote") if t.get("folder_id") == arg]
        elif kind in ("local", "remote"): tracks = self._tracks(kind)
        elif kind == "queue": tracks = list(self.player.queue)
        elif kind == "smart": tracks = smart.evaluate(self._tracks(), self._smart_spec(arg))
        else: tracks = self._playlist_tracks(arg)
        m = self.model
        saved = (m.genre, m.artist, m.album) if keep else (None, None, None)
        m.genre = m.artist = m.album = None
        hh = self.table.horizontalHeader()
        if kind in ("queue", "playlist", "smart"):               # these keep their own order
            hh.setSortIndicatorShown(False)
            m.set_tracks(tracks, sort_col=None)
        else:
            sec, order = hh.sortIndicatorSection(), hh.sortIndicatorOrder()
            if sec in (0, 1) or not self._sorted_once:
                sec, order = 3, Qt.SortOrder.AscendingOrder
                hh.setSortIndicator(sec, order); self._sorted_once = True
            hh.setSortIndicatorShown(True)
            m.set_tracks(tracks, sort_col=COLUMNS[sec][1], sort_desc=order == Qt.SortOrder.DescendingOrder)
        cur = self.player.current
        m.playing_id = cur["id"] if cur else None
        self._fill_browser("genre")
        if any(saved):
            self._restore_browser(saved)
        self._refresh_grid()
        self._update_status()
        self._sel_changed()

    def _header_sort(self, section):
        if self.source[0] in ("queue", "playlist", "smart") or section in (0,):
            return
        hh = self.table.horizontalHeader()
        desc = hh.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder if hh.sortIndicatorSection() == section else False
        order = Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder
        hh.setSortIndicator(section, order)
        self.model.sort_by(COLUMNS[section][1], desc)
        self._update_status()

    def _fill_browser(self, level):
        order = ["genre", "artist", "album"]
        for lv in order[order.index(level):]:
            lw = self.lists[lv]; lw.blockSignals(True); lw.setUpdatesEnabled(False); lw.clear()
            rows = self.model.narrowed(lv)
            if lv == "genre": vals = {t["genre"] for t in rows}
            elif lv == "artist": vals = {t["album_artist"] or t["artist"] for t in rows}
            else: vals = {t["album"] for t in rows}
            vals.discard("")
            lw.addItem(f"{ALL} ({len(vals)})")
            lw.addItems(sorted(vals, key=str.lower)); lw.setCurrentRow(0)
            lw.setUpdatesEnabled(True); lw.blockSignals(False)

    def _restore_browser(self, saved):
        """Re-select the Genre/Artist/Album that were chosen before a reload, where they still exist."""
        order = ["genre", "artist", "album"]
        for i, (lv, val) in enumerate(zip(order, saved)):
            if not val:
                break
            items = self.lists[lv].findItems(val, Qt.MatchFlag.MatchExactly)
            if not items:
                break
            self.lists[lv].blockSignals(True); self.lists[lv].setCurrentItem(items[0]); self.lists[lv].blockSignals(False)
            self.model.set_filters(**{lv: val})
            if i < 2:
                self._fill_browser(order[i + 1])

    def _on_browser(self, name):
        lw = self.lists[name]
        v = lw.currentItem().text() if lw.currentRow() > 0 else None
        order = ["genre", "artist", "album"]
        nxt = order.index(name) + 1
        below = {lv: None for lv in order[nxt:]}
        self.model.set_filters(**{name: v}, **below)
        if nxt < 3:
            self._fill_browser(order[nxt])
        self._update_status()

    def _on_search(self, text):
        self.model.set_filters(text=text, genre=None, artist=None, album=None)
        self._fill_browser("genre")
        if self.cfg.view_mode == "albums":                       # thousands of album tiles: rebuild after typing settles
            if not hasattr(self, "_grid_timer"):
                self._grid_timer = QTimer(self, singleShot=True, interval=150, timeout=self._refresh_grid)
            self._grid_timer.start()
        self._update_status()

    def _update_status(self):
        rows = self.model.tracks
        self.status_lbl.setText(f"{len(rows)} tracks · {fmt_time(sum(t['duration'] or 0 for t in rows))}")

    # ------------------------------------------------------- playback UI
    def _visible_tracks(self):
        return list(self.model.tracks)

    def _selected(self):
        rows = set()
        for r in self.table.selectionModel().selection():       # ranges, not one QModelIndex per row
            rows.update(range(r.top(), r.bottom() + 1))
        tr = self.model.tracks
        return [tr[i] for i in sorted(rows) if i < len(tr)]

    def _on_double(self, idx):
        if COLUMNS[idx.column()][1] == "rating":
            return
        if self.source[0] == "queue":
            self.player.jump(idx.row()); return
        self.player.play_list(self._visible_tracks(), idx.row())

    def _play_selected(self):
        idx = self.table.currentIndex()
        if idx.isValid(): self._on_double(idx)

    def _on_track(self, t):
        self.model.set_playing(t["id"] if t else None)
        self._refresh_next()
        self.np_panel.set_track(t)
        if not t:
            self.setWindowTitle("Hivefi"); self.mini.art.set_art(None); self.seek.set_peaks(None); return
        self._refresh_lyrics()
        self._load_waveform(t)
        self.setWindowTitle(f'{t["artist"]} – {t["title"]}')
        self._art_token += 1
        token, tcopy, api = self._art_token, dict(t), self.api
        w = Worker(lambda _p: art.get_art(tcopy, api, 440), self)
        w.done.connect(lambda data, tk=token: self._show_art(data, tk))
        self._run(w)

    def _show_art(self, data, token):
        if token != self._art_token: return
        pm = QPixmap()
        ok = bool(data) and pm.loadFromData(data)
        self.np_panel.art.set_art(pm if ok else None)
        self.mini.art.set_art(pm if ok else None)

    def _on_pos(self, ms):
        if not self.seek.isSliderDown(): self.seek.setValue(ms)
        self.lbl_pos.setText(fmt_time(ms / 1000))

    def _on_dur(self, ms):
        self.seek.setRange(0, ms); self.lbl_dur.setText(fmt_time(ms / 1000))

    def _set_volume(self, v): self.cfg.volume = v; self.player.set_volume(v)
    def _set_shuffle(self, v): self.cfg.shuffle = v; self.player.set_shuffle(v)

    def _cycle_repeat(self):
        self.player.set_repeat({"off": "all", "all": "one", "one": "off"}[self.player.repeat])
        self.cfg.repeat = self.player.repeat; self._update_repeat_label()

    def _update_repeat_label(self):
        self.btn_repeat.setText({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: one"}[self.cfg.repeat])

    # ------------------------------------------------ plays / ratings / stars
    def _on_played(self, t):
        self.db.bump_play(t["id"])
        fresh = self.db.track(t["id"]) or {}
        t["play_count"] = fresh.get("play_count", 0)
        self.model.update_track(t["id"], play_count=t["play_count"], last_played=fresh.get("last_played", 0))
        if t["source"] == "remote" and self.cfg.scrobble and self.api:
            self.db.queue_op("scrobble", {"id": t["remote_id"], "time_ms": int(time.time() * 1000)})
            self._kick_flush()

    def _on_now_playing(self, t):
        if t["source"] == "remote" and self.cfg.scrobble and self.api:
            api = self.api
            w = Worker(lambda _p: api.scrobble(t["remote_id"], submission=False), self)
            self._run(w)

    def _model_refresh(self, tracks, **fields):
        if len(tracks) > 400:                                    # big batch: one repaint instead of one per row
            for t in tracks: t.update(fields)
            if self.model.tracks:
                self.model.dataChanged.emit(self.model.index(0, 0),
                                            self.model.index(len(self.model.tracks) - 1, len(COLUMNS) - 1))
        else:
            for t in tracks: self.model.update_track(t["id"], **fields)

    def set_rating(self, tracks, r):
        self.db.set_ratings([t["id"] for t in tracks], r)
        self._model_refresh(tracks, rating=r)
        self.db.queue_ops("rating", [{"id": t["remote_id"], "rating": r} for t in tracks if t["source"] == "remote"])
        paths = [t["path"] for t in tracks if t["source"] == "local" and t.get("path")]
        if self.cfg.write_ratings and paths:
            def write(_p):
                bad = 0
                for p in paths:
                    try: tagedit.write_rating(p, r)
                    except Exception: bad += 1
                return bad
            w = Worker(write, self)
            w.done.connect(lambda bad: bad and self.statusBar().showMessage(f"Could not write rating to {bad} file(s)", 5000))
            self._run(w)
        self._kick_flush()

    def set_star(self, tracks, on):
        self.db.set_starred_many([t["id"] for t in tracks], on)
        self._model_refresh(tracks, starred=int(on))
        self.db.queue_ops("star", [{"id": t["remote_id"], "on": on} for t in tracks if t["source"] == "remote"])
        self._kick_flush()

    # ------------------------------------------------------- menus
    def _track_menu(self, pos):
        sel = self._selected()
        if not sel: return
        m = QMenu(self)
        m.addAction("Play Now", lambda: self.player.play_list(sel, 0))
        m.addAction("Play Next", lambda: self.player.enqueue(sel, next_=True))
        m.addAction("Queue Last", lambda: self.player.enqueue(sel))
        m.addSeparator()
        rm = m.addMenu("Rating")
        for r in range(6):
            rm.addAction("★" * r if r else "No rating", lambda r=r: self.set_rating(sel, r))
        starred = all(t["starred"] for t in sel)
        m.addAction("Unstar ♥" if starred else "Star ♥", lambda: self.set_star(sel, not starred))
        pm = m.addMenu("Add to Playlist")
        pm.addAction("New Playlist…", lambda: self._new_playlist(sel))
        for p in self.db.playlists():
            pm.addAction(p["name"], lambda p=p: self._add_to_playlist(p["id"], sel))
        if self.source[0] == "playlist":
            m.addAction("Remove from Playlist", lambda: self._remove_from_playlist(sel))
        local = [t for t in sel if t["source"] == "local"]
        if local:
            m.addSeparator()
            m.addAction("Edit Tags…", lambda: self._edit_tags(local))
            m.addAction("Show in File Manager",
                        lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(local[0]["path"]).parent))))
            tm = m.addMenu("Tools")
            tm.addAction("Organise Files…", lambda: self._organize(local))
            tm.addAction("Convert…", lambda: self._convert(local))
            tm.addAction("Scan ReplayGain…", lambda: self._scan_rg(local))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        m = QMenu(self)
        if d and d[0] == "playlist":
            pid = d[1]
            m.addAction("Rename…", lambda: self._rename_playlist(pid))
            m.addAction("Export as M3U…", lambda: self._export_m3u(pid))
            m.addAction("Delete", lambda: self._delete_playlist(pid))
            if self.api:
                pl = next(p for p in self.db.playlists() if p["id"] == pid)
                m.addAction("Stop syncing with Navidrome" if pl["sync"] else "Sync with Navidrome",
                            lambda: self._toggle_playlist_sync(pl))
        elif d and d == ("remote", None):
            m.addAction("Turn Navidrome off" if self.cfg.server_enabled else "Turn Navidrome on",
                        lambda: self.set_library_enabled(Config.NAVIDROME, not self.cfg.server_enabled))
        elif d and d[0] == "remote" and d[1] is not None:
            key = Config.server_lib_key(d[1]); on = self.cfg.lib_enabled(key)
            m.addAction("Turn this library off" if on else "Turn this library on",
                        lambda: self.set_library_enabled(key, not on))
        elif d and d == ("local", None):
            for folder in self.cfg.library_dirs:
                a = m.addAction(folder); a.setCheckable(True); a.setChecked(self.cfg.lib_enabled(folder))
                a.toggled.connect(lambda on, f=folder: self.set_library_enabled(f, on))
            if not self.cfg.library_dirs:
                m.addAction("No folders added (File → Add Folder)").setEnabled(False)
        elif d and d[0] == "smart" and isinstance(d[1], str):
            m.addAction("Make Editable Copy…", lambda: self._copy_smart(d[1]))
        elif d and d == ("queue", None):
            m.addAction("Save as Playlist…", self._save_queue_playlist)
        elif d and d[0] == "smart" and isinstance(d[1], int):
            m.addAction("Edit…", lambda: self._edit_smart(d[1]))
            m.addAction("Delete", lambda: self._delete_smart(d[1]))
        else:
            m.addAction("New Playlist…", lambda: self._new_playlist([]))
            m.addAction("New Auto Playlist…", self._new_smart)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _new_playlist(self, tracks):
        name, ok = QInputDialog.getText(self, "New Playlist", "Name:")
        if not ok or not name.strip(): return
        sync_it = 0
        if self.api:
            sync_it = int(QMessageBox.question(self, "Sync playlist",
                          "Sync this playlist with Navidrome?\n(Only server tracks are included on the server.)")
                          == QMessageBox.StandardButton.Yes)
        pid = self.db.create_playlist(name.strip(), sync=sync_it, dirty=sync_it)
        if tracks: self.db.set_playlist_tracks(pid, [t["id"] for t in tracks])
        self.refresh_playlists()
        if sync_it: self.sync_now(silent=True)

    def _add_to_playlist(self, pid, tracks):
        self.db.add_to_playlist(pid, [t["id"] for t in tracks])
        if self.source == ("playlist", pid): self.load_source()
        self.statusBar().showMessage(f"Added {len(tracks)} track(s)", 3000)
        self._sync_if_dirty()

    def _remove_from_playlist(self, tracks):
        pid = self.source[1]; ids = {t["id"] for t in tracks}
        self.db.set_playlist_tracks(pid, [t["id"] for t in self.db.playlist_tracks(pid) if t["id"] not in ids])
        self.load_source(); self._sync_if_dirty()

    def _rename_playlist(self, pid):
        cur = next(p for p in self.db.playlists() if p["id"] == pid)
        name, ok = QInputDialog.getText(self, "Rename Playlist", "Name:", text=cur["name"])
        if ok and name.strip():
            self.db.rename_playlist(pid, name.strip()); self.refresh_playlists(); self._sync_if_dirty()

    def _delete_playlist(self, pid):
        if QMessageBox.question(self, "Delete playlist", "Delete this playlist?") == QMessageBox.StandardButton.Yes:
            self.db.delete_playlist(pid); self.refresh_playlists(); self._kick_flush()

    def _toggle_playlist_sync(self, pl):
        on = 0 if pl["sync"] else 1
        self.db.conn.execute("UPDATE playlists SET sync=?, dirty=? WHERE id=?", (on, on, pl["id"]))
        self.db.commit(); self.refresh_playlists(); self._sync_if_dirty()

    def _sync_if_dirty(self):
        if self.api and any(p["sync"] and p["dirty"] for p in self.db.playlists()):
            self.sync_now(silent=True)

    # ------------------------------------------------ equalizer
    def _init_equalizer(self):
        mp = self.player.mp
        self.eq_panel.changed.connect(self._on_eq_changed)
        self.eq_panel.persist.connect(self.cfg.save)
        mp.engineChanged.connect(self._engine_changed)
        self.player.notice.connect(lambda m: self.statusBar().showMessage(m, 12000))
        self._engine_changed(mp.engine)
        self.player.configure()

    def _on_eq_changed(self):
        if not hasattr(self, "_eq_timer"):                     # coalesce slider drags into one engine update
            self._eq_timer = QTimer(self, singleShot=True, interval=40,
                                    timeout=lambda: self.player.configure(relook=False))
        self._eq_timer.start()

    def _engine_changed(self, e):
        self._update_vis_availability()
        self.eq_panel.set_status("Audio engine: GStreamer" if e == "gst" else
                                 "Audio engine: Qt Multimedia (no equalizer, gapless or crossfade)")

    def show_equalizer(self):
        self.dock_eq.show(); self.dock_eq.raise_()

    # ------------------------------------------------ libraries on/off
    def _build_lib_menu(self):
        m = self.lib_menu; m.clear()
        if self.cfg.server_configured:
            a = m.addAction("Navidrome / Subsonic server"); a.setCheckable(True); a.setChecked(self.cfg.server_enabled)
            a.toggled.connect(lambda on: self.set_library_enabled(Config.NAVIDROME, on))
        if self.cfg.server_configured and len(self.cfg.server_folder_names) > 1:
            for fid, name in self.cfg.server_folder_names.items():
                key = Config.server_lib_key(fid)
                a = m.addAction(f"    {name}  (Navidrome)"); a.setCheckable(True); a.setChecked(self.cfg.lib_enabled(key))
                a.setEnabled(self.cfg.server_enabled)
                a.toggled.connect(lambda on, k=key: self.set_library_enabled(k, on))
        for folder in self.cfg.library_dirs:
            a = m.addAction(folder); a.setCheckable(True); a.setChecked(self.cfg.lib_enabled(folder))
            a.toggled.connect(lambda on, f=folder: self.set_library_enabled(f, on))
        if m.isEmpty():
            m.addAction("No libraries yet (File → Preferences)").setEnabled(False)
        m.addSeparator()
        m.addAction("Local music only", self._local_only)
        m.addAction("Enable all libraries", self._enable_all)

    def _local_only(self):
        self.cfg.set_lib_enabled(Config.NAVIDROME, False)
        for d in self.cfg.library_dirs: self.cfg.set_lib_enabled(d, True)
        self._library_toggled(Config.NAVIDROME, False)

    def _enable_all(self):
        self.cfg.libraries_off = []
        self._library_toggled(Config.NAVIDROME, True)

    def set_library_enabled(self, key, on):
        self.cfg.set_lib_enabled(key, on)
        self._library_toggled(key, on)

    def _library_toggled(self, key, on):
        self.cfg.save()
        server_part = key.startswith(Config.NAVIDROME + ":")         # one library of the server: view-only switch
        if not server_part:
            self._rebuild_api(); self._apply_sync_timer()
        self._after_library_change()
        if key == Config.NAVIDROME and on and self.api:
            self.sync_now(silent=True)            # catch up on whatever changed while it was off
        elif key != Config.NAVIDROME and not server_part and on:
            self.scan_library(quiet=True)         # pick up files added while the folder was off
        state = "on" if on else "off"
        name = ("Navidrome" if key == Config.NAVIDROME else
                self.cfg.server_folder_names.get(key.split(":", 1)[1], key) if server_part else key)
        self.statusBar().showMessage(f"Library {name} turned {state}", 5000)

    def _find_duplicates(self):
        dlg = DuplicatesDialog(self._tracks(), self)
        if dlg.exec() and dlg.removed:
            playing = (self.player.current or {}).get("id")
            ids = [i for i in dlg.removed if i != playing]
            self.db.delete_tracks(ids)
            self.refresh_playlists(); self.load_source()
            self.statusBar().showMessage(f"Removed {len(ids)} duplicate track(s) from the library", 5000)

    # ------------------------------------------------ tools
    def _tool_tracks(self, tracks=None):
        """Selected local tracks (or the given ones); with no selection, the whole enabled local library."""
        if not isinstance(tracks, list):                         # menu actions pass `checked`
            tracks = [t for t in self._selected() if t["source"] == "local"]
        return tracks or self._tracks("local")

    def _default_root(self):
        return next((f for f in self.cfg.library_dirs if self.cfg.lib_enabled(f)), str(Path.home() / "Music"))

    def _job(self, fn, title):
        w = Worker(fn, self)
        w.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        w.failed.connect(lambda msg: QMessageBox.warning(self, title, msg))
        return w

    def _report(self, title, done, errors, verb):
        self.statusBar().showMessage(f"{verb} {done} file(s)" + (f", {len(errors)} problem(s)" if errors else ""), 8000)
        if errors:
            QMessageBox.warning(self, title, "\n".join(errors[:15]) + ("\n…" if len(errors) > 15 else ""))

    def _organize(self, tracks=None):
        tr = self._tool_tracks(tracks)
        dlg = OrganizeDialog(tr, self._default_root(), self)
        if not dlg.exec():
            return
        moves, mode = dlg.result_options()
        if not moves:
            self.statusBar().showMessage("Nothing to organise", 4000); return
        if mode == "move" and QMessageBox.question(self, "Organise Files", f"Move {len(moves)} file(s) on disk?") \
                != QMessageBox.StandardButton.Yes:
            return
        roots = [Path(f) for f in self.cfg.library_dirs]
        w = self._job(lambda prog: organizer.execute(self.db, moves, mode, prog,
                                                     prune_to=next((str(r) for r in roots if r in Path(moves[0][0]["path"]).parents), None)),
                      "Organise Files")
        w.done.connect(lambda r: (self._report("Organise Files", r[0], r[1], "Organised"), self._reload_tracks()))
        self._run(w)

    def _convert(self, tracks=None):
        if not convert.available():
            QMessageBox.warning(self, "Convert", "ffmpeg is not installed."); return
        tr = self._tool_tracks(tracks)
        dlg = ConvertDialog(len(tr), str(Path.home() / "Music" / "Converted"), self)
        if not dlg.exec():
            return
        label, dest, pattern = dlg.result_options()
        w = self._job(lambda prog: convert.convert_tracks(tr, dest, label, pattern, prog), "Convert")
        w.done.connect(lambda r: self._report("Convert", r[0], r[1], "Converted"))
        self._run(w)

    def _scan_rg(self, tracks=None):
        if not rgscan.available():
            QMessageBox.warning(self, "ReplayGain", "ffmpeg is not installed."); return
        tr = self._tool_tracks(tracks)
        dlg = ReplayGainDialog(len(tr), self)
        if not dlg.exec():
            return
        w = self._job(lambda prog: rgscan.scan_tracks(self.db, tr, prog, by_album=dlg.album.isChecked()),
                      "ReplayGain")
        w.done.connect(lambda r: (self._report("ReplayGain", r[0], r[1], "Tagged"), self._reload_tracks()))
        self._run(w)

    def _find_missing(self):
        import os
        missing = [t for t in self._tracks("local") if t.get("path") and not os.path.exists(t["path"])]
        if not missing:
            QMessageBox.information(self, "Missing Files", "All local files in the library exist."); return
        dlg = MissingDialog(missing, self)
        if dlg.exec() and dlg.checked_ids():
            self.db.delete_tracks(dlg.checked_ids())
            self.refresh_playlists(); self.load_source()

    def _reload_tracks(self):
        for q in self.player.queue:                              # files may have moved: refresh queued paths
            fresh = self.db.track(q["id"]) if q.get("id") else None
            if fresh:
                q.update(path=fresh["path"], key=fresh["key"], rg_gain=fresh["rg_gain"])
        self.refresh_playlists(); self.load_source()

    def _save_queue_playlist(self):
        q = list(self.player.queue)
        if not q:
            self.statusBar().showMessage("The queue is empty", 4000); return
        self._new_playlist(q)

    def _copy_smart(self, preset):
        import copy
        dlg = SmartDialog(self, preset + " (copy)", copy.deepcopy(smart.PRESETS[preset]))
        if dlg.exec():
            sid = self.db.save_smart(dlg.name.text().strip(), dlg.result_spec())
            self.source = ("smart", sid); self.refresh_playlists(); self.load_source()

    def _after_library_change(self):
        """Drop queued tracks that belong to switched-off libraries, then refresh every view."""
        P = self.player
        cur = P.current
        if cur and not self.cfg.track_enabled(cur):
            P.clear()
        elif P.queue:
            P.remove([i for i, t in enumerate(P.queue) if not self.cfg.track_enabled(t)])
        self.refresh_playlists(); self.load_source()

    # ------------------------------------------------ panels / views / skins
    def _dock(self, title, widget, name, area):
        d = QDockWidget(title, self); d.setObjectName(name); d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                      | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _reset_layout(self):
        self._apply_builtin_layout("Default")

    def _refresh_next(self):
        if self.dock_next.isVisible():
            self.next_panel.refresh(self.player.queue, self.player.index)

    def _on_queue_changed(self):
        self._refresh_next()
        if self.source[0] == "queue": self.load_source()

    def _sel_changed(self):
        if self.dock_info.isVisible():
            sel = self._selected()
            self.info_panel.set_tracks(sel or ([self.player.current] if self.player.current else []))

    def _set_view_mode(self, mode, initial=False):
        mode = mode if mode in ("browser", "albums") else "browser"
        self.cfg.view_mode = mode
        (self.act_albums if mode == "albums" else self.act_browser).setChecked(True)
        self.top_stack.setCurrentIndex(1 if mode == "albums" else 0)
        if not initial:
            total = sum(self.main_split.sizes()) or 780
            top = int(total * (0.5 if mode == "albums" else 0.25))
            self.main_split.setSizes([top, total - top])
        if not initial:
            self.model.set_filters(genre=None, artist=None, album=None)
            self._fill_browser("genre"); self._refresh_grid(); self._update_status()
        else:
            self._refresh_grid()

    def _refresh_grid(self):
        if self.cfg.view_mode != "albums": return
        self.grid.set_tracks(self.model.base())

    def _on_grid_album(self, artist, album):
        self.model.set_filters(genre=None, artist=artist, album=album); self._update_status()

    def _grid_menu(self, tracks, gpos):
        m = QMenu(self)
        m.addAction("Play Album", lambda: self.player.play_list(tracks, 0))
        m.addAction("Play Next", lambda: self.player.enqueue(tracks, next_=True))
        m.addAction("Queue Last", lambda: self.player.enqueue(tracks))
        pm = m.addMenu("Add to Playlist")
        pm.addAction("New Playlist…", lambda: self._new_playlist(tracks))
        for p in self.db.playlists():
            pm.addAction(p["name"], lambda p=p: self._add_to_playlist(p["id"], tracks))
        m.exec(gpos)

    def _header_menu(self, pos):
        hh = self.table.horizontalHeader(); m = QMenu(self)
        for i, (label, key, _w) in enumerate(COLUMNS):
            if i == 0: continue
            a = m.addAction(label); a.setCheckable(True); a.setChecked(not self.table.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: self.table.setColumnHidden(i, not on))
        m.exec(hh.mapToGlobal(pos))

    def _on_table_click(self, idx):
        """Click on the stars in the Rating column to rate (click the current rating again to clear)."""
        if COLUMNS[idx.column()][1] != "rating": return
        t = self.model.tracks[idx.row()]
        x = self.table.viewport().mapFromGlobal(QCursor.pos()).x() - self.table.columnViewportPosition(idx.column())
        sw = max(8, self.table.fontMetrics().horizontalAdvance("★"))
        star = 0 if x < 3 else min(5, (x - 3) // sw + 1)
        self.set_rating([t], 0 if star == t["rating"] else star)

    def _build_skin_menu(self):
        self.skin_menu.clear(); grp = QActionGroup(self)
        for name in skins.all_skins():
            a = self.skin_menu.addAction(name); a.setCheckable(True); a.setChecked(name == self.cfg.skin)
            grp.addAction(a); a.triggered.connect(lambda _c, n=name: self.set_skin(n))
        self.skin_menu.addSeparator()
        self.skin_menu.addAction("Import Skin…", self._import_skin)
        self.skin_menu.addAction("Reload Skins", self._build_skin_menu)
        self.skin_menu.addAction("Open Skins Folder…",
                                 lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(skins.skins_dir()))))

    def _import_skin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import skin", "", "Skins (*.json)")
        if not path:
            return
        try:
            name = skins.import_skin(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import skin", f"Could not import that skin: {e}"); return
        self._build_skin_menu(); self.set_skin(name)
        self.statusBar().showMessage(f"Skin '{name}' imported", 5000)

    def set_skin(self, name):
        self.cfg.skin = name
        self.skin_tokens = skins.apply_skin(QApplication.instance(), name)
        self._apply_skin_visuals()

    def _apply_skin_visuals(self):
        self.model.playing_color = self.skin_tokens["accent"]
        self.model.set_playing(self.model.playing_id)
        self.grid.set_placeholder_color(QColor(self.skin_tokens["button"]))
        self._refresh_grid()

    # ------------------------------------------------ tray / mini player / sleep
    def _build_tray(self):
        self.tray = None
        if not (self.cfg.show_tray and QSystemTrayIcon.isSystemTrayAvailable()):
            return
        icon = QIcon.fromTheme("multimedia-audio-player", self._icon(QStyle.StandardPixmap.SP_MediaPlay))
        self.tray = QSystemTrayIcon(icon, self); self.tray.setToolTip("Hivefi")
        m = QMenu()
        m.addAction("Play/Pause", self.player.toggle); m.addAction("Next", self.player.next)
        m.addAction("Previous", self.player.previous); m.addSeparator()
        m.addAction("Show / Hide", self._toggle_visible); m.addAction("Quit", self._quit)
        self.tray.setContextMenu(m); self._tray_menu = m
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.ActivationReason.Trigger and self._toggle_visible())
        self.tray.show()
        self.player.trackChanged.connect(lambda t: self.tray.setToolTip(
            f'{t["artist"]} – {t["title"]}' if t else "Hivefi"))

    def _toggle_visible(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal(); self.raise_(); self.activateWindow()

    def _quit(self):
        self._quitting = True; self.close()

    def _enter_mini(self):
        self.hide(); self.mini.show(); self.mini.set_track(self.player.current)

    def _leave_mini(self):
        self.mini.hide(); self.showNormal(); self.raise_(); self.activateWindow()

    def _set_sleep(self, minutes):
        self.sleep_timer.stop()
        if minutes:
            self.sleep_timer.start(minutes * 60_000)
            self.statusBar().showMessage(f"Sleep timer: playback will pause in {minutes} min", 5000)

    def _sleep_fire(self):
        self.player.mp.pause()
        for a in self.sleep_group.actions():
            a.setChecked(a.text() == "Off")
        self.statusBar().showMessage("Sleep timer: paused playback", 8000)

    # ------------------------------------------------ auto playlists
    def _smart_spec(self, key):
        if isinstance(key, str):
            return smart.PRESETS[key]
        return next((p["spec"] for p in self.db.smart_playlists() if p["id"] == key), {"rules": []})

    def _new_smart(self):
        dlg = SmartDialog(self)
        if dlg.exec():
            sid = self.db.save_smart(dlg.name.text().strip(), dlg.result_spec())
            self.source = ("smart", sid); self.refresh_playlists(); self.load_source()

    def _edit_smart(self, sid):
        cur = next(p for p in self.db.smart_playlists() if p["id"] == sid)
        dlg = SmartDialog(self, cur["name"], cur["spec"])
        if dlg.exec():
            self.db.save_smart(dlg.name.text().strip(), dlg.result_spec(), sid)
            self.refresh_playlists(); self.load_source()

    def _delete_smart(self, sid):
        if QMessageBox.question(self, "Delete auto playlist", "Delete this auto playlist?") == QMessageBox.StandardButton.Yes:
            self.db.delete_smart(sid); self.refresh_playlists()

    # ------------------------------------------------ extra features
    def _toggle_auto_dj(self, on):
        self.cfg.auto_dj = on
        self.player.auto_dj = self._auto_dj_tracks if on else None

    def _set_rg_mode(self, mode):
        self.cfg.rg_mode = mode; self.cfg.save(); self.player.configure()

    def open_audio_settings(self):
        dlg = AudioDialog(self.cfg, self.player.mp, self)
        dlg.on_apply = self.player.configure
        dlg.exec()

    def _auto_dj_tracks(self, n=10):
        pool = self.model.tracks if self.source[0] not in ("queue", "playlist") and self.model.tracks \
            else self._tracks()
        queued = {t["id"] for t in self.player.queue}
        fresh = [t for t in pool if t["id"] not in queued] or pool
        return random.sample(fresh, min(n, len(fresh)))

    def _drop_to_playlist(self, pid, ids):
        self._add_to_playlist(pid, [{"id": i} for i in ids])

    def _edit_tags(self, tracks):
        dlg = TagDialog(tracks, self)
        if not dlg.exec(): return
        vals = dlg.values()
        if not vals: return
        errors = []
        for t in tracks:
            try:
                tagedit.write_tags(t["path"], vals)
                mtime = Path(t["path"]).stat().st_mtime
                sets = ", ".join(f"{k}=?" for k in vals) + ", mtime=?"
                self.db.conn.execute(f"UPDATE tracks SET {sets} WHERE id=?", (*vals.values(), mtime, t["id"]))
                self.model.update_track(t["id"], **vals)
            except Exception as e:  # noqa: BLE001
                errors.append(f'{Path(t["path"]).name}: {e}')
        self.db.commit()
        if errors:
            QMessageBox.warning(self, "Some tags could not be saved", "\n".join(errors[:10]))
        self._fill_browser("genre")

    def _import_m3u(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import playlist", "", "Playlists (*.m3u *.m3u8)")
        if not path: return
        base = Path(path).parent
        by_path = {t["path"]: t["id"] for t in self.db.all_tracks("local")}
        ids, missing = [], 0
        for line in Path(path).read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            f = Path(line) if Path(line).is_absolute() else base / line
            tid = by_path.get(str(f)) or by_path.get(str(f.resolve()))
            if tid: ids.append(tid)
            else: missing += 1
        pid = self.db.create_playlist(Path(path).stem)
        self.db.set_playlist_tracks(pid, ids)
        self.refresh_playlists()
        self.statusBar().showMessage(f"Imported {len(ids)} tracks ({missing} not in library)", 6000)

    def _export_m3u(self, pid):
        pl = next(p for p in self.db.playlists() if p["id"] == pid)
        path, _ = QFileDialog.getSaveFileName(self, "Export playlist", pl["name"] + ".m3u8", "Playlists (*.m3u8)")
        if not path: return
        lines = ["#EXTM3U"]
        for t in self.db.playlist_tracks(pid):
            if t["source"] == "local":
                lines += [f'#EXTINF:{int(t["duration"])},{t["artist"]} - {t["title"]}', t["path"]]
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.statusBar().showMessage("Exported local tracks (server tracks have no file path)", 6000)

    def show_lyrics(self):
        self.dock_lyrics.show(); self.dock_lyrics.raise_()
        self._refresh_lyrics()

    def _refresh_lyrics(self):
        if not self.dock_lyrics.isVisible(): return
        t = self.player.current
        if not t:
            self.lyrics_panel.show_lyrics("", ""); return
        tc, api = dict(t), self.api
        title = f'{t["artist"]} – {t["title"]}'
        self.lyrics_panel.show_lyrics(title, "Loading…")
        w = Worker(lambda _p: lyrics.find_lyrics(tc, api), self)
        w.done.connect(lambda txt, tid=t["id"]: self.player.current and self.player.current["id"] == tid
                       and self.lyrics_panel.show_lyrics(title, txt))
        self._run(w)

    # --------------------------------------------------- background jobs
    def _run(self, w: Worker):
        self._workers.add(w)
        w.finished.connect(lambda: self._workers.discard(w))
        w.start()

    def _startup(self):
        if self.cfg.library_dirs and not self.db.all_tracks("local"):
            self.scan_library()
        elif self.cfg.library_dirs:
            self.scan_library(quiet=True)
        if self.api and self.cfg.sync_on_start:
            self.sync_now(silent=True)
        if self.cfg.check_updates and not os.environ.get("HIVEFI_NO_UPDATE_CHECK"):
            self.check_updates()

    # --------------------------------------------------- self-update
    def check_updates(self, manual=False):
        w = Worker(lambda _p: updater.check(self.cfg), self)
        w.done.connect(lambda remote: self._update_found(remote, manual))
        w.failed.connect(lambda msg: manual and QMessageBox.warning(self, "Updates", f"Update check failed: {msg}"))
        self._run(w)

    def _update_found(self, remote, manual):
        if not remote:
            if manual:
                QMessageBox.information(self, "Updates", "You are running the latest version.")
            return
        a = QMessageBox.question(self, "Update available",
                                 f"A newer version ({remote[:7]}) is available on GitHub.\n\n"
                                 "Download it and restart now?")
        if a != QMessageBox.StandardButton.Yes:
            return
        self.statusBar().showMessage("Downloading update…")
        w = Worker(lambda _p: updater.apply(self.cfg, remote=remote), self)
        w.done.connect(self._update_installed)
        w.failed.connect(lambda msg: QMessageBox.warning(self, "Update failed", msg))
        self._run(w)

    def _update_installed(self, _ver):
        self.restart_requested = True
        self._quit()

    def _apply_sync_timer(self):
        self.sync_timer.stop()
        if self.cfg.server_enabled and self.cfg.sync_interval_min > 0:
            self.sync_timer.start(self.cfg.sync_interval_min * 60_000)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add music folder")
        if d and d not in self.cfg.library_dirs:
            self.cfg.library_dirs.append(d); self.cfg.save(); self.scan_library()

    def scan_library(self, quiet=False):
        if self._scan_worker and self._scan_worker.isRunning(): return
        dirs = list(self.cfg.library_dirs)
        if not dirs:
            if not quiet: self.open_settings()
            return
        db = self.db
        skip = list(self.cfg.disabled_dirs)
        w = Worker(lambda prog: scanner.scan(db, dirs, lambda i, n: prog(f"Scanning {i}/{n}"), skip=skip,
                                             cancelled=lambda: self._closing), self)
        w.progress.connect(lambda m: self.sync_lbl.setText(m))
        def done(res):
            self.sync_lbl.setText(f"Library: {res[0]} updated, {res[1]} removed")
            if res[0] or res[1]:                                 # nothing changed: leave the views alone
                self.load_source(keep=True)
        w.done.connect(done)
        w.failed.connect(lambda e: self.sync_lbl.setText(f"Scan failed: {e}"))
        self._scan_worker = w; self._run(w)

    def sync_now(self, silent=False):
        if not self.api:
            if not silent:
                if self.cfg.server_configured:
                    QMessageBox.information(self, "Navidrome is off",
                                            "The Navidrome library is switched off. Turn it on under View → Libraries.")
                else:
                    self.open_settings()
            return
        if self._sync_worker and self._sync_worker.isRunning(): return
        api, db = self.api, self.db
        folders = list(self.cfg.server_folders)
        w = Worker(lambda prog: sync.full_sync(db, api, prog, folders), self)
        w.progress.connect(lambda m: self.sync_lbl.setText("☁ " + m))
        def done(r):
            self.sync_lbl.setText(f"☁ Synced {time.strftime('%H:%M')} · {r['songs']} songs")
            if r.get("folder_names") and r["folder_names"] != self.cfg.server_folder_names:
                self.cfg.server_folder_names = r["folder_names"]; self.cfg.save()
            self.refresh_playlists()
            if r["changed"] or r["removed"] or self.source[0] == "playlist":
                self.load_source(keep=True)
        def failed(e):
            self.sync_lbl.setText(f"☁ Sync failed: {e}")
            if not silent: QMessageBox.warning(self, "Sync failed", e)
        w.done.connect(done); w.failed.connect(failed)
        self._sync_worker = w; self._run(w)

    def _kick_flush(self):
        if not self.api or (self._flush_worker and self._flush_worker.isRunning()): return
        api, db = self.api, self.db
        w = Worker(lambda _p: sync.flush_ops(db, api), self)
        self._flush_worker = w; self._run(w)

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            dlg.apply(); self._rebuild_api(); self._apply_sync_timer()
            self.player.configure(); self._update_vis_availability()
            self._after_library_change()
            self.scan_library(quiet=True)
            if self.api: self.sync_now(silent=True)

    def closeEvent(self, e):
        if self.cfg.close_to_tray and self.tray and self.tray.isVisible() and not self._quitting:
            self.hide(); e.ignore(); return
        self._closing = True                                     # lets a running scan stop early
        self._save_session()
        if self.tray: self.tray.hide()
        self.mini.hide()
        self.player.mp.shutdown()
        self.player.stop()
        for w in list(self._workers): w.wait(2000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("hivefi"); app.setDesktopFileName("hivefi")
    cfg = Config.load()
    skins.apply_skin(app, cfg.skin)
    db = Database(data_dir() / "library.db")
    win = MainWindow(cfg, db)
    win.show()
    code = app.exec()
    if win.restart_requested:                       # update installed: relaunch the new code
        os.chdir(updater.ROOT)
        os.execv(sys.executable, [sys.executable, "-m", "hivefi", *sys.argv[1:]])
    sys.exit(code)
