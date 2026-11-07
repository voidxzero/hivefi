"""Dockable panel widgets: Now Playing, Up Next, Lyrics, Track Info."""
import bisect
import time
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .models import fmt_time


class ArtLabel(QLabel):
    """Square-fitting cover art that rescales with its container."""
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
        s = max(32, min(self.width(), self.height()) - 4)
        self.setPixmap(self._pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))


class NowPlayingPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.art = ArtLabel()
        self.title = QLabel("Nothing playing"); self.title.setWordWrap(True)
        f = self.title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 1); self.title.setFont(f)
        self.sub = QLabel(""); self.sub.setWordWrap(True)
        lay = QVBoxLayout(self); lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.art, 1); lay.addWidget(self.title); lay.addWidget(self.sub)

    def set_track(self, t):
        if not t:
            self.title.setText("Nothing playing"); self.sub.setText(""); self.art.set_art(None); return
        self.title.setText(t["title"]); self.sub.setText(f'{t["artist"]}\n{t["album"]}')


class UpNextPanel(QWidget):
    jumpRequested = pyqtSignal(int)
    removeRequested = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda it: self.jumpRequested.emit(self.list.row(it)))
        self.hint = QLabel("Double-click to jump · Delete removes"); self.hint.setStyleSheet("opacity:.6")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list); lay.addWidget(self.hint)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete:
            self.removeRequested.emit([self.list.row(i) for i in self.list.selectedItems()])
        else:
            super().keyPressEvent(e)

    def refresh(self, queue, index):
        self.list.clear()
        for i, t in enumerate(queue):
            it = QListWidgetItem(f'{"▶ " if i == index else ""}{t["artist"]} – {t["title"]}   {fmt_time(t["duration"])}')
            if i == index:
                f = it.font(); f.setBold(True); it.setFont(f)
            self.list.addItem(it)
        if 0 <= index < self.list.count():
            self.list.scrollToItem(self.list.item(index))


class LyricsPanel(QWidget):
    seekRequested = pyqtSignal(int)         # ms: a synced line was double-clicked

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
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.head); lay.addWidget(self.stack)

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
                ("Length", fmt_time(t["duration"])), ("Format", f'{(t["suffix"] or "").upper()} {t["bitrate"] or ""} kbps'),
                ("Size", _size(t["size"] or 0)), ("Rating", "★" * (t["rating"] or 0) or "—"),
                ("Plays", t["play_count"]), ("Last played", _date(t["last_played"])),
                ("Added", _date(t["date_added"])),
                ("ReplayGain", f'{t["rg_gain"]:+.2f} dB' if t.get("rg_gain") is not None else "—"),
                ("Source", "Navidrome" if t["source"] == "remote" else "Local file"),
                ("Path", t["path"] or f'server id {t["remote_id"]}')]
        from html import escape
        self.lbl.setText("<table cellpadding=2>" + "".join(
            f"<tr><td><i>{k}</i>&nbsp;&nbsp;</td><td>{escape(str(v))}</td></tr>" for k, v in rows) + "</table>")
