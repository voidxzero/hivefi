"""Search syntax: words, phrases, field filters, numbers, exclusion."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from hivefi.searchquery import Query
from hivefi.ui.models import TrackModel


def T(i, **kw):
    base = dict(id=i, title=f"Song {i}", artist="Artist", album="Album", genre="Rock", year=2000, rating=0, play_count=0,
                suffix="mp3", source="local", path=f"/music/rock/{i}.mp3", album_artist="Artist", bitrate=320,
                track_no=i, disc_no=1)
    base.update(kw)
    return base


def hay(t):
    return f'{t["title"]} {t["artist"]} {t["album"]} {t["genre"]}'.lower()


def ok(text, t):
    return Query(text).match(t, hay(t))


def test_plain_words_and_phrases():
    t = T(1, title="Comfortably Numb", artist="Pink Floyd", album="The Wall")
    assert ok("pink wall", t) and ok("comfortably numb", t) and ok('"pink floyd"', t)
    assert not ok('"floyd pink"', t) and not ok("pink queen", t)
    assert Query("beatles abbey").is_plain and not Query("-live").is_plain and not Query("artist:x").is_plain
    assert not Query("") and not Query("   ")
    assert Query('"dark side" moon').words == ["dark side", "moon"]


def test_field_filters():
    t = T(1, title="Time", artist="Pink Floyd", album="Dark Side", genre="Prog Rock", year=1973, rating=5,
          play_count=12, suffix="flac", album_artist="Pink Floyd")
    assert ok("artist:pink", t) and ok("album:dark title:time", t) and ok("genre:prog", t) and ok("format:flac", t)
    assert not ok("artist:queen", t) and not ok("format:mp3", t)
    assert ok('album:"dark side"', t) and ok("albumartist:floyd", t) and ok("folder:/music/rock", t)
    assert ok("artist:pink floyd", t)                                             # fields and plain words combine
    assert ok("source:local", t) and not ok("source:remote", t)


def test_numbers():
    t = T(1, year=1985, rating=4, play_count=0, bitrate=256)
    assert ok("year:1985", t) and ok("year:>=1980", t) and ok("year:>1984", t) and not ok("year:>1985", t)
    assert ok("year:1980-1989", t) and not ok("year:1990-1999", t) and ok("year:<=1985", t) and ok("year:<1986", t)
    assert ok("rating:>=4", t) and not ok("rating:5", t) and ok("plays:0", t) and ok("bitrate:>=192", t)
    assert not ok("year:abc", t)                                                  # nonsense matches nothing, no crash


def test_exclusion_and_partial_input():
    t = T(1, title="Song (Live)", genre="Rock")
    assert not ok("-live", t) and ok("-studio", t) and not ok("-genre:rock", t) and ok("-genre:jazz", t)
    assert ok("song -studio", t) and not ok("song -live", t)
    assert Query("artist:").words == ["artist:"]                                  # typed so far: plain text, not a filter
    assert Query("nosuch:thing").words == ["nosuch:thing"]
    assert Query("-").words == []


def test_model_uses_the_syntax():
    m = TrackModel()
    ts = [T(1, artist="A", year=1990, rating=5), T(2, artist="B", year=2005, rating=2, title="Live Thing"),
          T(3, artist="A", year=2010, rating=4, genre="Jazz")]
    m.set_tracks(ts, sort_col="title")
    def ids(text):
        m.set_filters(text=text); return sorted(t["id"] for t in m.tracks)
    assert ids("artist:a") == [1, 3] and ids("year:>=2000") == [2, 3] and ids("rating:>=4") == [1, 3]
    assert ids("-live") == [1, 3] and ids("genre:jazz year:2010") == [3] and ids("artist:a -genre:jazz") == [1]
    assert ids("") == [1, 2, 3] and ids("live") == [2]
    assert ids("song") == [1, 3] and ids("song 1") == [1]                         # plain narrowing still works
    assert ids("artist:a song") == [1, 3] and ids("song") == [1, 3]               # back from a field query to plain words
    assert ids("thing") == [2] and ids("song") == [1, 3]                           # a different word starts over
