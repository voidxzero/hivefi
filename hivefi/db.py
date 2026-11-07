"""SQLite library: local + remote (Subsonic) tracks, playlists, offline op queue."""
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,           -- local: file path, remote: 'sub:<id>'
  source TEXT NOT NULL,               -- 'local' | 'remote'
  remote_id TEXT,
  path TEXT,
  title TEXT, artist TEXT, album_artist TEXT, album TEXT, genre TEXT,
  year INTEGER, track_no INTEGER, disc_no INTEGER,
  duration REAL DEFAULT 0, bitrate INTEGER DEFAULT 0, suffix TEXT, size INTEGER DEFAULT 0,
  rating INTEGER DEFAULT 0, starred INTEGER DEFAULT 0,
  play_count INTEGER DEFAULT 0, last_played REAL DEFAULT 0, date_added REAL DEFAULT 0,
  mtime REAL DEFAULT 0, cover_id TEXT, seen INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_tracks_source ON tracks(source);
CREATE INDEX IF NOT EXISTS ix_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS ix_tracks_album ON tracks(album);
CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, remote_id TEXT,
  sync INTEGER DEFAULT 0, dirty INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS playlist_tracks(
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  pos INTEGER NOT NULL, track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  PRIMARY KEY(playlist_id, pos)
);
CREATE TABLE IF NOT EXISTS pending_ops(
  id INTEGER PRIMARY KEY, op TEXT NOT NULL, payload TEXT NOT NULL, created REAL
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS smart_playlists(id INTEGER PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL);
"""

TRACK_COLS = ("title artist album_artist album genre year track_no disc_no duration "
              "bitrate suffix size rating starred play_count last_played mtime cover_id rg_gain").split()


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tracks)")}
        if "rg_gain" not in cols:   # migration: ReplayGain (dB); force local files to be re-read once
            self.conn.execute("ALTER TABLE tracks ADD COLUMN rg_gain REAL")
            self.conn.execute("UPDATE tracks SET mtime=0 WHERE source='local'")
            self.conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.c = c
        return c

    # ---- tracks -------------------------------------------------------
    def upsert_track(self, key, source, remote_id=None, path=None, **f):
        """Insert/update by key. Never clobbers local play stats unless supplied."""
        c = self.conn
        f = {k: v for k, v in f.items() if k in TRACK_COLS}
        row = c.execute("SELECT id FROM tracks WHERE key=?", (key,)).fetchone()
        if row:
            sets = ", ".join(f"{k}=?" for k in f) + (", " if f else "") + "seen=1, remote_id=?, path=?"
            c.execute(f"UPDATE tracks SET {sets} WHERE id=?", (*f.values(), remote_id, path, row["id"]))
            return row["id"]
        f.setdefault("date_added", time.time())
        cols = ["key", "source", "remote_id", "path", *f]
        cur = c.execute(f"INSERT INTO tracks({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                        (key, source, remote_id, path, *f.values()))
        return cur.lastrowid

    def mark_unseen(self, source):
        self.conn.execute("UPDATE tracks SET seen=0 WHERE source=?", (source,))

    def delete_unseen(self, source) -> int:
        cur = self.conn.execute("DELETE FROM tracks WHERE source=? AND seen=0", (source,))
        return cur.rowcount

    def delete_tracks(self, ids):
        """Remove tracks from the library (files are never touched)."""
        ids = list(ids)
        self.conn.executemany("DELETE FROM playlist_tracks WHERE track_id=?", [(i,) for i in ids])
        self.conn.executemany("DELETE FROM tracks WHERE id=?", [(i,) for i in ids])
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def all_tracks(self, source=None):
        q = "SELECT * FROM tracks"
        args = ()
        if source:
            q += " WHERE source=?"
            args = (source,)
        return [dict(r) for r in self.conn.execute(q, args)]

    def track(self, tid):
        r = self.conn.execute("SELECT * FROM tracks WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

    def track_by_remote(self, rid):
        r = self.conn.execute("SELECT * FROM tracks WHERE key=?", ("sub:" + str(rid),)).fetchone()
        return dict(r) if r else None

    def set_rating(self, tid, rating):
        self.conn.execute("UPDATE tracks SET rating=? WHERE id=?", (rating, tid)); self.commit()

    def set_starred(self, tid, v):
        self.conn.execute("UPDATE tracks SET starred=? WHERE id=?", (int(v), tid)); self.commit()

    def bump_play(self, tid):
        self.conn.execute("UPDATE tracks SET play_count=play_count+1, last_played=? WHERE id=?",
                          (time.time(), tid)); self.commit()

    # ---- playlists ----------------------------------------------------
    def playlists(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM playlists ORDER BY name COLLATE NOCASE")]

    def create_playlist(self, name, remote_id=None, sync=0, dirty=0):
        cur = self.conn.execute("INSERT INTO playlists(name,remote_id,sync,dirty) VALUES(?,?,?,?)",
                                (name, remote_id, sync, dirty))
        self.commit(); return cur.lastrowid

    def rename_playlist(self, pid, name):
        self.conn.execute("UPDATE playlists SET name=?, dirty=sync WHERE id=?", (name, pid)); self.commit()

    def delete_playlist(self, pid):
        row = self.conn.execute("SELECT remote_id FROM playlists WHERE id=?", (pid,)).fetchone()
        if row and row["remote_id"]:
            self.queue_op("delete_playlist", {"remote_id": row["remote_id"]})
        self.conn.execute("DELETE FROM playlists WHERE id=?", (pid,)); self.commit()

    def playlist_tracks(self, pid):
        return [dict(r) for r in self.conn.execute(
            "SELECT t.* FROM playlist_tracks p JOIN tracks t ON t.id=p.track_id "
            "WHERE p.playlist_id=? ORDER BY p.pos", (pid,))]

    def set_playlist_tracks(self, pid, track_ids, mark_dirty=True):
        c = self.conn
        c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        c.executemany("INSERT INTO playlist_tracks VALUES(?,?,?)",
                      [(pid, i, t) for i, t in enumerate(track_ids)])
        if mark_dirty:
            c.execute("UPDATE playlists SET dirty=sync WHERE id=?", (pid,))
        self.commit()

    def add_to_playlist(self, pid, track_ids):
        cur = [t["id"] for t in self.playlist_tracks(pid)]
        self.set_playlist_tracks(pid, cur + list(track_ids))

    # ---- smart playlists ----------------------------------------------
    def smart_playlists(self):
        return [{"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"])}
                for r in self.conn.execute("SELECT * FROM smart_playlists ORDER BY name COLLATE NOCASE")]

    def save_smart(self, name, spec, sid=None):
        if sid:
            self.conn.execute("UPDATE smart_playlists SET name=?, spec=? WHERE id=?", (name, json.dumps(spec), sid))
        else:
            sid = self.conn.execute("INSERT INTO smart_playlists(name,spec) VALUES(?,?)",
                                    (name, json.dumps(spec))).lastrowid
        self.commit(); return sid

    def delete_smart(self, sid):
        self.conn.execute("DELETE FROM smart_playlists WHERE id=?", (sid,)); self.commit()

    # ---- offline op queue --------------------------------------------
    def queue_op(self, op, payload):
        self.conn.execute("INSERT INTO pending_ops(op,payload,created) VALUES(?,?,?)",
                          (op, json.dumps(payload), time.time())); self.commit()

    def pending_ops(self):
        return [(r["id"], r["op"], json.loads(r["payload"]))
                for r in self.conn.execute("SELECT * FROM pending_ops ORDER BY id")]

    def drop_op(self, oid):
        self.conn.execute("DELETE FROM pending_ops WHERE id=?", (oid,)); self.commit()

    def get_meta(self, k, default=None):
        r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k, v):
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, str(v))); self.commit()
