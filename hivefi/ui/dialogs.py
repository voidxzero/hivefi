from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout


class TagDialog(QDialog):
    """Edit tags for one or many local tracks. Blank fields are left unchanged in multi-edit."""
    FIELDS = [("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
              ("genre", "Genre"), ("year", "Year"), ("track_no", "Track #"), ("disc_no", "Disc #"),
              ("composer", "Composer"), ("bpm", "BPM"), ("comment", "Comment")]

    def __init__(self, tracks, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Tags ({len(tracks)} track{'s' if len(tracks) != 1 else ''})")
        self.edits = {}
        form = QFormLayout()
        for key, label in self.FIELDS:
            vals = {t.get(key) for t in tracks}
            e = QLineEdit()
            if len(tracks) == 1 or len(vals) == 1:
                v = next(iter(vals)); e.setText("" if v in (None, 0) else str(v))
            else:
                e.setPlaceholderText("<multiple values — leave blank to keep>")
            if len(tracks) > 1 and key in ("title", "track_no"):
                e.setEnabled(False)
            self.edits[key] = e; form.addRow(label, e)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(bb)
        self.resize(420, 0)

    def values(self):
        out = {}
        for k, e in self.edits.items():
            v = e.text().strip()
            if v and e.isEnabled():
                out[k] = int(v) if k in ("year", "track_no", "disc_no", "bpm") and v.isdigit() else v
        return out
