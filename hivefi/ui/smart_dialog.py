from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                             QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import smart


class RuleRow(QWidget):
    def __init__(self, on_remove, rule=None):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.field = QComboBox()
        for k, (label, _) in smart.FIELDS.items():
            self.field.addItem(label, k)
        self.op = QComboBox(); self.value = QLineEdit()
        rm = QPushButton("−"); rm.setFixedWidth(28); rm.clicked.connect(lambda: on_remove(self))
        for w in (self.field, self.op, self.value, rm):
            lay.addWidget(w)
        lay.setStretch(2, 1)
        self.field.currentIndexChanged.connect(self._fields_changed)
        self.op.currentIndexChanged.connect(self._op_changed)
        if rule:
            self.field.setCurrentIndex(max(0, self.field.findData(rule["field"])))
            self._fields_changed()
            self.op.setCurrentIndex(max(0, self.op.findText(rule["op"])))
            self.value.setText(str(rule.get("value", "")))
        else:
            self._fields_changed()

    def _kind(self):
        return smart.FIELDS[self.field.currentData()][1]

    def _fields_changed(self):
        self.op.blockSignals(True); self.op.clear(); self.op.addItems(smart.OPS[self._kind()])
        self.op.blockSignals(False); self._op_changed()

    def _op_changed(self):
        self.value.setVisible(self._kind() != "bool" and self.op.currentText() != "is never")

    def rule(self):
        return {"field": self.field.currentData(), "op": self.op.currentText(), "value": self.value.text().strip()}


class SmartDialog(QDialog):
    def __init__(self, parent=None, name="", spec=None):
        super().__init__(parent)
        self.setWindowTitle("Auto Playlist"); self.resize(620, 360)
        spec = spec or {"match": "all", "rules": [{"field": "rating", "op": ">=", "value": "4"}],
                        "limit": 0, "order": "title", "desc": False}
        self.name = QLineEdit(name)
        self.match = QComboBox(); self.match.addItem("all of these rules", "all"); self.match.addItem("any of these rules", "any")
        self.match.setCurrentIndex(0 if spec.get("match", "all") == "all" else 1)
        self.rows_box = QVBoxLayout()
        self.rows: list[RuleRow] = []
        for r in spec.get("rules", []):
            self._add(r)
        add = QPushButton("Add rule"); add.clicked.connect(lambda: self._add())
        self.limit = QSpinBox(); self.limit.setRange(0, 100000); self.limit.setSpecialValueText("no limit")
        self.limit.setValue(int(spec.get("limit") or 0))
        self.order = QComboBox()
        for k in smart.ORDER_FIELDS:
            self.order.addItem(smart.FIELDS[k][0], k)
        self.order.setCurrentIndex(max(0, self.order.findData(spec.get("order", "title"))))
        self.desc = QCheckBox("descending"); self.desc.setChecked(bool(spec.get("desc")))
        form = QFormLayout()
        form.addRow("Name", self.name); form.addRow("Match", self.match)
        ob = QHBoxLayout(); ob.addWidget(self.order); ob.addWidget(self.desc)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addLayout(self.rows_box); lay.addWidget(add)
        f2 = QFormLayout(); f2.addRow("Limit to", self.limit); f2.addRow("Order by", ob)
        lay.addLayout(f2); lay.addStretch(); lay.addWidget(bb)

    def _add(self, rule=None):
        r = RuleRow(self._remove, rule); self.rows.append(r); self.rows_box.addWidget(r)

    def _remove(self, row):
        self.rows.remove(row); row.setParent(None); row.deleteLater()

    def _ok(self):
        if self.name.text().strip():
            self.accept()
        else:
            self.name.setFocus()

    def result_spec(self):
        return {"match": self.match.currentData(), "rules": [r.rule() for r in self.rows],
                "limit": self.limit.value(), "order": self.order.currentData(), "desc": self.desc.isChecked()}
