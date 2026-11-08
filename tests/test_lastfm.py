"""Last.fm against a stand-in server that verifies every signature."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from hivefi import lastfm, sync
from hivefi.db import Database
from tests.test_gui import win, wait  # noqa: F401  (fixture)

KEY, SECRET = "key123", "secret456"


class Stub:
    def __init__(self):
        self.calls, self.fail = [], {}
        self.authorised = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _handle(self, params, verb):
                p = {k: v[0] for k, v in params.items()}
                sig = p.pop("api_sig", "")
                fmt = p.pop("format", None)
                bad_sig = sig != lastfm.signature(p, SECRET)
                outer.calls.append((verb, p.get("method"), p, bad_sig))
                if bad_sig:
                    return self._reply({"error": 13, "message": "Invalid method signature supplied"})
                if p.get("api_key") != KEY:
                    return self._reply({"error": 10, "message": "Invalid API key"})
                m = p["method"]
                if m in outer.fail:
                    return self._reply({"error": outer.fail[m], "message": "boom"})
                if m == "auth.getToken":
                    return self._reply({"token": "tok1"})
                if m == "auth.getSession":
                    if not outer.authorised or p.get("token") != "tok1":
                        return self._reply({"error": 14, "message": "Unauthorized Token"})
                    return self._reply({"session": {"name": "richard", "key": "sess789"}})
                if p.get("sk") != "sess789":
                    return self._reply({"error": 9, "message": "Invalid session key"})
                self._reply({"scrobbles": {"@attr": {"accepted": 1, "ignored": 0}}} if m == "track.scrobble" else {})

            def _reply(self, body):
                data = json.dumps(body).encode()
                self.send_response(200); self.send_header("content-type", "application/json")
                self.end_headers(); self.wfile.write(data)

            def do_GET(self):
                self._handle(parse_qs(urlparse(self.path).query), "GET")

            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"])).decode()
                self._handle(parse_qs(body), "POST")
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/"

    def methods(self):
        return [m for _v, m, _p, _b in self.calls]


@pytest.fixture
def stub():
    s = Stub(); yield s; s.srv.shutdown()


T = dict(id=1, source="local", title="Song", artist="Band", album="Album", duration=200.4)


def test_signature():
    # the documented construction: sorted names+values, secret appended, md5; 'format' is not signed
    assert lastfm.signature({"b": "2", "a": "1"}, "s") == lastfm.hashlib.md5(b"a1b2s").hexdigest()
    assert lastfm.signature({"a": "1", "format": "json"}, "s") == lastfm.signature({"a": "1"}, "s")
    assert lastfm.signature({"artist": "Björk", "track": "Jóga"}, "s") == lastfm.hashlib.md5("artistBjörktrackJógas".encode()).hexdigest()


def test_authorisation_flow(stub):
    token = lastfm.get_token(KEY, SECRET, stub.url)
    assert token == "tok1" and "api_key=key123" in lastfm.auth_url(KEY, token) and "token=tok1" in lastfm.auth_url(KEY, token)
    with pytest.raises(lastfm.LastFmError) as e:
        lastfm.get_session(token, KEY, SECRET, stub.url)                       # the user has not allowed it yet
    assert e.value.code == 14
    stub.authorised = True
    assert lastfm.get_session(token, KEY, SECRET, stub.url) == ("sess789", "richard")
    assert not any(bad for *_x, bad in stub.calls)                              # every request was signed correctly
    with pytest.raises(lastfm.LastFmError) as e:
        lastfm.get_token("wrong", SECRET, stub.url)
    assert e.value.code == 10
    with pytest.raises(lastfm.LastFmError) as e:
        lastfm.get_token(KEY, "wrong-secret", stub.url)
    assert e.value.code == 13
    with pytest.raises(lastfm.LastFmError) as e:
        lastfm.get_token(KEY, SECRET, "http://127.0.0.1:9/")
    assert e.value.code == 11                                                    # unreachable


def test_now_playing_and_love(stub):
    lastfm.now_playing(T, KEY, SECRET, "sess789", stub.url)
    lastfm.love(T, KEY, SECRET, "sess789", True, stub.url)
    lastfm.love(T, KEY, SECRET, "sess789", False, stub.url)
    assert stub.methods() == ["track.updateNowPlaying", "track.love", "track.unlove"]
    _v, _m, p, _b = stub.calls[0]
    assert (p["artist"], p["track"], p["album"], p["duration"], p["sk"]) == ("Band", "Song", "Album", "200", "sess789")
    assert all(verb == "POST" for verb, *_x in stub.calls)
    with pytest.raises(lastfm.LastFmError) as e:
        lastfm.now_playing(T, KEY, SECRET, "stale", stub.url)
    assert e.value.code == 9


def test_queue_rules_and_batch_flush(tmp_path, stub):
    db = Database(tmp_path / "l.db")
    assert not lastfm.queue_listen(db, dict(T, duration=20)) and not lastfm.queue_listen(db, dict(T, source="radio"))
    assert not lastfm.queue_listen(db, dict(T, artist="")) and not lastfm.queue_listen(db, dict(T, title=""))
    for i in range(55):
        assert lastfm.queue_listen(db, dict(T, title=f"S{i}"), 1000 + i)
    assert lastfm.flush(db, KEY, SECRET, "sess789", stub.url) == 55
    scrobbles = [c for c in stub.calls if c[1] == "track.scrobble"]
    assert len(scrobbles) == 2 and not any(c[3] for c in scrobbles)               # two requests (50 + 5), signed
    first = scrobbles[0][2]
    assert first["track[0]"] == "S0" and first["timestamp[0]"] == "1000" and first["artist[49]"] == "Band"
    assert "track[50]" not in first and scrobbles[1][2]["track[4]"] == "S54"
    assert db.pending_ops() == []
    assert lastfm.flush(db, KEY, SECRET, "sess789", stub.url) == 0


def test_flush_keeps_or_drops_depending_on_the_error(tmp_path, stub):
    db = Database(tmp_path / "l.db")
    lastfm.queue_listen(db, T, 5)
    assert lastfm.flush(db, KEY, SECRET, "stale-session", stub.url) == 0 and len(db.pending_ops()) == 1      # 9: keep
    stub.fail["track.scrobble"] = 11
    assert lastfm.flush(db, KEY, SECRET, "sess789", stub.url) == 0 and len(db.pending_ops()) == 1            # 11: later
    stub.fail["track.scrobble"] = 29
    assert lastfm.flush(db, KEY, SECRET, "sess789", stub.url) == 0 and len(db.pending_ops()) == 1            # rate limit
    assert lastfm.flush(db, KEY, SECRET, "sess789", "http://127.0.0.1:9/") == 0 and len(db.pending_ops()) == 1  # offline
    stub.fail["track.scrobble"] = 6
    assert lastfm.flush(db, KEY, SECRET, "sess789", stub.url) == 0 and db.pending_ops() == []                # malformed: dropped
    # the other queues are not disturbed
    lastfm.queue_listen(db, T, 6); db.queue_op("star", {"id": "1", "on": True})
    stub.fail.clear()
    assert lastfm.flush(db, KEY, SECRET, "sess789", stub.url) == 1
    assert [op for _o, op, _p in db.pending_ops()] == ["star"]
    class Api:
        def star(self, i): pass
    assert sync.flush_ops(db, Api()) == 1


def test_window_scrobbles_loves_and_the_preferences_connect(win, stub, monkeypatch):
    from PyQt6.QtGui import QDesktopServices
    from hivefi.ui.settings_dialog import SettingsDialog
    w = win
    c = w.cfg
    assert not c.lastfm_connected
    # connect through Preferences: token, browser, session
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    c.lastfm_url = stub.url
    dlg = SettingsDialog(c, w)
    dlg._lf_step(); assert "first" in dlg.lf_status.text()                       # no key yet
    dlg.lf_key.setText(KEY); dlg.lf_secret.setText(SECRET)
    dlg._lf_step(); wait(600)
    assert opened and "token=tok1" in opened[0] and dlg._lf_token == "tok1" and "finish" in dlg.lf_connect.text()
    dlg._lf_step(); wait(600)                                                    # not allowed yet: reported, can retry
    assert "✘" in dlg.lf_status.text() and dlg.lf_connect.text() == "Connect"
    stub.authorised = True
    dlg._lf_step(); wait(600); dlg._lf_step(); wait(600)
    assert dlg.lf_session == "sess789" and "richard" in dlg.lf_status.text()
    dlg.lf_love.setChecked(True); dlg.apply()
    assert c.lastfm_connected and c.lastfm_user == "richard" and c.lastfm_love_stars
    # a counted play is scrobbled (queued, then sent), and "now playing" is announced
    t = dict(w.db.all_tracks("local")[0], duration=200.0)                       # the fixture's songs are 2 s: too short for Last.fm
    w._on_now_playing(t); wait(500)
    short = dict(t, duration=2.0); n0 = len([m for m in stub.methods() if m == "track.scrobble"])
    w._on_played(short); wait(500)
    assert len([m for m in stub.methods() if m == "track.scrobble"]) == n0        # under 30 s is never scrobbled
    w._on_played(t); wait(1000)
    assert "track.updateNowPlaying" in stub.methods() and "track.scrobble" in stub.methods()
    assert [op for _o, op, _p in w.db.pending_ops() if op == "lastfm"] == []
    # starring loves the song when asked; unstarring un-loves it
    n = len(stub.calls)
    w.set_star([t], True); wait(800); w.set_star([t], False); wait(800)
    assert stub.methods()[n:] == ["track.love", "track.unlove"]
    c.lastfm_love_stars = False; n = len(stub.calls)
    w.set_star([t], True); wait(500); assert len(stub.calls) == n
    w._lastfm_love([t], True); wait(800); assert stub.methods()[-1] == "track.love"      # the context menu works regardless
    assert "Loved on Last.fm" in w.statusBar().currentMessage()
    # disconnect
    dlg2 = SettingsDialog(c, w); dlg2._lf_disconnect(); dlg2.apply()
    assert not c.lastfm_connected and c.lastfm_user == ""
    n = len(stub.calls); w._on_played(t); wait(500); assert len(stub.calls) == n             # nothing is sent once disconnected
