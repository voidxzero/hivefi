"""Online cover search against stand-ins for MusicBrainz and the Cover Art Archive."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from hivefi import art, coverart
from hivefi.db import Database

need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")


def png(color, tmp_path):
    f = tmp_path / f"{color}.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=16x16",
                    "-frames:v", "1", str(f)], check=True)
    return f.read_bytes()


class Stub:
    def __init__(self, images):
        self.queries, self.covers = [], []
        outer = self
        self.images = images                         # release id -> bytes (None = release without a cover)

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/ws/2/release/":
                    outer.queries.append(q["query"])
                    rels = []
                    if 'release:"Known Album"' in q["query"]:
                        rels = [{"id": "nocover", "score": 100}, {"id": "withcover", "score": 90}, {"id": "weak", "score": 10}]
                    if 'release:"Compilation"' in q["query"]:
                        assert "artist:" not in q["query"]                                 # Various Artists is not searched
                        rels = [{"id": "withcover", "score": 100}]
                    data = json.dumps({"releases": rels}).encode()
                    self.send_response(200); self.send_header("content-type", "application/json")
                    self.end_headers(); self.wfile.write(data)
                elif u.path.startswith("/release/"):
                    rid = u.path.split("/")[2]; outer.covers.append(rid)
                    img = outer.images.get(rid)
                    if img is None:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200); self.send_header("content-type", "image/png"); self.end_headers()
                    self.wfile.write(img)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


@need_ffmpeg
def test_find_cover_picks_the_first_release_that_has_one(tmp_path):
    red = png("red", tmp_path)
    s = Stub({"withcover": red}); coverart._last_mb = 0
    try:
        got = coverart.find_cover("The Artist", "Known Album", s.url, s.url, pause=0)
        assert got == (red, "withcover") and s.covers == ["nocover", "withcover"]         # skipped the one without art
        assert 'artist:"The Artist"' in s.queries[0]
        assert coverart.find_cover("A", "Unknown Album", s.url, s.url, pause=0) is None   # nothing to search for
        assert coverart.find_cover("A", "Nothing Like It", s.url, s.url, pause=0) is None
        assert coverart.find_cover("A", "Known Album", "http://127.0.0.1:9", "http://127.0.0.1:9", timeout=1, pause=0) is None
    finally:
        s.srv.shutdown()


@need_ffmpeg
def test_searches_are_spaced_out(tmp_path):
    import time
    s = Stub({}); coverart._last_mb = 0
    try:
        t0 = time.monotonic()
        coverart.find_cover("A", "x", s.url, s.url, pause=0.4)
        coverart.find_cover("A", "y", s.url, s.url, pause=0.4)
        assert time.monotonic() - t0 >= 0.35                                               # at most ~1 request per `pause`
    finally:
        s.srv.shutdown()


@need_ffmpeg
def test_find_covers_for_albums(tmp_path):
    from hivefi import scanner
    red = png("red", tmp_path)
    m = tmp_path / "music"; (m / "a").mkdir(parents=True); (m / "b").mkdir(); (m / "c").mkdir()
    def mk(path, **tags):
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1"]
        for k, v in tags.items():
            cmd += ["-metadata", f"{k}={v}"]
        subprocess.run(cmd + [str(path)], check=True)
    for i in range(2):
        mk(m / "a" / f"{i}.flac", title=f"t{i}", artist="The Artist", album="Known Album", album_artist="The Artist")
    for i, a in enumerate("XY"):
        mk(m / "b" / f"{i}.flac", title=f"c{i}", artist=a, album="Compilation")
    mk(m / "c" / "0.flac", title="n", artist="Z", album="Nowhere Land")
    mk(m / "c" / "1.flac", title="o", artist="Z", album="Has Art")
    from hivefi import tagedit
    tagedit.write_cover(str(m / "c" / "1.flac"), red)                                        # one already has a cover
    db = Database(tmp_path / "l.db"); scanner.scan(db, [str(m)])
    tracks = db.all_tracks("local")
    s = Stub({"withcover": red}); coverart._last_mb = 0
    try:
        progress = []
        done, missing, errors = coverart.find_covers(tracks, True, progress.append, s.url, s.url, pause=0)
        assert done == 2 and not errors and missing == ["Z – Nowhere Land"]
        for t in tracks:
            if t["album"] in ("Known Album", "Compilation"):
                assert art._embedded(t["path"])[:8] == b"\x89PNG\r\n\x1a\n", t["path"]         # every track of the album
        assert len(s.queries) == 3                                                           # "Has Art" was not even searched
        assert any("Known Album" in p for p in progress)
        # only_missing=False replaces covers that exist
        s.queries.clear()
        done, missing, errors = coverart.find_covers([t for t in tracks if t["album"] == "Known Album"], False, pause=0,
                                                     mb=s.url, caa=s.url)
        assert done == 1 and len(s.queries) == 1
        # remote tracks are ignored
        assert coverart.find_covers([dict(source="remote", album="Known Album", artist="A")], pause=0, mb=s.url, caa=s.url) == (0, [], [])
    finally:
        s.srv.shutdown()
