"""MusicBee's "Album Artist" panel: round artist pictures (the cover of their first album), name and album count,
with "All Artists" on top. Pictures load lazily for the rows on screen, like the album grid."""
from PyQt6.QtCore import QObject, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyledItemDelegate

from .. import art
from ..sortkey import sort_name

KEY = Qt.ItemDataRole.UserRole
COUNT = Qt.ItemDataRole.UserRole + 1
PIC = 60
ROW = 70


class _Signals(QObject):
    loaded = pyqtSignal(int, str, bytes)


class _Job(QRunnable):
    def __init__(self, gen, key, tracks, api, sigs, current):
        super().__init__()
        self.gen, self.key, self.tracks, self.api, self.sigs, self.current = gen, key, tracks, api, sigs, current

    def run(self):
        if self.current[0] != self.gen:
            return
        try:
            data = art.album_art(self.tracks, self.api, PIC * 2) or b""
        except Exception:  # noqa: BLE001
            data = b""
        self.sigs.loaded.emit(self.gen, self.key, data)


class _Delegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW)

    def paint(self, p, option, index):
        p.save(); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect
        if option.state & option.state.State_Selected:
            p.fillRect(r, QColor(255, 255, 255, 22))
        elif option.state & option.state.State_MouseOver:
            p.fillRect(r, QColor(255, 255, 255, 12))
        pic = QRect(r.x() + 8, r.y() + (r.height() - PIC) // 2, PIC, PIC)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        path = QPainterPath(); path.addEllipse(QRectF(pic))
        p.setClipPath(path)
        p.fillRect(pic, QColor(255, 255, 255, 18))
        if isinstance(icon, QIcon) and not icon.isNull():
            pm = icon.pixmap(PIC * 2, PIC * 2)
            side = min(pm.width(), pm.height())
            p.drawPixmap(pic, pm, QRect((pm.width() - side) // 2, (pm.height() - side) // 2, side, side))
        p.setClipping(False)
        tx = pic.right() + 12
        w = r.right() - tx - 6
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        sub = index.data(COUNT) or ""
        f = QFont(option.font); fm = p.fontMetrics()
        p.setPen(option.palette.text().color()); p.setFont(f)
        p.drawText(QRect(tx, r.y() + 14, w, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(name, Qt.TextElideMode.ElideRight, w))
        small = QFont(f); small.setPointSizeF(max(7.0, f.pointSizeF() - 1.5)); p.setFont(small)
        dim = QColor(option.palette.text().color()); dim.setAlpha(110); p.setPen(dim)
        p.drawText(QRect(tx, r.y() + 34, w, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub)
        p.restore()


class ArtistList(QListWidget):
    artistSelected = pyqtSignal(object)                   # artist name, or None for All Artists

    def __init__(self, api_getter):
        super().__init__()
        self.setObjectName("artistlist")
        self.api_getter = api_getter
        self.setItemDelegate(_Delegate(self))
        self.setUniformItemSizes(True); self.setIconSize(QSize(PIC, PIC))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.artists: dict[str, list] = {}               # name -> that artist's tracks
        self._items: dict[str, QListWidgetItem] = {}
        self._gen = 0
        self._current = [0]
        self._requested: set[str] = set()
        self._prio = 0
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(2)
        self.sigs = _Signals(); self.sigs.loaded.connect(self._on_loaded)
        self._timer = QTimer(self, singleShot=True, interval=80, timeout=self._request_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._timer.start())
        self.currentItemChanged.connect(self._changed)
        self._quiet = False

    def set_tracks(self, tracks, selected=None):
        """Fill from a track list (one row per album artist). `selected` = artist to keep selected, if present."""
        self._gen += 1; self._current[0] = self._gen; self._requested.clear(); self.pool.clear()
        groups: dict[str, dict[str, list]] = {}
        for t in tracks:
            a = t.get("_aa") or t["album_artist"] or t["artist"]
            if not a:
                continue
            k = t.get("_ak") or f"{a}\x1f{t['album']}"
            groups.setdefault(a, {}).setdefault(k, []).append(t)
        self._quiet = True
        self.clear(); self.artists = {}; self._items = {}
        total = sum(len(g) for g in groups.values())
        it = QListWidgetItem("All Artists"); it.setData(KEY, None); it.setData(COUNT, f"{total} albums")
        self.addItem(it)
        for a in sorted(groups, key=sort_name):
            albums = groups[a]
            first = next(iter(albums.values()))
            self.artists[a] = first                      # the cover comes from their first album
            n = len(albums)
            it = QListWidgetItem(a); it.setData(KEY, a); it.setData(COUNT, f"{n} album" + ("" if n == 1 else "s"))
            self.addItem(it); self._items[a] = it
        self.setCurrentItem(self._items.get(selected) or self.item(0))
        self._quiet = False
        self._timer.start()

    def _changed(self, cur, _prev):
        if not self._quiet and cur is not None:
            self.artistSelected.emit(cur.data(KEY))

    def resizeEvent(self, e):
        super().resizeEvent(e); self._timer.start()

    def showEvent(self, e):
        super().showEvent(e); self._timer.start()

    def _request_visible(self):
        if not self.isVisible():
            return
        top = self.verticalScrollBar().value()
        h = self.viewport().height()
        first = max(0, top // ROW - 2); last = min(self.count(), (top + h) // ROW + 3)
        api = self.api_getter()
        for i in range(first, last):
            it = self.item(i); key = it.data(KEY)
            if key is None or key in self._requested:
                continue
            self._requested.add(key)
            self._prio = min(self._prio + 1, 2 ** 30)
            self.pool.start(_Job(self._gen, key, self.artists[key], api, self.sigs, self._current), self._prio)

    def _on_loaded(self, gen, key, data):
        if gen != self._gen or not data:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            it = self._items.get(key)
            if it is not None:
                it.setIcon(QIcon(pm))
