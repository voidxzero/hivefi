"""Library tabs, the Album and Tracks view, Edit / Help menu commands, Shuffle Play and Remove from Library."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QMessageBox
from tests.test_gui import wait, win  # noqa: F401 - the shared window fixture


def labels(w):
    return [w.tab_bar.tabText(i) for i in range(w.tab_bar.count())]


def test_tabs_keep_their_own_view(win):
    w = win
    w._set_list_mode("details")                                        # the default is now Album and Tracks (MusicBee)
    assert labels(w) == ["All Music"] and w.tab_bar.isVisible()
    w.search.setText("L1"); w._on_search("L1")
    assert labels(w) == ["All Music: L1"] and w.model.rowCount() == 1
    w._new_tab(); wait(50)
    assert w.tab_bar.count() == 2 and w.tab_bar.currentIndex() == 1
    assert w.search.text() == "" and w.model.rowCount() == 9 and w.source == ("all", None)
    w.tree.setCurrentItem(w._items[("local", None)]); wait(50)        # this tab: This Computer
    assert w.model.rowCount() == 2 and labels(w)[1] == "This Computer"
    w._set_list_mode("albums")
    w.tab_bar.setCurrentIndex(0); wait(50)                             # back: search and list mode come back
    assert w.search.text() == "L1" and w.model.rowCount() == 1 and w.source == ("all", None)
    assert w.cfg.list_mode == "details" and w.list_stack.currentWidget() is w.table
    w.tab_bar.setCurrentIndex(1); wait(50)
    assert w.source == ("local", None) and w.model.rowCount() == 2 and w.cfg.list_mode == "albums"
    # moving tabs keeps each one's view
    w.tab_bar.moveTab(1, 0); wait(20)
    assert labels(w) == ["This Computer", "All Music: L1"] and w.tab_bar.currentIndex() == 0
    w.tab_bar.setCurrentIndex(1); wait(50); assert w.search.text() == "L1"
    # the session remembers every tab
    st = w._session_state()
    assert st["tab"] == 1 and [t["source"] for t in st["tabs"]] == [["local", None], ["all", None]]
    assert st["tabs"][1]["search"] == "L1"
    # closing the current tab goes to its neighbour; the last tab cannot be closed
    w._close_tab(); wait(50)
    assert w.tab_bar.count() == 1 and w.source == ("local", None) and w.search.text() == ""
    w._close_tab(); assert w.tab_bar.count() == 1
    w._new_tab(st["tabs"][1]); wait(50)                                # a copy of a saved view
    assert w.search.text() == "L1" and w.tab_bar.count() == 2
    # restoring a saved session rebuilds the tabs and shows the saved current one
    w._restore_tabs(st); w._apply_view_state(st["tabs"][st["tab"]]); wait(50)
    assert labels(w) == ["This Computer", "All Music: L1"] and w.tab_bar.currentIndex() == 1
    w.tab_bar.setCurrentIndex(0); wait(50)
    assert w.source == ("local", None) and w.search.text() == ""


def test_album_and_tracks_view(win):
    w = win
    w._set_view_mode("browser"); w._set_list_mode("details")           # start from the Columns + Details arrangement
    w.tree.setCurrentItem(w._items[("remote", None)]); wait(50)
    w._set_list_mode("albums"); wait(100)
    v = w.album_view
    assert w.list_stack.currentWidget() is v and w.act_list_toggle.isChecked() and w.act_list_albums.isChecked()
    assert w.model.sort_col == "album_artist"                          # albums together, in track order
    assert [e - s for s, e, _k in v.groups] == [3, 4] and v.total > 0
    v.grab()                                                            # paints without errors
    # click a track row; Ctrl-click adds; Shift-click selects a range; the selection is the table's
    r0, r2 = v.row_rect(0).center(), v.row_rect(2).center()
    from PyQt6.QtTest import QTest
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=r0); assert [t["id"] for t in w._selected()] == [w.model.tracks[0]["id"]]
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, r2)
    assert len(w._selected()) == 3
    # a click on the album line selects the whole album
    s, e, _k = v.groups[1]
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(20, v.tops[1] - v.verticalScrollBar().value() + 10))
    assert len(w._selected()) == e - s
    # double-click a row plays the list from there
    rr = v.row_rect(1)
    QTest.mouseDClick(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rr.left() + 60, rr.center().y())); wait(200)   # on the title, not the stars
    assert w.player.current is w.model.tracks[1]
    # rating stars
    x0, sw = v._rating_x()
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(int(x0 + 3 + sw * 3.5), v.row_rect(0).center().y()))
    assert w.db.track(w.model.tracks[0]["id"])["rating"] == 4
    # keyboard
    v.setFocus(); QTest.keyClick(v, Qt.Key.Key_Home); QTest.keyClick(v, Qt.Key.Key_Down)
    assert w.table.currentIndex().row() == 1
    # back to Details gives back the sort it had (Album and Tracks needed Album Artist); the setting is saved
    w._set_list_mode("details"); assert w.list_stack.currentWidget() is w.table and w.cfg.list_mode == "details"
    assert w.model.sort_col == "artist"
    title = next(i for i, c in enumerate(__import__("hivefi.ui.models", fromlist=["COLUMNS"]).COLUMNS) if c[1] == "title")
    w._header_sort(title); w._set_list_mode("albums"); w._set_list_mode("details")
    assert w.model.sort_col == "title" and w.table.horizontalHeader().sortIndicatorSection() == title


def test_edit_help_shuffle_and_remove(win, monkeypatch):
    w = win
    acts = {a.text().replace("&", ""): a for a in w.shortcut_actions.values()}
    assert acts["Select All"].shortcut().toString() == "Ctrl+A" and acts["New Tab"].shortcut().toString() == "Ctrl+T"
    w.tree.setCurrentItem(w._items[("all", None)]); wait(50)
    acts["Select All"].trigger(); assert len(w._selected()) == w.model.rowCount() == 9
    # Shuffle Play: every selected track, in a random order
    w._shuffle_play(w._selected()); wait(200)
    assert sorted(t["id"] for t in w.player.queue) == sorted(t["id"] for t in w.model.tracks)
    # Remove from Library asks first, keeps the playing track and drops the rest from the queue
    playing = w.player.current
    victims = [t for t in w.model.tracks if t["source"] == "remote"][:3]
    if playing not in victims:
        victims.append(playing)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    w._remove_from_library(victims); assert w.model.rowCount() == 9
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w._remove_from_library(victims); wait(50)
    gone = {t["id"] for t in victims} - {playing["id"]}
    assert w.model.rowCount() == 9 - len(gone) and not gone & {t["id"] for t in w.db.all_tracks()}
    assert w.player.current is playing and not gone & {t["id"] for t in w.player.queue}
    # Help → About opens without errors
    shown = []
    monkeypatch.setattr(QMessageBox, "about", lambda *a: shown.append(a[2]))
    acts["About Hivefi…"].trigger(); assert shown and "Hivefi" in shown[0]


def test_delete_key_depends_on_the_view(win, monkeypatch):
    w = win
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    q = w.db.all_tracks("remote")
    pid = w.db.create_playlist("p"); w.db.set_playlist_tracks(pid, [q[0]["id"], q[1]["id"], q[2]["id"]])
    w.refresh_playlists(); w.tree.setCurrentItem(w._items[("playlist", pid)]); wait(50)
    w.table.selectRow(1); w._delete_key()                              # playlist: leaves the playlist, no question
    assert [t["id"] for t in w.db.playlist_tracks(pid)] == [q[0]["id"], q[2]["id"]] and not asked
    assert len(w.db.all_tracks("remote")) == 7
    w.player.play_list(q[:4], 0); wait(200)
    w.tree.setCurrentItem(w._items[("queue", None)]); wait(50)
    w.table.selectRow(2); w._delete_key()                              # Now Playing: leaves the queue
    assert [t["id"] for t in w.player.queue] == [q[0]["id"], q[1]["id"], q[3]["id"]] and not asked
    w.tree.setCurrentItem(w._items[("history", None)]); wait(50)
    w.table.selectAll(); w._delete_key(); assert not asked              # history: nothing
    w.tree.setCurrentItem(w._items[("all", None)]); wait(50)
    w.table.selectRow(0); w._delete_key()                               # library: asks (answered No here)
    assert len(asked) == 1 and "Remove 1 track" in asked[0] and len(w.db.all_tracks()) == 9
