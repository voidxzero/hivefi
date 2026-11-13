"""Cover-art grid of albums with lazy, threaded cover loading."""
import math

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygon
from PyQt6.QtWidgets import (QAbstractItemView, QListView, QListWidget, QListWidgetItem, QStyledItemDelegate,
                             QToolTip, QVBoxLayout, QWidget)

from .. import art
from ..albums import title_of
from ..sortkey import sort_name
from .models import fmt_time
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
        if getattr(self.parent(), "rounded", False):               # the Glow skin: rounded covers
            from PyQt6.QtGui import QPainterPath
            path = QPainterPath(); path.addRoundedRect(cover.x(), cover.y(), ICON, ICON, 14, 14)
            p.setClipPath(path); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if isinstance(icon, QIcon) and not icon.isNull():
            pm = icon.pixmap(ICON * 2, ICON * 2)
            sz = pm.size().scaled(ICON, ICON, Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap(QRect(cover.x() + (ICON - sz.width()) // 2, cover.bottom() + 1 - sz.height(),
                               sz.width(), sz.height()), pm)
        p.setClipping(False)
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


class LetterBar(QWidget):
    """MusicBee's  # A B C ... Z  strip: click a letter to jump to the first album whose artist starts with it."""
    picked = pyqtSignal(str)
    LETTERS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def __init__(self):
        super().__init__()
        self.setFixedHeight(22); self.setMouseTracking(True)
        self._hot = -1; self._have: set[str] = set(self.LETTERS)

    def set_present(self, letters):
        self._have = set(letters); self.update()

    def _cell(self):
        return self.width() / len(self.LETTERS)

    def paintEvent(self, _e):
        p = QPainter(self); w = self._cell()
        for i, ch in enumerate(self.LETTERS):
            c = QColor(self.palette().text().color())
            c.setAlpha(255 if i == self._hot else (190 if ch in self._have else 60))
            p.setPen(c)
            p.drawText(QRect(int(i * w), 0, int(w) + 1, self.height()), Qt.AlignmentFlag.AlignCenter, ch)

    def mouseMoveEvent(self, e):
        i = int(e.position().x() // self._cell()); self._hot = i if 0 <= i < len(self.LETTERS) else -1; self.update()

    def leaveEvent(self, _e):
        self._hot = -1; self.update()

    def mousePressEvent(self, e):
        i = int(e.position().x() // self._cell())
        if 0 <= i < len(self.LETTERS):
            self.picked.emit(self.LETTERS[i])


class AlbumsPage(QWidget):
    """Letter strip above the album grid."""

    def __init__(self, grid):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.bar = LetterBar(); self.grid = grid
        lay.addWidget(self.bar); lay.addWidget(grid)
        self.bar.picked.connect(grid.scroll_to_letter)
        grid.lettersChanged.connect(self.bar.set_present)


class AlbumExpansion(QWidget):
    """MusicBee's expanded album: a band across the grid, under the row of the album that was clicked, with the
    cover, the album's details and its tracks in columns. Tinted with the cover's own colour."""
    trackActivated = pyqtSignal(int)                 # index into .tracks
    trackContext = pyqtSignal(int, object)           # index, global pos
    closed = pyqtSignal()
    PAD, ART, ROW, HEAD, COL_MIN, NOTCH = 18, 190, 24, 64, 290, 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.tracks: list = []
        self.cover: QPixmap | None = None
        self.bg = QColor(38, 38, 44)
        self.notch_x = 40
        self.playing_id = None
        self._hot = -1

    # ---- contents
    def set_album(self, tracks, cover=None):
        self.tracks = list(tracks); self._hot = -1
        self.set_cover(cover)

    def set_cover(self, pm):
        self.cover = pm if pm is not None and not pm.isNull() else None
        self.bg = QColor(38, 38, 44)
        if self.cover is not None:                    # the cover's average colour, kept dark enough for white text
            avg = self.cover.toImage().scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
            h, sat, val, _a = avg.getHsvF()
            self.bg = QColor.fromHsvF(max(h, 0.0), min(sat, 0.60), min(max(val * 0.45, 0.14), 0.30))
        self.update()

    def set_playing(self, tid):
        if tid != self.playing_id:
            self.playing_id = tid; self.update()

    # ---- geometry
    def _art(self, width):
        return self.ART if width >= 560 else 96

    def _cols(self, width):
        room = width - 3 * self.PAD - self._art(width)
        return max(1, min(3, room // self.COL_MIN, len(self.tracks) or 1))

    def _rows(self, width):
        return max(1, math.ceil(len(self.tracks) / self._cols(width)))

    def height_for(self, width):
        return self.NOTCH + 2 * self.PAD + max(self._art(width), self.HEAD + self._rows(width) * self.ROW)

    def _cell_rect(self, i):
        w = self.width(); art = self._art(w); cols, rows = self._cols(w), self._rows(w)
        x0 = 2 * self.PAD + art; cw = (w - x0 - self.PAD) // cols
        return QRect(x0 + (i // rows) * cw, self.NOTCH + self.PAD + self.HEAD + (i % rows) * self.ROW, cw - 12, self.ROW)

    def _close_rect(self):
        return QRect(self.width() - 34, self.NOTCH + 8, 24, 24)

    def hit(self, pos):
        for i in range(len(self.tracks)):
            if self._cell_rect(i).contains(pos):
                return i
        return -1

    # ---- painting
    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        w, h, N, P = self.width(), self.height(), self.NOTCH, self.PAD
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(self.bg)
        p.drawRect(0, N, w, h - N)
        x = min(max(self.notch_x, N + 2), w - N - 2)
        p.drawPolygon(QPolygon([QPoint(x - N, N), QPoint(x, 0), QPoint(x + N, N)]))
        fg = QColor(255, 255, 255); dim = QColor(255, 255, 255, 150)
        art = self._art(w); box = QRect(P, N + P, art, art)
        if self.cover is not None:
            sz = self.cover.size().scaled(art, art, Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap(QRect(box.x() + (art - sz.width()) // 2, box.y(), sz.width(), sz.height()), self.cover)
        else:
            p.setBrush(QColor(255, 255, 255, 22)); p.drawRect(box)
            f = QFont(self.font()); f.setPointSize(max(18, art // 4)); p.setFont(f); p.setPen(QColor(255, 255, 255, 80))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "♪")
        if not self.tracks:
            return
        ts = self.tracks; t0 = ts[0]
        x0 = 2 * P + art; tw = w - x0 - P - 30
        artist = t0.get("_aa") or t0["album_artist"] or t0["artist"]
        f = QFont(self.font()); f.setBold(True); f.setPointSize(f.pointSize() + 4); p.setFont(f); p.setPen(fg)
        p.drawText(QRect(x0, N + P - 4, tw, 28), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(title_of(ts), Qt.TextElideMode.ElideRight, tw))
        p.setFont(self.font()); fm = p.fontMetrics()
        years = sorted({t["year"] for t in ts if t.get("year")})
        genres = list(dict.fromkeys(t["genre"] for t in ts if t.get("genre")))
        total = sum(t.get("duration") or 0 for t in ts)
        bits = [artist] + ([str(years[0])] if years else []) + genres[:1]
        bits.append(f"{len(ts)} track{'s' if len(ts) != 1 else ''}, {fmt_time(total)}")
        p.setPen(dim)
        p.drawText(QRect(x0, N + P + 24, tw, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText("  ·  ".join(b for b in bits if b), Qt.TextElideMode.ElideRight, tw))
        c = self._close_rect()
        p.setPen(fg if self._hot == -2 else dim); p.drawText(c, Qt.AlignmentFlag.AlignCenter, "✕")
        discs = len({t["disc_no"] or 1 for t in ts}) > 1
        nw = fm.horizontalAdvance("0-000") if discs else fm.horizontalAdvance("000")
        accent = self.palette().link().color().lighter(140)
        VC = Qt.AlignmentFlag.AlignVCenter
        for i, t in enumerate(ts):
            r = self._cell_rect(i)
            playing = t["id"] == self.playing_id
            if i == self._hot:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 30)); p.drawRoundedRect(r, 4, 4)
            no = t["track_no"] or 0
            num = "▶" if playing else (f"{t['disc_no'] or 1}-{no:02d}" if discs else (str(no) if no else ""))
            p.setPen(accent if playing else dim)
            p.drawText(QRect(r.x() + 4, r.y(), nw, r.height()), Qt.AlignmentFlag.AlignRight | VC, num)
            dur = fmt_time(t["duration"]) if t.get("duration") else ""
            dw = fm.horizontalAdvance(dur) + 8
            p.drawText(QRect(r.right() - dw, r.y(), dw - 4, r.height()), Qt.AlignmentFlag.AlignRight | VC, dur)
            name = t["title"]
            if t["artist"] and t["artist"] != artist:
                name += f"  –  {t['artist']}"
            tx = r.x() + nw + 14; room = r.right() - dw - tx - 4
            p.setPen(accent if playing else fg)
            p.drawText(QRect(tx, r.y(), room, r.height()), Qt.AlignmentFlag.AlignLeft | VC,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, room))

    # ---- mouse
    def _set_hot(self, i):
        if i != self._hot:
            self._hot = i; self.update()

    def mouseMoveEvent(self, e):
        self._set_hot(-2 if self._close_rect().contains(e.pos()) else self.hit(e.pos()))

    def leaveEvent(self, _e):
        self._set_hot(-1)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._close_rect().contains(e.pos()):
            self.closed.emit()
        e.accept()                                     # clicks in the band never reach the grid under it

    def mouseDoubleClickEvent(self, e):
        i = self.hit(e.pos())
        if i >= 0 and e.button() == Qt.MouseButton.LeftButton:
            self.trackActivated.emit(i)

    def contextMenuEvent(self, e):
        i = self.hit(e.pos())
        if i >= 0:
            self._set_hot(i); self.trackContext.emit(i, e.globalPos())


class AlbumGrid(QListWidget):
    lettersChanged = pyqtSignal(object)              # the letters that have albums (for the A-Z strip)
    albumSelected = pyqtSignal(object)               # the album's tracks, or None when nothing is selected
    playRequested = pyqtSignal(list)
    playFrom = pyqtSignal(list, int)                 # the album's tracks and the one to start with (expanded album)
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
        # no grid size and no uniform sizes: every tile carries its size, so that the expanded album can be one
        # item as wide as the view, in a row of its own
        self._cell = QSize(ICON + 30, ICON + 52)
        self.setIconSize(QSize(ICON, ICON)); self.setSpacing(0)
        self.setWordWrap(True); self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.expand = True                            # a click opens the album under its row
        self._spacer = None; self._open_key = None
        self.panel = AlbumExpansion(self.viewport()); self.panel.hide()
        self.panel.closed.connect(self.clearSelection)
        self.panel.trackActivated.connect(lambda i: self.playFrom.emit(list(self.panel.tracks), i))
        self.panel.trackContext.connect(lambda i, gpos: self.contextRequested.emit([self.panel.tracks[i]], gpos))
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
        self.rounded = False
        self.placeholder = _placeholder(QColor(60, 60, 66), border_color(self))

    def set_flat(self, on: bool):
        """MusicBee style: bare covers (no frame), bold album / dim artist text, accent frame on the selection."""
        self.flat = on
        self.setItemDelegate(_FlatDelegate(self) if on else QStyledItemDelegate(self))
        self._cell = QSize(ICON + 24, ICON + 50) if on else QSize(ICON + 30, ICON + 52)
        for it in self._items.values():
            it.setSizeHint(self._cell)
        self._reopen()
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
        self._spacer = None; self._open_key = None; self.panel.hide()
        named = [(key, ts, title_of(ts), ts[0].get("_aa") or ts[0]["album_artist"] or ts[0]["artist"])
                 for key, ts in groups.items()]
        for key, ts, album, artist in sorted(named, key=lambda g: (sort_name(g[3]), sort_name(g[2]))):
            self.albums[key] = ts                      # track order is sorted on demand (album_tracks)
            it = QListWidgetItem(self.placeholder, f"{album}\n{artist}")
            it.setData(KEY, key); it.setSizeHint(self._cell)
            self.addItem(it); self._items[key] = it
        self.blockSignals(False)
        self._letter_rows = {}
        for i in range(self.count()):
            ts = self.albums[self.item(i).data(KEY)]
            ch = sort_name(ts[0].get("_aa") or ts[0]["album_artist"] or ts[0]["artist"])[:1].upper()
            self._letter_rows.setdefault(ch if ch.isalpha() and ch.isascii() else "#", self.item(i))
        self.lettersChanged.emit(set(self._letter_rows))
        self._timer.start()

    def scroll_to_letter(self, ch):
        """Jump to the first album of an artist starting with `ch` (the next letter that has some, if none does)."""
        letters = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for c in letters[letters.index(ch):]:
            it = getattr(self, "_letter_rows", {}).get(c)
            if it is not None:
                self.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtTop)
                return

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
        if getattr(self, "_expanding", False):            # the band being put in or taken out, not the user
            return
        ts = self.selected_album()
        self._expand(self.currentItem().data(KEY) if ts and self.expand else None)
        self.albumSelected.emit(ts)

    # ---- the expanded album
    def set_expand(self, on: bool):
        self.expand = bool(on)
        it = self.currentItem()
        self._expand(it.data(KEY) if on and it is not None and it.isSelected() else None)

    def set_playing(self, tid):
        self.panel.set_playing(tid)

    def _expand(self, key, scroll=True):
        """Open the album `key` under its row (None: close). The band is a list item as wide as the view, put
        after the last tile of that row, with the panel laid over it."""
        if getattr(self, "_expanding", False):
            return
        self._expanding = True
        try:
            self._expand_now(key, scroll)
        finally:
            self._expanding = False

    def _expand_now(self, key, scroll):
        if self._spacer is not None:
            sp, self._spacer = self._spacer, None
            self.takeItem(self.row(sp))
        self._open_key = key if key in self._items else None
        if self._open_key is None:
            self.panel.hide(); self._timer.start()
            return
        it = self._items[key]
        self.doItemsLayout()
        y = self.visualItemRect(it).y(); last = self.row(it)
        while last + 1 < self.count() and self.visualItemRect(self.item(last + 1)).y() == y:
            last += 1
        icon = it.icon()
        cover = None if icon.cacheKey() == self.placeholder.cacheKey() else icon.pixmap(ICON * 2, ICON * 2)
        if self.panel.tracks[:1] != self.album_tracks(it)[:1] or self.panel.cover is None:
            self.panel.set_album(self.album_tracks(it), cover)
        w = self.viewport().width()
        sp = QListWidgetItem(); sp.setFlags(Qt.ItemFlag.NoItemFlags); sp.setSizeHint(QSize(w, self.panel.height_for(w)))
        self.insertItem(last + 1, sp); self._spacer = sp
        self.doItemsLayout()
        if scroll:
            self.scrollToItem(sp); self.scrollToItem(it)      # the band in view, but the album itself first
        self.panel.notch_x = self.visualItemRect(it).center().x()
        self._place_panel(); self.panel.show(); self.panel.update()
        # the cover at full size (the tile only has a small one)
        self.pool.start(_Job(self._gen, key, self.albums[key], self.api_getter(), self.sigs, self._current), 2 ** 30)
        self._timer.start()

    def _reopen(self):
        if self._open_key is not None:
            self._expand(self._open_key, scroll=False)

    def _place_panel(self):
        if self._spacer is not None:
            self.panel.setGeometry(self.visualItemRect(self._spacer))

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy); self._place_panel()

    def mousePressEvent(self, e):
        it = self.itemAt(e.pos())
        if (self.expand and e.button() == Qt.MouseButton.LeftButton and it is not None and it.isSelected()
                and it.data(KEY) == self._open_key):
            self.clearSelection()                         # a click on the open album closes it again
            return
        super().mousePressEvent(e)

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it:
            self.setCurrentItem(it)
            self.contextRequested.emit(self.album_tracks(it), self.viewport().mapToGlobal(pos))

    def resizeEvent(self, e):
        super().resizeEvent(e); self._timer.start()
        if self._spacer is not None:
            if self._spacer.sizeHint().width() != self.viewport().width():     # the number of columns may differ now
                QTimer.singleShot(0, self._reopen)
            self._place_panel()

    def showEvent(self, e):
        super().showEvent(e); self._timer.start()

    def _request_visible(self):
        if not self.isVisible():
            return
        # items flow left-to-right in fixed cells: work out which rows are on screen instead of asking Qt about
        # every one of thousands of items
        gw, gh = self._cell.width(), self._cell.height()
        vp = self.viewport().rect()
        cols = max(1, vp.width() // gw)
        top = self.verticalScrollBar().value()
        first = max(0, (top - ICON) // gh) * cols
        last = min(self.count(), ((top + vp.height() + ICON) // gh + 1) * cols)
        if self._spacer is not None:                     # the open album pushes the rows after it down
            first = max(0, first - (self._spacer.sizeHint().height() // gh + 1) * cols); last = min(self.count(), last + 1)
        api = self.api_getter()
        for i in range(first, last):
            it = self.item(i); key = it.data(KEY)
            if key is None or key in self._requested:
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
        if key == self._open_key:
            self.panel.set_cover(pm)
        edge = ICON if self.flat else ICON - 2 * BORDER
        pm = pm.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        it = self._items.get(key)
        if it is not None:
            it.setIcon(QIcon(pm if self.flat else with_border(pm, border_color(self))))
