import json
import os
import random
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QEventLoop, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QCursor, QDesktopServices, QIcon, QKeySequence, QLinearGradient,
                         QPainter, QPixmap, QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QMainWindow, QMenu, QMenuBar, QMessageBox, QPushButton, QSlider,
                             QSplitter, QStackedWidget, QStyle, QSystemTrayIcon, QTableView, QToolBar,
                             QSizePolicy, QTabWidget, QTreeWidget, QTreeWidgetItem, QToolButton, QVBoxLayout, QHBoxLayout, QWidget)

from .. import art, scanner, sync
from . import icons
from ..config import Config, data_dir
from ..db import Database
from ..mpris import Mpris
from ..player import Player
from ..subsonic import Subsonic, SubsonicError
from .. import art, convert, coverart, devicesync, inbox as inbox_mod, lastfm, notify, podcasts, sortkey, folders, stats as libstats, libexport, listenbrainz, lyrics, radio, tagtools, organizer, rgscan, skins, smart, tagedit, updater
from .artist_list import ArtistList
from .album_grid import AlbumGrid, AlbumsPage
from .album_tracks import AlbumTracksView
from .dialogs import TagDialog
from .audio_dialog import AudioDialog
from .duplicates_dialog import DuplicatesDialog
from . import icons, shortcuts
from .stats_dialog import StatsDialog
from .devicesync_dialog import DeviceSyncDialog
from .np_dialog import NowPlayingDialog
from .station_dialog import StationDialog
from .watcher import FolderWatcher
from .tagtools_dialog import TagToolsDialog
from .tools_dialogs import ConvertDialog, MissingDialog, OrganizeDialog, ReplayGainDialog
from .equalizer import EqualizerPanel
from .mini import MiniPlayer
from .tabs_mixin import TabsMixin
from .panels_mixin import STATE_VERSION, PanelsMixin
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


class WindowButtons(QWidget):
    """The corner of the menu bar: the window title and minimise / maximise / close, in place of a title bar."""

    def __init__(self, win, with_title=True):
        super().__init__(win)
        self.win = win
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.title = QLabel(""); self.title.setVisible(with_title); self.title.setStyleSheet("color: palette(mid); padding-right: 14px;")
        self.title.setFixedWidth(self.TITLE_W)                   # the bar sizes its corner once: a fixed, elided label
        self.title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.title)
        self.btns = []
        for name, text, tip, fn in (("min", "–", "Minimise", win.showMinimized), ("max", "□", "Maximise / restore", self.toggle),
                                    ("close", "✕", "Close", win.close)):
            b = QToolButton(); b.setObjectName("winbtn_" + name); b.setText(text); b.setToolTip(tip)
            b.setFixedSize(42, 28); b.setAutoRaise(True); b.clicked.connect(lambda _c=False, f=fn: f())
            b.setStyleSheet("QToolButton { border: 0; background: transparent; font-size: 14px; }"
                            "QToolButton:hover { background: rgba(128,128,128,70); }"
                            + ("QToolButton:hover { background: #e81123; color: white; }" if name == "close" else ""))
            lay.addWidget(b); self.btns.append(b)
        win.windowTitleChanged.connect(self.set_title)
        self.set_title(win.windowTitle())

    TITLE_W = 340

    def set_title(self, text):
        self.title.setText(self.title.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self.TITLE_W - 18))
        self.title.setToolTip(text)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "title"):
            self.set_title(self.win.windowTitle())

    def toggle(self):
        w = self.win
        w.showNormal() if w.isMaximized() else w.showMaximized()


class MenuWindow(QWidget):
    """The menu bar in a small window of its own (Settings → General → menu bar)."""
    closed = pyqtSignal()

    def __init__(self, actions):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Hivefi menu")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.bar = QMenuBar(); lay.addWidget(self.bar)
        for a in actions:
            self.bar.addAction(a)                                # the same actions (and menus) as the main window's bar
        self.setMinimumWidth(self.bar.sizeHint().width() + 8)

    def closeEvent(self, e):
        self.closed.emit(); super().closeEvent(e)


class MainWindow(PanelsMixin, TabsMixin, QMainWindow):
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
        sortkey.set_articles(cfg.ignore_articles)
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
        self.shortcut_actions = shortcuts.collect(self.menuBar())
        self.shortcut_defaults = {k: shortcuts.portable(a.shortcut()) for k, a in self.shortcut_actions.items()}
        self.apply_shortcuts()
        self._connect_player()
        self.mpris = Mpris(self, self.player)
        self.mini = MiniPlayer(self.player)
        self.mini.restoreRequested.connect(self._leave_mini)
        self._build_tray()
        self._init_equalizer()
        self._default_state = self.saveState(STATE_VERSION)
        self._first_run = not cfg.state
        if cfg.geometry:
            self.restoreGeometry(QByteArray.fromBase64(cfg.geometry.encode()))
        if cfg.state:
            self.restoreState(QByteArray.fromBase64(cfg.state.encode()), STATE_VERSION)
        if cfg.header_state:
            self._restore_header(QByteArray.fromBase64(cfg.header_state.encode()))
            self._sorted_once = True
        self._set_edit_mode(False)                               # always starts locked; View → Edit Mode unlocks
        QTimer.singleShot(0, self._restore_splits)               # needs the window laid out at its final size
        self._start_autosave()
        self._set_view_mode(cfg.view_mode, initial=True)
        self._set_list_mode(cfg.list_mode, sort=False)
        self._apply_skin_visuals()
        self._apply_menu_mode()
        self._apply_titlebar()
        self.sleep_timer = QTimer(self, singleShot=True, timeout=self._sleep_fire)
        self._setup_watch()

        self.sync_timer = QTimer(self, timeout=lambda: self.sync_now(silent=True))
        self._apply_sync_timer()
        self.refresh_playlists()
        self.status_lbl.setText("Loading library…")
        w = Worker(lambda _p: len(self.db.all_tracks()), self)       # fill the track cache off the UI thread
        w.done.connect(lambda _n: (self._restore_session(), self._startup(), self._after_first_load()))
        w.failed.connect(lambda _e: (self.load_source(), self._startup(), self._after_first_load()))
        self._run(w)

    # ------------------------------------------------ page tabs (Visualizer, Now Playing)
    def _show_page(self, kind):
        pages = self.__dict__.setdefault("_pages", {})
        shown = getattr(self, "_page_shown", None)
        if shown and shown != kind and shown in pages:
            pages[shown].hide()
        self._page_shown = kind
        for w in (getattr(self, "search", None), self.findChild(QToolBar, "tb_mode")):
            if w is not None:
                w.setEnabled(not kind)                           # search and Columns / Albums belong to library tabs
        if not kind:
            return
        if kind not in pages:
            from .pages import NowPlayingPage, VisualizerPage
            pages[kind] = (VisualizerPage if kind == "visualizer" else NowPlayingPage)(self)
        pg = pages[kind]
        self._place_page(pg); pg.show(); pg.raise_()

    def _place_page(self, pg=None):
        """The page covers everything between the top toolbars (tabs...) and the bottom toolbar (player bar)."""
        pg = pg or self.__dict__.get("_pages", {}).get(getattr(self, "_page_shown", None))
        if pg is None:
            return
        top = self.menuBar().geometry().bottom() + 1 if self.menuBar().isVisible() else 0
        bottom = self.height() - (self.statusBar().height() if self.statusBar().isVisible() else 0)
        for tb in self.toolbars:
            if not tb.isVisible():
                continue
            area = self.toolBarArea(tb)
            if area == Qt.ToolBarArea.TopToolBarArea:
                top = max(top, tb.geometry().bottom() + 1)
            elif area == Qt.ToolBarArea.BottomToolBarArea:
                bottom = min(bottom, tb.geometry().top())
        pg.setGeometry(0, top, self.width(), max(50, bottom - top))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_page()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.skin_tokens.get("glow"):
            return
        p = QPainter(self); r = self.rect()
        cover = getattr(self, "_last_cover", None)
        if cover is not None and not cover.isNull():                 # the playing cover, blurred (once per cover)
            if getattr(self, "_glow_key", None) != cover.cacheKey():
                from .panels import blurred
                self._glow_key, self._glow_bg = cover.cacheKey(), blurred(cover, sigma=14.0)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(r, self._glow_bg)
            p.fillRect(r, QColor(255, 255, 255, 95))
        else:
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, QColor("#a9c2c6")); g.setColorAt(1, QColor("#d3c98f"))
            p.fillRect(r, g)

    def _after_first_load(self):
        if self._first_run:                       # no saved layout: size the panels once the content is in
            self._first_run = False
            QTimer.singleShot(100, self._size_default_docks)

    def _size_default_docks(self):
        """First-run proportions (needs a laid-out window): a compact browser above the track list, and the two
        side panels sharing the left column. Pairs of docks must be resized together for Qt to honour it."""
        O = Qt.Orientation
        self.resizeDocks([self.dock_artists, self.dock_browser, self.dock_tracks],
                         [260, max(300, self.width() - 260 - 400), 400], O.Horizontal)

    # ------------------------------------------------------------ setup
    def _rebuild_api(self):
        c = self.cfg
        self.api = Subsonic(c.server_url, c.username, c.password) if c.server_enabled else None

    def _resolve(self, t):
        if t["source"] == "local":
            return QUrl.fromLocalFile(t["path"])
        if t["source"] == "radio":
            return QUrl(t["url"])
        if t["source"] == "podcast":
            return QUrl.fromLocalFile(t["path"]) if t.get("path") else QUrl(t["url"])
        if self.api is None:
            return QUrl()
        return QUrl(self.api.stream_url(t["remote_id"], self.cfg.max_bitrate))

    # tracks from libraries that are switched off never show up anywhere
    def _tracks(self, source=None):
        return self.cfg.enabled_tracks(self.db.all_tracks(source))

    def _playlist_tracks(self, pid):
        return [t for t in self.db.playlist_tracks(pid) if self.cfg.track_enabled(t)]

    def _restore_header(self, state: QByteArray):
        """Restore the track table's column order, widths and visibility.

        QHeaderView.restoreState() on a state saved before columns were added appends the new sections without
        counting them in the header's length; hiding them afterwards then shrinks the length below the real width
        and the table stops painting cells part-way across (everything after Artist came out empty). So the state
        is restored into a throwaway header and copied over section by section, which keeps the length right."""
        tmp = QTableView(); tmp.setModel(self.model)
        th = tmp.horizontalHeader()
        if not th.restoreState(state):
            tmp.deleteLater(); return
        h, n = self.table.horizontalHeader(), min(th.count(), len(COLUMNS))
        # Qt shows the columns an older state did not know about: keep the optional ones hidden
        known = set(self.cfg.header_columns) or {c[1] for c in COLUMNS[:17]}      # states saved before this was recorded
        for v in range(n):
            li = th.logicalIndex(v)
            h.moveSection(h.visualIndex(li), v)
        hide = {i: th.isSectionHidden(i) or (key not in known and key in DEFAULT_HIDDEN)
                for i, (_, key, _w) in enumerate(COLUMNS[:n])}
        shown = [li for li in (h.logicalIndex(v) for v in range(n)) if not hide.get(li, False)]
        last = shown[-1] if shown else -1                   # the stretch section: let it fill the rest of the row
        for i, hidden in hide.items():
            if not hidden and i != last:
                h.resizeSection(i, th.sectionSize(i))
            h.setSectionHidden(i, hidden)
        h.setSortIndicator(th.sortIndicatorSection(), th.sortIndicatorOrder())
        tmp.deleteLater()

    def _icon(self, sp):
        return icons.icon(self, sp)

    def _build_ui(self):
        self._build_toolbars()
        self._build_tabs()
        by = {tb.objectName(): tb for tb in self.toolbars}
        tabs_tb = by["tb_tabs"]
        for tb in (tabs_tb, by["tb_mode"], by["tb_search"]):      # one row, as in MusicBee: tabs ... view mode, search
            self.removeToolBar(tb); self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb); tb.show()
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        by["tb_search"].insertWidget(by["tb_search"].actions()[0], spacer)       # the last toolbar fills the row
        for area in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea):
            self.setTabPosition(area, QTabWidget.TabPosition.North)

        # sources tree
        self.tree = SourceTree(); self.tree.setHeaderHidden(True)
        self.tree.tracksDropped.connect(self._drop_to_playlist); self.tree.setMinimumWidth(170)
        self.tree.itemSelectionChanged.connect(self._on_source)
        self.tree.itemExpanded.connect(self._fill_folder_children)
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
        self.top_stack = QStackedWidget(); self.top_stack.addWidget(browser); self.top_stack.addWidget(AlbumsPage(self.grid))

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
        self.album_view = AlbumTracksView(self.model, self.table.selectionModel(), lambda: self.api)
        self.album_view.activated.connect(lambda r: self._on_double(self.model.index(r, 2)))
        self.album_view.contextMenu.connect(lambda gpos: self._track_menu(None, gpos))
        self.album_view.ratingClicked.connect(lambda r, star: self._rate_row(r, star))
        QShortcut(QKeySequence(Qt.Key.Key_Space), self.album_view, self.player.toggle,
                  context=Qt.ShortcutContext.WidgetShortcut)
        for w in (self.table, self.album_view):                  # Delete: remove from the library (asks first)
            QShortcut(QKeySequence(Qt.Key.Key_Delete), w, self._delete_key,
                      context=Qt.ShortcutContext.WidgetShortcut)
        self.list_stack = QStackedWidget(); self.list_stack.addWidget(self.table); self.list_stack.addWidget(self.album_view)

        # Every part of the window is a panel, the track list included. Qt wants a central widget: this one is an
        # anchor that is as wide as the window but has no height, so the panels take all the room. The browser and
        # the track list share the top dock area (split vertically) and keep their proportions when the window grows.
        self._build_edit_bar()
        anchor = QWidget(); anchor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        anchor.setMaximumHeight(0); anchor.setMinimumSize(0, 0)
        self.setCentralWidget(anchor)
        self.setDockNestingEnabled(True)
        self.top_stack.setMinimumHeight(110); self.list_stack.setMinimumHeight(80)
        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.TopRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        # MusicBee's arrangement: Album Artist | Albums | Album and Tracks. The Sources tree is a tab behind Album Artist.
        self.artist_list = ArtistList(lambda: self.api)
        self.artist_list.artistSelected.connect(self._on_artist_pick)
        self.dock_browser = self._dock("Library Browser", self.top_stack, "dock_browser",
                                       Qt.DockWidgetArea.TopDockWidgetArea)
        self.dock_tracks = self._dock("Track List", self.list_stack, "dock_tracks", Qt.DockWidgetArea.RightDockWidgetArea)

        # dockable panels
        self.np_panel = NowPlayingPanel(self.cfg); self.next_panel = UpNextPanel()
        self.np_panel.navigate.connect(self._np_navigate); self.np_panel.configureRequested.connect(self._configure_now_playing)
        self.lyrics_panel = LyricsPanel(); self.info_panel = TrackInfoPanel()
        L, R = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        self.dock_artists = self._dock("Album Artist", self.artist_list, "dock_artists", L)
        self.dock_sources = self._dock("Sources", self.tree, "dock_sources", L)
        self.tabifyDockWidget(self.dock_artists, self.dock_sources); self.dock_artists.raise_()
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
        self.dock_np.setMinimumHeight(200); self.dock_np.hide()
        self.dock_artists.visibilityChanged.connect(lambda v: v and self._fill_artists())
        self.dock_lyrics.visibilityChanged.connect(lambda v: v and self._refresh_lyrics())
        self.dock_next.visibilityChanged.connect(lambda v: v and self._refresh_next())
        self.dock_info.visibilityChanged.connect(lambda v: v and self._sel_changed())
        self.next_panel.jumpRequested.connect(self.player.jump)
        self.next_panel.removeRequested.connect(self.player.remove)
        self.next_panel.moveRequested.connect(self.player.move)
        self.now_lbl = QLabel(""); self.statusBar().addWidget(self.now_lbl, 1)         # "Artist - Title", as in MusicBee
        self.status_lbl = QLabel(""); self.statusBar().addPermanentWidget(self.status_lbl)
        self.sync_lbl = QLabel(""); self.statusBar().addPermanentWidget(self.sync_lbl)
        self.btn_edit = QPushButton("✎ Edit layout"); self.btn_edit.setCheckable(True); self.btn_edit.setFlat(True)
        self.btn_edit.setToolTip("Unlock the layout to move, float, tab or close panels (Ctrl+Shift+E); resizing always works")
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
        e = mb.addMenu("&Edit")
        e.addAction("Select &All", QKeySequence("Ctrl+A"), self._select_all)
        e.addAction("&Edit Tags…", QKeySequence("Shift+Return"),
                    lambda: (lambda loc: loc and self._edit_tags(loc))([t for t in self._selected() if t["source"] == "local"]))
        e.addSeparator()
        e.addAction("&Shuffle Play Selection", lambda: self._shuffle_play(self._selected()))
        e.addAction("&Remove from Library…", lambda: self._remove_from_library())
        p = mb.addMenu("&Playback")
        p.addAction("Play/Pause", QKeySequence("Ctrl+Space"), self.player.toggle)
        p.addAction("Next", QKeySequence("Ctrl+Right"), self.player.next)
        p.addAction("Previous", QKeySequence("Ctrl+Left"), self.player.previous)
        p.addAction("Stop", self.player.stop)
        self.act_stop_after = p.addAction("Stop after Current"); self.act_stop_after.setCheckable(True)
        self.act_stop_after.toggled.connect(self.player.set_stop_after_current)
        self.player.stopAfterChanged.connect(self.act_stop_after.setChecked)
        p.addAction("Clear Queue", self.player.clear)
        p.addSeparator()
        self.act_shuffle = p.addAction("Shuffle", QKeySequence("Ctrl+H")); self.act_shuffle.setCheckable(True)
        self.act_shuffle.setChecked(self.cfg.shuffle)
        self.act_shuffle.toggled.connect(self.btn_shuffle.setChecked)          # the button does the work
        self.btn_shuffle.toggled.connect(self.act_shuffle.setChecked)
        p.addAction("Repeat (Off / All / One)", QKeySequence("Ctrl+R"), self._cycle_repeat)
        p.addAction("Volume Up", QKeySequence("Ctrl+Up"), lambda: self.vol.setValue(min(100, self.vol.value() + 5)))
        p.addAction("Volume Down", QKeySequence("Ctrl+Down"), lambda: self.vol.setValue(max(0, self.vol.value() - 5)))
        p.addAction("Mute / Unmute", QKeySequence("Ctrl+Shift+M"), self._toggle_mute)
        p.addAction("Seek Forward 10 s", QKeySequence("Ctrl+Shift+Right"), lambda: self._seek_by(10_000))
        p.addAction("Seek Back 10 s", QKeySequence("Ctrl+Shift+Left"), lambda: self._seek_by(-10_000))
        rp = p.addMenu("Rate Playing Track")
        for r in range(6):
            rp.addAction("★" * r if r else "No rating", QKeySequence(f"Ctrl+{r}"),
                         lambda r=r: self.player.current and self.set_rating([self.player.current], r))
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
        v.addAction("Now Playing Options…", self._configure_now_playing)
        v.addSeparator()
        self.lib_menu = v.addMenu("Libraries"); self.lib_menu.aboutToShow.connect(self._build_lib_menu)
        pm = v.addMenu("Panels")
        for d in (self.dock_sources, self.dock_browser, self.dock_tracks, self.dock_np, self.dock_next, self.dock_lyrics,
                  self.dock_info, self.dock_eq, self.dock_vis):
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
        vm.addSeparator()
        vm.addAction("Fullscreen Visualizer", QKeySequence("F8"), self.open_fullscreen_vis)
        fm = vm.addMenu("Fullscreen Effect"); fm.aboutToShow.connect(lambda: self._fill_fullscreen_menu(fm))
        vm.addSeparator()
        cm = vm.addMenu("Colors"); cm.aboutToShow.connect(lambda: self.vis.fill_colors_menu(cm))
        vm.addAction("Show Visualizer", self.dock_vis.show)
        v.addAction("Columns View", lambda: self._set_view_mode("browser"))
        v.addAction("Albums View", lambda: self._set_view_mode("albums"))
        lg = QActionGroup(self)
        self.act_list_details = v.addAction("Track List: Details"); self.act_list_albums = v.addAction("Track List: Album and Tracks")
        for a, mode in ((self.act_list_details, "details"), (self.act_list_albums, "albums")):
            a.setCheckable(True); lg.addAction(a); a.setChecked(self.cfg.list_mode == mode)
            a.triggered.connect(lambda _c, md=mode: self._set_list_mode(md))
        v.addSeparator()
        v.addAction("New Tab", QKeySequence("Ctrl+T"), lambda: self._new_tab())
        v.addAction("Close Tab", QKeySequence("Ctrl+W"), lambda: self._close_tab())
        v.addAction("Visualizer Tab", lambda: self._open_page("visualizer"))
        v.addAction("Now Playing Tab", lambda: self._open_page("nowplaying"))
        self.skin_menu = v.addMenu("Skin"); self._build_skin_menu()
        v.addSeparator()
        v.addAction("Mini Player", QKeySequence("Ctrl+M"), self._enter_mini)
        v.addAction("Full Screen", QKeySequence("F11"),
                    lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        tl = mb.addMenu("&Tools")
        tl.addAction("&Organise Files…", self._organize)
        tl.addAction("&Convert…", self._convert)
        tl.addAction("S&ync to Device…", self._sync_device)
        tl.addAction("Scan &ReplayGain…", self._scan_rg)
        tl.addAction("Find Cover Art &Online…", self._find_covers_online)
        tl.addAction("&Batch Tag Tools…", self._tag_tools)
        tl.addAction("&Export Library / Selection…", self._export_tracks)
        tl.addSeparator()
        tl.addAction("Library &Statistics…", self._show_stats)
        tl.addAction("Process &Inbox Now", self._process_inbox)
        tl.addAction("Find &Duplicates…", self._find_duplicates)
        tl.addAction("Find &Missing Files…", self._find_missing)
        h = mb.addMenu("&Help")
        h.addAction("Check for &Updates…", lambda: self.check_updates(manual=True))
        h.addAction("&About Hivefi…", self._about)

    def _connect_player(self):
        P = self.player
        P.stateChanged.connect(lambda _p: self.np_panel.set_context(self._np_context()))
        P.positionChanged.connect(self.lyrics_panel.set_position)
        self.lyrics_panel.seekRequested.connect(P.seek)
        self.lyrics_panel.searchOnline.connect(lambda: self._refresh_lyrics(force_online=True))
        P.streamTitle.connect(self._on_stream_title)
        P.skipped.connect(self._on_skipped)
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
                       ("history", None): item(lib, "Play History", ("history", None)),
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
        for src, parent in (("local", self._items[("local", None)]),
                            ("remote", self._items.get(("remote", None)))):
            if parent is not None:
                self._add_folder_node(parent, src, "*", "", "Folders", None)
        self._items[("queue", None)] = item(self.tree, "Now Playing", ("queue", None))
        rad = item(self.tree, "Radio", ("radio", None))
        self._items[("radio", None)] = rad
        for st in self.db.radio_stations():
            self._items[("radio", st["id"])] = item(rad, st["name"], ("radio", st["id"]))
        pod = item(self.tree, "Podcasts", ("podcast", None))
        self._items[("podcast", None)] = pod
        for p in self.db.podcasts():
            self._items[("podcast", p["id"])] = item(pod, p["title"], ("podcast", p["id"]))
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
        for top in (lib, pls, auto, rad, pod):
            top.setExpanded(True)
        if self.cfg.server_configured:
            self._items[("remote", None)].setExpanded(True)
        if sel[0] == "folder" and sel not in self._items:
            self._reveal_folder(sel[1])                          # the folder tree is built lazily: open the way to it
        self.tree.setCurrentItem(self._items.get(sel, self._items[("all", None)]))
        self.tree.blockSignals(False)
        if sel not in self._items:
            self.source = ("all", None); self.load_source()

    # ------------------------------------------------ podcasts
    def _podcast_dir(self):
        from ..config import data_dir
        return self.cfg.podcast_dir or str(data_dir() / "podcasts")

    def _add_podcast(self, _checked=False):
        url, ok = QInputDialog.getText(self, "Add Podcast", "Feed address (RSS):")
        if not ok or not url.strip():
            return
        w = self._job(lambda _p: podcasts.subscribe(self.db, url), "Add Podcast")
        def done(pid):
            self.source = ("podcast", pid); self.refresh_playlists(); self.load_source()
            self.statusBar().showMessage(f"Subscribed: {self.db.podcast(pid)['title']}", 6000)
        w.failed.connect(lambda m: self.statusBar().showMessage(f"Could not add the podcast: {m}", 8000))
        w.done.connect(done); self._run(w)

    def _refresh_podcasts(self, quiet=True):
        if not self.db.podcasts() or (getattr(self, "_pod_worker", None) and self._pod_worker.isRunning()):
            return
        folder = self._podcast_dir()
        def job(prog):
            new, errors, auto = 0, [], 0
            for p in self.db.podcasts():
                try:
                    fresh = podcasts.refresh(self.db, p["id"])
                    new += len(fresh)
                    if self.db.podcast(p["id"])["auto_download"]:
                        for e in fresh:
                            try:
                                podcasts.download(self.db, e["id"], folder, prog); auto += 1
                            except Exception as ex:  # noqa: BLE001
                                errors.append(f"{e['title']}: {ex}")
                except Exception as ex:  # noqa: BLE001
                    errors.append(f"{p['title']}: {ex}")
            return new, auto, errors
        w = self._job(job, "Podcasts")
        def done(r):
            new, auto, errors = r
            if new:
                self.statusBar().showMessage(f"{new} new podcast episode(s)" + (f", {auto} downloaded" if auto else ""), 8000)
                self.refresh_playlists()
                if self.source[0] == "podcast":
                    self.load_source(keep=True)
            if errors and not quiet:
                QMessageBox.warning(self, "Podcasts", "\n".join(errors[:8]))
        w.done.connect(done); self._pod_worker = w; self._run(w)

    def _unsubscribe(self, pid):
        p = self.db.podcast(pid)
        if p and QMessageBox.question(self, "Unsubscribe", f"Unsubscribe from {p['title']}? Downloaded files are kept.") \
                == QMessageBox.StandardButton.Yes:
            self.db.delete_podcast(pid)
            if self.source == ("podcast", pid):
                self.source = ("podcast", None)
            self.refresh_playlists(); self.load_source()

    def _download_episodes(self, tracks):
        ids = [podcasts.episode_id(t) for t in tracks if podcasts.episode_id(t) and not t.get("downloaded")]
        folder = self._podcast_dir()
        def job(prog):
            errors = []
            for i in ids:
                try:
                    podcasts.download(self.db, i, folder, prog)
                except Exception as ex:  # noqa: BLE001
                    errors.append(str(ex))
            return errors
        w = self._job(job, "Podcasts")
        def done(errors):
            self.statusBar().showMessage(f"Downloaded {len(ids) - len(errors)} episode(s)", 6000)
            if errors:
                QMessageBox.warning(self, "Podcasts", "\n".join(errors[:6]))
            self.load_source(keep=True)
        w.done.connect(done); self._run(w)

    def _delete_episode_files(self, tracks):
        for t in tracks:
            eid = podcasts.episode_id(t)
            if eid and t.get("path"):
                try:
                    os.remove(t["path"])
                except OSError:
                    pass
                self.db.set_episode_file(eid, None)
        self.load_source(keep=True)

    def _mark_episodes(self, tracks, played):
        for t in tracks:
            if podcasts.episode_id(t):
                self.db.set_episode_played(podcasts.episode_id(t), played)
        self.load_source(keep=True)

    # ------------------------------------------------ internet radio
    def _station_dialog(self, st=None):
        dlg = StationDialog(st, self)
        return dlg.values() if dlg.exec() else None

    def _add_station(self):
        v = self._station_dialog()
        if v:
            sid = self.db.add_radio(**v)
            self.source = ("radio", sid); self.refresh_playlists(); self.load_source()

    def _edit_station(self, sid):
        st = self.db.radio_station(sid)
        v = st and self._station_dialog(st)
        if v:
            self.db.update_radio(sid, **v); self.refresh_playlists(); self.load_source()

    def _delete_station(self, sid, ask=True):
        if ask and QMessageBox.question(self, "Delete station", "Delete this radio station?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_radio(sid)
        if self.source == ("radio", sid):
            self.source = ("radio", None)
        self.refresh_playlists(); self.load_source()

    def _np_context(self):
        """Things the Now Playing panel can show that come from the audio engine (the format after the DSP chain)."""
        try:
            return {"out": self.player.mp.output_caps()}
        except Exception:  # noqa: BLE001 - a missing engine detail must never break the panel
            return {}

    def _configure_now_playing(self, _checked=False):
        dlg = NowPlayingDialog(self.cfg.np_fields, self.cfg.np_labels, self.player.current, self._np_context(), self)
        if dlg.exec():
            self.cfg.np_fields = dlg.result_fields() or ["title"]
            self.cfg.np_labels = dlg.labels.isChecked()
            self.cfg.save(); self.np_panel.render()

    def show_in_library(self, genre=None, artist=None, album=None, search=None, track=None):
        """Jump to a place in the library: the Genre / Artist / Album selection, or a search, with a track highlighted."""
        self._search_timer.stop()                                # a search typed a moment ago must not undo this jump
        self._nav_remember()
        if getattr(self, "_grid_timer", None):
            self._grid_timer.stop()
        if self.source != ("all", None):
            self.source = ("all", None)
            self.tree.blockSignals(True); self.tree.setCurrentItem(self._items[("all", None)]); self.tree.blockSignals(False)
        self.search.blockSignals(True); self.search.setText(search or ""); self.search.blockSignals(False)
        self.model.text = (search or "").lower(); self.model._base = None
        self.load_source()
        if genre or artist or album:
            self._restore_browser((genre, artist, album))
        self._refresh_grid()
        if self.cfg.view_mode == "albums" and track is not None and album:
            self.grid.select_album(track.get("_ak"))             # shows exactly that album's tracks
        self._update_status()
        if track is not None:
            row = self.model.row_of(track["id"])
            if row is not None:
                self._reveal_row(row, select=True)
        return True

    def _np_navigate(self, kind, t):
        """A click in the Now Playing panel: take me to that album / artist / genre / year / composer / the song."""
        if not t or t.get("source") not in ("local", "remote"):
            return False
        aa = t.get("_aa") or t.get("album_artist") or t.get("artist")
        if kind == "album":
            return self.show_in_library(artist=aa, album=t.get("album"), track=t)
        if kind == "album_artist" or (kind == "artist" and t.get("artist") == aa):
            return self.show_in_library(artist=aa, track=t)
        if kind == "artist":                                     # a track artist that is not the album artist
            return self.show_in_library(search=f'artist:"{t["artist"]}"', track=t)
        if kind == "genre" and t.get("genre"):
            return self.show_in_library(genre=t["genre"], track=t)
        if kind == "year" and t.get("year"):
            return self.show_in_library(search=f"year:{t['year']}", track=t)
        if kind == "composer" and t.get("composer"):
            return self.show_in_library(search=f'composer:"{t["composer"]}"', track=t)
        if kind == "track":
            return self.show_in_library(track=t)
        return False

    def _on_stream_title(self, title):
        cur = self.player.current
        if cur and cur["source"] == "radio":
            self.np_panel.set_stream_title(title)
            self.setWindowTitle(f'{title} — {cur["title"]}')
            self.statusBar().showMessage(f"♪ {title}", 8000)
            self._notify_track(cur, title=title)

    # ------------------------------------------------ folder tree (Navidrome's and this computer's directories)
    def _folder_index(self, src):
        """FolderIndex of the enabled tracks of 'local' or 'remote' (rebuilt when the library changed)."""
        key = (self.db.generation, tuple(self.cfg.libraries_off), tuple(self.cfg.library_dirs))
        cache = self.__dict__.setdefault("_fi_cache", {})
        if src in cache and cache[src][0] == key:
            return cache[src][1]
        tracks = self._tracks(src)
        entries = (folders.remote_entries(tracks) if src == "remote"
                   else folders.local_entries(tracks, self.cfg.library_dirs))
        cache[src] = (key, folders.FolderIndex(entries))
        return cache[src][1]

    def _folder_root_name(self, src, root):
        if src == "remote":
            return self.cfg.server_folder_names.get(root) or "Navidrome"
        return root.replace(str(Path.home()), "~", 1)

    def _add_folder_node(self, parent, src, root, prefix, name, count):
        it = QTreeWidgetItem(parent, [name if count is None else f"{name}  ({count})"])
        arg = (src, root, prefix)
        it.setData(0, Qt.ItemDataRole.UserRole, ("folder", arg))
        self._items[("folder", arg)] = it
        ph = QTreeWidgetItem(it, ["…"]); ph.setData(0, Qt.ItemDataRole.UserRole + 1, "placeholder")
        return it

    def _fill_folder_children(self, it):
        """Create a folder node's sub-folders the first time it is opened (a library can have thousands)."""
        d = it.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "folder" or it.childCount() != 1 or \
                it.child(0).data(0, Qt.ItemDataRole.UserRole + 1) != "placeholder":
            return
        it.takeChild(0)
        src, root, prefix = d[1]
        idx = self._folder_index(src)
        if root == "*":
            roots = idx.roots()
            if len(roots) == 1:                                   # one library: show its folders directly
                r = roots[0][0]
                for name, pre, cnt in idx.children(r, ""):
                    self._add_folder_node(it, src, r, pre, name, cnt)
            else:
                for r, cnt in roots:
                    self._add_folder_node(it, src, r, "", self._folder_root_name(src, r), cnt)
        else:
            for name, pre, cnt in idx.children(root, prefix):
                self._add_folder_node(it, src, root, pre, name, cnt)

    def _reveal_folder(self, arg):
        """Expand the tree down to the folder (src, root, prefix); returns its item or None."""
        src, root, prefix = arg
        node = self._items.get(("folder", (src, "*", "")))
        if node is None:
            return None
        self._fill_folder_children(node); node.setExpanded(True)
        if root != "*":
            if ("folder", (src, root, "")) in self._items:
                node = self._items[("folder", (src, root, ""))]
                self._fill_folder_children(node); node.setExpanded(True)
            cum = ""
            for part in [p for p in prefix.split("/") if p]:
                cum = f"{cum}/{part}" if cum else part
                nxt = self._items.get(("folder", (src, root, cum)))
                if nxt is None:
                    return None
                node = nxt
                self._fill_folder_children(node); node.setExpanded(True)
        return node

    def _on_source(self):
        it = self.tree.currentItem()
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if d:
            self._nav_remember()
            self.source = tuple(d); self.load_source()

    def load_source(self, keep=False):
        """Reload the track list of the current source. keep=True preserves the Genre/Artist/Album selection."""
        kind, arg = self.source
        if kind == "all": tracks = self._tracks()
        elif kind == "remote" and arg is not None:
            tracks = [t for t in self._tracks("remote") if t.get("folder_id") == arg]
        elif kind == "podcast":
            pods = {p["id"]: p for p in self.db.podcasts()}
            tracks = [podcasts.episode_track(e, pods[e["podcast_id"]]) for e in self.db.episodes(arg)
                      if e["podcast_id"] in pods]
        elif kind == "history":
            tracks = self.cfg.enabled_tracks(self.db.play_history())
        elif kind == "radio":
            stations = self.db.radio_stations()
            tracks = [radio.radio_track(s) for s in stations if arg is None or s["id"] == arg]
        elif kind == "folder":
            src, root, prefix = arg
            tracks = self._folder_index(src).tracks(root, prefix)
        elif kind in ("local", "remote"): tracks = self._tracks(kind)
        elif kind == "queue": tracks = list(self.player.queue)
        elif kind == "smart": tracks = smart.evaluate(self._tracks(), self._smart_spec(arg))
        else: tracks = self._playlist_tracks(arg)
        m = self.model
        saved = (m.genre, m.artist, m.album) if keep else (None, None, None)
        m.genre = m.artist = m.album = m.ids = None
        hh = self.table.horizontalHeader()
        if kind in ("queue", "playlist", "smart", "folder", "radio", "history", "podcast"):   # these keep their own order
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
        self._update_tab_label()

    def _header_sort(self, section):
        if self.source[0] in ("queue", "playlist", "smart", "folder", "radio", "history", "podcast") or section in (0,):
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
            elif lv == "artist": vals = {t.get("_aa") or t["album_artist"] or t["artist"] for t in rows}
            else: vals = {t["album"] for t in rows}
            vals.discard("")
            lw.addItem(f"{ALL} ({len(vals)})")
            lw.addItems(sorted(vals, key=str.lower if lv == "genre" else sortkey.sort_name)); lw.setCurrentRow(0)
            lw.setUpdatesEnabled(True); lw.blockSignals(False)

    def _restore_browser(self, saved):
        """Select Genre / Artist / Album (any of them may be None = [All]) where they exist."""
        order = ["genre", "artist", "album"]
        for i, (lv, val) in enumerate(zip(order, saved)):
            if val:
                items = self.lists[lv].findItems(val, Qt.MatchFlag.MatchExactly)
                if items:
                    self.lists[lv].blockSignals(True); self.lists[lv].setCurrentItem(items[0]); self.lists[lv].blockSignals(False)
                    self.model.set_filters(**{lv: val})
                elif lv != "genre":
                    pass
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
        self._update_tab_label()
        self._fill_browser("genre")
        if self.cfg.view_mode == "albums":                       # thousands of album tiles: rebuild after typing settles
            if not hasattr(self, "_grid_timer"):
                self._grid_timer = QTimer(self, singleShot=True, interval=150, timeout=self._refresh_grid)
            self._grid_timer.start()
        self._update_status()

    def _update_status(self):
        """MusicBee's status line: what is listed (albums, size, days) and how long the queue still runs."""
        rows = self.model.tracks
        secs = sum(t["duration"] or 0 for t in rows)
        size = sum(t.get("size") or 0 for t in rows)
        albums = len({t.get("_ak") or (t["album_artist"], t["album"]) for t in rows})
        sz = f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.1f} MB"
        q = self.player.queue
        left = 0.0
        if q and self.player.index is not None and self.player.index >= 0:
            left = sum((q[i]["duration"] or 0) for i in self.player.upcoming() if i != self.player.index) \
                + max(0.0, ((q[self.player.index]["duration"] or 0) - self.player.mp.position() / 1000))
        text = f"{albums} albums, {sz}, {secs / 86400:.1f} days"
        self.status_lbl.setText(text + (f"  /  queued: {fmt_time(left)}" if left else ""))

    # ------------------------------------------------------- playback UI
    def _visible_tracks(self):
        return list(self.model.tracks)

    def _selected_rows(self):
        rows = set()
        for r in self.table.selectionModel().selection():
            rows.update(range(r.top(), r.bottom() + 1))
        return sorted(i for i in rows if i < len(self.model.tracks))

    def _selected(self):
        rows = set()
        for r in self.table.selectionModel().selection():       # ranges, not one QModelIndex per row
            rows.update(range(r.top(), r.bottom() + 1))
        tr = self.model.tracks
        return [tr[i] for i in sorted(rows) if i < len(tr)]

    def _on_double(self, idx):
        if COLUMNS[idx.column()][1] == "rating":
            return
        if self.source[0] == "queue":                         # the view may be searched: find the queue row
            t = self.model.tracks[idx.row()]
            qi = next((i for i, q in enumerate(self.player.queue) if q is t), None)
            if qi is not None:
                self.player.jump(qi)
            return
        self.player.play_list(self._visible_tracks(), idx.row())

    def _play_selected(self):
        idx = self.table.currentIndex()
        if idx.isValid(): self._on_double(idx)

    def _on_track(self, t):
        self.model.set_playing(t["id"] if t else None)
        self._refresh_next()
        self.np_panel.set_track(t, self._np_context())
        if not t:
            self.setWindowTitle("Hivefi"); self.now_lbl.setText(""); self.mini.art.set_art(None)
            self.seek.set_peaks(None); return
        self._refresh_lyrics()
        self._update_heart(); self._update_status()
        self._load_waveform(t)
        self.setWindowTitle(f'{t["artist"]} – {t["title"]}')
        self.now_lbl.setText(f'{t["artist"]} - {t["title"]}')
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
        self._last_cover = pm if ok else None
        if self.skin_tokens.get("glow"):
            self.update()
        np_page = self.__dict__.get("_pages", {}).get("nowplaying")
        if np_page is not None:
            np_page.set_cover(self._last_cover)

    def _on_pos(self, ms):
        if not self.seek.isSliderDown(): self.seek.setValue(ms)
        self.lbl_pos.setText(fmt_time(ms / 1000))

    def _heart_clicked(self):
        t = self.player.current
        if not t or t.get("source") not in ("local", "remote"):
            self.btn_heart.setText("♡"); return
        on = not t.get("starred")
        self.set_star([t], on); t["starred"] = int(on)
        self._update_heart()

    def _update_heart(self):
        t = self.player.current
        on = bool(t and t.get("starred"))
        self.btn_heart.setText("♥" if on else "♡")
        self.btn_heart.setStyleSheet("color: #e0202a;" if on else "")

    def _on_dur(self, ms):
        self.seek.setRange(0, ms); self.lbl_dur.setText(fmt_time(ms / 1000))

    def _seek_by(self, ms):
        """Jump forwards or backwards in the playing track (not in live streams)."""
        t = self.player.current
        if not t or t.get("source") == "radio":
            return
        dur = self.player.mp.duration() or int((t.get("duration") or 0) * 1000)
        pos = max(0, self.player.mp.position() + ms)
        self.player.seek(min(pos, max(0, dur - 500)) if dur else pos)

    def _set_volume(self, v): self.cfg.volume = v; self.player.set_volume(v)
    def _set_shuffle(self, v): self.cfg.shuffle = v; self.player.set_shuffle(v)

    def _cycle_repeat(self):
        self.player.set_repeat({"off": "all", "all": "one", "one": "off"}[self.player.repeat])
        self.cfg.repeat = self.player.repeat; self._update_repeat_label()

    def _update_repeat_label(self):
        mode = self.cfg.repeat
        self.btn_repeat.setIcon(icons.toggle_icon("repeat_one" if mode == "one" else "repeat"))
        self.btn_repeat.setChecked(mode != "off")                 # lit in the accent colour while repeating
        self.btn_repeat.setToolTip({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: one"}[mode])

    # ------------------------------------------------ plays / ratings / stars
    def _on_played(self, t):
        if t["source"] == "radio":
            return
        if t["source"] == "podcast":
            if podcasts.episode_id(t):
                self.db.set_episode_played(podcasts.episode_id(t))
                self.model.update_track(t["id"], play_count=1)
            return
        if self.cfg.listenbrainz_token and listenbrainz.queue_listen(self.db, t):
            self._lb_flush()
        if self.cfg.lastfm_connected and lastfm.queue_listen(self.db, t):
            self._lf_flush()
        self.db.bump_play(t["id"])
        fresh = self.db.track(t["id"]) or {}
        t["play_count"] = fresh.get("play_count", 0)
        self.model.update_track(t["id"], play_count=t["play_count"], last_played=fresh.get("last_played", 0))
        if t["source"] == "remote" and self.cfg.scrobble and self.api:
            self.db.queue_op("scrobble", {"id": t["remote_id"], "time_ms": int(time.time() * 1000)})
            self._kick_flush()

    def _on_skipped(self, t):
        if t.get("source") not in ("local", "remote") or not t.get("id"):
            return
        self.db.bump_skip(t["id"])
        fresh = self.db.track(t["id"]) or {}
        t["skip_count"] = fresh.get("skip_count", 0)
        self.model.update_track(t["id"], skip_count=t["skip_count"])

    def _lf_flush(self):
        c = self.cfg
        if c.lastfm_connected and not (getattr(self, "_lf_worker", None) and self._lf_worker.isRunning()):
            key, secret, sk, base = c.lastfm_key, c.lastfm_secret, c.lastfm_session, c.lastfm_url
            w = Worker(lambda _p: lastfm.flush(self.db, key, secret, sk, base), self)
            self._lf_worker = w; self._run(w)

    def _lastfm_love(self, tracks, on=True):
        c = self.cfg
        if not c.lastfm_connected:
            return
        key, secret, sk, base, tr = c.lastfm_key, c.lastfm_secret, c.lastfm_session, c.lastfm_url, [dict(t) for t in tracks]
        def job(_p):
            bad = 0
            for t in tr:
                try:
                    lastfm.love(t, key, secret, sk, on, base)
                except lastfm.LastFmError:
                    bad += 1
            return bad
        w = Worker(job, self)
        w.done.connect(lambda bad: self.statusBar().showMessage(
            ("Loved" if on else "Un-loved") + f" on Last.fm" + (f" ({bad} failed)" if bad else ""), 5000))
        self._run(w)

    def _lb_flush(self):
        tok, base = self.cfg.listenbrainz_token, self.cfg.listenbrainz_url
        if tok and not (getattr(self, "_lb_worker", None) and self._lb_worker.isRunning()):
            w = Worker(lambda _p: listenbrainz.flush(self.db, tok, base), self)
            self._lb_worker = w; self._run(w)

    def _notify_track(self, t, title=None):
        """"Now playing" bubble (off by default; optionally only while the window is not in front)."""
        c = self.cfg
        if not c.notify_track or (c.notify_only_inactive and self.isActiveWindow()):
            return
        api, tc = self.api, dict(t)
        summary = title or tc["title"]
        body = tc["title"] if title else f'{tc["artist"]}\n{tc["album"]}' if tc.get("album") else tc["artist"]
        def job(_p):
            icon = ""
            try:
                data = art.get_art(tc, api, 200)
                if data:
                    from ..config import cache_dir
                    f = cache_dir() / "notify-cover"
                    f.write_bytes(data); icon = str(f)
            except Exception:  # noqa: BLE001 - a notification without a cover is fine
                pass
            if not notify.send(summary, body, icon) and self.tray:
                self.tray.showMessage(summary, body)
        self._run(Worker(job, self))

    def _on_now_playing(self, t):
        self._notify_track(t)
        if self.cfg.lastfm_connected and t["source"] in ("local", "remote"):
            c, tc = self.cfg, dict(t)
            def lf(_p):
                try:
                    lastfm.now_playing(tc, c.lastfm_key, c.lastfm_secret, c.lastfm_session, c.lastfm_url)
                except lastfm.LastFmError:
                    pass                                         # "now playing" is best effort
            self._run(Worker(lf, self))
        if self.cfg.listenbrainz_token and t["source"] in ("local", "remote"):
            tok, base, tc = self.cfg.listenbrainz_token, self.cfg.listenbrainz_url, dict(t)
            def send(_p):
                try:
                    listenbrainz.now_playing(tok, tc, base)
                except Exception:  # noqa: BLE001 - "now playing" is best effort
                    pass
            self._run(Worker(send, self))
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
        tracks = [t for t in tracks if t["source"] in ("local", "remote")]
        if not tracks:
            return
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
        tracks = [t for t in tracks if t["source"] in ("local", "remote")]
        if not tracks:
            return
        self.db.set_starred_many([t["id"] for t in tracks], on)
        if self.cfg.lastfm_love_stars:
            self._lastfm_love(tracks, on)
        self._model_refresh(tracks, starred=int(on))
        self.db.queue_ops("star", [{"id": t["remote_id"], "on": on} for t in tracks if t["source"] == "remote"])
        self._kick_flush()

    # ------------------------------------------------------- menus
    def _track_menu(self, pos, gpos=None):
        sel = self._selected()
        if not sel: return
        gpos = gpos if gpos is not None else self.table.viewport().mapToGlobal(pos)
        m = QMenu(self)
        m.addAction("Play Now", lambda: self.player.play_list(sel, 0))
        if len(sel) > 1:
            m.addAction("Shuffle Play", lambda: self._shuffle_play(sel))
        m.addAction("Play Next", lambda: self.player.enqueue(sel, next_=True))
        m.addAction("Queue Last", lambda: self.player.enqueue(sel))
        if all(t["source"] == "podcast" for t in sel):
            m.addSeparator()
            for t in sel[:1]:
                if len(sel) == 1 and not t.get("downloaded"):
                    m.addAction("Download Episode", lambda: self._download_episodes(sel))
                elif len(sel) == 1:
                    m.addAction("Delete Downloaded File", lambda: self._delete_episode_files(sel))
            if len(sel) > 1:
                m.addAction("Download Episodes", lambda: self._download_episodes(sel))
            played = all(t.get("play_count") for t in sel)
            m.addAction("Mark as Unplayed" if played else "Mark as Played", lambda: self._mark_episodes(sel, not played))
            m.exec(gpos); return
        if all(t["source"] == "radio" for t in sel):
            m.addSeparator()
            if len(sel) == 1:
                m.addAction("Edit Station…", lambda: self._edit_station(-sel[0]["id"]))
            m.addAction("Delete Station" + ("s" if len(sel) > 1 else ""),
                        lambda: [self._delete_station(-t["id"], ask=False) for t in sel])
            m.exec(gpos); return
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
            rows = self._selected_rows()
            m.addAction("Remove from Playlist", lambda: self._remove_from_playlist(rows))
        if self.source[0] == "queue":
            m.addAction("Remove from Now Playing", lambda: self._remove_from_queue(sel))
        local = [t for t in sel if t["source"] == "local"]
        if local:
            m.addSeparator()
            m.addAction("Edit Tags…", lambda: self._edit_tags(local))
            m.addAction("Show in File Manager",
                        lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(local[0]["path"]).parent))))
            if self.cfg.lastfm_connected:
                lm = m.addMenu("Last.fm")
                lm.addAction("Love", lambda: self._lastfm_love(sel, True))
                lm.addAction("Remove Love", lambda: self._lastfm_love(sel, False))
            tm = m.addMenu("Tools")
            tm.addAction("Organise Files…", lambda: self._organize(local))
            tm.addAction("Convert…", lambda: self._convert(local))
            tm.addAction("Scan ReplayGain…", lambda: self._scan_rg(local))
            tm.addAction("Batch Tag Tools…", lambda: self._tag_tools(local))
            cm = m.addMenu("Cover Art")
            cm.addAction("Set Cover from Image…", lambda: self._set_cover(local))
            cm.addAction("Find Online…", lambda: self._find_covers_online(local, only_missing=False))
            cm.addAction("Remove Embedded Cover", lambda: self._set_cover(local, remove=True))
        m.addSeparator()
        m.addAction("Remove from Library…", lambda: self._remove_from_library(sel))
        m.exec(gpos)

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        m = QMenu(self)
        if d and d[0] in ("playlist", "smart"):
            src = tuple(d)
            m.addAction("Play", lambda: (lambda ts: ts and self.player.play_list(ts, 0))(self._source_tracks(src)))
            m.addAction("Shuffle Play", lambda: self._shuffle_play(self._source_tracks(src)))
            m.addSeparator()
        if d and d[0] == "playlist":
            pid = d[1]
            m.addAction("Rename…", lambda: self._rename_playlist(pid))
            m.addAction("Export Playlist…", lambda: self._export_m3u(pid))
            m.addAction("Delete", lambda: self._delete_playlist(pid))
            if self.api:
                pl = next(p for p in self.db.playlists() if p["id"] == pid)
                m.addAction("Stop syncing with Navidrome" if pl["sync"] else "Sync with Navidrome",
                            lambda: self._toggle_playlist_sync(pl))
        elif d and d == ("remote", None):
            m.addAction("Turn Navidrome off" if self.cfg.server_enabled else "Turn Navidrome on",
                        lambda: self.set_library_enabled(Config.NAVIDROME, not self.cfg.server_enabled))
        elif d and d[0] == "podcast":
            m.addAction("Add Podcast…", self._add_podcast)
            m.addAction("Refresh All", lambda: self._refresh_podcasts(quiet=False))
            if d[1] is not None:
                p = self.db.podcast(d[1])
                a = m.addAction("Download new episodes automatically"); a.setCheckable(True)
                a.setChecked(bool(p and p["auto_download"]))
                a.toggled.connect(lambda on: self.db.set_podcast_auto(d[1], on))
                m.addAction("Unsubscribe", lambda: self._unsubscribe(d[1]))
        elif d and d[0] == "radio":
            m.addAction("Add Station…", self._add_station)
            if d[1] is not None:
                m.addAction("Edit…", lambda: self._edit_station(d[1]))
                m.addAction("Delete", lambda: self._delete_station(d[1]))
        elif d and d[0] == "folder":
            src, root, prefix = d[1]
            tr = lambda: self._folder_index(src).tracks(root, prefix)
            m.addAction("Play Folder", lambda: tr() and self.player.play_list(tr(), 0))
            m.addAction("Shuffle Play Folder", lambda: self._shuffle_play(tr()))
            m.addAction("Queue Folder", lambda: self.player.enqueue(tr()))
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

    def _remove_from_playlist(self, rows):
        """Remove these rows of the playlist view. A track that is in the playlist twice is the same object
        twice, so each row is matched by occurrence: the 2nd copy in the view is the 2nd copy in the playlist (a
        playlist keeps its own order, and searches or disabled libraries hide every copy of a track alike)."""
        pid = self.source[1]; view = self.model.tracks
        drop = set()                                              # (track id, occurrence)
        for r in rows:
            if 0 <= r < len(view):
                tid = view[r]["id"]
                drop.add((tid, sum(1 for t in view[:r] if t["id"] == tid)))
        seen, keep = {}, []
        for t in self.db.playlist_tracks(pid):
            k = seen.get(t["id"], 0); seen[t["id"]] = k + 1
            if (t["id"], k) not in drop:
                keep.append(t["id"])
        self.db.set_playlist_tracks(pid, keep)
        sb = self.table.verticalScrollBar(); y = sb.value()
        self.load_source(keep=True); sb.setValue(y); self._sync_if_dirty()

    def _select_all(self):
        (self.album_view if self.cfg.list_mode == "albums" else self.table).setFocus()
        self.table.selectAll()

    def _source_tracks(self, src):
        """The tracks of a playlist or auto playlist, in its order (without changing the view)."""
        kind, arg = src
        if kind == "smart":
            return smart.evaluate(self._tracks(), self._smart_spec(arg))
        if kind == "playlist":
            return self._playlist_tracks(arg)
        return []

    def _shuffle_play(self, tracks):
        """Play these tracks in a random order (whatever the Shuffle setting is)."""
        tracks = [t for t in tracks if t.get("source") != "radio"] if tracks else []
        if tracks:
            self.player.play_list(random.sample(tracks, len(tracks)), 0)

    def _delete_key(self):
        """Delete in the track list does what it means where you are: leave the playlist, leave Now Playing,
        or (in the library) remove from the library after asking. Nothing in history, radio or podcasts."""
        kind = self.source[0]
        if kind == "playlist":
            rows = self._selected_rows()
            if rows:
                self._remove_from_playlist(rows)
        elif kind == "queue":
            self._remove_from_queue(self._selected())
        elif kind in ("history", "radio", "podcast"):
            return
        else:
            self._remove_from_library()

    def _remove_from_library(self, tracks=None):
        """Remove tracks from the library after asking. Files are never deleted; what is still in a music folder
        or on the server comes back at the next rescan or sync."""
        if not isinstance(tracks, list):
            tracks = self._selected()
        tracks = [t for t in tracks if t.get("source") in ("local", "remote")]
        playing = (self.player.current or {}).get("id")
        ids = [t["id"] for t in tracks if t["id"] != playing]
        if not ids:
            if tracks:
                self.statusBar().showMessage("The playing track cannot be removed from the library", 5000)
            return
        note = (f"Remove {len(ids)} track{'s' if len(ids) != 1 else ''} from the library?\n\n"
                "The files are not deleted. Tracks still in your music folders come back at the next rescan, "
                "Navidrome tracks at the next sync.")
        if playing in {t["id"] for t in tracks}:
            note += "\n\nThe playing track is kept."
        if QMessageBox.question(self, "Remove from Library", note) != QMessageBox.StandardButton.Yes:
            return
        gone = set(ids)
        self.player.remove([i for i, q in enumerate(self.player.queue) if q.get("id") in gone])
        self.db.delete_tracks(ids)
        sb = self.table.verticalScrollBar(); y = sb.value()
        self.refresh_playlists(); self.load_source(keep=True); sb.setValue(y)
        self.statusBar().showMessage(f"Removed {len(ids)} track(s) from the library", 5000)

    def _set_list_mode(self, mode, sort=True):
        """Show the track list as Details (columns) or Album and Tracks (grouped by album with the cover)."""
        mode = mode if mode in ("details", "albums") else "details"
        self.cfg.list_mode = mode
        (self.act_list_albums if mode == "albums" else self.act_list_details).setChecked(True)
        self.act_list_toggle.setChecked(mode == "albums")
        self.list_stack.setCurrentWidget(self.album_view if mode == "albums" else self.table)
        hh = self.table.horizontalHeader()
        sortable = sort and self.source[0] not in self._OWN_ORDER
        if mode == "albums" and sortable and COLUMNS[hh.sortIndicatorSection()][1] not in ("album", "album_artist"):
            self._details_sort = (hh.sortIndicatorSection(), hh.sortIndicatorOrder())   # given back on the way out
            sec = next(i for i, c in enumerate(COLUMNS) if c[1] == "album_artist")   # albums together, in order
            hh.setSortIndicator(sec, Qt.SortOrder.AscendingOrder)
            self.model.sort_by("album_artist", False)
        elif mode == "details" and sortable and getattr(self, "_details_sort", None):
            sec, order = self._details_sort
            hh.setSortIndicator(sec, order)
            self.model.sort_by(COLUMNS[sec][1], order == Qt.SortOrder.DescendingOrder)
        if mode == "details":
            self._details_sort = None
        cur = self.table.currentIndex()
        if cur.isValid():
            self._reveal_row(cur.row())

    _OWN_ORDER = ("queue", "playlist", "smart", "folder", "radio", "history", "podcast")

    def _about(self):
        from .. import __version__, updater
        try:
            commit = (updater.local_version() or "")[:7]
        except Exception:  # noqa: BLE001
            commit = ""
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        engine = "GStreamer" if self.player.mp.engine == "gst" else "Qt Multimedia"
        QMessageBox.about(self, "About Hivefi",
                          f"<h3>Hivefi {__version__}</h3>"
                          + (f"<p>Build {commit}</p>" if commit else "")
                          + "<p>A music manager and player for Linux with Navidrome / Subsonic sync.</p>"
                          f"<p>Audio engine: {engine}<br>Qt {QT_VERSION_STR} · PyQt {PYQT_VERSION_STR} · "
                          f"Python {sys.version.split()[0]}</p>"
                          f"<p>Library: {len(self.db.all_tracks())} tracks<br>Data folder: {data_dir()}</p>")

    def _remove_from_queue(self, tracks):
        """Remove these rows of the Now Playing view from the queue (found by identity: a track queued twice is
        only removed where it was selected; the playing track stays)."""
        ids = {id(t) for t in tracks}
        self.player.remove([i for i, q in enumerate(self.player.queue) if id(q) in ids])

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

    # ------------------------------------------------ folder monitoring and the inbox
    def _setup_watch(self):
        """(Re)start watching the library folders (and the inbox) according to the preferences."""
        w = getattr(self, "_watcher", None)
        if w is None:
            w = self._watcher = FolderWatcher(self)
            w.changed.connect(self._folders_changed)
        c = self.cfg
        roots = [d for d in c.library_dirs if c.lib_enabled(d) and os.path.isdir(d)] if c.watch_folders else []
        if c.inbox_dir and os.path.isdir(c.inbox_dir):
            roots.append(c.inbox_dir)
        if not roots:
            w.stop(); return 0
        n = w.watch(roots)
        if w.overflow:
            self.statusBar().showMessage("Too many folders to watch them all; use Rescan Library for the rest", 8000)
        return n

    def _folders_changed(self):
        if self.cfg.inbox_dir:
            self._process_inbox(quiet=True)                      # rescans by itself when it moved something
        if self.cfg.watch_folders:
            self.scan_library(quiet=True)                        # files added straight into a library folder

    def _inbox_target(self):
        c = self.cfg
        if c.inbox_target and os.path.isdir(c.inbox_target):
            return c.inbox_target
        return next((d for d in c.library_dirs if c.lib_enabled(d) and os.path.isdir(d)), "")

    def _process_inbox(self, _checked=False, quiet=False, min_age=5.0):
        c, target = self.cfg, self._inbox_target()
        if not c.inbox_dir or not target:
            if not quiet:
                QMessageBox.information(self, "Inbox", "Choose an inbox folder and a library folder in Preferences → Library.")
            return
        if getattr(self, "_inbox_worker", None) and self._inbox_worker.isRunning():
            return
        inbox, pattern = c.inbox_dir, c.inbox_pattern
        w = self._job(lambda _p: inbox_mod.process(inbox, target, pattern, min_age), "Inbox")
        def done(res):
            moved, left, errors = res
            if moved:
                self.statusBar().showMessage(f"Inbox: {len(moved)} file(s) moved into the library", 8000)
                self.scan_library(quiet=True)
            if errors and not quiet:
                QMessageBox.warning(self, "Inbox", "\n".join(errors[:10]))
            young = [p for p in left if os.path.exists(p)]
            if young and quiet:                                 # still being copied: look again shortly
                QTimer.singleShot(8000, lambda: self._process_inbox(quiet=True))
        w.done.connect(done)
        self._inbox_worker = w
        self._run(w)

    def _sync_device(self, _checked=False):
        sources = []
        sel = self._selected()
        if sel:
            sources.append(("Selected tracks", sel, None))
        sources.append(("The tracks shown now", self._visible_tracks(), None))
        sources.append(("All music", self._tracks(), None))
        for p in self.db.playlists():
            tr = self.cfg.enabled_tracks(self.db.playlist_tracks(p["id"]))
            if tr:
                sources.append((f"Playlist: {p['name']}", tr, p["name"]))
        dlg = DeviceSyncDialog(sources, self, self.api)
        if not dlg.exec():
            return
        o, tracks, name, api = dlg.options(), dlg.tracks(), dlg.playlist_name(), self.api
        pl = devicesync.plan(tracks, o)
        if o.delete_extra and pl.extra and QMessageBox.question(
                self, "Sync to Device", f"{len(pl.extra)} music file(s) on the device are not part of this sync and "
                "will be deleted. Continue?") != QMessageBox.StandardButton.Yes:
            return
        w = self._job(lambda prog: devicesync.execute(pl, o, api, prog, lambda: self._closing,
                                                      {name: tracks} if name else None), "Sync to Device")
        def done(st):
            msg = (f"Synced: {st['copied']} copied, {st['converted']} converted, {st['downloaded']} downloaded, "
                   f"{st['skipped']} up to date, {st['deleted']} deleted")
            self.statusBar().showMessage(msg, 12000)
            if st["errors"]:
                QMessageBox.warning(self, "Sync to Device", "\n".join(st["errors"][:12]))
        w.done.connect(done)
        self._run(w)

    def _tag_tools(self, tracks=None):
        tr = self._tool_tracks(tracks)
        dlg = TagToolsDialog(tr, self)
        if not dlg.exec():
            return
        ch = dlg.changes()
        if not ch:
            return
        w = self._job(lambda prog: tagtools.apply_changes(self.db, ch, prog), "Batch Tag Tools")
        w.done.connect(lambda r: (self._report("Batch Tag Tools", r[0], r[1], "Retagged"), self.load_source(keep=True)))
        self._run(w)

    def _export_tracks(self, _checked=False):
        tr = self._selected() or self._visible_tracks()
        path, _ = QFileDialog.getSaveFileName(self, "Export tracks", "hivefi-library.csv",
                                              "Spreadsheet (*.csv);;JSON (*.json);;Playlist (*.m3u8)")
        if not path:
            return
        w = self._job(lambda _p: libexport.export_any(tr, path), "Export")
        w.done.connect(lambda n: self.statusBar().showMessage(f"Exported {n} track(s) to {path}", 8000))
        self._run(w)

    def _find_covers_online(self, tracks=None, only_missing=True):
        tr = self._tool_tracks(tracks)
        if QMessageBox.question(self, "Find cover art online",
                                f"Look up the covers of {len({t.get('_ak') for t in tr})} album(s) on MusicBrainz and the "
                                "Cover Art Archive? Their names are sent to those services (about one request per second) "
                                "and found covers are embedded in the files."
                                + (" Albums that already have a cover are skipped." if only_missing else "")) \
                != QMessageBox.StandardButton.Yes:
            return
        w = self._job(lambda prog: coverart.find_covers(tr, only_missing, prog), "Cover art")
        def done(r):
            n, missing, errors = r
            self.statusBar().showMessage(f"Cover art: {n} album(s) updated, {len(missing)} not found", 10000)
            if errors:
                QMessageBox.warning(self, "Cover art", "\n".join(errors[:12]))
            cur = self.player.current
            if cur:
                self._on_track(cur)
            self._refresh_grid()
        w.done.connect(done)
        self._run(w)

    def _set_cover(self, tracks, remove=False):
        from ..tagedit import remove_cover, write_cover
        data = b""
        if not remove:
            path, _ = QFileDialog.getOpenFileName(self, "Choose cover image", "", "Images (*.jpg *.jpeg *.png)")
            if not path:
                return
            data = Path(path).read_bytes()
        def job(_p):
            bad = []
            for t in tracks:
                try:
                    (remove_cover(t["path"]) if remove else write_cover(t["path"], data))
                    self.db.conn.execute("UPDATE tracks SET mtime=? WHERE id=?", (Path(t["path"]).stat().st_mtime, t["id"]))
                    art.invalidate(t)
                except Exception as e:  # noqa: BLE001
                    bad.append(f'{Path(t["path"]).name}: {e}')
            self.db.commit()
            return bad
        w = self._job(job, "Cover Art")
        def done(bad):
            self._report("Cover Art", len(tracks) - len(bad), bad, "Updated cover of")
            cur = self.player.current
            if cur:
                self._on_track(cur)                              # redraw the now-playing art
            self._refresh_grid()
        w.done.connect(done)
        self._run(w)

    def _show_stats(self):
        s = libstats.compute(self._tracks(), self.db.plays_since(0))
        StatsDialog(libstats.render_html(s), self).exec()

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
            self.next_panel.refresh(self.player.queue, self.player.index, self.player.upcoming())

    def _on_queue_changed(self):
        self._refresh_next(); self._update_status()
        if self.source[0] == "queue":
            sb = self.table.verticalScrollBar(); y = sb.value()
            self.load_source(keep=True); sb.setValue(y)

    def _sel_changed(self):
        if self.dock_info.isVisible():
            sel = self._selected()
            self.info_panel.set_tracks(sel or ([self.player.current] if self.player.current else []))

    def _set_view_mode(self, mode, initial=False):
        mode = mode if mode in ("browser", "albums") else "browser"
        self.cfg.view_mode = mode
        (self.act_albums if mode == "albums" else self.act_browser).setChecked(True)
        self.top_stack.setCurrentIndex(1 if mode == "albums" else 0)
        if not initial and self.dock_browser.isVisible() and self.dock_tracks.isVisible() \
                and not self.dock_browser.isFloating() and not self.dock_tracks.isFloating() \
                and self.dockWidgetArea(self.dock_browser) == self.dockWidgetArea(self.dock_tracks):
            total = (self.dock_browser.height() + self.dock_tracks.height()) or 780
            top = int(total * (0.5 if mode == "albums" else 0.25))
            self.resizeDocks([self.dock_browser, self.dock_tracks], [top, total - top], Qt.Orientation.Vertical)
        if not initial:
            self.model.set_filters(genre=None, artist=None, album=None)
            self._fill_browser("genre"); self._refresh_grid(); self._update_status()
        else:
            self._refresh_grid()

    def _refresh_grid(self, artists=True):
        if artists:
            self._fill_artists()
        if self.cfg.view_mode != "albums": return
        self.grid.set_tracks(self.model.narrowed("album"))

    def _fill_artists(self):
        if self.dock_artists.isVisible():
            self.artist_list.set_tracks(self.model.narrowed("artist"), self.model.artist)

    def _on_artist_pick(self, name):
        """An artist was picked in the Album Artist panel (None = All Artists)."""
        self._nav_remember()
        if self.cfg.view_mode == "albums":
            self.model.set_filters(artist=name, album=None, ids=None)
            self._refresh_grid(artists=False); self._update_status()
        else:                                                    # Columns: drive the Artist column of the browser
            lw = self.lists["artist"]
            items = lw.findItems(name, Qt.MatchFlag.MatchExactly) if name else []
            lw.setCurrentItem(items[0]) if items else lw.setCurrentRow(0)

    def _on_grid_album(self, tracks):
        """An album was picked in the grid: show exactly its tracks (None = back to all of the artist's)."""
        self.model.set_filters(album=None, ids={t["id"] for t in tracks} if tracks else None)
        self._update_status()

    def _grid_menu(self, tracks, gpos):
        m = QMenu(self)
        m.addAction("Play Album", lambda: self.player.play_list(tracks, 0))
        m.addAction("Shuffle Play", lambda: self._shuffle_play(tracks))
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
        self._rate_row(idx.row(), star)

    def _rate_row(self, row, star):
        """A click on the stars of a row (clicking the current rating again clears it)."""
        if 0 <= row < len(self.model.tracks):
            t = self.model.tracks[row]
            self.set_rating([t], 0 if star == t["rating"] else star)

    def _build_skin_menu(self):
        self.skin_menu.clear(); grp = QActionGroup(self)
        for cat, names in skins.grouped().items():               # Flat / Neomorphic / Classic / Modern / ...
            sub = self.skin_menu.addMenu(cat)
            for name in names:
                a = sub.addAction(name); a.setCheckable(True); a.setChecked(name == self.cfg.skin)
                grp.addAction(a); a.triggered.connect(lambda _c, n=name: self.set_skin(n))
        self.skin_menu.addSeparator()
        self.skin_menu.addAction("Import Skin…", self._import_skin)
        self.skin_menu.addAction("Reload Skins", self._build_skin_menu)
        self.skin_menu.addAction("Open Skins Folder…",
                                 lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(skins.skins_dir()))))

    def current_shortcuts(self) -> dict:
        """{command: shortcut now in effect}"""
        return {k: shortcuts.portable(a.shortcut()) for k, a in self.shortcut_actions.items()}

    def apply_shortcuts(self, overrides=None):
        """Apply the user's shortcut changes (cfg.shortcuts, or `overrides` which also replaces them)."""
        if overrides is not None:
            self.cfg.shortcuts = {k: v for k, v in overrides.items() if self.shortcut_defaults.get(k) != v}
        for key, a in self.shortcut_actions.items():
            seq = self.cfg.shortcuts.get(key, self.shortcut_defaults[key])
            a.setShortcut(QKeySequence(seq) if seq else QKeySequence())

    def _fill_fullscreen_menu(self, menu):
        """Effect choice, automatic cycling and quality of the full-screen visualizer."""
        from .. import vizfx
        menu.clear(); c = self.cfg
        g = QActionGroup(menu)
        a = menu.addAction("Auto (cycle through all)"); a.setCheckable(True); a.setChecked(c.vis_full_effect == "auto")
        g.addAction(a); a.triggered.connect(lambda: self._set_full("vis_full_effect", "auto"))
        menu.addSeparator()
        for e in vizfx.effect_classes():                        # ◈ = MilkDrop-style feedback, ▮ = classic
            act = menu.addAction(("◈ " if e.family == "milkdrop" else "▮ ") + e.name)
            act.setCheckable(True); act.setChecked(c.vis_full_effect == e.name); g.addAction(act)
            act.triggered.connect(lambda _c, n=e.name: self._set_full("vis_full_effect", n))
        menu.addSeparator()
        sm = menu.addMenu("Change effect every"); sg = QActionGroup(sm)
        for secs in (0, 15, 30, 60, 120):
            act = sm.addAction("never" if secs == 0 else f"{secs} seconds"); act.setCheckable(True)
            act.setChecked(c.vis_full_cycle == secs); sg.addAction(act)
            act.triggered.connect(lambda _c, s=secs: self._set_full("vis_full_cycle", s))
        qm = menu.addMenu("Quality"); qg = QActionGroup(qm)
        for q in ("low", "medium", "high"):
            act = qm.addAction(q.capitalize()); act.setCheckable(True); act.setChecked(c.vis_full_quality == q)
            qg.addAction(act); act.triggered.connect(lambda _c, q=q: self._set_full("vis_full_quality", q))

    def _set_full(self, key, value):
        setattr(self.cfg, key, value)
        fs = getattr(self, "fs_vis", None)
        if fs is not None:
            if key == "vis_full_effect":
                (fs.set_auto(True) if value == "auto" else fs.select(value, remember=False))
            elif key == "vis_full_cycle":
                fs.cycle = value

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
        if self.edit_mode:
            self._style_edit_mode(True)                          # the dashed frame follows the skin's accent
        self._update_vol_icon()                                  # a QLabel: repaint in the new skin's colours
        self.model.playing_color = self.skin_tokens["accent"]
        self.model.set_playing(self.model.playing_id)
        flat = self.skin_tokens.get("style") == "musicbee"
        self.grid.set_flat(flat)
        self.grid.rounded = bool(self.skin_tokens.get("glow"))
        self.album_view.see_through = self.grid.rounded; self.album_view.viewport().update()
        self.update()
        self.grid.set_placeholder_color(QColor(self.skin_tokens["button"]))
        self.btn_edit.setVisible(not flat)                       # MusicBee has no such button: it is in View → Panels
        self._refresh_grid()

    # ------------------------------------------------ tray / mini player / sleep
    def _build_tray(self):
        self.tray = None
        if not (self.cfg.show_tray and QSystemTrayIcon.isSystemTrayAvailable()):
            return
        icon = icons.app_icon()
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
                dbv = dict(vals)
                if "album_artist" in vals:
                    dbv["aa_tag"] = vals["album_artist"]             # an edited album artist is an explicit tag now
                sets = ", ".join(f"{k}=?" for k in dbv) + ", mtime=?"
                self.db.conn.execute(f"UPDATE tracks SET {sets} WHERE id=?", (*dbv.values(), mtime, t["id"]))
                self.model.update_track(t["id"], **vals)
            except Exception as e:  # noqa: BLE001
                errors.append(f'{Path(t["path"]).name}: {e}')
        self.db.commit()
        if errors:
            QMessageBox.warning(self, "Some tags could not be saved", "\n".join(errors[:10]))
        self.load_source(keep=True)                              # the commit dropped the cached tracks: album grouping may change

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
        path, _ = QFileDialog.getSaveFileName(self, "Export playlist", pl["name"] + ".m3u8",
                                              "M3U playlist (*.m3u8);;PLS playlist (*.pls);;XSPF playlist (*.xspf)")
        if not path: return
        n = libexport.export_any(self.db.playlist_tracks(pid), path)
        self.statusBar().showMessage(f"Exported {n} local track(s) (server tracks have no file path)", 6000)

    def show_lyrics(self):
        self.dock_lyrics.show(); self.dock_lyrics.raise_()
        self._refresh_lyrics()

    def _refresh_lyrics(self, force_online=False):
        if not self.dock_lyrics.isVisible(): return
        t = self.player.current
        if not t:
            self.lyrics_panel.show_lyrics("", ""); return
        tc, api = dict(t), self.api
        title = f'{t["artist"]} – {t["title"]}'
        self.lyrics_panel.show_lyrics(title, "Loading…")
        online = force_online or self.cfg.lyrics_online
        save = self.cfg.lyrics_save_sidecar
        w = Worker(lambda _p: lyrics.find_lyrics(tc, api, online=online, save_sidecar=save), self)
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
        self._refresh_podcasts(quiet=True)
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

    # ------------------------------------------------ own title bar
    _EDGE = 6

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._apply_titlebar)               # only now is it known whether the menu bar is in the window

    def _menu_in_window(self):
        """False with the menu in its own window or taken by the desktop (global menu in the panel, as on KDE or
        macOS): the menu bar's corner is then not on screen and the window buttons go to the top toolbar row."""
        mb = self.menuBar()
        if self.cfg.menu_separate or mb.isHidden():
            return False
        return not self.isVisible() or (mb.isVisible() and mb.height() > 4)

    def _place_window_toolbar(self):
        """Keep the window-button toolbar at the right end of the top row (also after a layout was restored)."""
        tb = getattr(self, "tb_window", None)
        if tb is not None and not tb.isHidden():
            ed = self.edit_toolbar                                 # on a row of its own below: go in front of its break
            self.removeToolBar(tb)
            self.insertToolBar(ed, tb); self.removeToolBarBreak(tb); self.insertToolBarBreak(ed)
            tb.show()

    def _apply_titlebar(self):
        """Frameless window with its own minimise / maximise / close: in the menu bar's corner, or at the right of
        the top toolbar row when the menu bar is not in the window."""
        own = bool(self.cfg.own_titlebar)
        in_menu = own and self._menu_in_window()
        tb = getattr(self, "tb_window", None)
        if own and not in_menu and tb is None:
            tb = self.tb_window = QToolBar("Window Buttons", self); tb.setObjectName("tb_window")
            tb.setMovable(False); tb.setFloatable(False); tb.toggleViewAction().setVisible(False)
            tb.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
            gap = QWidget(); gap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            gap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # clicks reach the toolbar: drag to move
            tb.addWidget(gap); tb.addWidget(WindowButtons(self, with_title=False))
        if tb is not None:
            tb.setVisible(own and not in_menu)
            self._place_window_toolbar()
        own_filter = own and not getattr(self, "_title_filter", False)
        if own_filter:
            self._title_filter = True
            self.menuBar().installEventFilter(self); QApplication.instance().installEventFilter(self)
        own = in_menu                                              # below: the menu bar's corner
        frameless = bool(self.cfg.own_titlebar)
        was = bool(self.windowFlags() & Qt.WindowType.FramelessWindowHint)
        if frameless != was:
            vis = self.isVisible()
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, frameless)
            if vis:
                self.show()
        mb = self.menuBar()
        if own:
            if getattr(self, "win_buttons", None) is None:
                self.win_buttons = WindowButtons(self)
            mb.setCornerWidget(self.win_buttons, Qt.Corner.TopRightCorner); self.win_buttons.show()
        elif getattr(self, "win_buttons", None) is not None:
            mb.setCornerWidget(None, Qt.Corner.TopRightCorner); self.win_buttons.hide()

    def _edges_at(self, gp):
        if not (self.windowFlags() & Qt.WindowType.FramelessWindowHint) or self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        r, m = self.frameGeometry(), self._EDGE
        e = Qt.Edge(0)
        if abs(gp.x() - r.left()) <= m: e |= Qt.Edge.LeftEdge
        if abs(gp.x() - r.right()) <= m: e |= Qt.Edge.RightEdge
        if abs(gp.y() - r.top()) <= m: e |= Qt.Edge.TopEdge
        if abs(gp.y() - r.bottom()) <= m: e |= Qt.Edge.BottomEdge
        return e

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.Leave and obj is self and getattr(self, "_edge_cursor", False):
            QApplication.restoreOverrideCursor(); self._edge_cursor = False      # never leave a resize cursor behind
        if getattr(self, "_title_filter", False) and self.windowFlags() & Qt.WindowType.FramelessWindowHint:
            # drag the window by the empty part of the menu bar or of a top toolbar; double-click maximises
            bar = (obj is self.menuBar() and self.menuBar().actionAt(e.position().toPoint()) is None) \
                if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) else False
            if not bar and t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) \
                    and isinstance(obj, QToolBar) and obj.window() is self and not obj.isFloating() and not self.edit_mode \
                    and self.toolBarArea(obj) == Qt.ToolBarArea.TopToolBarArea \
                    and not self._edges_at(e.globalPosition().toPoint()):
                bar = True
            if bar and e.button() == Qt.MouseButton.LeftButton:
                if t == QEvent.Type.MouseButtonDblClick:
                    self.showNormal() if self.isMaximized() else self.showMaximized()
                elif self.windowHandle():
                    self.windowHandle().startSystemMove()
                return True
            if t in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress) and isinstance(obj, QWidget) \
                    and obj.window() is self and not QApplication.mouseButtons() & ~Qt.MouseButton.LeftButton:
                edges = self._edges_at(e.globalPosition().toPoint())
                if t == QEvent.Type.MouseMove:
                    cur = {Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                           Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor}
                    if edges and not getattr(self, "_edge_cursor", False):
                        corner = bool((edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge)) and (edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge)))
                        main_diag = edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
                        shape = (Qt.CursorShape.SizeFDiagCursor if main_diag else Qt.CursorShape.SizeBDiagCursor) if corner \
                            else cur.get(edges, Qt.CursorShape.SizeAllCursor)
                        QApplication.setOverrideCursor(shape); self._edge_cursor = True
                    elif not edges and getattr(self, "_edge_cursor", False):
                        QApplication.restoreOverrideCursor(); self._edge_cursor = False
                elif edges and e.button() == Qt.MouseButton.LeftButton and self.windowHandle():
                    self.windowHandle().startSystemResize(edges)
                    return True
        return super().eventFilter(obj, e)

    def _apply_menu_mode(self):
        """Menu bar in the main window (default) or in a separate window."""
        mb = self.menuBar()
        sep = self.cfg.menu_separate
        win = getattr(self, "menu_win", None)
        ctx = Qt.ShortcutContext.ApplicationShortcut if sep else Qt.ShortcutContext.WindowShortcut
        for a in self.shortcut_actions.values():                 # hotkeys keep working while the menu window has focus
            a.setShortcutContext(ctx)
        if sep:
            if win is None:
                win = self.menu_win = MenuWindow(mb.actions())
                win.closed.connect(self._menu_window_closed)
            mb.setVisible(False); win.show()
        else:
            if win is not None:
                win.hide()
            mb.setVisible(True)

    def _menu_window_closed(self):
        if not self._closing and not self._quitting:             # closing it puts the bar back in the main window
            self.cfg.menu_separate = False; self.cfg.save(); self._apply_menu_mode()

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            dlg.apply(); self._rebuild_api(); self._apply_sync_timer()
            self.player.configure(); self._update_vis_availability(); self._setup_watch(); self._apply_menu_mode(); self._apply_titlebar()
            sortkey.set_articles(self.cfg.ignore_articles); self.load_source(keep=True)
            self._after_library_change()
            self.scan_library(quiet=True)
            if self.api: self.sync_now(silent=True)

    def closeEvent(self, e):
        if self.cfg.close_to_tray and self.tray and self.tray.isVisible() and not self._quitting:
            self.hide(); e.ignore()
            if getattr(self, "menu_win", None): self.menu_win.hide()
            return
        self._closing = True                                     # lets a running scan stop early
        if getattr(self, "menu_win", None): self.menu_win.close()
        self._save_session()
        if self.tray: self.tray.hide()
        self.mini.hide()
        self.player.mp.shutdown()
        self.player.stop()
        for w in list(self._workers): w.wait(2000)
        super().closeEvent(e)


def install_crash_log(path=None):
    """Write every unhandled error to crash.log in the data folder instead of losing it. An error in a Qt slot no
    longer takes the whole program down (PyQt aborts only while the default excepthook is in place); real crashes
    in native code (GStreamer, Qt) still end it, but faulthandler writes the Python stack of every thread first."""
    import faulthandler, threading, traceback
    path = Path(path) if path else data_dir() / "crash.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2_000_000:          # keep it small: start over
            path.unlink()
        fh = open(path, "a", buffering=1, encoding="utf-8")
    except OSError:
        return None
    fh.write(f"\n=== Hivefi started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    faulthandler.enable(fh, all_threads=True)

    def report(kind, etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        fh.write(f"--- {kind} {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}")
        sys.__stderr__ and sys.__stderr__.write(text)
        app = QApplication.instance()
        win = next((w for w in (app.topLevelWidgets() if app else []) if isinstance(w, MainWindow)), None)
        if win is not None and threading.current_thread() is threading.main_thread():
            try:
                win.statusBar().showMessage(f"Internal error: {value!r} (details in {path})", 15000)
            except Exception:  # noqa: BLE001
                pass

    sys.excepthook = lambda et, v, tb: report("error", et, v, tb)
    threading.excepthook = lambda a: report(f"error in thread {a.thread.name if a.thread else '?'}",
                                            a.exc_type, a.exc_value, a.exc_traceback)
    return path


def main():
    app = QApplication(sys.argv)
    install_crash_log()
    app.setApplicationName("hivefi"); app.setDesktopFileName("hivefi")
    app.setWindowIcon(icons.app_icon())
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
