"""Find Duplicates dialog: groups of probable duplicates; remove extra copies from the library."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout)

from ..duplicates import find_duplicates
from .models import fmt_time

COLS = ["Title", "Artist", "Album", "Source", "Format", "Bitrate", "Length", "Path"]


class DuplicatesDialog(QDialog):
    def __init__(self, tracks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find Duplicates"); self.resize(1000, 560)
        self.groups = find_duplicates(tracks)
        self.removed: list[int] = []
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(COLS); self.tree.setUniformRowHeights(True)
        self.info = QLabel("")
        self.note = QLabel("Removing only deletes the entries from the library; music files and server tracks are "
                           "never deleted. Tracks marked on the server come back on the next sync.")
        self.note.setWordWrap(True)
        for g in self.groups:
            top = QTreeWidgetItem([f"{g[0]['title']} — {g[0]['artist']}  ({len(g)} copies)"])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(top)
            for i, t in enumerate(g):
                it = QTreeWidgetItem([t["title"], t["artist"], t["album"], t["source"], (t["suffix"] or "").upper(),
                                      f"{t['bitrate']} kbps" if t["bitrate"] else "", fmt_time(t["duration"]),
                                      t["path"] or "(server)"])
                it.setData(0, Qt.ItemDataRole.UserRole, t["id"])
                it.setCheckState(0, Qt.CheckState.Unchecked if i == 0 else Qt.CheckState.Checked)  # keep the best
                top.addChild(it)
            top.setExpanded(True)
        self.tree.header().resizeSection(0, 300)
        self.tree.itemChanged.connect(self._count)
        self.btn = QPushButton("Remove checked from library"); self.btn.clicked.connect(self._remove)
        row = QHBoxLayout(); row.addWidget(self.info, 1); row.addWidget(self.btn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(self.groups)} group(s) of probable duplicates (same artist and title, similar "
                             "length). The best copy in each group is kept unchecked."))
        lay.addWidget(self.tree, 1); lay.addWidget(self.note); lay.addLayout(row); lay.addWidget(bb)
        self._count()

    def checked_ids(self) -> list[int]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                if c.checkState(0) == Qt.CheckState.Checked:
                    out.append(c.data(0, Qt.ItemDataRole.UserRole))
        return out

    def _count(self, *_):
        n = len(self.checked_ids())
        self.info.setText(f"{n} track(s) checked")
        self.btn.setEnabled(n > 0)

    def _remove(self):
        self.removed = self.checked_ids()
        self.accept()
