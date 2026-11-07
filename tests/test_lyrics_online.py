"""Online lyrics (LRCLIB-compatible service), caching and the sidecar file."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from hivefi import lyrics
from tests.test_gui import win, wait  # noqa: F401  (fixture)

LRC = "[00:01.00]hello\n[00:05.00]world"


class Stub:
    def __init__(self):
        self.hits, self.mode = [], "exact"
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.hits.append((u.path, q))
                code, body = 200, {}
                if u.path == "/api/get":
                    if outer.mode == "exact" and q.get("track_name") == "Known":
                        body = {"syncedLyrics": LRC, "plainLyrics": "hello\nworld"}
                    elif outer.mode == "plain":
                        body = {"syncedLyrics": None, "plainLyrics": "just words"}
                    else:
                        code, body = 404, {"message": "not found"}
                elif u.path == "/api/search":
                    body = [{"syncedLyrics": None, "plainLyrics": None},
                            {"syncedLyrics": "[00:02.00]found by search\n[00:04.00]x", "plainLyrics": "found by search"}] \
                        if q.get("track_name") == "Fuzzy" else []
                data = json.dumps(body).encode()
                self.send_response(code); self.send_header("content-type", "application/json")
                self.end_headers(); self.wfile.write(data)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


@pytest.fixture
def stub():
    s = Stub(); yield s; s.srv.shutdown()


def T(title, **kw):
    base = dict(source="local", artist="Band", title=title, album="Al", duration=200.4, path="/nonexistent/a.mp3")
    base.update(kw)
    return base


def test_exact_search_plain_and_unknown(stub):
    assert lyrics.fetch_online(T("Known"), stub.url) == LRC
    path, q = stub.hits[0]
    assert path == "/api/get" and q == {"artist_name": "Band", "track_name": "Known", "album_name": "Al", "duration": "200"}
    assert "found by search" in lyrics.fetch_online(T("Fuzzy"), stub.url)                  # 404 -> /api/search
    assert [p for p, _ in stub.hits][-2:] == ["/api/get", "/api/search"]
    assert lyrics.fetch_online(T("Nobody"), stub.url) == ""
    stub.mode = "plain"; assert lyrics.fetch_online(T("Known"), stub.url) == "just words"
    assert lyrics.fetch_online(dict(T("x"), artist=""), stub.url) == "" and lyrics.fetch_online(T("Known"), "http://127.0.0.1:9") is None


def test_cache_sidecar_and_offline(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    song = tmp_path / "s.flac"; song.write_bytes(b"")
    t = T("Known", path=str(song))
    assert lyrics.find_lyrics(t, online=False) == ""                                       # off by default: no request
    assert stub.hits == []
    assert lyrics.find_lyrics(t, online=True, base=stub.url, save_sidecar=True) == LRC
    assert (tmp_path / "s.lrc").read_text() == LRC and len(stub.hits) == 1
    assert lyrics.find_lyrics(dict(t, path=str(tmp_path / "other.flac")), online=True, base=stub.url) == LRC
    assert len(stub.hits) == 1                                                             # second lookup came from the cache
    (tmp_path / "s.lrc").write_text("mine")                                                # an existing file is never replaced
    lyrics.online_lyrics(t, stub.url, save_sidecar=True); assert (tmp_path / "s.lrc").read_text() == "mine"
    # lyrics that exist locally are used without asking the internet
    assert lyrics.find_lyrics(t, online=True, base="http://127.0.0.1:9") == "mine"
    # an unreachable service is not remembered as "no lyrics"
    t2 = T("Offline", path=str(tmp_path / "o.flac"))
    assert lyrics.find_lyrics(t2, online=True, base="http://127.0.0.1:9") == ""
    stub.mode = "plain"
    assert lyrics.find_lyrics(t2, online=True, base=stub.url, save_sidecar=True) == "just words"
    assert (tmp_path / "o.txt").read_text() == "just words"                                # plain text goes to .txt


def test_lyrics_panel_offers_an_online_search(win, monkeypatch, stub):
    w = win
    monkeypatch.setattr(lyrics, "LRCLIB", stub.url)
    monkeypatch.setattr(lyrics, "find_lyrics", lambda t, api=None, online=False, base=None, save_sidecar=False:
                        ("ONLINE" if online else ""))
    w.dock_lyrics.show(); w.player.play_list(w.db.all_tracks("local"), 0); wait(600)
    assert "No lyrics" in w.lyrics_panel.body.toPlainText()
    got = []
    w.lyrics_panel.searchOnline.connect(lambda: got.append(1))
    w.lyrics_panel.searchOnline.emit(); wait(600)
    assert got and w.lyrics_panel.body.toPlainText() == "ONLINE"
    w.cfg.lyrics_online = True; w._refresh_lyrics(); wait(600)                              # the Preferences option does it automatically
    assert w.lyrics_panel.body.toPlainText() == "ONLINE"
