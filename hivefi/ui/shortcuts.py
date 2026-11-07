"""Customisable keyboard shortcuts for every menu command."""
from PyQt6.QtGui import QAction, QKeySequence


def _walk(menu, path):
    for a in menu.actions():
        if a.menu() is not None:
            yield from _walk(a.menu(), path + [a.text().replace("&", "")])
        elif a.text() and not a.isSeparator():
            yield " ▸ ".join(path + [a.text().replace("&", "")]), a


def collect(menubar) -> dict:
    """{'Playback ▸ Play/Pause': QAction, ...} for the commands that exist from the start (the Libraries, Skin and
    Layouts menus are rebuilt on demand and are not included)."""
    out = {}
    for top in menubar.actions():
        if top.menu() is not None:
            for key, a in _walk(top.menu(), [top.text().replace("&", "")]):
                out.setdefault(key, a)
    return out


def portable(seq) -> str:
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.PortableText) if seq else ""


def conflicts(mapping: dict) -> dict:
    """{shortcut: [commands]} for shortcuts used by more than one command."""
    seen: dict[str, list] = {}
    for key, seq in mapping.items():
        if seq:
            seen.setdefault(seq, []).append(key)
    return {s: ks for s, ks in seen.items() if len(ks) > 1}
