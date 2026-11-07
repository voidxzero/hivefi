import json
import time

from PyQt6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt, QSortFilterProxyModel
from PyQt6.QtGui import QBrush, QColor

MIME = "application/x-hivefi-tracks"

COLUMNS = [  # (header, key, width)
    ("", "_state", 28), ("#", "track_no", 40), ("Title", "title", 280), ("Artist", "artist", 180),
    ("Album", "album", 200), ("Genre", "genre", 100), ("Year", "year", 50), ("Time", "duration", 60),
    ("Rating", "rating", 80), ("Plays", "play_count", 50), ("Source", "source", 70),
    ("Album Artist", "album_artist", 150), ("Disc", "disc_no", 40), ("Bitrate", "bitrate", 70),
    ("Format", "suffix", 55), ("Added", "date_added", 90), ("Last Played", "last_played", 100),
]
DEFAULT_HIDDEN = ("album_artist", "disc_no", "bitrate", "suffix", "date_added", "last_played")
RIGHT_ALIGNED = ("track_no", "year", "duration", "play_count", "disc_no", "bitrate")


def fmt_time(sec):
    sec = int(sec or 0)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TrackModel(QAbstractTableModel):
    playing_color = "#4fc3f7"

    def __init__(self):
        super().__init__()
        self.tracks: list[dict] = []
        self.playing_id = None

    def set_tracks(self, tracks):
        self.beginResetModel(); self.tracks = tracks; self.endResetModel()

    def set_playing(self, tid):
        self.playing_id = tid
        if self.tracks:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.tracks) - 1, 0))

    def update_track(self, tid, **fields):
        for r, t in enumerate(self.tracks):
            if t["id"] == tid:
                t.update(fields)
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    def flags(self, idx):
        return super().flags(idx) | Qt.ItemFlag.ItemIsDragEnabled

    def mimeTypes(self): return [MIME]

    def mimeData(self, indexes):
        ids = sorted({self.tracks[i.row()]["id"] for i in indexes})
        md = QMimeData(); md.setData(MIME, json.dumps(ids).encode()); return md

    def rowCount(self, p=QModelIndex()): return 0 if p.isValid() else len(self.tracks)
    def columnCount(self, p=QModelIndex()): return len(COLUMNS)

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if o == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[s][0]

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        t = self.tracks[idx.row()]
        key = COLUMNS[idx.column()][1]
        if role == Qt.ItemDataRole.DisplayRole:
            if key == "_state":
                return ("▶" if t["id"] == self.playing_id else "") + ("♥" if t["starred"] else "")
            v = t.get(key)
            if key == "duration": return fmt_time(v)
            if key == "rating": return "★" * (v or 0) + "☆" * (5 - (v or 0))
            if key in ("track_no", "year", "play_count", "disc_no"): return str(v) if v else ""
            if key == "bitrate": return f"{v} kbps" if v else ""
            if key == "suffix": return (v or "").upper()
            if key in ("date_added", "last_played"):
                return time.strftime("%Y-%m-%d", time.localtime(v)) if v else ""
            if key == "source": return "Navidrome" if v == "remote" else "Local"
            return v
        if role == Qt.ItemDataRole.UserRole:      # sort key
            v = t.get(key)
            if key == "_state": return t["id"] == self.playing_id
            return v.lower() if isinstance(v, str) else (v or 0)
        if role == Qt.ItemDataRole.ForegroundRole and t["id"] == self.playing_id:
            return QBrush(QColor(self.playing_color))
        if role == Qt.ItemDataRole.TextAlignmentRole and key in RIGHT_ALIGNED:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class TrackProxy(QSortFilterProxyModel):
    """Sorting + text search + Genre/Artist/Album browser filters."""
    def __init__(self):
        super().__init__()
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.text = ""
        self.genre = self.artist = self.album = None

    def set_filters(self, text=None, genre="keep", artist="keep", album="keep"):
        if text is not None: self.text = text.lower()
        if genre != "keep": self.genre = genre
        if artist != "keep": self.artist = artist
        if album != "keep": self.album = album
        self.invalidateFilter()

    def matches(self, t, ignore=()):
        if "genre" not in ignore and self.genre and t["genre"] != self.genre: return False
        if "artist" not in ignore and self.artist and (t["album_artist"] or t["artist"]) != self.artist: return False
        if "album" not in ignore and self.album and t["album"] != self.album: return False
        if self.text:
            hay = f'{t["title"]} {t["artist"]} {t["album"]} {t["genre"]}'.lower()
            return all(w in hay for w in self.text.split())
        return True

    def filterAcceptsRow(self, row, parent):
        return self.matches(self.sourceModel().tracks[row])

    def lessThan(self, a, b):
        m = self.sourceModel()
        ta, tb = m.tracks[a.row()], m.tracks[b.row()]
        if a.column() in (3, 4):  # artist/album: tie-break so albums stay in track order
            col = COLUMNS[a.column()][1]
            ka = (ta[col].lower(), ta["album"].lower(), ta["disc_no"], ta["track_no"])
            kb = (tb[col].lower(), tb["album"].lower(), tb["disc_no"], tb["track_no"])
            return ka < kb
        return super().lessThan(a, b)
