"""Graphic equalizer panel: 10 / 15 / 31 bands, presets, preamp. Mutates the config, then emits `changed`."""
import math

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QPushButton,
                             QScrollArea, QSlider, QVBoxLayout, QWidget)

from ..audio import EQ_LAYOUTS, resample_gains

PRESETS_10 = {   # defined on the 10-band layout; other layouts are interpolated from these
    "Flat": [0] * 10,
    "Bass Boost": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Bass Reducer": [-6, -5, -4, -2, 0, 0, 0, 0, 0, 0],
    "Treble Boost": [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
    "Rock": [4, 3, 2, -1, -2, -1, 2, 3, 4, 5],
    "Pop": [-1, 2, 4, 5, 3, 0, -1, -1, -1, -1],
    "Jazz": [3, 2, 1, 2, -2, -2, 0, 1, 2, 3],
    "Classical": [4, 3, 3, 2, -1, -1, 0, 2, 3, 4],
    "Electronic": [5, 4, 1, 0, -2, 2, 1, 1, 4, 5],
    "Vocal": [-2, -3, -3, 1, 4, 4, 3, 1, 0, -1],
    "Loudness": [6, 4, 1, 0, -1, 0, -1, -3, 4, 2],
    "Headphones (warm)": [3, 3, 2, 1, 0, -1, -1, 0, -1, -2],
    "Small speakers": [-6, -4, -2, 0, 1, 2, 2, 1, 0, 0],
}
CUSTOM = "Custom"


def hz_label(f):
    if f >= 1000:
        return f"{f / 1000:g}k"
    return f"{f:g}"


class EqualizerPanel(QWidget):
    changed = pyqtSignal()          # settings on cfg were modified
    persist = pyqtSignal()          # user presets changed -> save config

    def __init__(self, cfg, available: bool):
        super().__init__()
        self.cfg = cfg
        self.available = available
        self._loading = False
        self.sliders: list[QSlider] = []
        self.val_lbls: list[QLabel] = []

        self.enable = QCheckBox("Enable equalizer"); self.enable.setChecked(cfg.eq_enabled and available)
        self.enable.setEnabled(available)
        self.layout_box = QComboBox()
        for n in EQ_LAYOUTS:
            self.layout_box.addItem(f"{n} bands", n)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(cfg.eq_bands)))
        self.preset = QComboBox()
        self.btn_save, self.btn_del = QPushButton("Save…"), QPushButton("Delete")
        self.btn_reset, self.btn_auto = QPushButton("Reset"), QPushButton("Auto preamp")
        self.btn_auto.setCheckable(True); self.btn_auto.setChecked(bool(cfg.eq_auto_preamp))
        self.btn_auto.setToolTip("While on, the preamp follows the curve: it is lowered by the highest point of the whole "
                                 "curve (neighbouring bands add up) so the EQ cannot clip.\n"
                                 "Moving the Pre slider yourself turns this off.")
        self.shelf = QCheckBox("Shelving end bands"); self.shelf.setChecked(cfg.eq_shelf_ends)
        self.shelf.setToolTip("Lowest/highest band act as low/high shelves instead of peaking filters")
        row1 = QHBoxLayout()
        for w in (self.enable, self.layout_box, self.preset):
            row1.addWidget(w)
        row1.setStretch(2, 1)
        row2 = QHBoxLayout()
        for w in (self.btn_save, self.btn_del, self.btn_reset, self.btn_auto, self.shelf):
            row2.addWidget(w)
        row2.addStretch()

        self.grid_host = QWidget(); self.grid = QGridLayout(self.grid_host); self.grid.setHorizontalSpacing(2)
        scroll = QScrollArea(); scroll.setWidget(self.grid_host); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.status = QLabel(""); self.status.setWordWrap(True)
        if not available:
            self.status.setText("Equalizer unavailable: GStreamer (python gi + equalizer-nbands plugin) not found.")
        lay = QVBoxLayout(self); lay.addLayout(row1); lay.addLayout(row2); lay.addWidget(scroll, 1)
        lay.addWidget(self.status)

        self._build_sliders(cfg.eq_bands)
        self._fill_presets(cfg.eq_preset)
        self._set_values(cfg.eq_gains, cfg.eq_preamp, emit=False)
        self.enable.toggled.connect(self._emit)
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self.preset.currentTextChanged.connect(self._preset_chosen)
        self.shelf.toggled.connect(self._emit)
        self.btn_save.clicked.connect(self._save_preset)
        self.btn_del.clicked.connect(self._delete_preset)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_auto.toggled.connect(self._auto_toggled)
        self._sync_buttons()

    # ---- layout
    @property
    def n(self) -> int:
        return self.layout_box.currentData()

    def _build_sliders(self, n):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.sliders, self.val_lbls = [], []
        cols = [("Pre", None)] + [(hz_label(f), f) for f in EQ_LAYOUTS[n]]
        for c, (name, f) in enumerate(cols):
            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(-24, 24) if f else sl.setRange(-24, 12)       # half-dB steps
            sl.setTickPosition(QSlider.TickPosition.TicksRight); sl.setTickInterval(12)
            sl.setMinimumHeight(140)
            val = QLabel("0"); val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab = QLabel(name); lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if n > 15:                                             # 31 bands: keep columns compact
                small = lab.font(); small.setPointSize(max(6, small.pointSize() - 2))
                lab.setFont(small); val.setFont(small)
            self.grid.addWidget(val, 0, c); self.grid.addWidget(sl, 1, c, Qt.AlignmentFlag.AlignHCenter)
            self.grid.addWidget(lab, 2, c)
            sl.valueChanged.connect(lambda _v, i=c: self._slider_moved(i))
            self.sliders.append(sl); self.val_lbls.append(val)

    def _layout_changed(self):
        if self._loading:
            return
        old, pre = self.gains(), self.preamp()          # read before the sliders are rebuilt
        gains = resample_gains(old, len(old), self.n)
        self._loading = True
        self._build_sliders(self.n)
        self._loading = False
        self._fill_presets(CUSTOM)
        self._set_values(gains, pre)

    # ---- presets
    def all_presets(self):
        n = self.n
        out = {k: resample_gains(v, 10, n) for k, v in PRESETS_10.items()}
        for k, v in self.cfg.eq_user_presets.items():
            out[k] = resample_gains(v["gains"], v["bands"], n)
        return out

    def _fill_presets(self, current):
        self._loading = True
        self.preset.clear(); self.preset.addItems(list(self.all_presets()) + [CUSTOM])
        self.preset.setCurrentText(current if self.preset.findText(current) >= 0 else CUSTOM)
        self._loading = False

    def gains(self):
        return [s.value() / 2 for s in self.sliders[1:]]

    def preamp(self):
        return self.sliders[0].value() / 2

    def _set_values(self, gains, preamp, emit=True):
        self._loading = True
        for s, g in zip(self.sliders[1:], list(gains) + [0] * 40):
            s.setValue(round(g * 2))
        self.sliders[0].setValue(round(preamp * 2))
        self._loading = False
        self._labels()
        if emit:
            self._emit()

    def _labels(self):
        for s, l in zip(self.sliders, self.val_lbls):
            l.setText(f"{s.value() / 2:+g}" if s.value() else "0")

    def _slider_moved(self, _i):
        if self._loading:
            return
        if _i == 0 and self.btn_auto.isChecked():              # a hand-set preamp: stop following the curve
            self.btn_auto.blockSignals(True); self.btn_auto.setChecked(False); self.btn_auto.blockSignals(False)
            self.cfg.eq_auto_preamp = False
        self._labels()
        match = next((n for n, g in self.all_presets().items()
                      if [float(x) for x in g] == self.gains()), CUSTOM)
        self._loading = True; self.preset.setCurrentText(match); self._loading = False
        self._sync_buttons(); self._emit()

    def _preset_chosen(self, name):
        if self._loading:
            return
        g = self.all_presets().get(name)
        if g is not None:
            self._set_values(g, self.preamp())
        self._sync_buttons()

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = name.strip()
        if not ok or not name or name in PRESETS_10 or name == CUSTOM:
            return
        self.cfg.eq_user_presets[name] = {"bands": self.n, "gains": self.gains()}
        self._fill_presets(name); self._sync_buttons(); self.persist.emit()

    def _delete_preset(self):
        name = self.preset.currentText()
        if name in self.cfg.eq_user_presets:
            del self.cfg.eq_user_presets[name]
            self._fill_presets(CUSTOM); self._sync_buttons(); self.persist.emit()

    def _reset(self):
        self._set_values([0] * self.n, 0)
        self._loading = True; self.preset.setCurrentText("Flat"); self._loading = False
        self._sync_buttons()

    def _auto_value(self) -> int:
        """The Pre slider position (half dB) that offsets the peak of the whole curve: bands add up."""
        from ..audio import eq_peak_db
        return -math.ceil(eq_peak_db(self.n, self.gains(), self.shelf.isChecked()) * 2 - 1e-6)

    def _auto_preamp(self):
        """Set the preamp from the curve once."""
        was, self._loading = self._loading, True
        self.sliders[0].setValue(self._auto_value())
        self._loading = was
        self._labels(); self._emit()

    def _auto_toggled(self, on):
        self.cfg.eq_auto_preamp = bool(on)
        self._emit() if on else self.persist.emit()

    def _sync_buttons(self):
        self.btn_del.setEnabled(self.preset.currentText() in self.cfg.eq_user_presets)
        self.btn_save.setEnabled(self.preset.currentText() == CUSTOM)

    def _emit(self, *_):
        if self._loading:
            return
        c = self.cfg
        if self.btn_auto.isChecked() and self.sliders[0].value() != self._auto_value():
            self._loading = True; self.sliders[0].setValue(self._auto_value()); self._loading = False
            self._labels()
        c.eq_enabled = self.enable.isChecked()
        c.eq_bands, c.eq_gains, c.eq_preamp = self.n, self.gains(), self.preamp()
        c.eq_preset, c.eq_shelf_ends = self.preset.currentText(), self.shelf.isChecked()
        self.changed.emit()

    def set_status(self, text):
        if self.available:
            self.status.setText(text)

    def force_disabled(self):
        self.enable.blockSignals(True); self.enable.setChecked(False); self.enable.blockSignals(False)
        self.cfg.eq_enabled = False
