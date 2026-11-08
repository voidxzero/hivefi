"""Batch tag tools dialog: pick an operation, see the before/after of every track, then apply."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import tagtools

OPS = [("title_case", "Title Case"), ("find_replace", "Find and replace"), ("tidy", "Trim extra spaces"),
       ("upper", "UPPER CASE"), ("lower", "lower case"), ("from_filename", "Fill tags from the file name")]
FIELDS = [("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
          ("genre", "Genre"), ("composer", "Composer"), ("comment", "Comment")]


class TagToolsDialog(QDialog):
    def __init__(self, tracks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Tag Tools"); self.resize(820, 540)
        self.tracks = [t for t in tracks if t.get("source") == "local" and t.get("path")]
        self.op = QComboBox()
        for k, label in OPS:
            self.op.addItem(label, k)
        self.field = QComboBox()
        for k, label in FIELDS:
            self.field.addItem(label, k)
        self.find, self.repl = QLineEdit(), QLineEdit()
        self.regex, self.case = QCheckBox("Regular expression"), QCheckBox("Match case")
        self.pattern = QLineEdit("%track% - %title%")
        self.pattern.setToolTip("%title% %artist% %album% %albumartist% %track% %disc% %year% %genre%")
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["File", "Tag", "Before", "After"])
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 90); self.table.setColumnWidth(2, 220)
        self.count = QLabel("")
        form = QFormLayout()
        form.addRow("Operation", self.op); form.addRow("Tag", self.field)
        form.addRow("Find", self.find); form.addRow("Replace with", self.repl)
        form.addRow(self.regex); form.addRow(self.case)
        form.addRow("File name pattern", self.pattern)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText("Apply to files")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(self.tracks)} local track(s). Nothing is written until you press Apply."))
        lay.addLayout(form); lay.addWidget(self.table, 1); lay.addWidget(self.count); lay.addWidget(bb)
        for w in (self.op, self.field):
            w.currentIndexChanged.connect(self._refresh)
        for w in (self.find, self.repl, self.pattern):
            w.textChanged.connect(self._refresh)
        for w in (self.regex, self.case):
            w.toggled.connect(self._refresh)
        self._refresh()

    def params(self):
        return dict(op=self.op.currentData(), field=self.field.currentData(), find=self.find.text(),
                    repl=self.repl.text(), regex=self.regex.isChecked(), case=self.case.isChecked(),
                    pattern=self.pattern.text())

    def changes(self):
        p = self.params()
        try:
            return tagtools.plan(self.tracks, p.pop("op"), p.pop("field"), **p) if True else []
        except Exception:  # noqa: BLE001 - a half-typed regular expression
            return []

    def _refresh(self):
        op = self.op.currentData()
        self.field.setEnabled(op != "from_filename")
        for w in (self.find, self.repl, self.regex, self.case):
            w.setEnabled(op == "find_replace")
        self.pattern.setEnabled(op == "from_filename")
        ch = self.changes()
        rows = [(t, k, t.get(k), v) for t, c in ch for k, v in c.items()]
        self.table.setRowCount(min(len(rows), 400))
        import os
        for r, (t, k, old, new) in enumerate(rows[:400]):
            for c, text in enumerate((os.path.basename(t["path"]), k, str(old or ""), str(new))):
                it = QTableWidgetItem(text); it.setFlags(Qt.ItemFlag.ItemIsEnabled); self.table.setItem(r, c, it)
        self.count.setText(f"{len(ch)} of {len(self.tracks)} track(s) would change" +
                           (" (first 400 changes shown)" if len(rows) > 400 else ""))
        self.ok.setEnabled(bool(ch))
