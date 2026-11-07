import json
import time

from PyQt6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt
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


_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled


def _sort_key(col_key, playing_id):
    """Key function for sorting track dicts by a column (artist/album keep albums in track order)."""
    if col_key in ("artist", "album"):
        return lambda t: ((t[col_key] or "").lower(), (t["album"] or "").lower(), t["disc_no"] or 0, t["track_no"] or 0)
    if col_key == "_state":
        return lambda t: t["id"] == playing_id
    return lambda t: (lambda v: v.lower() if isinstance(v, str) else (v or 0))(t.get(col_key))


class TrackModel(QAbstractTableModel):
    """All tracks of the current source (`all`, kept in sort order) and the rows currently shown (`tracks`).

    Sorting and the Genre/Artist/Album/search filters are plain Python list operations on the whole library
    (a handful of milliseconds even for 100k tracks) instead of a QSortFilterProxyModel, whose per-row Python
    callbacks made every sort or keystroke take seconds."""
    playing_color = "#4fc3f7"

    def __init__(self):
        super().__init__()
        self.all: list[dict] = []
        self.tracks: list[dict] = []          # rows shown
        self.playing_id = None
        self.text = ""
        self.genre = self.artist = self.album = None
        self.ids: set | None = None
        self.sort_col: str | None = None
        self.sort_desc = False
        self._rows: dict | None = None        # track id -> row, built lazily
        self._base: list[dict] | None = None  # `all` narrowed by the search text only
        self._base_prev: list[dict] | None = None   # last non-empty search result (starting point for longer text)
        self._base_words: list[str] | None = None

    # ---- content
    def set_tracks(self, tracks, sort_col="__keep__", sort_desc=None):
        self.all = tracks
        self._base = self._base_prev = self._base_words = None
        if sort_col != "__keep__":
            self.sort_col = sort_col
        if sort_desc is not None:
            self.sort_desc = sort_desc
        self._resort_all()
        self._refilter()

    def _resort_all(self):
        if self.sort_col:
            self.all.sort(key=_sort_key(self.sort_col, self.playing_id), reverse=self.sort_desc)
            self._base = self._base_prev = self._base_words = None

    def sort_by(self, col_key, desc=False):
        self.sort_col, self.sort_desc = col_key, desc
        self._resort_all(); self._refilter()

    # ---- filters
    @staticmethod
    def _prime(rows):
        """Give every track its lowercase search string (once; the dicts are shared with the library cache)."""
        for t in rows:
            if "_hay" not in t:
                t["_hay"] = f'{t["title"]} {t["artist"]} {t["album"]} {t["genre"]}'.lower()

    def base(self):
        """`all` narrowed by the search text. Typing more characters narrows the previous result instead of
        rescanning the whole library."""
        if self._base is None:
            words = self.text.split()
            if not words:
                self._base = self.all
            else:
                src = self.all
                prev = self._base_words
                if self._base_prev is not None and prev and all(any(o in n for n in words) for o in prev):
                    src = self._base_prev                      # new matches are a subset of the old ones
                self._prime(src)
                rows = src
                for w in words:
                    rows = [t for t in rows if w in t["_hay"]]
                self._base = rows
                self._base_prev, self._base_words = rows, words
        return self._base

    def set_filters(self, text=None, genre="keep", artist="keep", album="keep", ids="keep"):
        if text is not None and text.lower() != self.text:
            self.text = text.lower(); self._base = None
            if not self.text.split():
                self._base_prev, self._base_words = None, None
        if genre != "keep": self.genre = genre
        if artist != "keep": self.artist = artist
        if album != "keep": self.album = album
        if ids != "keep":
            self.ids = ids
        elif text is not None or "keep" != genre or "keep" != artist or "keep" != album:
            self.ids = None                                  # any other filter replaces "this one album"
        self._refilter()

    @staticmethod
    def artist_of(t):
        """The album artist the browser groups by (see albums.annotate)."""
        return t.get("_aa") or t["album_artist"] or t["artist"]

    def narrowed(self, upto="album"):
        """Base list narrowed by the browser filters above `upto` ('genre' = none, 'artist' = genre, 'album' = both)."""
        rows = self.base()
        if upto != "genre" and self.genre:
            g = self.genre
            rows = [t for t in rows if t["genre"] == g]
        if upto == "album" and self.artist:
            a = self.artist
            rows = [t for t in rows if (t.get("_aa") or t["album_artist"] or t["artist"]) == a]
        return rows

    def _refilter(self):
        rows = self.narrowed("album")
        if self.album:
            al = self.album
            rows = [t for t in rows if t["album"] == al]
        if self.ids is not None:                              # one album picked in the Albums grid
            ids = self.ids
            rows = [t for t in rows if t["id"] in ids]
        self.beginResetModel()
        self.tracks = rows if rows is not self.all else list(rows)
        self._rows = None
        self.endResetModel()

    # ---- point updates
    def row_of(self, tid):
        if self._rows is None:
            self._rows = {t["id"]: r for r, t in enumerate(self.tracks)}
        return self._rows.get(tid)

    def set_playing(self, tid):
        old, self.playing_id = self.playing_id, tid
        for x in (old, tid):
            r = self.row_of(x) if x is not None else None
            if r is not None:
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    def update_track(self, tid, **fields):
        r = self.row_of(tid)
        if r is None:
            return
        self.tracks[r].update(fields)
        if any(k in fields for k in ("title", "artist", "album", "genre")):
            self.tracks[r].pop("_hay", None)
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    # ---- Qt model interface
    def flags(self, idx):
        return _FLAGS                    # called once per cell (select-all = hundreds of thousands of calls)

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
        if role == Qt.ItemDataRole.ForegroundRole and t["id"] == self.playing_id:
            return QBrush(QColor(self.playing_color))
        if role == Qt.ItemDataRole.TextAlignmentRole and key in RIGHT_ALIGNED:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
