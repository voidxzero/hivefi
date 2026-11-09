"""Up Next panel (play order, drag to reorder) and the Now Playing view."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])   # must outlive every widget
from tests.test_gui import wait, win  # noqa: F401 - the shared window fixture


def tracks(n):
    return [dict(id=i, source="local", title=f"t{i}", artist="A", album="x", album_artist="A", duration=100)
            for i in range(n)]


def test_panel_shows_the_play_order_and_reports_queue_rows():
    from hivefi.ui.panels import UpNextPanel
    p = UpNextPanel(); q = tracks(5)
    p.refresh(q, 2, [2, 4, 0, 3, 1])                           # shuffled: the list follows the play order
    assert [p.list.item(i).text().split(" – ")[1].split()[0] for i in range(5)] == ["t2", "t4", "t0", "t3", "t1"]
    assert p.list.item(0).text().startswith("▶")
    jumped, moved, removed = [], [], []
    p.jumpRequested.connect(jumped.append); p.moveRequested.connect(lambda r, b: moved.append((r, b)))
    p.removeRequested.connect(removed.append)
    p._double(p.list.item(1)); assert jumped == [4]
    p._dropped([3, 4], 1); assert moved == [([3, 1], 4)]     # rows t3, t1 dropped before t4
    p._dropped([1], -1); assert moved[-1] == ([4], None)     # dropped below the last row: to the end
    p.list.item(2).setSelected(True)
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent
    p.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert removed == [[0]]


def test_panel_window_points_past_its_end():
    from hivefi.ui.panels import UpNextPanel
    p = UpNextPanel(); q = tracks(700)
    p.refresh(q, 0)
    assert len(p.rows) == 600 and p.after == 600 and p.list.count() == 601   # "… and 100 more"


def test_double_click_in_a_searched_now_playing_view(win):
    w = win
    q = w.db.all_tracks("remote")
    w.player.play_list(q, 0); wait(200)
    w.source = ("queue", None); w.load_source()
    target = q[-1]
    w.model.set_filters(text=target["title"])
    row = w.model.row_of(target["id"]); assert row is not None and row < len(q) - 1
    w._on_double(w.model.index(row, 2)); wait(100)
    assert w.player.current is target
    # click the speaker to mute, again to get the volume back
    w.vol.setValue(63); w._toggle_mute()
    assert w.vol.value() == 0 and w.cfg.volume == 0
    w._toggle_mute(); assert w.vol.value() == 63
    # Playback → Stop after Current is one-shot and follows the player
    w.act_stop_after.setChecked(True); assert w.player.stop_after_current
    w.player.stopAfterChanged.emit(False); assert not w.act_stop_after.isChecked()
    # right-click → Remove from Now Playing (the playing track stays)
    w.model.set_filters(text=""); w.source = ("queue", None); w.load_source()
    n = len(w.player.queue); cur = w.player.current
    w._remove_from_queue([w.player.queue[0], cur]); wait(50)
    assert len(w.player.queue) == n - 1 and w.player.current is cur and w.model.rowCount() == n - 1


def test_playback_menu_commands_and_playlist_duplicates(win):
    w = win
    acts = {a.text(): a for a in w.shortcut_actions.values()}
    for name, keys in (("Volume Up", "Ctrl+Up"), ("Mute / Unmute", "Ctrl+Shift+M"), ("Shuffle", "Ctrl+H"),
                       ("Seek Forward 10 s", "Ctrl+Shift+Right"), ("★★★", "Ctrl+3")):
        assert acts[name].shortcut().toString() == keys            # listed in Preferences → Hotkeys too
    w.vol.setValue(50); acts["Volume Up"].trigger(); assert w.vol.value() == 55 and w.cfg.volume == 55
    acts["Volume Down"].trigger(); acts["Volume Down"].trigger(); assert w.vol.value() == 45
    on = w.btn_shuffle.isChecked()
    acts["Shuffle"].trigger(); assert w.btn_shuffle.isChecked() != on and w.player.shuffle != on
    w.btn_shuffle.setChecked(on); assert acts["Shuffle"].isChecked() == on
    rep = w.player.repeat; acts["Repeat (Off / All / One)"].trigger(); assert w.player.repeat != rep
    q = w.db.all_tracks("remote")
    w.player.play_list(q, 0); wait(300)
    acts["★★★"].trigger(); assert w.db.track(q[0]["id"])["rating"] == 3
    seeks = []; w.player.seek = seeks.append
    w.player.mp.position = lambda: 5_000; w.player.mp.duration = lambda: 60_000
    w._seek_by(10_000); w._seek_by(-10_000); w.player.mp.position = lambda: 55_000; w._seek_by(10_000)
    assert seeks == [15_000, 0, 59_500]                             # never before the start or past the end
    # Remove from Playlist takes away the selected copy only
    a, b = q[0]["id"], q[1]["id"]
    pid = w.db.create_playlist("dupes"); w.db.set_playlist_tracks(pid, [a, b, a, b])
    w.refresh_playlists(); w.source = ("playlist", pid); w.load_source()
    w._remove_from_playlist([2])                                    # the second copy of `a`
    assert [t["id"] for t in w.db.playlist_tracks(pid)] == [a, b, b]
    w._remove_from_playlist([0, 2])
    assert [t["id"] for t in w.db.playlist_tracks(pid)] == [b]
