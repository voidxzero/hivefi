"""Minimal Subsonic / OpenSubsonic (Navidrome) API client."""
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests

API_VERSION = "1.16.1"
CLIENT = "hivefi"


class SubsonicError(Exception):
    def __init__(self, msg, code=0):
        super().__init__(msg)
        self.code = code


class Subsonic:
    def __init__(self, url, user, password, timeout=30):
        self.base = url.rstrip("/")
        self.user, self.password, self.timeout = user, password, timeout
        self.http = requests.Session()
        self.http.headers["User-Agent"] = CLIENT

    def _auth(self):
        salt = secrets.token_hex(8)
        tok = hashlib.md5((self.password + salt).encode()).hexdigest()
        return {"u": self.user, "t": tok, "s": salt, "v": API_VERSION, "c": CLIENT, "f": "json"}

    def _params(self, extra=None):
        p = self._auth()
        for k, v in (extra or {}).items():
            if v is not None:
                p[k] = v
        return p

    def call(self, endpoint, **params):
        try:
            r = self.http.get(f"{self.base}/rest/{endpoint}.view", params=self._params(params),
                              timeout=self.timeout)
            r.raise_for_status()
            body = r.json()["subsonic-response"]
        except (requests.RequestException, ValueError, KeyError) as e:
            raise SubsonicError(f"{endpoint}: {e}") from e
        if body.get("status") != "ok":
            err = body.get("error", {})
            raise SubsonicError(err.get("message", "unknown error"), err.get("code", 0))
        return body

    def ping(self):
        return self.call("ping")

    # URLs usable directly by the media player / image loader
    def stream_url(self, song_id, max_bitrate=0):
        extra = {"id": song_id}
        if max_bitrate:
            extra["maxBitRate"] = max_bitrate
        else:
            extra["format"] = "raw"
        return f"{self.base}/rest/stream.view?{urlencode(self._params(extra))}"

    def cover_url(self, cover_id, size=0):
        extra = {"id": cover_id}
        if size:
            extra["size"] = size
        return f"{self.base}/rest/getCoverArt.view?{urlencode(self._params(extra))}"

    def get_cover(self, cover_id, size=0) -> bytes | None:
        try:
            r = self.http.get(self.cover_url(cover_id, size), timeout=self.timeout)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                return r.content
        except requests.RequestException:
            pass
        return None

    # --- library ---
    def iter_songs(self, page=500):
        """Yield every song. Fast path: search3 with empty query (Navidrome/OpenSubsonic);
        fall back to walking albums."""
        offset, got_any = 0, False
        while True:
            body = self.call("search3", query='""', artistCount=0, albumCount=0,
                             songCount=page, songOffset=offset)
            songs = _aslist(body.get("searchResult3", {}).get("song"))
            if not songs:
                break
            got_any = True
            yield from songs
            if len(songs) < page:
                return
            offset += page
        if got_any:
            return
        # fallback: albums
        offset = 0
        while True:
            body = self.call("getAlbumList2", type="alphabeticalByName", size=page, offset=offset)
            albums = _aslist(body.get("albumList2", {}).get("album"))
            if not albums:
                return
            for a in albums:
                yield from _aslist(self.call("getAlbum", id=a["id"]).get("album", {}).get("song"))
            offset += page

    def playlists(self):
        return _aslist(self.call("getPlaylists").get("playlists", {}).get("playlist"))

    def playlist(self, pid):
        return self.call("getPlaylist", id=pid).get("playlist", {})

    def create_playlist(self, name, song_ids):
        return self.call("createPlaylist", name=name, songId=list(song_ids)).get("playlist", {})

    def replace_playlist(self, pid, name, song_ids):
        self.call("createPlaylist", playlistId=pid, songId=list(song_ids))
        self.call("updatePlaylist", playlistId=pid, name=name)

    def delete_playlist(self, pid):
        self.call("deletePlaylist", id=pid)

    def star(self, song_id): self.call("star", id=song_id)
    def unstar(self, song_id): self.call("unstar", id=song_id)
    def set_rating(self, song_id, rating): self.call("setRating", id=song_id, rating=rating)

    def scrobble(self, song_id, time_ms=None, submission=True):
        self.call("scrobble", id=song_id, time=time_ms, submission=str(submission).lower())

    def lyrics(self, song_id, artist="", title="", synced=False):
        """Lyrics (LRC text if synced=True and the server has timed lines, else plain): OpenSubsonic getLyricsBySongId, then legacy getLyrics."""
        try:
            lists = self.call("getLyricsBySongId", id=song_id).get("lyricsList", {}).get("structuredLyrics")
            for sl in _aslist(lists):
                lines = _aslist(sl.get("line"))
                if synced and sl.get("synced") and any("start" in ln for ln in lines):
                    return "\n".join(f"[{int(ln.get('start', 0)) // 60000:02d}:"
                                      f"{(int(ln.get('start', 0)) % 60000) / 1000:05.2f}]{ln.get('value', '')}"
                                      for ln in lines)
                vals = [ln.get("value", "") for ln in lines]
                if any(vals):
                    return "\n".join(vals)
        except SubsonicError:
            pass
        try:
            return self.call("getLyrics", artist=artist, title=title).get("lyrics", {}).get("value", "") or ""
        except SubsonicError:
            return ""

    # requests encodes list params as repeated keys already (songId=1&songId=2)


def _aslist(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]
