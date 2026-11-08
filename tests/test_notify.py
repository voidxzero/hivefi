"""Desktop notifications on song change."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import stat

import pytest

from hivefi import notify
from tests.test_gui import win, wait  # noqa: F401  (fixture)


@pytest.fixture
def fake_notify_send(tmp_path, monkeypatch):
    log = tmp_path / "calls.txt"
    exe = tmp_path / "bin" / "notify-send"
    exe.parent.mkdir()
    exe.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {log}; done\nprintf -- "---\\n" >> {log}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{exe.parent}:{os.environ['PATH']}")
    return log


def calls(log):
    if not log.exists():
        return []
    return [c.strip().splitlines() for c in log.read_text().split("---\n") if c.strip()]


def test_command_and_send(fake_notify_send):
    cmd = notify.command("Song", "Artist\nAlbum", "/tmp/c.png", 3000)
    assert cmd[0] == "notify-send" and "-i" in cmd and cmd[cmd.index("-i") + 1] == "/tmp/c.png"
    assert cmd[-3:] == ["--", "Song", "Artist\nAlbum"] and "3000" in cmd
    assert any("synchronous" in a for a in cmd)                                # replaces the previous bubble
    assert "-i" not in notify.command("S", "B")
    assert notify.available() and notify.send("Hello", "World")
    sent = calls(fake_notify_send)[0]
    assert "Hello" in sent and "World" in sent and "Hivefi" in sent
    assert notify.send("--dangerous", "-x") and "--dangerous" in calls(fake_notify_send)[1]   # titles are never options


def test_unavailable(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert not notify.available() and notify.send("x") is False


def test_window_notifies_only_when_asked(win, fake_notify_send, monkeypatch):
    w = win
    t = w.db.all_tracks("local")[0]
    w._on_now_playing(t); wait(500)
    assert calls(fake_notify_send) == []                                       # off by default
    w.cfg.notify_track = True
    monkeypatch.setattr(w, "isActiveWindow", lambda: True)
    w._on_now_playing(t); wait(500)
    assert calls(fake_notify_send) == []                                       # window in front: no bubble
    w.cfg.notify_only_inactive = False
    w._on_now_playing(t); wait(800)
    got = calls(fake_notify_send)
    assert len(got) == 1 and t["title"] in got[0] and t["artist"] in "\n".join(got[0])
    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    w.cfg.notify_only_inactive = True
    w._on_now_playing(t); wait(800)
    assert len(calls(fake_notify_send)) == 2
    # radio: the stream's title is the summary
    from hivefi import radio
    st = radio.radio_track({"id": 1, "name": "Jazz FM", "url": "http://x/s"})
    w.player.queue = [st]; w.player.index = 0
    w._on_stream_title("Miles Davis - So What"); wait(800)
    last = calls(fake_notify_send)[-1]
    assert "Miles Davis - So What" in last and "Jazz FM" in last
    # no notify-send: falls back to the tray balloon when there is a tray
    class Tray:
        shown = []
        def showMessage(self, a, b): self.shown.append((a, b))
    monkeypatch.setenv("PATH", "/nonexistent"); w.tray = Tray()
    w._on_now_playing(t); wait(800)
    assert Tray.shown and Tray.shown[0][0] == t["title"]
    # the preferences keep the options
    from hivefi.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(w.cfg, w)
    assert dlg.notify_on.isChecked() and dlg.notify_idle.isChecked()
    dlg.notify_on.setChecked(False); dlg.notify_idle.setChecked(False); dlg.apply()
    assert not w.cfg.notify_track and not w.cfg.notify_only_inactive
