"""ListenBrainz: queueing, sending, retrying, and the window hooks."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hivefi import listenbrainz as lb, sync
from hivefi.db import Database
from tests.test_gui import win, wait  # noqa: F401  (fixture)


class Server:
    def __init__(self, status=200):
        self.status, self.posts, self.valid = status, [], True
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code); self.send_header("content-type", "application/json")
                self.end_headers(); self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.posts.append((self.headers.get("Authorization"), body))
                self._send(outer.status, {"status": "ok"} if outer.status == 200 else {"error": "x"})

            def do_GET(self):
                outer.posts.append((self.headers.get("Authorization"), self.path))
                self._send(200, {"valid": outer.valid, "user_name": "richard", "message": "bad token"})
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def stop(self): self.srv.shutdown()


@pytest.fixture
def server():
    s = Server(); yield s; s.stop()


T = dict(id=1, source="local", title="Song", artist="Band", album="Album", duration=200.5)


def test_metadata_and_queue(tmp_path):
    m = lb.metadata(T)
    assert m["artist_name"] == "Band" and m["track_name"] == "Song" and m["release_name"] == "Album"
    assert m["additional_info"]["duration_ms"] == 200500 and m["additional_info"]["media_player"] == "Hivefi"
    db = Database(tmp_path / "l.db")
    assert lb.queue_listen(db, T, 1000) and not lb.queue_listen(db, dict(T, source="radio"))
    assert not lb.queue_listen(db, dict(T, title="")) and not lb.queue_listen(db, dict(T, artist=""))
    ops = db.pending_ops()
    assert len(ops) == 1 and ops[0][1] == "listenbrainz" and ops[0][2]["listened_at"] == 1000


def test_flush_sends_and_clears(tmp_path, server):
    db = Database(tmp_path / "l.db")
    for i in range(3):
        lb.queue_listen(db, dict(T, title=f"S{i}"), 1000 + i)
    assert lb.flush(db, "tok123", server.url) == 3
    auth, body = server.posts[0]
    assert auth == "Token tok123" and body["listen_type"] == "import" and len(body["payload"]) == 3
    assert [p["listened_at"] for p in body["payload"]] == [1000, 1001, 1002]
    assert db.pending_ops() == []
    lb.queue_listen(db, T, 2000); lb.flush(db, "tok123", server.url)
    assert server.posts[-1][1]["listen_type"] == "single"
    assert lb.flush(db, "tok123", server.url) == 0                          # nothing queued: no request
    n = len(server.posts); lb.flush(db, "tok123", server.url); assert len(server.posts) == n


def test_flush_keeps_listens_when_offline_or_throttled(tmp_path, server):
    db = Database(tmp_path / "l.db")
    lb.queue_listen(db, T, 1)
    assert lb.flush(db, "t", "http://127.0.0.1:9") == 0 and len(db.pending_ops()) == 1       # offline
    server.status = 429
    assert lb.flush(db, "t", server.url) == 0 and len(db.pending_ops()) == 1                   # rate limited
    server.status = 401
    assert lb.flush(db, "t", server.url) == 0 and len(db.pending_ops()) == 1                   # bad token: keep them
    server.status = 400
    assert lb.flush(db, "t", server.url) == 0 and db.pending_ops() == []                       # malformed: dropped
    server.status = 200


def test_subsonic_flush_leaves_listenbrainz_ops_alone(tmp_path):
    db = Database(tmp_path / "l.db")
    lb.queue_listen(db, T, 5)
    db.queue_op("star", {"id": "1", "on": True})
    class Api:
        def star(self, i): pass
    assert sync.flush_ops(db, Api()) == 1
    assert [op for _o, op, _p in db.pending_ops()] == ["listenbrainz"]


def test_validate_and_now_playing(server):
    assert lb.validate("abc", server.url) == "richard" and server.posts[-1][0] == "Token abc"
    server.valid = False
    with pytest.raises(ValueError, match="bad token"):
        lb.validate("abc", server.url)
    lb.now_playing("abc", T, server.url)
    body = server.posts[-1][1]
    assert body["listen_type"] == "playing_now" and "listened_at" not in body["payload"][0]


def test_window_scrobbles_plays_to_listenbrainz(win, server):
    w = win
    w.cfg.listenbrainz_token, w.cfg.listenbrainz_url = "secret", server.url
    t = w.db.all_tracks("local")[0]
    w._on_played(t); wait(800)
    posted = [b for a, b in server.posts if isinstance(b, dict) and b.get("listen_type") in ("single", "import")]
    assert posted and posted[0]["payload"][0]["track_metadata"]["track_name"] == t["title"]
    assert [op for _o, op, _p in w.db.pending_ops() if op == "listenbrainz"] == []
    # nothing is queued without a token, and radio plays are never scrobbled
    w.cfg.listenbrainz_token = ""
    n = len(w.db.pending_ops()); w._on_played(t); assert len(w.db.pending_ops()) == n
    w.cfg.listenbrainz_token = "secret"
    from hivefi import radio
    w._on_played(radio.radio_track({"id": 1, "name": "FM", "url": "http://x/s"}))
    assert [op for _o, op, _p in w.db.pending_ops() if op == "listenbrainz"] == []
    # the settings dialog keeps the token
    from hivefi.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(w.cfg); dlg.lb_token.setText("newtoken"); dlg.lb_url.setText("")
    dlg.apply()
    assert w.cfg.listenbrainz_token == "newtoken" and w.cfg.listenbrainz_url == "https://api.listenbrainz.org"
