"""Folder view: Navidrome's directories and this computer's, as a lazily built tree in the sources panel."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shutil
import subprocess

import pytest

from hivefi import folders, session
from hivefi.config import Config
from hivefi.db import Database
from tests.test_gui import win, wait  # noqa: F401  (fixture)


def T(i, path, root="", **kw):
    return dict(id=i, rpath=path, path=None, folder_id=root or None, disc_no=1, track_no=i, **kw)


def test_folder_index_counts_children_and_tracks():
    ts = [T(1, "Rock/Band/Album A/01.mp3"), T(2, "Rock/Band/Album A/02.mp3"), T(3, "Rock/Band/Album B/01.mp3"),
          T(4, "Jazz/Solo/01.mp3"), T(5, "loose.mp3")]
    idx = folders.FolderIndex(folders.remote_entries(ts))
    assert idx.roots() == [("", 5)]
    assert idx.children("", "") == [("Jazz", "Jazz", 1), ("Rock", "Rock", 3)]
    assert idx.children("", "Rock/Band") == [("Album A", "Rock/Band/Album A", 2), ("Album B", "Rock/Band/Album B", 1)]
    assert [t["id"] for t in idx.tracks("", "Rock/Band/Album A")] == [1, 2]
    assert [t["id"] for t in idx.tracks("", "Rock")] == [1, 2, 3]                       # recursive, in path order
    assert len(idx.tracks("*", "")) == 5
    assert idx.tracks("", "Rock/Ban") == []                                             # a prefix is a whole folder name
    assert idx.children("", "nope") == []
    assert folders.split_dirs("a\\b\\c.mp3") == ("a", "b")


def test_local_entries_are_relative_to_their_library():
    ts = [dict(id=1, path="/music/a/x/1.mp3"), dict(id=2, path="/music/a/2.mp3"), dict(id=3, path="/other/lib/b/3.mp3"),
          dict(id=4, path="/music/a/x/y/4.mp3")]
    idx = folders.FolderIndex(folders.local_entries(ts, ["/music/a", "/other/lib"]))
    assert dict(idx.roots()) == {"/music/a": 3, "/other/lib": 1}
    assert [c[0] for c in idx.children("/music/a", "")] == ["x"]
    assert [t["id"] for t in idx.tracks("/music/a", "x")] == [1, 4]


def test_two_server_libraries_are_two_roots():
    ts = [T(1, "A/1.mp3", "1"), T(2, "A/2.mp3", "2"), T(3, "B/3.mp3", "2")]
    idx = folders.FolderIndex(folders.remote_entries(ts))
    assert idx.roots() == [("1", 1), ("2", 2)]
    assert [t["id"] for t in idx.tracks("2", "")] == [2, 3] and [t["id"] for t in idx.tracks("2", "A")] == [2]


def top(w, label):
    return next(w.tree.topLevelItem(0).child(i) for i in range(w.tree.topLevelItem(0).childCount())
                if w.tree.topLevelItem(0).child(i).text(0).startswith(label))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
def test_navidrome_folder_tree(win):
    w, db = win, win.db
    nav = top(w, "Navidrome")
    node = next(nav.child(i) for i in range(nav.childCount()) if nav.child(i).text(0) == "Folders")
    assert node.childCount() == 1 and node.child(0).text(0) == "…"                  # built lazily
    node.setExpanded(True)                                                            # -> fills "Band" (single library)
    assert [node.child(i).text(0) for i in range(node.childCount())] == ["Band  (7)"]
    band = node.child(0); band.setExpanded(True)
    names = sorted(band.child(i).text(0) for i in range(band.childCount()))
    assert names == ["Album0  (3)", "Album1  (4)"]
    # selecting a folder shows its tracks, in path order, recursively
    w.tree.setCurrentItem(band.child(1)); wait(200)
    assert w.source == ("folder", ("remote", "", "Band/Album1")) and w.model.rowCount() == 4
    assert [t["rpath"] for t in w.model.tracks] == sorted(t["rpath"] for t in w.model.tracks)
    w.tree.setCurrentItem(band); wait(200)
    assert w.model.rowCount() == 7
    w.tree.setCurrentItem(node); wait(200)                                            # "Folders" itself = everything there
    assert w.source == ("folder", ("remote", "*", "")) and w.model.rowCount() == 7
    # local files get the same treatment
    here = top(w, "This Computer")
    lnode = next(here.child(i) for i in range(here.childCount()) if here.child(i).text(0) == "Folders")
    lnode.setExpanded(True)
    assert lnode.childCount() == 0                                                    # the fixture's files sit in the library root
    w.tree.setCurrentItem(lnode); wait(200)
    assert w.source[0] == "folder" and w.model.rowCount() == 2 and {t["source"] for t in w.model.tracks} == {"local"}
    # the source and the opened folder survive a tree refresh (sync, library changes...)
    w.source = ("folder", ("remote", "", "Band/Album0")); w.refresh_playlists(); wait(100)
    cur = w.tree.currentItem()
    assert cur is not None and cur.text(0).startswith("Album0") and w.source == ("folder", ("remote", "", "Band/Album0"))
    # hiding Navidrome empties its folders: the view falls back to All Music, and the tree finds them again afterwards
    w.set_library_enabled(Config.NAVIDROME, False); wait(100)
    assert w.source == ("all", None) and w._folder_index("remote").roots() == []
    w.set_library_enabled(Config.NAVIDROME, True); wait(1500)
    assert w._folder_index("remote").roots() == [("", 7)]
    assert w._reveal_folder(("remote", "", "Band/Album0")).text(0).startswith("Album0")


def test_folder_source_is_restored_with_the_session(win, tmp_path):
    w = win
    w.source = ("folder", ("remote", "", "Band/Album1")); w.refresh_playlists(); w.load_source()
    assert w.model.rowCount() == 4
    w._save_session()
    assert session.load()["source"] == ["folder", ["remote", "", "Band/Album1"]]
    from hivefi.ui.main_window import MainWindow
    cfg2 = Config.load(w.cfg._path)
    cfg2.library_dirs = w.cfg.library_dirs; cfg2.server_url = w.cfg.server_url
    cfg2.username, cfg2.password = "u", "pw"; cfg2.show_tray = False; cfg2.sync_on_start = False
    w.player.stop(); w.player.mp.shutdown()
    w2 = MainWindow(cfg2, Database(tmp_path / "l.db")); w2.show(); wait(1800)
    try:
        assert w2.source == ("folder", ("remote", "", "Band/Album1")) and w2.model.rowCount() == 4
        assert w2.tree.currentItem().text(0).startswith("Album1")
    finally:
        w2.player.stop(); w2.player.mp.shutdown()


def test_two_libraries_show_one_root_each(win):
    w = win
    st = w._st
    st.folders = [{"id": "1", "name": "Music"}, {"id": "2", "name": "Audiobooks"}]
    for s in st.songs:
        s["folder"] = "1" if int(s["id"]) <= 4 else "2"
    w.sync_now(silent=True); wait(1800)
    nav = top(w, "Navidrome")
    node = next(nav.child(i) for i in range(nav.childCount()) if nav.child(i).text(0) == "Folders")
    node.setExpanded(True)
    assert sorted(node.child(i).text(0) for i in range(node.childCount())) == ["Audiobooks  (3)", "Music  (4)"]
    audio = next(node.child(i) for i in range(node.childCount()) if node.child(i).text(0).startswith("Audiobooks"))
    w.tree.setCurrentItem(audio); wait(200)
    assert w.source == ("folder", ("remote", "2", "")) and w.model.rowCount() == 3
