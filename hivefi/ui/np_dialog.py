"""Configure what the Now Playing panel shows."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout)

from .. import npfields


class NowPlayingDialog(QDialog):
    def __init__(self, fields, labels, track=None, ctx=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Now Playing"); self.resize(520, 560)
        self.track, self.ctx = track or SAMPLE, ctx or {"out": {"rate": 48000, "format": "F32LE", "channels": 2}}
        self.list = QListWidget()
        order = [k for k in fields if k in npfields.FIELDS] + [k for k in npfields.FIELDS if k not in fields]
        for key in order:
            it = QListWidgetItem(npfields.FIELDS[key][0]); it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            it.setCheckState(Qt.CheckState.Checked if key in fields else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        up, down, reset = QPushButton("Move up"), QPushButton("Move down"), QPushButton("Defaults")
        up.clicked.connect(lambda: self._move(-1)); down.clicked.connect(lambda: self._move(1))
        reset.clicked.connect(self.reset)
        self.labels = QCheckBox("Show the name of each field"); self.labels.setChecked(labels)
        self.preview = QLabel(); self.preview.setWordWrap(True); self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setMinimumHeight(110); self.preview.setFrameShape(QLabel.Shape.StyledPanel)
        side = QVBoxLayout(); [side.addWidget(b) for b in (up, down, reset)]; side.addStretch()
        row = QHBoxLayout(); row.addWidget(self.list, 1); row.addLayout(side)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Tick what the panel should show and put it in order. The first line is the headline; "
                             "artist, album, genre, year and composer are links that take you there."))
        lay.addLayout(row); lay.addWidget(self.labels); lay.addWidget(QLabel("Preview:")); lay.addWidget(self.preview)
        lay.addWidget(bb)
        self.list.itemChanged.connect(self._refresh); self.labels.toggled.connect(self._refresh)
        self.list.model().rowsMoved.connect(self._refresh)
        self._refresh()

    def _move(self, d):
        r = self.list.currentRow()
        if 0 <= r + d < self.list.count():
            it = self.list.takeItem(r); self.list.insertItem(r + d, it); self.list.setCurrentRow(r + d)
            self._refresh()

    def reset(self):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.CheckState.Checked if self.list.item(i).data(Qt.ItemDataRole.UserRole)
                                            in npfields.DEFAULT_FIELDS else Qt.CheckState.Unchecked)
        order = npfields.DEFAULT_FIELDS + [k for k in npfields.FIELDS if k not in npfields.DEFAULT_FIELDS]
        items = {self.list.item(i).data(Qt.ItemDataRole.UserRole): i for i in range(self.list.count())}
        for pos, key in enumerate(order):
            it = self.list.takeItem([self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())].index(key))
            self.list.insertItem(pos, it)
        self.labels.setChecked(False)
        self._refresh()

    def result_fields(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _refresh(self, *_):
        f = self.result_fields()
        self.preview.setText(npfields.html(f, self.track, self.ctx, self.labels.isChecked())
                             or "<i>Nothing selected</i>")


SAMPLE = dict(title="Comfortably Numb", artist="Pink Floyd", album="The Wall", album_artist="Pink Floyd", year=1979,
              genre="Rock", composer="Waters / Gilmour", track_no=6, disc_no=2, suffix="flac", sample_rate=96000,
              bit_depth=24, channels=2, bitrate=2900, duration=382, size=62_000_000, rating=5, play_count=42,
              skip_count=1, bpm=128, rg_gain=-7.3, source="local", path="/music/Pink Floyd/The Wall/2-06.flac",
              date_added=1_700_000_000, last_played=1_730_000_000, comment="")
