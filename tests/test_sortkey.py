"""Sorting that ignores 'The'."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import pytest

from hivefi import sortkey
from hivefi.ui.models import TrackModel
from tests.test_gui import win, wait  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def reset_articles():
    sortkey.set_articles(["the"])
    yield
    sortkey.set_articles(["the"])


def test_sort_name():
    n = sortkey.sort_name
    assert n("The Beatles") == "beatles" and n("the beatles") == "beatles" and n("THE WALL") == "wall"
    assert n("Thelonious Monk") == "thelonious monk" and n("The") == "the" and n("The ") == "the"          # only a whole word
    assert n('"Weird Al" Yankovic') == "weird al\" yankovic" and n("(Sandy) Alex G") == "sandy) alex g"      # quotes/brackets skipped
    assert n("") == "" and n(None) == ""
    sortkey.set_articles(["the", "a", "an", "die", " "])
    assert n("A Tribe Called Quest") == "tribe called quest" and n("An Horse") == "horse" and n("Die Ärzte") == "ärzte"
    assert n("Anna") == "anna" and sortkey.articles() == ("the", "a", "an", "die")
    sortkey.set_articles([])
    assert n("The Beatles") == "the beatles"                                                               # turned off


def T(i, artist, album="Alb", **kw):
    d = dict(id=i, title=f"t{i}", artist=artist, album_artist=artist, album=album, genre="g", disc_no=1, track_no=i,
             starred=0, duration=1)
    d.update(kw)
    return d


def test_columns_sort_without_articles():
    m = TrackModel()
    ts = [T(1, "The Beatles"), T(2, "Abba"), T(3, "Cream"), T(4, "The Alan Parsons Project"), T(5, "Zappa")]
    m.set_tracks(ts, sort_col="artist")
    assert [t["artist"] for t in m.tracks] == ["Abba", "The Alan Parsons Project", "The Beatles", "Cream", "Zappa"]
    m.sort_by("artist", desc=True)
    assert [t["artist"] for t in m.tracks][0] == "Zappa" and [t["artist"] for t in m.tracks][-1] == "Abba"
    m.sort_by("album_artist")
    assert [t["artist"] for t in m.tracks][2] == "The Beatles"
    m.set_tracks([T(1, "X", "The Wall"), T(2, "X", "Animals"), T(3, "X", "Meddle")], sort_col="album")
    assert [t["album"] for t in m.tracks] == ["Animals", "Meddle", "The Wall"]
    sortkey.set_articles([])
    m.sort_by("album"); assert [t["album"] for t in m.tracks] == ["Animals", "Meddle", "The Wall"]            # still fine with it off
    sortkey.set_articles([]); m.set_tracks([T(1, "X", "The Wall"), T(2, "X", "Zoo")], sort_col="album")
    assert [t["album"] for t in m.tracks] == ["The Wall", "Zoo"]


def test_browser_grid_and_preferences(win):
    from hivefi.ui.album_grid import AlbumGrid
    from hivefi.ui.settings_dialog import SettingsDialog
    w = win
    ts = [T(1, "The Beatles", "Abbey Road"), T(2, "Abba", "Arrival"), T(3, "Cream", "Disraeli Gears")]
    for t in ts:
        t["_aa"], t["_ak"] = t["artist"], f"{t['artist']}|{t['album']}"
        t["source"], t["path"] = "local", f"/x/{t['id']}.mp3"
    g = AlbumGrid(lambda: None); g.set_tracks(ts)
    assert [g.item(i).text().split("\n")[1] for i in range(g.count())] == ["Abba", "The Beatles", "Cream"]
    w.model.set_tracks(ts, sort_col="artist"); w._fill_browser("genre")
    artists = [w.lists["artist"].item(i).text() for i in range(1, w.lists["artist"].count())]
    assert artists == ["Abba", "The Beatles", "Cream"]
    # preference: stored, applied, and the views redraw
    dlg = SettingsDialog(w.cfg, w)
    assert dlg.articles.text() == "the"
    dlg.articles.setText("the, a ,, an"); dlg.apply()
    assert w.cfg.ignore_articles == ["the", "a", "an"]
    w.cfg.save()
    from hivefi.config import Config
    assert Config.load(w.cfg._path).ignore_articles == ["the", "a", "an"]
    assert Config().ignore_articles == ["the"]
