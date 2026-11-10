"""Album and Tracks view: the track list grouped by album, the cover on the left and the album's tracks beside it.

It shows the same TrackModel as the detail table and shares its selection model, so everything that works on the
selection (right-click menu, Track Info, tools, drag sources elsewhere) works the same in both views. Only the
albums on screen are painted, so a 100,000-track library scrolls as smoothly as a short playlist."""
import bisect

from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QPoint, QRect, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QAbstractScrollArea

from .album_grid import _Job, _Signals
from .models import COLUMNS, fmt_time
from .panels import BORDER, border_color, with_border

TITLE_COL = next(i for i, c in enumerate(COLUMNS) if c[1] == "title")
SEL = QItemSelectionModel.SelectionFlag


def album_key(t):
    return t.get("_ak") or f'{(t.get("album_artist") or t.get("artist") or "").lower()}\x1f{(t.get("album") or "").lower()}'


class AlbumTracksView(QAbstractScrollArea):
    activated = pyqtSignal(int)                 # row double-clicked / Enter
    contextMenu = pyqtSignal(QPoint)            # global position of a right-click on a row
    ratingClicked = pyqtSignal(int, int)        # row, stars (0..5) clicked in the rating column

    ART_FULL, ART_SMALL = 140, 84
    COMPACT_BELOW = 520                          # narrower than this (a side panel): small cover, no rating column
    ROW = 22
    HEAD = 30                                    # album title line
    GAP = 12

    def __init__(self, model, selection_model, api_getter):
        super().__init__()
        self.setObjectName("albumtracks")
        self.model, self.sel, self.api_getter = model, selection_model, api_getter
        self.groups: list[tuple[int, int, str]] = []      # (first row, row after the last, album key)
        self.tops: list[int] = []                          # y of each group in the content
        self.total = 0
        self.anchor = None
        self._art: dict[str, QPixmap | None] = {}
        self._requested: set[str] = set()
        self._gen = 0
        self._current = [0]
        self._prio = 0
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(3)
        self.sigs = _Signals(); self.sigs.loaded.connect(self._on_loaded)
        self._art_timer = QTimer(self, singleShot=True, interval=60, timeout=self._request_visible)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().setSingleStep(self.ROW)
        self.verticalScrollBar().valueChanged.connect(lambda _v: (self.viewport().update(), self._art_timer.start()))
        self._stale = True
        self._compact = False
        model.modelReset.connect(self._model_reset)
        model.dataChanged.connect(lambda *_: self.viewport().update())
        selection_model.selectionChanged.connect(lambda *_: self.viewport().update())
        selection_model.currentChanged.connect(self._current_changed)

    # ------------------------------------------------------------------ layout
    @property
    def ART(self):
        return self.ART_SMALL if self._compact else self.ART_FULL

    @property
    def LEFT(self):                              # width of the cover column
        return self.ART + 24

    def _left_height(self):
        return self.ART + 2 * BORDER + 8

    def _model_reset(self):
        """Regroup now when showing, otherwise when next shown (no extra pass over the library in Details)."""
        self.anchor = None
        if self.isVisible():
            self._regroup()
        else:
            self._stale = True

    def _ensure_grouped(self):
        if self._stale:
            self._regroup()

    def _regroup(self):
        self._stale = False
        rows = self.model.tracks
        groups, start, key = [], 0, None
        for i, t in enumerate(rows):
            k = album_key(t)
            if k != key:
                if key is not None:
                    groups.append((start, i, key))
                start, key = i, k
        if key is not None:
            groups.append((start, len(rows), key))
        self.groups = groups
        self.tops, y = [], 0
        lh = self._left_height()
        for s, e, _k in groups:
            self.tops.append(y)
            y += self.HEAD + max(lh, (e - s) * self.ROW) + self.GAP
        self.total = y
        self._gen += 1; self._current[0] = self._gen
        self._requested.clear()                               # the album list changed: covers already loaded stay
        self.anchor = None
        self._update_scroll()
        self.viewport().update()
        self._art_timer.start()

    def _update_scroll(self):
        sb = self.verticalScrollBar()
        sb.setPageStep(self.viewport().height())
        sb.setRange(0, max(0, self.total - self.viewport().height()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        compact = self.viewport().width() < self.COMPACT_BELOW
        if compact != self._compact:
            self._compact = compact
            if not self._stale:
                top = self.top_row()
                self._regroup()                               # block heights depend on the cover size
                if top is not None:
                    self.verticalScrollBar().setValue(self.verticalScrollBar().value() + self.row_rect(top).top())
        self._update_scroll(); self._art_timer.start()

    def showEvent(self, e):
        self._ensure_grouped()
        super().showEvent(e); self._update_scroll(); self._art_timer.start()

    def _group_at_y(self, y):
        """Index of the group whose block contains content y (or None)."""
        g = bisect.bisect_right(self.tops, y) - 1
        return g if 0 <= g < len(self.groups) else None

    def _group_of_row(self, row):
        starts = [s for s, _e, _k in self.groups]
        g = bisect.bisect_right(starts, row) - 1
        return g if 0 <= g < len(self.groups) else None

    def row_rect(self, row):
        """Rectangle of a track row in viewport coordinates."""
        self._ensure_grouped()
        g = self._group_of_row(row)
        if g is None:
            return QRect()
        s = self.groups[g][0]
        y = self.tops[g] + self.HEAD + (row - s) * self.ROW - self.verticalScrollBar().value()
        return QRect(self.LEFT, y, self.viewport().width() - self.LEFT, self.ROW)

    def hit(self, pos):
        """('row', row) | ('album', group) | None for a viewport position."""
        y = pos.y() + self.verticalScrollBar().value()
        g = self._group_at_y(y)
        if g is None:
            return None
        s, e, _k = self.groups[g]
        dy = y - self.tops[g] - self.HEAD
        if dy < 0:
            return ("album", g)
        if pos.x() >= self.LEFT:
            r = s + dy // self.ROW
            return ("row", r) if r < e else None
        return ("album", g) if dy < self._left_height() else None

    def scroll_to_row(self, row, center=False):
        if not self.isVisible():
            return                                               # (re)grouped and placed when shown
        r = self.row_rect(row)
        if r.isNull():
            return
        sb = self.verticalScrollBar(); h = self.viewport().height()
        if center:
            sb.setValue(sb.value() + r.center().y() - h // 2)
        elif r.top() < 0:
            g = self._group_of_row(row)
            sb.setValue(self.tops[g] if row == self.groups[g][0] else sb.value() + r.top())
        elif r.bottom() > h:
            sb.setValue(sb.value() + r.bottom() - h + 1)

    def top_row(self):
        """The first row on screen (for remembering the scroll position)."""
        self._ensure_grouped()
        y = self.verticalScrollBar().value()
        g = self._group_at_y(y)
        if g is None:
            return None
        s, e, _k = self.groups[g]
        return min(e - 1, s + max(0, y - self.tops[g] - self.HEAD) // self.ROW)

    def _current_changed(self, cur, _prev):
        if cur.isValid() and self.isVisible():
            self.scroll_to_row(cur.row())

    # ------------------------------------------------------------------ covers
    def _request_visible(self):
        if not self.isVisible() or not self.groups:
            return
        y0 = self.verticalScrollBar().value() - self.ART
        y1 = y0 + self.viewport().height() + 2 * self.ART
        g = self._group_at_y(max(0, y0)) or 0
        api = self.api_getter()
        while g < len(self.groups) and self.tops[g] < y1:
            s, e, k = self.groups[g]
            if k not in self._art and k not in self._requested:
                self._requested.add(k)
                self._prio = min(self._prio + 1, 2 ** 30)
                self.pool.start(_Job(self._gen, k, self.model.tracks[s:e], api, self.sigs, self._current), self._prio)
            g += 1

    def _on_loaded(self, _gen, key, data):
        pm = QPixmap()
        if data and pm.loadFromData(data):
            inner = self.ART_FULL
            self._art[key] = with_border(pm.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation), border_color(self))
        else:
            self._art[key] = None
        self.viewport().update()

    def _placeholder(self):
        pm = QPixmap(self.ART_FULL, self.ART_FULL); pm.fill(self.palette().color(QPalette.ColorRole.Button))
        p = QPainter(pm); p.setPen(QColor(255, 255, 255, 70)); f = p.font(); f.setPointSize(36); p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "♪"); p.end()
        return with_border(pm, border_color(self))

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e):
        p = QPainter(self.viewport())
        pal = self.palette()
        w, h = self.viewport().width(), self.viewport().height()
        if not getattr(self, "see_through", False):               # the Glow skin shows the blurred cover behind
            p.fillRect(0, 0, w, h, pal.color(QPalette.ColorRole.Base))
        if not self.groups:
            p.end(); return
        off = self.verticalScrollBar().value()
        g = self._group_at_y(off) or 0
        rows = self.model.tracks
        text, dim = pal.color(QPalette.ColorRole.Text), pal.color(QPalette.ColorRole.PlaceholderText)
        hl, hl_text = pal.color(QPalette.ColorRole.Highlight), pal.color(QPalette.ColorRole.HighlightedText)
        alt = pal.color(QPalette.ColorRole.AlternateBase)
        if getattr(self, "see_through", False):
            alt = QColor(255, 255, 255, 38)                       # a hint of striping over the blurred cover
        playing = QColor(self.model.playing_color)
        bold = QFont(self.font()); bold.setBold(True); bold.setPointSizeF(self.font().pointSizeF() * 1.1)
        fm = self.fontMetrics()
        star_w = max(8, fm.horizontalAdvance("★"))
        compact = self._compact
        rating_w, time_w, no_w = (0 if compact else star_w * 5 + 8), 56, 34
        side = self.ART + 2 * BORDER
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ph = None
        while g < len(self.groups) and self.tops[g] - off < h:
            s, e, k = self.groups[g]
            top = self.tops[g] - off
            first = rows[s]
            # album line: title, album artist; year, number of tracks and length on the right
            p.setPen(QColor(dim.red(), dim.green(), dim.blue(), 60))
            p.drawLine(8, top + self.HEAD - 3, w - 8, top + self.HEAD - 3)
            aa = first.get("_aa") or first.get("album_artist") or first.get("artist") or ""
            info = " · ".join(x for x in (str(first.get("year") or ""), f"{e - s} track{'s' if e - s != 1 else ''}",
                                          fmt_time(sum(t.get("duration") or 0 for t in rows[s:e]))) if x)
            p.setPen(dim); iw = fm.horizontalAdvance(info) + 10
            p.drawText(QRect(w - iw - 8, top, iw, self.HEAD - 4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, info)
            p.setFont(bold); p.setPen(text)
            album = first.get("album") or "Unknown album"
            aw = min(p.fontMetrics().horizontalAdvance(album) + 4, max(40, w - iw - 24))
            p.drawText(QRect(10, top, aw, self.HEAD - 4), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(album, Qt.TextElideMode.ElideRight, aw))
            p.setFont(self.font()); p.setPen(dim)
            rest = max(0, w - iw - 24 - aw - 12)
            p.drawText(QRect(10 + aw + 12, top, rest, self.HEAD - 4), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(aa, Qt.TextElideMode.ElideRight, rest))
            # cover
            art = self._art.get(k)
            if art is None:
                art = ph = ph or self._placeholder()
            if compact:
                sz = art.size().scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio)
                p.drawPixmap(QRect(10, top + self.HEAD + 2, sz.width(), sz.height()), art)
            else:
                p.drawPixmap(10, top + self.HEAD + 2, art)
            discs = len({t.get("disc_no") or 1 for t in rows[s:e]}) > 1     # several discs: 1-01, 2-01...
            # tracks
            y = top + self.HEAD
            for r in range(s, e):
                if y + self.ROW >= 0 and y < h:
                    t = rows[r]
                    rect = QRect(self.LEFT, y, w - self.LEFT, self.ROW)
                    selected = self.sel.isRowSelected(r)
                    if selected:
                        p.fillRect(rect, hl)
                    elif (r - s) % 2:
                        p.fillRect(rect, alt)
                    col = hl_text if selected else (playing if t["id"] == self.model.playing_id else text)
                    p.setPen(col)
                    x = self.LEFT + 4
                    no = str(t.get("track_no") or "")
                    if discs and no:
                        no = f'{t.get("disc_no") or 1}-{no.zfill(2)}'
                    if t["id"] == self.model.playing_id:
                        no = "▶ " + no
                    p.drawText(QRect(x, y, no_w + (14 if discs else 0), self.ROW),
                               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, no)
                    x += no_w + 12 + (14 if discs else 0)
                    title_w = max(40, w - x - rating_w - time_w - 12)
                    title = t.get("title") or ""
                    if (t.get("artist") or "").strip() and t.get("artist") != aa:
                        title = f'{title}  —  {t["artist"]}'
                    if t.get("starred"):
                        title = "♥ " + title
                    p.drawText(QRect(x, y, title_w, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                               fm.elidedText(title, Qt.TextElideMode.ElideRight, title_w))
                    if not compact:
                        rv = t.get("rating") or 0
                        p.drawText(QRect(w - rating_w - time_w - 4, y, rating_w, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                                   "★" * rv + "☆" * (5 - rv))
                    dur = "live" if t.get("source") == "radio" else fmt_time(t.get("duration"))
                    p.drawText(QRect(w - time_w - 8, y, time_w, self.ROW),
                               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, dur)
                y += self.ROW
            g += 1
        p.end()

    def _rating_x(self):
        star_w = max(8, self.fontMetrics().horizontalAdvance("★"))
        rating_w = star_w * 5 + 8
        if self._compact:                                      # no rating column: nothing to hit
            return self.viewport().width() + 10000, star_w
        return self.viewport().width() - rating_w - 56 - 4, star_w

    # ------------------------------------------------------------------ selection and input
    def _select_rows(self, first, last, flags):
        m = self.model
        sel = QItemSelection(m.index(first, 0), m.index(last, len(COLUMNS) - 1))
        self.sel.select(sel, flags | SEL.Rows)

    def _set_current(self, row):
        self.sel.setCurrentIndex(self.model.index(row, TITLE_COL), SEL.NoUpdate)

    def _click_row(self, row, mods):
        if mods & Qt.KeyboardModifier.ShiftModifier and self.anchor is not None:
            a, b = sorted((self.anchor, row))
            self._select_rows(a, b, SEL.ClearAndSelect if not mods & Qt.KeyboardModifier.ControlModifier else SEL.Select)
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self._select_rows(row, row, SEL.Toggle); self.anchor = row
        else:
            self._select_rows(row, row, SEL.ClearAndSelect); self.anchor = row
        self._set_current(row)

    def mousePressEvent(self, e):
        self.setFocus()
        h = self.hit(e.position().toPoint())
        if h is None:
            if e.button() == Qt.MouseButton.LeftButton:
                self.sel.clearSelection()
            return
        kind, v = h
        right = e.button() == Qt.MouseButton.RightButton
        if kind == "album":                                    # the album line or the cover: the whole album
            s, end, _k = self.groups[v]
            if not (right and all(self.sel.isRowSelected(r) for r in range(s, end))):
                ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
                self._select_rows(s, end - 1, SEL.Select if ctrl else SEL.ClearAndSelect)
                self.anchor = s; self._set_current(s)
        elif right:
            if not self.sel.isRowSelected(v):
                self._click_row(v, Qt.KeyboardModifier.NoModifier)
        else:
            x0, sw = self._rating_x()
            x = e.position().x()
            if x0 <= x < x0 + sw * 5 + 8 and not e.modifiers():
                self._click_row(v, Qt.KeyboardModifier.NoModifier)
                self.ratingClicked.emit(v, 0 if x < x0 + 3 else min(5, int((x - x0 - 3) // sw) + 1))
                return
            self._click_row(v, e.modifiers())
        if right:
            self.contextMenu.emit(self.viewport().mapToGlobal(e.position().toPoint()))

    def mouseDoubleClickEvent(self, e):
        h = self.hit(e.position().toPoint())
        if not h or e.button() != Qt.MouseButton.LeftButton:
            return
        kind, v = h
        if kind == "row":
            x0, sw = self._rating_x()
            if not x0 <= e.position().x() < x0 + sw * 5 + 8:
                self.activated.emit(v)
        else:
            self.activated.emit(self.groups[v][0])               # double-click the cover: play the album

    def keyPressEvent(self, e):
        n = len(self.model.tracks)
        if not n:
            return super().keyPressEvent(e)
        cur = self.sel.currentIndex().row() if self.sel.currentIndex().isValid() else -1
        k = e.key()
        step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1,
                Qt.Key.Key_PageUp: -max(1, self.viewport().height() // self.ROW),
                Qt.Key.Key_PageDown: max(1, self.viewport().height() // self.ROW)}.get(k)
        if k == Qt.Key.Key_Home:
            nxt = 0
        elif k == Qt.Key.Key_End:
            nxt = n - 1
        elif step is not None:
            nxt = min(n - 1, max(0, (cur if cur >= 0 else -1 if step > 0 else n) + step))
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and cur >= 0 and not e.modifiers():
            self.activated.emit(cur); return
        elif k == Qt.Key.Key_A and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._select_rows(0, n - 1, SEL.ClearAndSelect); return
        else:
            return super().keyPressEvent(e)
        self._click_row(nxt, e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.scroll_to_row(nxt)

    def sizeHint(self):
        return QSize(600, 400)
