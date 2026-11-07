"""Audio settings: output device/format, transitions (gapless/crossfade), volume & ReplayGain, sound shaping."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import audio


def _note(text):
    lbl = QLabel(text); lbl.setWordWrap(True); lbl.setStyleSheet("color: gray")
    return lbl


def _combo(items, current):
    box = QComboBox()
    for label, data in items:
        box.addItem(label, data)
    i = box.findData(current)
    box.setCurrentIndex(max(0, i))
    return box


class AudioDialog(QDialog):
    def __init__(self, cfg, backend, parent=None):
        super().__init__(parent)
        self.cfg, self.backend = cfg, backend
        self.setWindowTitle("Audio Settings")
        self.resize(620, 560)
        gst = audio.GST_OK
        tabs = QTabWidget()

        # ------------------------------------------------------------ Output
        out = QWidget(); f = QFormLayout(out)
        self.engine = _combo([("Automatic (GStreamer when available)", "auto"),
                              ("Qt Multimedia only (basic: no DSP, gapless or crossfade)", "qt")], cfg.engine)
        self.device = QComboBox(); self._fill_devices()
        refresh = QPushButton("Refresh"); refresh.clicked.connect(lambda: self._fill_devices(True))
        dev_row = QHBoxLayout(); dev_row.addWidget(self.device, 1); dev_row.addWidget(refresh)
        self.bitperfect = QCheckBox("Bit-perfect mode (bypass equalizer, ReplayGain, balance, crossfade and software volume)")
        self.bitperfect.setChecked(cfg.bit_perfect)
        self.rate = _combo([("Native (no resampling)", 0)] + [(f"{r:,} Hz", r) for r in
                                                              (44100, 48000, 88200, 96000, 176400, 192000)], cfg.output_rate)
        self.depth = _combo([("Native / automatic", "auto"), ("16-bit", "16"), ("24-bit (in 32-bit container)", "24"),
                             ("32-bit integer", "32"), ("32-bit float", "float")], str(cfg.output_depth))
        self.quality = QSpinBox(); self.quality.setRange(0, 10); self.quality.setValue(cfg.resample_quality)
        self.quality.setToolTip("0 = fastest, 10 = best (higher CPU use)")
        self.dither = _combo([("None", "none"), ("Rectangular (RPDF)", "rpdf"), ("Triangular (TPDF)", "tpdf"),
                              ("Triangular, high-frequency (TPDF-HF)", "tpdf-hf")], cfg.dither)
        self.shaping = _combo([("None", "none"), ("Error feedback", "error-feedback"), ("Simple", "simple"),
                               ("Medium", "medium"), ("High", "high")], cfg.noise_shaping)
        self.buffer = QSpinBox(); self.buffer.setRange(0, 5000); self.buffer.setSingleStep(50)
        self.buffer.setSpecialValueText("device default"); self.buffer.setSuffix(" ms"); self.buffer.setValue(cfg.buffer_ms)
        self.seek_acc = QCheckBox("Sample-accurate seeking (slower on some formats)"); self.seek_acc.setChecked(cfg.seek_accurate)
        self.path_lbl = QLabel(""); self.path_lbl.setWordWrap(True)
        chk = QPushButton("Check now"); chk.clicked.connect(self._refresh_path)
        path_row = QHBoxLayout(); path_row.addWidget(self.path_lbl, 1); path_row.addWidget(chk)
        f.addRow("Engine", self.engine); f.addRow("Output device", dev_row); f.addRow(self.bitperfect)
        f.addRow("Sample rate", self.rate); f.addRow("Bit depth", self.depth)
        f.addRow("Resampler quality", self.quality); f.addRow("Dither", self.dither)
        f.addRow("Noise shaping", self.shaping); f.addRow("Output buffer", self.buffer); f.addRow(self.seek_acc)
        f.addRow("Signal path", path_row)
        f.addRow(_note("Dither and noise shaping only apply when the output is converted to an integer bit depth. "
                       "For true bit-perfect output pick an ALSA hardware (hw:) device; PipeWire/Pulse may still mix."))
        tabs.addTab(out, "Output")

        # ------------------------------------------------------------ Transitions
        tr = QWidget(); f = QFormLayout(tr)
        self.gapless = QCheckBox("Gapless playback"); self.gapless.setChecked(cfg.gapless)
        self.xf = QDoubleSpinBox(); self.xf.setRange(0, 12); self.xf.setSingleStep(0.5); self.xf.setDecimals(1)
        self.xf.setSuffix(" s"); self.xf.setSpecialValueText("off"); self.xf.setValue(cfg.crossfade_ms / 1000)
        self.xf_curve = _combo([("Equal power (smooth)", "equal-power"), ("Linear", "linear")], cfg.crossfade_curve)
        self.xf_manual = QCheckBox("Crossfade when skipping tracks manually"); self.xf_manual.setChecked(cfg.crossfade_manual)
        self.xf_album = QCheckBox("Also crossfade between tracks of the same album")
        self.xf_album.setChecked(cfg.crossfade_same_album)
        self.fade_pause = QSpinBox(); self.fade_pause.setRange(0, 3000); self.fade_pause.setSingleStep(50)
        self.fade_pause.setSuffix(" ms"); self.fade_pause.setSpecialValueText("off"); self.fade_pause.setValue(cfg.fade_pause_ms)
        f.addRow(self.gapless); f.addRow("Crossfade length", self.xf); f.addRow("Crossfade curve", self.xf_curve)
        f.addRow(self.xf_manual); f.addRow(self.xf_album); f.addRow("Fade on pause / resume", self.fade_pause)
        f.addRow(_note("Consecutive tracks of one album normally stay gapless (live albums, DJ mixes) unless you "
                       "tick the album option. Crossfade needs an output that can be opened twice (PipeWire, Pulse, "
                       "ALSA dmix); on exclusive hardware devices it falls back to gapless automatically."))
        tabs.addTab(tr, "Transitions")

        # ------------------------------------------------------------ Volume
        vo = QWidget(); f = QFormLayout(vo)
        self.curve = _combo([("Linear", "linear"), ("Cubic (natural feel)", "cubic"), ("Logarithmic (−60…0 dB)", "db")],
                            cfg.volume_curve)
        self.rg = _combo([("Off", "off"), ("Track gain", "track"), ("Album gain", "album")], cfg.rg_mode)
        self.rg_pre = self._dbspin(-15, 15, cfg.rg_preamp)
        self.rg_fall = self._dbspin(-15, 6, cfg.rg_fallback)
        self.rg_head = self._dbspin(0, 6, cfg.rg_headroom)
        self.limiter = QCheckBox("Peak limiter (catches clipping from EQ / preamp)"); self.limiter.setChecked(cfg.limiter)
        self.lim_thr = self._dbspin(-12, 0, cfg.limiter_threshold_db)
        f.addRow("Volume slider curve", self.curve); f.addRow("ReplayGain", self.rg)
        f.addRow("Pre-amp for tagged tracks", self.rg_pre); f.addRow("Gain for untagged tracks", self.rg_fall)
        f.addRow("Extra headroom", self.rg_head); f.addRow(self.limiter); f.addRow("Limiter threshold", self.lim_thr)
        f.addRow(_note("ReplayGain reads the tags in each file (and never lets the gain clip the track's own peak). "
                       "Tracks played with transcoding from a server have no tags, so the untagged gain is used."))
        tabs.addTab(vo, "Volume")

        # ------------------------------------------------------------ Sound
        so = QWidget(); f = QFormLayout(so)
        self.balance = QSlider(Qt.Orientation.Horizontal); self.balance.setRange(-100, 100)
        self.balance.setValue(round(cfg.balance * 100)); self.bal_lbl = QLabel("")
        self.balance.valueChanged.connect(self._bal_text); self._bal_text()
        bal_row = QHBoxLayout(); bal_row.addWidget(self.balance, 1); bal_row.addWidget(self.bal_lbl)
        self.mono = QCheckBox("Mono downmix"); self.mono.setChecked(cfg.mono)
        self.swap = QCheckBox("Swap left / right channels"); self.swap.setChecked(cfg.swap_channels)
        self.cross = _combo([("Off", "off"), ("Default (700 Hz, 4.5 dB)", "default"), ("Cmoy (700 Hz, 6 dB)", "cmoy"),
                             ("Jan Meier (650 Hz, 9.5 dB)", "jmeier")], cfg.crossfeed)
        f.addRow("Balance", bal_row); f.addRow(self.mono); f.addRow(self.swap); f.addRow("Headphone crossfeed", self.cross)
        f.addRow(_note("Crossfeed mixes a little of each channel into the other to reduce the extreme stereo "
                       "separation of headphones. The equalizer has its own panel (Playback → Equalizer)."))
        tabs.addTab(so, "Sound")

        banner = None
        if not gst:
            banner = _note("GStreamer (python3-gobject + gst plugins) was not found, so only the basic Qt engine is "
                           "available. Install GStreamer to unlock gapless, crossfade, EQ and the options below.")
            for w in (out, tr, vo, so):
                w.setEnabled(False)
        else:
            for w, el in ((self.cross, "bs2b"), (self.swap, "audiomixmatrix"), (self.limiter, "audiodynamic"),
                          (self.rg, "rgvolume"), (self.balance, "audiopanorama")):
                if not audio.have(el):
                    w.setEnabled(False); w.setToolTip(f"GStreamer element '{el}' is not installed")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        lay = QVBoxLayout(self)
        if banner:
            lay.addWidget(banner)
        lay.addWidget(tabs); lay.addWidget(bb)
        self.on_apply = None
        self._refresh_path()

    # ---- helpers
    def _dbspin(self, lo, hi, v):
        s = QDoubleSpinBox(); s.setRange(lo, hi); s.setSingleStep(0.5); s.setDecimals(1); s.setSuffix(" dB"); s.setValue(v)
        return s

    def _bal_text(self):
        v = self.balance.value()
        self.bal_lbl.setText("center" if v == 0 else f"{'L' if v < 0 else 'R'} {abs(v)}%")

    def _fill_devices(self, refresh=False):
        cur = self.device.currentData() if self.device.count() else self.cfg.output_device
        self.device.clear()
        for key, label in audio.list_output_devices(refresh):
            self.device.addItem(label, key)
        i = self.device.findData(cur)
        if i < 0 and cur:
            self.device.addItem(f"{cur} (not found)", cur); i = self.device.count() - 1
        self.device.setCurrentIndex(max(0, i))

    def _refresh_path(self):
        caps = self.backend.output_caps()
        if not audio.GST_OK:
            self.path_lbl.setText("Qt engine (no processing information)")
        elif caps:
            self.path_lbl.setText(f"{caps['rate']:,} Hz · {caps['format']} · {caps['channels']} ch (leaving the DSP chain)")
        else:
            self.path_lbl.setText("Play a track to see the output format.")

    def apply(self):
        c = self.cfg
        c.engine = self.engine.currentData(); c.output_device = self.device.currentData() or ""
        c.bit_perfect = self.bitperfect.isChecked(); c.output_rate = self.rate.currentData()
        c.output_depth = self.depth.currentData(); c.resample_quality = self.quality.value()
        c.dither, c.noise_shaping = self.dither.currentData(), self.shaping.currentData()
        c.buffer_ms, c.seek_accurate = self.buffer.value(), self.seek_acc.isChecked()
        c.gapless, c.crossfade_ms = self.gapless.isChecked(), round(self.xf.value() * 1000)
        c.crossfade_curve, c.crossfade_manual = self.xf_curve.currentData(), self.xf_manual.isChecked()
        c.crossfade_same_album, c.fade_pause_ms = self.xf_album.isChecked(), self.fade_pause.value()
        c.volume_curve, c.rg_mode = self.curve.currentData(), self.rg.currentData()
        c.rg_preamp, c.rg_fallback, c.rg_headroom = self.rg_pre.value(), self.rg_fall.value(), self.rg_head.value()
        c.limiter, c.limiter_threshold_db = self.limiter.isChecked(), self.lim_thr.value()
        c.balance, c.mono, c.swap_channels = self.balance.value() / 100, self.mono.isChecked(), self.swap.isChecked()
        c.crossfeed = self.cross.currentData()
        c.save()
        if self.on_apply:
            self.on_apply()
        self._refresh_path()

    def _ok(self):
        self.apply(); self.accept()
