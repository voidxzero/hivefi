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
CREATE INDEX IF NOT EXISTS ix_tracks_remote ON tracks(remote_id);
CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, remote_id TEXT,
  sync INTEGER DEFAULT 0, dirty INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS playlist_tracks(
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  pos INTEGER NOT NULL, track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  PRIMARY KEY(playlist_id, pos)
);
CREATE INDEX IF NOT EXISTS ix_pt_track ON playlist_tracks(track_id);
CREATE TABLE IF NOT EXISTS pending_ops(
  id INTEGER PRIMARY KEY, op TEXT NOT NULL, payload TEXT NOT NULL, created REAL
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS smart_playlists(id INTEGER PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL);
"""

TRACK_COLS = ("title artist album_artist album genre year track_no disc_no duration "
              "bitrate suffix size rating starred play_count last_played mtime cover_id rg_gain folder_id").split()


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        self._cache: dict = {}            # source|None -> shared list of track dicts (see all_tracks)
        self._cache_lock = threading.Lock()
        self._by_id: dict = {}
        self._by_id_for = None
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tracks)")}
        if "rg_gain" not in cols:   # migration: ReplayGain (dB); force local files to be re-read once
            self.conn.execute("ALTER TABLE tracks ADD COLUMN rg_gain REAL")
            self.conn.execute("UPDATE tracks SET mtime=0 WHERE source='local'")
            self.conn.commit()
        if "folder_id" not in cols:   # migration: which Subsonic music folder (library) a server track came from
            self.conn.execute("ALTER TABLE tracks ADD COLUMN folder_id TEXT")
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
        """Insert/update by key in one statement. Rows whose values did not change are not written at all, so
        the return value tells a sync whether anything really changed. Never clobbers local play stats unless supplied."""
        f = {k: v for k, v in f.items() if k in TRACK_COLS}
        cols = ["key", "source", "remote_id", "path", *f, "date_added"]
        vals = (key, source, remote_id, path, *f.values(), time.time())
        upd = ["remote_id", "path", *f]
        sql = (f"INSERT INTO tracks({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
               f"ON CONFLICT(key) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in upd)} "
               f"WHERE {' OR '.join(f'{c} IS NOT excluded.{c}' for c in upd)}")
        cur = self.conn.execute(sql, vals)
        self._mark_seen((key,))
        return cur.rowcount > 0                  # False when the row already had exactly these values

    # ---- "which rows still exist" passes (scan / sync): much cheaper than flagging every row twice -------
    def begin_pass(self):
        c = self.conn
        c.execute("CREATE TEMP TABLE IF NOT EXISTS pass_seen(key TEXT PRIMARY KEY) WITHOUT ROWID")
        c.execute("DELETE FROM pass_seen")
        self._local.in_pass = True

    def _mark_seen(self, keys):
        if getattr(self._local, "in_pass", False):
            self.conn.executemany("INSERT OR IGNORE INTO pass_seen VALUES(?)", [(k,) for k in keys])

    def touch(self, keys):
        """Keep these rows (they exist but were not re-read)."""
        self._mark_seen(keys)

    def end_pass(self, source) -> int:
        """Delete rows of `source` that were neither upserted nor touched since begin_pass()."""
        self._local.in_pass = False
        cur = self.conn.execute("DELETE FROM tracks WHERE source=? AND key NOT IN (SELECT key FROM pass_seen)",
                                (source,))
        self.conn.execute("DELETE FROM pass_seen")
        return cur.rowcount

    def abort_pass(self):
        self._local.in_pass = False

    def delete_tracks(self, ids):
        """Remove tracks from the library (files are never touched)."""
        ids = list(ids)
        self.conn.executemany("DELETE FROM playlist_tracks WHERE track_id=?", [(i,) for i in ids])
        self.conn.executemany("DELETE FROM tracks WHERE id=?", [(i,) for i in ids])
        self.commit()

    def commit(self):
        self.conn.commit()
        self.invalidate()

    def invalidate(self):
        with self._cache_lock:
            self._cache = {}

    def all_tracks(self, source=None):
        """Every track as a dict. The dicts are shared and cached until the next commit() (a 60k-track library
        would otherwise be re-read and re-converted on every refresh): the *list* is yours, the dicts are the
        library's. Point updates (rating/star/plays) patch the cached dicts in place."""
        with self._cache_lock:
            hit = self._cache.get(source)
        if hit is not None:
            return list(hit)
        if source is None:
            cur = self.conn.cursor(); cur.row_factory = None          # plain tuples + zip: ~25% faster than dict(Row)
            cur.execute("SELECT * FROM tracks")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur]
        else:
            rows = [t for t in self.all_tracks(None) if t["source"] == source]
        with self._cache_lock:
            return list(self._cache.setdefault(source, rows))

    def _patch(self, ids, **fields):
        with self._cache_lock:
            full = self._cache.get(None)
            if full is None:
                return
            if self._by_id_for is not full:
                self._by_id = {t["id"]: t for t in full}; self._by_id_for = full
            for i in ids:
                t = self._by_id.get(i)
                if t:
                    t.update(fields)

    def track(self, tid):
        r = self.conn.execute("SELECT * FROM tracks WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

    def track_by_remote(self, rid):
        r = self.conn.execute("SELECT * FROM tracks WHERE key=?", ("sub:" + str(rid),)).fetchone()
        return dict(r) if r else None

    def set_rating(self, tid, rating):
        self.set_ratings([tid], rating)

    def set_ratings(self, ids, rating):
        ids = list(ids)
        self.conn.executemany("UPDATE tracks SET rating=? WHERE id=?", [(rating, i) for i in ids])
        self.conn.commit(); self._patch(ids, rating=rating)

    def set_starred(self, tid, v):
        self.set_starred_many([tid], v)

    def set_starred_many(self, ids, v):
        ids = list(ids)
        self.conn.executemany("UPDATE tracks SET starred=? WHERE id=?", [(int(v), i) for i in ids])
        self.conn.commit(); self._patch(ids, starred=int(v))

    def bump_play(self, tid):
        now = time.time()
        self.conn.execute("UPDATE tracks SET play_count=play_count+1, last_played=? WHERE id=?", (now, tid))
        self.conn.commit()
        t = self.track(tid)
        if t:
            self._patch([tid], play_count=t["play_count"], last_played=now)

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
        """Tracks of a playlist in order (the same shared dicts all_tracks() returns)."""
        ids = [r[0] for r in self.conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY pos", (pid,))]
        if not ids:
            return []
        self.all_tracks(None)
        with self._cache_lock:
            full = self._cache.get(None)
            if full is not None:
                if self._by_id_for is not full:
                    self._by_id = {t["id"]: t for t in full}; self._by_id_for = full
                by_id = self._by_id
                return [by_id[i] for i in ids if i in by_id]
        return [t for t in (self.track(i) for i in ids) if t]

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
    def queue_op(self, op, payload, commit=True):
        self.conn.execute("INSERT INTO pending_ops(op,payload,created) VALUES(?,?,?)",
                          (op, json.dumps(payload), time.time()))
        if commit:
            self.conn.commit()

    def queue_ops(self, op, payloads):
        """Queue many operations of one kind in a single transaction."""
        now = time.time()
        self.conn.executemany("INSERT INTO pending_ops(op,payload,created) VALUES(?,?,?)",
                              [(op, json.dumps(p), now) for p in payloads])
        self.conn.commit()

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
