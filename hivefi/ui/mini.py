from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QVBoxLayout, QWidget

from .models import fmt_time
from .panels import ArtLabel


class MiniPlayer(QWidget):
    restoreRequested = pyqtSignal()

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.setWindowTitle("Hivefi – Mini Player")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.art = ArtLabel(); self.art.setFixedSize(84, 84); self.art.setMinimumSize(84, 84)
        self.title = QLabel("Nothing playing"); f = self.title.font(); f.setBold(True); self.title.setFont(f)
        self.sub = QLabel("")
        S = QStyle.StandardPixmap
        def btn(sp, fn):
            b = QPushButton(self.style().standardIcon(sp), ""); b.setFlat(True); b.clicked.connect(fn); return b
        self.b_play = btn(S.SP_MediaPlay, player.toggle)
        row = QHBoxLayout()
        row.addWidget(btn(S.SP_MediaSkipBackward, player.previous)); row.addWidget(self.b_play)
        row.addWidget(btn(S.SP_MediaSkipForward, player.next)); row.addStretch()
        full = QPushButton("⤢"); full.setToolTip("Back to full window"); full.setFlat(True)
        full.clicked.connect(self.restoreRequested.emit); row.addWidget(full)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.sliderMoved.connect(player.seek)
        self.time = QLabel("0:00")
        srow = QHBoxLayout(); srow.addWidget(self.seek, 1); srow.addWidget(self.time)
        col = QVBoxLayout(); col.addWidget(self.title); col.addWidget(self.sub); col.addLayout(row); col.addLayout(srow)
        lay = QHBoxLayout(self); lay.addWidget(self.art); lay.addLayout(col, 1)
        self.resize(420, 110)
        player.stateChanged.connect(lambda pl: self.b_play.setIcon(
            self.style().standardIcon(S.SP_MediaPause if pl else S.SP_MediaPlay)))
        player.positionChanged.connect(self._pos)
        player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
        player.trackChanged.connect(self.set_track)

    def set_track(self, t):
        self.title.setText(t["title"] if t else "Nothing playing")
        self.sub.setText(f'{t["artist"]} — {t["album"]}' if t else "")

    def _pos(self, ms):
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)
        self.time.setText(fmt_time(ms / 1000))

    def closeEvent(self, e):
        e.ignore(); self.restoreRequested.emit()
