from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout


class StatsDialog(QDialog):
    def __init__(self, html, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library Statistics"); self.resize(560, 640)
        self.view = QTextBrowser(); self.view.setHtml(html)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(self.view); lay.addWidget(bb)
