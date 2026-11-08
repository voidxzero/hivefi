"""Tiny in-memory Subsonic server for tests."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import hashlib

SONGS = [{"id": str(i), "title": f"Song {i}", "artist": "Band", "album": "Album" + str(i % 2),
          "genre": "Rock", "year": 2000 + i, "track": i, "duration": 120 + i, "bitRate": 320,
          "suffix": "mp3", "size": 1000, "coverArt": "al" + str(i % 2),
          "albumId": "alb" + str(i % 2), "albumArtist": "Band",
          "path": f"Band/Album{i % 2}/{i:02d} Song {i}.mp3"} for i in range(1, 8)]


class State:
    def __init__(self, password="pw"):
        self.password = password
        self.songs = [dict(s) for s in SONGS]
        self.playlists = {"p1": {"id": "p1", "name": "Server list", "songIds": ["1", "2"]}}
        self.calls = []
        self.next_pl = 2
        self.radio = None                # e.g. [{'id': 'r1', 'name': 'FM', 'streamUrl': 'http://x/s'}]: enables radio
        self.folders = None              # e.g. [{'id': '1', 'name': 'Music'}, ...]: enables getMusicFolders


def make_server(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            u = urlparse(self.path); q = {k: v for k, v in parse_qs(u.query).items()}
            one = lambda k, d=None: q.get(k, [d])[0]
            ep = u.path.rsplit("/", 1)[-1].replace(".view", "")
            ok = hashlib.md5((state.password + (one("s") or "")).encode()).hexdigest() == one("t")
            def reply(extra=None, status="ok", err=None):
                body = {"status": status, "version": "1.16.1"}
                if err: body["error"] = err
                body.update(extra or {})
                data = json.dumps({"subsonic-response": body}).encode()
                self.send_response(200); self.send_header("content-type", "application/json")
                self.end_headers(); self.wfile.write(data)
            if not ok:
                return reply(status="failed", err={"code": 40, "message": "Wrong username or password"})
            state.calls.append((ep, {k: v for k, v in q.items() if k not in "utsvcf"}))
            if ep == "stream":
                data = b"FAKE-AUDIO-" + one("id", "").encode()
                self.send_response(200); self.send_header("content-length", str(len(data))); self.end_headers()
                self.wfile.write(data); return
            if ep == "ping": return reply()
            if ep == "getMusicFolders" and state.folders is not None:
                return reply({"musicFolders": {"musicFolder": state.folders}})
            if ep == "search3":
                off, n = int(one("songOffset", 0)), int(one("songCount", 20))
                songs = state.songs
                if one("musicFolderId") is not None:
                    songs = [s for s in songs if s.get("folder") == one("musicFolderId")]
                return reply({"searchResult3": {"song": songs[off:off + n]}})
            if ep == "getInternetRadioStations" and state.radio is not None:
                return reply({"internetRadioStations": {"internetRadioStation": state.radio}})
            if ep == "getPlaylists":
                return reply({"playlists": {"playlist": [{"id": p["id"], "name": p["name"]}
                                                          for p in state.playlists.values()]}})
            if ep == "getPlaylist":
                p = state.playlists[one("id")]
                ent = [s for i in p["songIds"] for s in state.songs if s["id"] == i]
                return reply({"playlist": {"id": p["id"], "name": p["name"], "entry": ent}})
            if ep == "createPlaylist":
                pid = one("playlistId")
                if not pid:
                    pid = f"p{state.next_pl}"; state.next_pl += 1
                    state.playlists[pid] = {"id": pid, "name": one("name")}
                state.playlists[pid]["songIds"] = q.get("songId", [])
                return reply({"playlist": {"id": pid, "name": state.playlists[pid]["name"]}})
            if ep == "updatePlaylist":
                if one("name"): state.playlists[one("playlistId")]["name"] = one("name")
                return reply()
            if ep == "deletePlaylist":
                state.playlists.pop(one("id"), None); return reply()
            if ep in ("scrobble", "star", "unstar", "setRating"): return reply()
            reply(status="failed", err={"code": 70, "message": "not found"})
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
