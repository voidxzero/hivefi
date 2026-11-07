import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from hivefi.db import Database
from hivefi.subsonic import Subsonic, SubsonicError
from hivefi import sync
from tests.mock_server import State, make_server


@pytest.fixture
def env():
    st = State(); srv = make_server(st)
    api = Subsonic(f"http://127.0.0.1:{srv.server_port}", "u", "pw")
    db = Database(tempfile.mktemp(suffix=".db"))
    yield st, api, db
    srv.shutdown()


def test_bad_password(env):
    st, api, db = env
    with pytest.raises(SubsonicError):
        Subsonic(api.base, "u", "wrong").ping()


def test_pull_library_and_playlists(env):
    st, api, db = env
    r = sync.full_sync(db, api)
    assert r["songs"] == 7
    assert len(db.all_tracks("remote")) == 7
    pls = db.playlists()
    assert [p["name"] for p in pls] == ["Server list"] and pls[0]["sync"] == 1
    assert [t["title"] for t in db.playlist_tracks(pls[0]["id"])] == ["Song 1", "Song 2"]


def test_pagination_and_removal(env):
    st, api, db = env
    st.songs = st.songs * 1
    assert sum(1 for _ in api.iter_songs(page=3)) == 7
    sync.full_sync(db, api)
    st.songs = st.songs[:4]
    r = sync.full_sync(db, api)
    assert r["removed"] == 3 and len(db.all_tracks("remote")) == 4


def test_push_local_playlist_and_ops(env):
    st, api, db = env
    sync.full_sync(db, api)
    pid = db.create_playlist("Mine", sync=1, dirty=1)
    db.set_playlist_tracks(pid, [db.track_by_remote("3")["id"], db.track_by_remote("5")["id"]])
    db.queue_op("scrobble", {"id": "3", "time_ms": 1})
    db.queue_op("star", {"id": "3", "on": True})
    db.queue_op("rating", {"id": "3", "rating": 4})
    sync.full_sync(db, api)
    names = {p["name"]: p for p in st.playlists.values()}
    assert names["Mine"]["songIds"] == ["3", "5"]
    eps = [c[0] for c in st.calls]
    assert {"scrobble", "star", "setRating"} <= set(eps)
    assert db.pending_ops() == []
    # deleting locally deletes on server
    local = [p for p in db.playlists() if p["name"] == "Mine"][0]
    db.delete_playlist(local["id"]); sync.full_sync(db, api)
    assert "Mine" not in {p["name"] for p in st.playlists.values()}


def test_offline_ops_are_kept(env):
    st, api, db = env
    db.queue_op("scrobble", {"id": "1"})
    dead = Subsonic("http://127.0.0.1:1", "u", "pw", timeout=1)
    assert sync.flush_ops(db, dead) == 0 and len(db.pending_ops()) == 1
    assert sync.flush_ops(db, api) == 1
