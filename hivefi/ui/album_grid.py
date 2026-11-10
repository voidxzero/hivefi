"""Cover-art grid of albums with lazy, threaded cover loading."""
from PyQt6.QtCore import QEvent, QObject, QRect, QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QListView, QListWidget, QListWidgetItem, QStyledItemDelegate,
                             QToolTip)

from .. import art
from ..albums import title_of
from ..sortkey import sort_name
from .panels import BORDER, border_color, with_border

ICON = 150
KEY = Qt.ItemDataRole.UserRole


class _Signals(QObject):
    loaded = pyqtSignal(int, str, bytes)


class _Job(QRunnable):
    def __init__(self, gen, key, tracks, api, sigs, current):
        super().__init__()
        self.gen, self.key, self.tracks, self.api, self.sigs, self.current = gen, key, tracks, api, sigs, current

    def run(self):
        if self.current[0] != self.gen:                  # the grid was refilled meanwhile: skip the (slow) file reads
            return
        try:
            data = art.album_art(self.tracks, self.api, ICON * 2) or b""
        except Exception:  # noqa: BLE001
            data = b""
        self.sigs.loaded.emit(self.gen, self.key, data)


def _placeholder(color: QColor, frame: QColor, bordered=True):
    inner = ICON - 2 * BORDER if bordered else ICON
    pm = QPixmap(inner, inner); pm.fill(color)
    p = QPainter(pm); p.setPen(QColor(255, 255, 255, 70)); f = p.font(); f.setPointSize(40); p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "♪"); p.end()
    return QIcon(with_border(pm, frame) if bordered else pm)


class _FlatDelegate(QStyledItemDelegate):
    """MusicBee's tiles: a bare cover, the album in bold under it and the artist dimmed; the selected cover gets
    a frame in the accent colour."""

    def paint(self, p, option, index):
        p.save(); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = option.rect
        cover = QRect(r.x() + (r.width() - ICON) // 2, r.y() + 6, ICON, ICON)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            pm = icon.pixmap(ICON * 2, ICON * 2)
            sz = pm.size().scaled(ICON, ICON, Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap(QRect(cover.x() + (ICON - sz.width()) // 2, cover.bottom() + 1 - sz.height(),
                               sz.width(), sz.height()), pm)
        if option.state & option.state.State_Selected:
            p.setPen(QPen(option.palette.link().color(), 3)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(cover.adjusted(1, 1, -1, -1))
        elif option.state & option.state.State_MouseOver:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1)); p.drawRect(cover.adjusted(0, 0, -1, -1))
        title, _, artist = (index.data(Qt.ItemDataRole.DisplayRole) or "").partition("\n")
        bold = QFont(option.font); bold.setBold(True); p.setFont(bold)
        p.setPen(option.palette.text().color())
        fm = p.fontMetrics()
        p.drawText(QRect(cover.x(), cover.bottom() + 6, ICON, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, ICON))
        small = QFont(option.font); p.setFont(small)
        dim = QColor(option.palette.text().color()); dim.setAlpha(105); p.setPen(dim)
        p.drawText(QRect(cover.x(), cover.bottom() + 24, ICON, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(artist, Qt.TextElideMode.ElideRight, ICON))
        p.restore()


class AlbumGrid(QListWidget):
    albumSelected = pyqtSignal(object)               # the album's tracks, or None when nothing is selected
    playRequested = pyqtSignal(list)
    contextRequested = pyqtSignal(list, object)      # tracks, global pos

    def __init__(self, api_getter):
        super().__init__()
        self.setObjectName("albumgrid")
        self.api_getter = api_getter
        self.albums: dict[str, list] = {}
        self._gen = 0
        self._current = [0]                           # shared with the jobs, so stale ones can bail out early
        self._prio = 0
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
        self.flat = False
        self.placeholder = _placeholder(QColor(60, 60, 66), border_color(self))

    def set_flat(self, on: bool):
        """MusicBee style: bare covers (no frame), bold album / dim artist text, accent frame on the selection."""
        self.flat = on
        self.setItemDelegate(_FlatDelegate(self) if on else QStyledItemDelegate(self))
        self.setGridSize(QSize(ICON + 24, ICON + 50) if on else QSize(ICON + 30, ICON + 52))
        self.setSpacing(0)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def set_placeholder_color(self, c: QColor):
        self.placeholder = _placeholder(c, border_color(self), not self.flat)

    def set_tracks(self, tracks):
        self._gen += 1; self._current[0] = self._gen; self._requested.clear()
        self.pool.clear()                              # covers queued for the previous contents are not needed
        groups: dict[str, list] = {}
        for t in tracks:                               # albums = title + album artist (see hivefi.albums)
            groups.setdefault(t.get("_ak") or f"{t['album_artist'] or t['artist']}\x1f{t['album']}", []).append(t)
        self.blockSignals(True); self.clear(); self.albums = {}; self._items = {}
        named = [(key, ts, title_of(ts), ts[0].get("_aa") or ts[0]["album_artist"] or ts[0]["artist"])
                 for key, ts in groups.items()]
        for key, ts, album, artist in sorted(named, key=lambda g: (sort_name(g[3]), sort_name(g[2]))):
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
                QToolTip.showText(e.globalPos(), f"{title_of(ts)} — {ts[0].get('_aa') or ts[0]['artist']}\n"
                                                 f"{len(ts)} tracks", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def select_album(self, key):
        """Select (and scroll to) the album with this key, if it is in the grid."""
        it = self._items.get(key)
        if it is not None:
            self.setCurrentItem(it); it.setSelected(True); self.scrollToItem(it)
        return it is not None

    def selected_album(self):
        it = self.currentItem()
        return self.album_tracks(it) if it and it.isSelected() else None

    def _sel(self):
        self.albumSelected.emit(self.selected_album())

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
            # newest first: where the user stops scrolling gets its covers before the rows scrolled past
            self._prio = min(self._prio + 1, 2 ** 30)
            self.pool.start(_Job(self._gen, key, self.albums[key], api, self.sigs, self._current), self._prio)

    def _on_loaded(self, gen, key, data):
        if gen != self._gen or not data:
            return
        pm = QPixmap()
        if not pm.loadFromData(data):
            return
        edge = ICON if self.flat else ICON - 2 * BORDER
        pm = pm.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        it = self._items.get(key)
        if it is not None:
            it.setIcon(QIcon(pm if self.flat else with_border(pm, border_color(self))))
