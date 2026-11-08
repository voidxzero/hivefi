"""Restoring a track-list column layout saved before columns were added must not break the table's painting."""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from types import SimpleNamespace
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QApplication, QTableView
from hivefi.ui.main_window import MainWindow
from hivefi.ui.models import COLUMNS, DEFAULT_HIDDEN, TrackModel


def test_old_header_state_keeps_header_length_consistent():
    app = QApplication.instance() or QApplication([])
    old = QTableView(); m17 = QStandardItemModel(0, 17); old.setModel(m17)    # a state from the 17-column days
    for i, (_, key, w) in enumerate(COLUMNS[:17]):
        old.setColumnWidth(i, w); old.setColumnHidden(i, key in DEFAULT_HIDDEN)
    old.setColumnWidth(10, 428)
    state = old.horizontalHeader().saveState()

    model = TrackModel(); table = QTableView(); table.setModel(model)
    h = table.horizontalHeader(); h.setStretchLastSection(True)
    for i, (_, key, w) in enumerate(COLUMNS):
        table.setColumnWidth(i, w); table.setColumnHidden(i, key in DEFAULT_HIDDEN)
    fake = SimpleNamespace(table=table, model=model, cfg=SimpleNamespace(header_columns=[]))
    MainWindow._restore_header(fake, state)

    visible = [i for i in range(h.count()) if not h.isSectionHidden(i)]
    assert h.length() == sum(h.sectionSize(i) for i in visible)
    assert [COLUMNS[i][1] for i in visible] == [c[1] for c in COLUMNS[:11]]   # new optional columns stay hidden
    assert h.sectionSize(2) == COLUMNS[2][2]
