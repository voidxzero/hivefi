"""MPRIS2 service so media keys, KDE/GNOME widgets and playerctl can control the app."""
from PyQt6.QtCore import QMetaType, QObject, QVariant, pyqtClassInfo, pyqtProperty, pyqtSlot
from PyQt6.QtDBus import (QDBusAbstractAdaptor, QDBusArgument, QDBusConnection, QDBusMessage,
                          QDBusObjectPath)
from PyQt6.QtMultimedia import QMediaPlayer

PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"


def _i64(v: int):
    a = QDBusArgument(); a.add(int(v), int(QMetaType.Type.LongLong.value)); return a


@pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2")
def _strlist(items):
    a = QDBusArgument()
    a.beginArray(int(QMetaType.Type.QString.value))
    for i in items:
        a.add(str(i), int(QMetaType.Type.QString.value))
    a.endArray()
    return a


class _Root(QDBusAbstractAdaptor):

    def __init__(self, parent, win):
        super().__init__(parent); self.win = win

    @pyqtProperty(bool)
    def CanQuit(self): return True
    @pyqtProperty(bool)
    def CanRaise(self): return True
    @pyqtProperty(bool)
    def HasTrackList(self): return False
    @pyqtProperty(str)
    def Identity(self): return "Hivefi"
    @pyqtProperty(str)
    def DesktopEntry(self): return "hivefi"
    @pyqtProperty('QStringList')
    def SupportedUriSchemes(self): return ["file", "http", "https"]
    @pyqtProperty('QStringList')
    def SupportedMimeTypes(self): return ["audio/mpeg", "audio/flac", "audio/ogg"]

    @pyqtSlot()
    def Quit(self): self.win.close()
    @pyqtSlot()
    def Raise(self): self.win.showNormal(); self.win.raise_(); self.win.activateWindow()


@pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2.Player")
class _Player(QDBusAbstractAdaptor):

    def __init__(self, svc):
        super().__init__(svc); self.svc = svc; self.p = svc.player

    # properties
    @pyqtProperty(str)
    def PlaybackStatus(self):
        s = self.p.mp.playbackState()
        return {QMediaPlayer.PlaybackState.PlayingState: "Playing",
                QMediaPlayer.PlaybackState.PausedState: "Paused"}.get(s, "Stopped")
    @pyqtProperty(str)
    def LoopStatus(self): return {"off": "None", "all": "Playlist", "one": "Track"}[self.p.repeat]
    @pyqtProperty(float)
    def Rate(self): return 1.0
    @pyqtProperty(bool)
    def Shuffle(self): return self.p.shuffle
    @pyqtProperty('QVariantMap')
    def Metadata(self): return self.svc.metadata()
    @pyqtProperty(float)
    def Volume(self): return self.p.out.volume()
    @pyqtProperty(float)
    def MinimumRate(self): return 1.0
    @pyqtProperty(float)
    def MaximumRate(self): return 1.0
    @pyqtProperty(bool)
    def CanGoNext(self): return bool(self.p.queue)
    @pyqtProperty(bool)
    def CanGoPrevious(self): return bool(self.p.queue)
    @pyqtProperty(bool)
    def CanPlay(self): return True
    @pyqtProperty(bool)
    def CanPause(self): return True
    @pyqtProperty(bool)
    def CanSeek(self): return True
    @pyqtProperty(bool)
    def CanControl(self): return True
    @pyqtProperty('qlonglong')
    def Position(self): return self.p.mp.position() * 1000

    # methods
    @pyqtSlot()
    def Next(self): self.p.next()
    @pyqtSlot()
    def Previous(self): self.p.previous()
    @pyqtSlot()
    def Pause(self): self.p.mp.pause()
    @pyqtSlot()
    def PlayPause(self): self.p.toggle()
    @pyqtSlot()
    def Stop(self): self.p.stop()
    @pyqtSlot()
    def Play(self):
        if self.p.mp.playbackState() != QMediaPlayer.PlaybackState.PlayingState: self.p.toggle()
    @pyqtSlot('qlonglong')
    def Seek(self, off): self.p.seek(max(0, self.p.mp.position() + off // 1000))
    @pyqtSlot(QDBusObjectPath, 'qlonglong')
    def SetPosition(self, tid, pos): self.p.seek(pos // 1000)
    @pyqtSlot(str)
    def OpenUri(self, uri): pass


class Mpris(QObject):
    def __init__(self, win, player):
        super().__init__(win)
        self.player = player
        self.ok = False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        self._root = _Root(self, win)
        self._pl = _Player(self)
        if not (bus.registerService("org.mpris.MediaPlayer2.hivefi") and
                bus.registerObject(PATH, self, QDBusConnection.RegisterOption.ExportAdaptors)):
            return
        self.ok = True
        player.trackChanged.connect(lambda _t: self._changed("Metadata", "PlaybackStatus"))
        player.stateChanged.connect(lambda _s: self._changed("PlaybackStatus"))
        player.queueChanged.connect(lambda: self._changed("CanGoNext", "CanGoPrevious"))

    def metadata(self):
        t = self.player.current
        if not t:
            return {"mpris:trackid": QDBusObjectPath(NO_TRACK)}
        m = {"mpris:trackid": QDBusObjectPath(f"/org/hivefi/track/{t['id']}"),
             "mpris:length": _i64(int((t.get("duration") or 0) * 1_000_000)),
             "xesam:title": t["title"], "xesam:artist": _strlist([t["artist"]]), "xesam:album": t["album"],
             "xesam:genre": _strlist([t["genre"]] if t.get("genre") else [])}
        if t.get("track_no"): m["xesam:trackNumber"] = int(t["track_no"])
        return m

    def _changed(self, *props):
        msg = QDBusMessage.createSignal(PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged")
        vals = {n: getattr(self._pl, n) for n in props}
        msg.setArguments(["org.mpris.MediaPlayer2.Player", vals, []])
        QDBusConnection.sessionBus().send(msg)
