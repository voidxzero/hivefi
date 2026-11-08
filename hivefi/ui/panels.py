"""Dockable panel widgets: Now Playing, Up Next, Lyrics, Track Info."""
import bisect
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .models import fmt_time


BORDER = 2


def with_border(pm: QPixmap, color, width=BORDER) -> QPixmap:
    """The pixmap framed by a border drawn around it (the result is 2*width larger in each direction)."""
    out = QPixmap(pm.width() + 2 * width, pm.height() + 2 * width)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(width, width, pm)
    p.setPen(QPen(QColor(color), width)); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(width / 2, width / 2, out.width() - width, out.height() - width))
    p.setPen(QPen(QColor(0, 0, 0, 90), 1))                      # thin dark line where the art meets the frame
    p.drawRect(QRectF(width - 0.5, width - 0.5, pm.width() + 1, pm.height() + 1))
    p.end()
    return out


def border_color(widget) -> QColor:
    """Frame colour that stands out from the current skin's background (follows light and dark skins)."""
    pal = widget.palette()
    return pal.mid().color().lighter(135) if pal.window().color().lightness() < 128 else pal.mid().color().darker(125)


class ArtLabel(QLabel):
    """Square-fitting cover art that rescales with its container, with a border around the picture."""
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._pm: QPixmap | None = None
        self.setMinimumSize(96, 96)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: rgba(0,0,0,40);")

    def set_art(self, pm: QPixmap | None):
        self._pm = pm; self._update()

    def resizeEvent(self, e):
        super().resizeEvent(e); self._update()

    def _update(self):
        if self._pm is None or self._pm.isNull():
            self.clear(); self.setText("♪"); self.setFont(QFont(self.font().family(), 36)); return
        s = max(32, min(self.width(), self.height()) - 4 - 2 * BORDER)
        scaled = self._pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(with_border(scaled, border_color(self)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.PaletteChange and self._pm is not None:
            self._update()                                      # skin changed: redraw the frame in its colour
        super().changeEvent(e)


class NowPlayingPanel(QWidget):
    navigate = pyqtSignal(str, object)       # what was clicked ("album", "artist", "genre", "year"...), the playing track
    configureRequested = pyqtSignal()

    def __init__(self, cfg=None):
        super().__init__()
        from .. import npfields
        self.cfg, self._nf = cfg, npfields
        self._t = None
        self._ctx: dict = {}
        self._stream = ""
        self.art = ArtLabel()
        self.art.clicked.connect(lambda: self._t and self.navigate.emit("album", self._t))
        self.info = QLabel(); self.info.setWordWrap(True); self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.info.linkActivated.connect(lambda kind: self._t and self.navigate.emit(kind, self._t))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        lay = QVBoxLayout(self); lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.art, 1); lay.addWidget(self.info)
        self.render()

    def fields(self):
        return list(self.cfg.np_fields) if self.cfg else list(self._nf.DEFAULT_FIELDS)

    def labels(self):
        return bool(self.cfg and self.cfg.np_labels)

    def set_track(self, t, ctx=None):
        self._t, self._stream = t, ""
        self._ctx = ctx or {}
        if not t:
            self.art.set_art(None)
        self.art.setCursor(Qt.CursorShape.PointingHandCursor if t else Qt.CursorShape.ArrowCursor)
        self.art.setToolTip("Show this album in the library" if t else "")
        self.render()

    def set_context(self, ctx):
        self._ctx = ctx or {}
        self.render()

    def set_stream_title(self, title):
        self._stream = title or ""
        self.render()

    def render(self):
        if not self._t:
            self.info.setText("<div style='font-size:large;font-weight:bold'>Nothing playing</div>"); return
        link = self.palette().link().color().name()
        self.info.setText(self._nf.html(self.fields(), self._t, self._ctx, self.labels(), link, lead=self._stream or None))

    def plain_text(self) -> str:
        from PyQt6.QtGui import QTextDocumentFragment
        return QTextDocumentFragment.fromHtml(self.info.text()).toPlainText()

    def _menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("Configure Now Playing…", self.configureRequested.emit)
        m.exec(self.mapToGlobal(pos))

    def changeEvent(self, e):
        if e.type() == QEvent.Type.PaletteChange:
            self.render()                                      # link colour follows the skin
        super().changeEvent(e)


class UpNextPanel(QWidget):
    jumpRequested = pyqtSignal(int)
    removeRequested = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.offset = 0
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda it: self.jumpRequested.emit(self.offset + self.list.row(it)))
        self.hint = QLabel("Double-click to jump · Delete removes"); self.hint.setStyleSheet("opacity:.6")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list); lay.addWidget(self.hint)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete:
            self.removeRequested.emit([self.offset + self.list.row(i) for i in self.list.selectedItems()])
        else:
            super().keyPressEvent(e)

    WINDOW_BEFORE, WINDOW_AFTER = 30, 570

    def refresh(self, queue, index):
        """Show a window of the queue around the current track: a 60,000-track queue must not mean 60,000
        list items rebuilt on every track change."""
        self.offset = start = max(0, index - self.WINDOW_BEFORE)
        end = min(len(queue), start + self.WINDOW_BEFORE + self.WINDOW_AFTER)
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for i in range(start, end):
            t = queue[i]
            it = QListWidgetItem(f'{"▶ " if i == index else ""}{t["artist"]} – {t["title"]}   {fmt_time(t["duration"])}')
            if i == index:
                f = it.font(); f.setBold(True); it.setFont(f)
            self.list.addItem(it)
        if len(queue) > end:
            more = QListWidgetItem(f"… and {len(queue) - end} more"); more.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(more)
        self.list.setUpdatesEnabled(True)
        if 0 <= index - start < self.list.count():
            self.list.scrollToItem(self.list.item(index - start))


class LyricsPanel(QWidget):
    seekRequested = pyqtSignal(int)         # ms: a synced line was double-clicked
    searchOnline = pyqtSignal()             # the user asked to look these lyrics up on the internet

    def __init__(self):
        super().__init__()
        self.head = QLabel(""); f = self.head.font(); f.setBold(True); self.head.setFont(f)
        self.body = QPlainTextEdit(); self.body.setReadOnly(True)
        self.lines = QListWidget(); self.lines.setWordWrap(True)
        self.lines.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lines.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lines.itemDoubleClicked.connect(lambda it: self.seekRequested.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.stack = QStackedWidget(); self.stack.addWidget(self.body); self.stack.addWidget(self.lines)
        self.times: list[int] = []
        self._cur = -1
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.body.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.lines.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.head); lay.addWidget(self.stack)

    def _menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("Search for these lyrics online (LRCLIB)", self.searchOnline.emit)
        m.exec(self.mapToGlobal(pos))

    @property
    def synced(self) -> bool:
        return self.stack.currentWidget() is self.lines

    def show_lyrics(self, title, text):
        from ..lyrics import is_synced, parse_lrc
        self.head.setText(title)
        self.times, self._cur = [], -1
        self.lines.clear()
        if text and is_synced(text):
            for ms, ln in parse_lrc(text):
                it = QListWidgetItem(ln or "♪"); it.setData(Qt.ItemDataRole.UserRole, ms)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.lines.addItem(it); self.times.append(ms)
            self.stack.setCurrentWidget(self.lines)
            return
        self.body.setPlainText(text or "No lyrics found.")
        self.stack.setCurrentWidget(self.body)

    def set_position(self, ms):
        """Highlight and scroll to the line being sung."""
        if not self.times:
            return
        i = bisect.bisect_right(self.times, ms) - 1
        if i == self._cur:
            return
        if self._cur >= 0:
            self._style(self._cur, False)
        self._cur = i
        if i >= 0:
            self._style(i, True)
            self.lines.scrollToItem(self.lines.item(i), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _style(self, row, on):
        it = self.lines.item(row)
        f = it.font(); f.setBold(on); it.setFont(f)
        it.setForeground(self.palette().highlight() if on else self.palette().text())


def _size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def _date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


class TrackInfoPanel(QScrollArea):
    def __init__(self):
        super().__init__()
        self.lbl = QLabel("Select a track"); self.lbl.setWordWrap(True)
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignTop); self.lbl.setTextFormat(Qt.TextFormat.RichText)
        self.lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setWidget(self.lbl); self.setWidgetResizable(True)

    def set_tracks(self, tracks):
        if not tracks:
            self.lbl.setText("Select a track"); return
        if len(tracks) > 1:
            self.lbl.setText(f"<b>{len(tracks)} tracks</b><br>Total time: {fmt_time(sum(t['duration'] for t in tracks))}"
                             f"<br>Total size: {_size(sum(t['size'] or 0 for t in tracks))}")
            return
        t = tracks[0]
        rows = [("Title", t["title"]), ("Artist", t["artist"]), ("Album artist", t["album_artist"]),
                ("Album", t["album"]), ("Genre", t["genre"] or "—"), ("Year", t["year"] or "—"),
                ("Track / Disc", f'{t["track_no"] or "—"} / {t["disc_no"] or "—"}'),
                ("Composer", t.get("composer") or "—"), ("BPM", t.get("bpm") or "—"),
                ("Comment", t.get("comment") or "—"),
                ("Length", fmt_time(t["duration"])), ("Format", f'{(t["suffix"] or "").upper()} {t["bitrate"] or ""} kbps'),
                ("Size", _size(t["size"] or 0)), ("Rating", "★" * (t["rating"] or 0) or "—"),
                ("Plays", t["play_count"]), ("Last played", _date(t["last_played"])),
                ("Skips", t.get("skip_count") or 0),
                ("Added", _date(t["date_added"])),
                ("ReplayGain", f'{t["rg_gain"]:+.2f} dB' if t.get("rg_gain") is not None else "—"),
                ("Source", "Navidrome" if t["source"] == "remote" else "Local file"),
                ("Path", t["path"] or f'server id {t["remote_id"]}')]
        from html import escape
        self.lbl.setText("<table cellpadding=2>" + "".join(
            f"<tr><td><i>{k}</i>&nbsp;&nbsp;</td><td>{escape(str(v))}</td></tr>" for k, v in rows) + "</table>")
