"""Cover-art grid of albums with lazy, threaded cover loading."""
from PyQt6.QtCore import QEvent, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QToolTip

from .. import art

ICON = 150
KEY = Qt.ItemDataRole.UserRole


class _Signals(QObject):
    loaded = pyqtSignal(int, str, bytes)


class _Job(QRunnable):
    def __init__(self, gen, key, track, api, sigs):
        super().__init__(); self.gen, self.key, self.track, self.api, self.sigs = gen, key, track, api, sigs

    def run(self):
        try:
            data = art.get_art(self.track, self.api, ICON * 2) or b""
        except Exception:  # noqa: BLE001
            data = b""
        self.sigs.loaded.emit(self.gen, self.key, data)


def _placeholder(color: QColor):
    pm = QPixmap(ICON, ICON); pm.fill(color)
    p = QPainter(pm); p.setPen(QColor(255, 255, 255, 70)); f = p.font(); f.setPointSize(40); p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "♪"); p.end()
    return QIcon(pm)


class AlbumGrid(QListWidget):
    albumSelected = pyqtSignal(object, object)       # (artist, album) or (None, None)
    playRequested = pyqtSignal(list)
    contextRequested = pyqtSignal(list, object)      # tracks, global pos

    def __init__(self, api_getter):
        super().__init__()
        self.setObjectName("albumgrid")
        self.api_getter = api_getter
        self.albums: dict[str, list] = {}
        self._gen = 0
        self._requested: set[str] = set()
        self._items: dict[str, QListWidgetItem] = {}
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(ICON, ICON)); self.setGridSize(QSize(ICON + 30, ICON + 52))
        self.setWordWrap(True); self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemSelectionChanged.connect(self._sel)
        self.itemDoubleClicked.connect(lambda it: self.playRequested.emit(self.album_tracks(it)))
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(3)
        self.sigs = _Signals(); self.sigs.loaded.connect(self._on_loaded)
        self._timer = QTimer(self, singleShot=True, interval=80, timeout=self._request_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._timer.start())
        self.placeholder = _placeholder(QColor(60, 60, 66))

    def set_placeholder_color(self, c: QColor):
        self.placeholder = _placeholder(c)

    def set_tracks(self, tracks):
        self._gen += 1; self._requested.clear()
        groups: dict[tuple, list] = {}
        for t in tracks:
            groups.setdefault(((t["album_artist"] or t["artist"]), t["album"]), []).append(t)
        self.blockSignals(True); self.clear(); self.albums = {}; self._items = {}
        for (artist, album), ts in sorted(groups.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1].lower())):
            key = f"{artist}\x00{album}"
            self.albums[key] = ts                      # track order is sorted on demand (album_tracks)
            it = QListWidgetItem(self.placeholder, f"{album}\n{artist}")
            it.setData(KEY, key)
            self.addItem(it); self._items[key] = it
        self.blockSignals(False)
        self._timer.start()

    def album_tracks(self, it):
        ts = self.albums.get(it.data(KEY), [])
        return sorted(ts, key=lambda t: (t["disc_no"] or 1, t["track_no"] or 0, t["title"].lower()))

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:               # built when shown, not for thousands of items up front
            it = self.itemAt(e.pos())
            ts = self.albums.get(it.data(KEY)) if it else None
            if ts:
                QToolTip.showText(e.globalPos(), f"{ts[0]['album']} — {ts[0]['album_artist'] or ts[0]['artist']}\n"
                                                 f"{len(ts)} tracks", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def selected_album(self):
        it = self.currentItem()
        return tuple(it.data(KEY).split("\x00")) if it and it.isSelected() else (None, None)

    def _sel(self):
        self.albumSelected.emit(*self.selected_album())

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it:
            self.setCurrentItem(it)
            self.contextRequested.emit(self.album_tracks(it), self.viewport().mapToGlobal(pos))

    def resizeEvent(self, e):
        super().resizeEvent(e); self._timer.start()

    def showEvent(self, e):
        super().showEvent(e); self._timer.start()

    def _request_visible(self):
        if not self.isVisible():
            return
        # items flow left-to-right in fixed cells: work out which rows are on screen instead of asking Qt about
        # every one of thousands of items
        gw, gh = self.gridSize().width(), self.gridSize().height()
        vp = self.viewport().rect()
        cols = max(1, vp.width() // gw)
        top = self.verticalScrollBar().value()
        first = max(0, (top - ICON) // gh) * cols
        last = min(self.count(), ((top + vp.height() + ICON) // gh + 1) * cols)
        api = self.api_getter()
        for i in range(first, last):
            it = self.item(i); key = it.data(KEY)
            if key in self._requested:
                continue
            self._requested.add(key)
            ts = self.albums[key]
            rep = next((t for t in ts if t.get("cover_id")), ts[0]) if ts[0]["source"] == "remote" else ts[0]
            self.pool.start(_Job(self._gen, key, rep, api, self.sigs))

    def _on_loaded(self, gen, key, data):
        if gen != self._gen or not data:
            return
        pm = QPixmap()
        if not pm.loadFromData(data):
            return
        pm = pm.scaled(ICON, ICON, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        it = self._items.get(key)
        if it is not None:
            it.setIcon(QIcon(pm))
