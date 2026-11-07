import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pathlib import Path
from hivefi import smart, skins
from hivefi.db import Database

NOW = 1_000_000_000.0
T = [dict(id=1, title="A", artist="X", album="a", genre="Rock", year=1999, rating=5, play_count=10,
          date_added=NOW - 86400, last_played=NOW - 86400, starred=1, duration=100, source="local"),
     dict(id=2, title="B", artist="Y", album="b", genre="Jazz", year=2010, rating=2, play_count=0,
          date_added=NOW - 86400 * 90, last_played=0, starred=0, duration=200, source="remote"),
     dict(id=3, title="C", artist="X", album="c", genre="Rock", year=2005, rating=4, play_count=3,
          date_added=NOW - 86400 * 5, last_played=NOW - 86400 * 40, starred=0, duration=300, source="local")]


def ev(rules, **kw):
    return [t["id"] for t in smart.evaluate(T, {"rules": rules, **kw}, NOW)]


def test_rules():
    assert ev([{"field": "genre", "op": "is", "value": "rock"}], order="title") == [1, 3]
    assert ev([{"field": "year", "op": ">", "value": "2000"}], order="title") == [2, 3]
    assert ev([{"field": "last_played", "op": "is never"}]) == [2]
    assert ev([{"field": "last_played", "op": "not in the last (days)", "value": "30"}], order="title") == [2, 3]
    assert ev([{"field": "date_added", "op": "in the last (days)", "value": "7"}], order="title") == [1, 3]
    assert ev([{"field": "starred", "op": "is true"}]) == [1]
    assert ev([{"field": "artist", "op": "is", "value": "x"}, {"field": "rating", "op": ">=", "value": "5"}]) == [1]
    assert ev([{"field": "artist", "op": "is", "value": "y"}, {"field": "rating", "op": ">=", "value": "5"}],
              match="any", order="title") == [1, 2]


def test_order_limit_and_presets():
    assert ev([], order="play_count", desc=True, limit=2) == [1, 3]
    for spec in smart.PRESETS.values():
        smart.evaluate(T, spec, NOW)       # none may raise


def test_smart_db_roundtrip(tmp_path):
    db = Database(tmp_path / "d.db")
    sid = db.save_smart("Mine", {"rules": []}); db.save_smart("Mine2", {"rules": [1]}, sid)
    assert [p["name"] for p in db.smart_playlists()] == ["Mine2"]
    db.delete_smart(sid); assert db.smart_playlists() == []


def test_skins(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    (tmp_path / "ocean.json").write_text(json.dumps({"name": "Ocean", "base": "Nord", "accent": "#00ffcc",
                                                     "bogus": "x", "qss": "QLabel{color:red;}"}))
    (tmp_path / "bad.json").write_text("{nope")
    sk = skins.all_skins(tmp_path)
    assert "Ocean" in sk and sk["Ocean"]["accent"] == "#00ffcc" and sk["Ocean"]["window"] == skins.BUILTIN["Nord"]["window"]
    for name in sk:
        assert skins.apply_skin(app, name, tmp_path)["accent"]
    assert skins.apply_skin(app, "does-not-exist", tmp_path)["accent"] == skins.BUILTIN["Dark"]["accent"]
