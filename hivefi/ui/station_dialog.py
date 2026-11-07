from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from .. import radio


class StationDialog(QDialog):
    """Add or edit an internet radio station."""
    def __init__(self, st=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Radio Station" if st else "Add Radio Station")
        st = st or {}
        self.name = QLineEdit(st.get("name", "")); self.url = QLineEdit(st.get("url", ""))
        self.genre = QLineEdit(st.get("genre", "")); self.home = QLineEdit(st.get("homepage", ""))
        self.url.setPlaceholderText("http://example.com:8000/stream")
        self.msg = QLabel(""); self.msg.setStyleSheet("color: #e5483f;")
        form = QFormLayout()
        for label, w in (("Name", self.name), ("Stream URL", self.url), ("Genre", self.genre), ("Website", self.home)):
            form.addRow(label, w)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.accepted.connect(self._ok); self.bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(self.msg); lay.addWidget(self.bb)
        self.resize(460, 0)

    def _ok(self):
        if not radio.valid_url(self.url.text()):
            self.msg.setText("Enter a stream address that starts with http:// or https://"); return
        if not self.name.text().strip():
            self.name.setText(self.url.text().strip())
        self.accept()

    def values(self):
        return dict(name=self.name.text().strip(), url=self.url.text().strip(), genre=self.genre.text().strip(),
                    homepage=self.home.text().strip())
