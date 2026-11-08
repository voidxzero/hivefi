"""Notice when music files are added to the library folders (and the inbox)."""
import os

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal


class FolderWatcher(QObject):
    changed = pyqtSignal()                        # something changed in a watched folder (bursts are merged)

    def __init__(self, parent=None, delay_ms=3000, limit=4000):
        super().__init__(parent)
        self.fsw = QFileSystemWatcher(self)
        self.delay_ms, self.limit = delay_ms, limit
        self.overflow = False                     # too many folders for the operating system's watch limit
        self.fsw.directoryChanged.connect(self._dir)
        self.timer = QTimer(self, singleShot=True, timeout=self.changed.emit)

    def watch(self, roots) -> int:
        """Watch these folders and everything below them. Returns how many folders are being watched."""
        old = self.fsw.directories()
        if old:
            self.fsw.removePaths(old)
        self.overflow = False
        for r in roots:
            self._add_tree(r)
        return len(self.fsw.directories())

    def _add_tree(self, root):
        for base, dirs, _files in os.walk(root):
            if len(self.fsw.directories()) >= self.limit:
                self.overflow = True; return
            self.fsw.addPath(base)

    def _dir(self, path):
        if os.path.isdir(path):
            self._add_tree(path)                  # new sub-folders are watched from now on
        self.timer.start(self.delay_ms)

    def stop(self):
        old = self.fsw.directories()
        if old:
            self.fsw.removePaths(old)
        self.timer.stop()
