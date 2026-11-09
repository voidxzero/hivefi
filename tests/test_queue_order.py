"""Queue order: shuffle rounds, Play Next / Queue Last in shuffle, removing and moving rows, Stop after Current."""
import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])   # must outlive every widget


@pytest.fixture
def player():
    from hivefi.config import Config
    from hivefi.player import Player
    p = Player(lambda t: QUrl(), Config())
    st = {"s": QMediaPlayer.PlaybackState.PlayingState, "pos": 0, "stopped": 0, "next": []}
    p.mp.playbackState = lambda: st["s"]; p.mp.position = lambda: st["pos"]; p.mp.duration = lambda: 100_000
    p.mp.setSource = lambda *a, **k: None; p.mp.play = lambda: None
    p.mp.stop = lambda: st.__setitem__("stopped", st["stopped"] + 1)
    p.mp.setPosition = lambda ms: st.__setitem__("pos", ms)
    p.mp.preload_paused = lambda *a: None; p.mp.set_next = lambda *a: st["next"].append(a[0])
    p._st = st
    return p


def tracks(n, base=0):
    return [dict(id=base + i, source="local", title=f"t{base + i}", artist="A", album="x", album_artist="A",
                 duration=100, path=f"/x{base + i}.mp3") for i in range(n)]


def ids(p, rows):
    return [p.queue[r]["id"] for r in rows]


def test_shuffle_repeat_all_never_replays_the_last_track(player):
    p = player
    for _ in range(30):
        p.shuffle, p.repeat = True, "all"
        p.play_list(tracks(4), 0)
        for _ in range(3):
            p.next()
        last = p.current["id"]
        peeked = p._next_idx                                 # what gapless was told comes next
        p.next()
        assert p.current["id"] != last                       # a new round starts with another track
        assert p.queue[peeked]["id"] == p.current["id"]      # and it is the one the look-ahead announced


def test_play_next_in_shuffle_really_plays_next(player):
    p = player; p.shuffle = True
    p.play_list(tracks(20), 5)
    before = ids(p, p.upcoming())
    p.enqueue(tracks(2, 100), next_=True)
    up = ids(p, p.upcoming())
    pos = up.index(p.current["id"])
    assert up[pos + 1:pos + 3] == [100, 101]
    assert [i for i in up if i < 100] == before              # nothing else was reshuffled
    p.next(); assert p.current["id"] == 100
    p.next(); assert p.current["id"] == 101


def test_queue_last_in_shuffle_keeps_the_lined_up_order(player):
    p = player; p.shuffle = True
    p.play_list(tracks(20), 0)
    before = ids(p, p.upcoming())
    p.enqueue(tracks(3, 100))
    up = ids(p, p.upcoming())
    assert [i for i in up if i < 100] == before
    assert sorted(i for i in up if i >= 100) == [100, 101, 102]
    assert up.index(100) > up.index(p.current["id"])         # never already behind us


def test_remove_keeps_the_shuffle_order(player):
    p = player; p.shuffle = True
    p.play_list(tracks(10), 3)
    before = ids(p, p.upcoming())
    gone = [r for r in range(10) if r != p.index][:3]
    gone_ids = ids(p, gone)
    p.remove(gone)
    assert ids(p, p.upcoming()) == [i for i in before if i not in gone_ids]
    assert p.current["id"] == 3


def test_move_rows(player):
    p = player
    p.play_list(tracks(6), 1)
    p.move([4, 5], 2)                                         # drag the last two right after the playing track
    assert [t["id"] for t in p.queue] == [0, 1, 4, 5, 2, 3] and p.current["id"] == 1
    p.next(); assert p.current["id"] == 4
    p.move([0], None)                                         # to the end
    assert [t["id"] for t in p.queue] == [1, 4, 5, 2, 3, 0] and p.current["id"] == 4
    p.shuffle = True; p.set_shuffle(True)
    up = p.upcoming(); target = up[-1]
    p.move([target], up[up.index(p.index) + 1])               # in shuffle the play order changes, not the queue
    assert [t["id"] for t in p.queue] == [1, 4, 5, 2, 3, 0]
    p.next(); assert p.index == target


def test_previous_on_the_first_track_restarts_it(player):
    p = player
    p.play_list(tracks(3), 0); p._st["pos"] = 1000
    p.previous()
    assert p.current["id"] == 0 and p._st["pos"] == 0


def test_stop_after_current(player):
    p = player
    p.play_list(tracks(3), 0)
    p.set_stop_after_current(True)
    assert p._st["next"][-1] is None                          # no gapless hand-over
    seen = []; p.stopAfterChanged.connect(seen.append)
    p._on_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert p._st["stopped"] == 1 and p.current["id"] == 0 and seen == [False] and not p.stop_after_current
    p.next(); assert p.current["id"] == 1


def test_resumed_track_still_scrobbles(player):
    p = player
    played = []; p.played.connect(played.append)
    p.restore(tracks(2), 0, position_ms=10_000)               # resumed at 10 s of 100 s: not counted yet
    p._on_pos(60_000)
    assert [t["id"] for t in played] == [0]
