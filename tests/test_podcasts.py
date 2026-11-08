"""Podcasts: parsing feeds, subscribing, refreshing, downloading, episodes as tracks, the window."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PyQt6.QtCore import QUrl

from hivefi import podcasts
from hivefi.db import Database

FEED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
 <title>Tech Talk</title><link>http://site.example</link><itunes:image href="http://img.example/c.jpg"/>
 <item><title>Episode 2</title><guid>g2</guid><pubDate>Wed, 02 Oct 2024 10:00:00 +0000</pubDate>
  <enclosure url="{base}/audio/ep2.mp3" length="1234" type="audio/mpeg"/><itunes:duration>01:02:03</itunes:duration>
  <description>&lt;p&gt;Second &lt;b&gt;show&lt;/b&gt;&lt;/p&gt;</description></item>
 <item><title>Episode 1</title><pubDate>Tue, 01 Oct 2024 10:00:00 +0000</pubDate>
  <enclosure url="{base}/audio/ep1.mp3?token=1" length="x"/><itunes:duration>754</itunes:duration></item>
 <item><title>A blog post</title><guid>post</guid></item>
</channel></rss>"""


class Server:
    def __init__(self):
        self.feed_extra = ""
        self.hits = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path.startswith("/feed"):
                    data = FEED.replace("{base}", outer.url).replace("</channel>", outer.feed_extra + "</channel>").encode()
                    ct = "application/rss+xml"
                elif self.path.startswith("/audio/ep1"):
                    data, ct = b"EPISODE-ONE-BYTES", "audio/mpeg"
                elif self.path.startswith("/audio/ep2"):
                    data, ct = b"<html>login</html>", "text/html"
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header("content-type", ct); self.send_header("content-length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    s = Server(); yield s; s.srv.shutdown()


def test_parsing():
    assert podcasts.parse_duration("01:02:03") == 3723 and podcasts.parse_duration("12:34") == 754
    assert podcasts.parse_duration("754") == 754 and podcasts.parse_duration("") == 0 and podcasts.parse_duration("x:y") == 0
    assert podcasts.parse_date("Wed, 02 Oct 2024 10:00:00 +0000") > 1.7e9 and podcasts.parse_date("garbage") == 0
    info, eps = podcasts.parse_feed(FEED.replace("{base}", "http://h").encode())
    assert info == {"title": "Tech Talk", "link": "http://site.example", "image": "http://img.example/c.jpg"}
    assert [e["title"] for e in eps] == ["Episode 2", "Episode 1"]                       # newest first, posts without audio dropped
    assert eps[0]["duration"] == 3723 and eps[0]["size"] == 1234 and eps[0]["description"] == "Second show"
    assert eps[1]["guid"] == "http://h/audio/ep1.mp3?token=1" and eps[1]["size"] == 0     # no guid: the audio URL stands in
    with pytest.raises(ValueError):
        podcasts.parse_feed(b"<html><body>no</body></html>")
    with pytest.raises(ValueError):
        podcasts.parse_feed(b"not xml at all")


def test_subscribe_refresh_download(tmp_path, server):
    db = Database(tmp_path / "l.db")
    url = server.url + "/feed.xml"
    pid = podcasts.subscribe(db, url)
    assert db.podcast(pid)["title"] == "Tech Talk" and [e["title"] for e in db.episodes(pid)] == ["Episode 2", "Episode 1"]
    assert podcasts.subscribe(db, url) == pid and len(db.podcasts()) == 1 and len(db.episodes()) == 2   # again: no duplicates
    assert podcasts.refresh(db, pid) == []                                                          # nothing new
    server.feed_extra = ('<item><title>Episode 3</title><guid>g3</guid><pubDate>Thu, 03 Oct 2024 10:00:00 +0000</pubDate>'
                         f'<enclosure url="{server.url}/audio/ep3.mp3" length="9"/></item>')
    new = podcasts.refresh(db, pid)
    assert [e["title"] for e in new] == ["Episode 3"] and db.episodes(pid)[0]["title"] == "Episode 3"
    # downloads
    ep1 = next(e for e in db.episodes(pid) if e["title"] == "Episode 1")
    msgs = []
    path = podcasts.download(db, ep1["id"], str(tmp_path / "pod"), msgs.append)
    assert path == str(tmp_path / "pod/Tech Talk/Episode 1.mp3") and open(path, "rb").read() == b"EPISODE-ONE-BYTES"
    assert db.episode(ep1["id"])["file"] == path and msgs and not list((tmp_path / "pod").rglob("*.part"))
    ep2 = next(e for e in db.episodes(pid) if e["title"] == "Episode 2")
    with pytest.raises(RuntimeError, match="audio"):
        podcasts.download(db, ep2["id"], str(tmp_path / "pod"))                                      # an HTML page is not audio
    assert not list((tmp_path / "pod").rglob("Episode 2*"))
    # played / unplayed and deleting the podcast removes its episodes
    db.set_episode_played(ep1["id"]); assert [e["title"] for e in db.episodes(pid, unplayed=True)] == ["Episode 3", "Episode 2"]
    db.set_podcast_auto(pid, True); assert db.podcast(pid)["auto_download"] == 1
    db.delete_podcast(pid); assert db.podcasts() == [] and db.episodes() == []


def test_episode_as_a_track(tmp_path):
    ep = dict(id=7, title="Ep", url="http://x/a.mp3", duration=600, size=5, published=1_700_000_000, played=0, file="", description="d")
    pod = dict(id=2, title="Show")
    t = podcasts.episode_track(ep, pod)
    assert t["id"] == -1_000_007 and t["source"] == "podcast" and t["artist"] == "Show" and t["path"] is None and not t["downloaded"]
    assert podcasts.episode_id(t) == 7 and podcasts.episode_id({"source": "local"}) is None
    f = tmp_path / "a.mp3"; f.write_bytes(b"x")
    t = podcasts.episode_track(dict(ep, file=str(f), played=1), pod)
    assert t["path"] == str(f) and t["downloaded"] and t["play_count"] == 1 and t["suffix"] == "mp3"
    assert podcasts.episode_track(dict(ep, file="/gone.mp3"), pod)["path"] is None                       # file deleted since
    assert podcasts.safe_name('a/b:c?"d"') == "a_b_c__d_" and podcasts.safe_name("...") == "episode"


from tests.test_gui import win, wait  # noqa: E402,F401  (fixture)


def test_podcasts_in_the_window(win, monkeypatch, tmp_path, server):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox
    w, db = win, win.db
    w.cfg.podcast_dir = str(tmp_path / "pods")
    assert ("podcast", None) in w._items
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (server.url + "/feed.xml", True))
    w._add_podcast(); wait(1000)
    pid = db.podcasts()[0]["id"]
    assert w.source == ("podcast", pid) and ("podcast", pid) in w._items
    assert [t["title"] for t in w.model.tracks] == ["Episode 2", "Episode 1"] and w.model.sort_col is None
    t1 = next(t for t in w.model.tracks if t["title"] == "Episode 1")
    assert w._resolve(t1) == QUrl(server.url + "/audio/ep1.mp3?token=1")                      # streamed until it is downloaded
    assert w.model.data(w.model.index(0, 3)) == "Tech Talk" and w.model.data(w.model.index(0, 14)) == "STREAM"
    # download, then it plays from the file
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    w._download_episodes([t1]); wait(1200)
    t1 = next(t for t in w.model.tracks if t["title"] == "Episode 1")
    assert t1["downloaded"] and w._resolve(t1) == QUrl.fromLocalFile(t1["path"]) and os.path.exists(t1["path"])
    # played / unplayed
    w._on_played(t1); wait(100)
    assert db.episode(t1["episode_id"])["played"] == 1
    w._mark_episodes([t1], False); assert db.episode(t1["episode_id"])["played"] == 0
    w._mark_episodes([t1], True); assert db.episode(t1["episode_id"])["played"] == 1
    assert next(t for t in w.model.tracks if t["title"] == "Episode 1")["play_count"] == 1
    # deleting the file brings back streaming; podcast episodes never reach ratings, scrobbles or Auto-DJ
    w._delete_episode_files([t1]); t1 = next(t for t in w.model.tracks if t["title"] == "Episode 1")
    assert not t1["downloaded"] and not os.path.exists(str(tmp_path / "pods/Tech Talk/Episode 1.mp3"))
    w.set_rating([t1], 5); w.set_star([t1], True)
    assert all(t["source"] != "podcast" for t in w._tracks())
    # new episodes arrive on refresh (and are downloaded when the podcast asks for it)
    server.feed_extra = ('<item><title>Episode 3</title><guid>g3</guid><pubDate>Thu, 03 Oct 2024 10:00:00 +0000</pubDate>'
                         f'<enclosure url="{server.url}/audio/ep1.mp3" length="9"/></item>')
    db.set_podcast_auto(pid, True)
    w._refresh_podcasts(quiet=True); wait(1500)
    assert w.model.tracks[0]["title"] == "Episode 3" and w.model.tracks[0]["downloaded"]
    assert "new podcast episode" in w.statusBar().currentMessage()
    # unsubscribe
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w._unsubscribe(pid); wait(100)
    assert db.podcasts() == [] and w.source == ("podcast", None) and w.model.rowCount() == 0
    # a bad address is reported, not raised
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("http://127.0.0.1:9/nope", True))
    w._add_podcast(); wait(1500)
    assert "Could not add the podcast" in w.statusBar().currentMessage() and db.podcasts() == []
