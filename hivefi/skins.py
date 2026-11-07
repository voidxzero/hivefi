"""Skins: built-in colour schemes plus user skins (JSON, optional QSS) from ~/.config/hivefi/skins."""
import json
from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .config import config_dir

# tokens: window text base alt button highlight hl_text accent
BUILTIN = {
    "Dark": dict(window="#252529", text="#dcdcdc", base="#1c1c1f", alt="#222226", button="#303036",
                 highlight="#1e78be", hl_text="#ffffff", accent="#4fc3f7"),
    "Light": dict(window="#f0f0f2", text="#1e1e1e", base="#ffffff", alt="#f5f6f8", button="#e4e5e9",
                  highlight="#2f80d0", hl_text="#ffffff", accent="#2f80d0"),
    "Classic": dict(window="#e9edf3", text="#1b1b1b", base="#ffffff", alt="#f1f5fb", button="#d5dde9",
                             highlight="#3d7fd8", hl_text="#ffffff", accent="#1f5fbf"),
    "Midnight": dict(window="#141a2b", text="#d6dcf0", base="#0e1322", alt="#121830", button="#1d2540",
                     highlight="#3a6fd8", hl_text="#ffffff", accent="#7aa2ff"),
    "AMOLED": dict(window="#000000", text="#e0e0e0", base="#000000", alt="#0b0b0b", button="#161616",
                   highlight="#6f3fd0", hl_text="#ffffff", accent="#bb86fc"),
    "Nord": dict(window="#2e3440", text="#d8dee9", base="#272c36", alt="#2b303b", button="#3b4252",
                 highlight="#5e81ac", hl_text="#ffffff", accent="#88c0d0"),
    "Solarized Dark": dict(window="#002b36", text="#93a1a1", base="#00212b", alt="#012630", button="#073642",
                           highlight="#268bd2", hl_text="#ffffff", accent="#b58900"),
}
DEFAULT = "Dark"
TOKENS = tuple(BUILTIN[DEFAULT])


def skins_dir() -> Path:
    p = config_dir() / "skins"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_user_skins(directory: Path | None = None) -> dict:
    """User skin = JSON with any of TOKENS, optional "base" (built-in name), "name", "qss", "qss_file"."""
    out = {}
    d = directory or skins_dir()
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            tokens = dict(BUILTIN.get(data.get("base", DEFAULT), BUILTIN[DEFAULT]))
            tokens.update({k: v for k, v in data.items() if k in TOKENS and QColor(v).isValid()})
            qss = data.get("qss", "")
            if data.get("qss_file"):
                qss += "\n" + (f.parent / data["qss_file"]).read_text()
            tokens["qss"] = qss
            out[data.get("name", f.stem)] = tokens
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return out


def all_skins(directory: Path | None = None) -> dict:
    return {**BUILTIN, **load_user_skins(directory)}


def build_qss(t: dict) -> str:
    return f"""
QDockWidget::title {{ background: {t['button']}; padding: 4px 8px; }}
QHeaderView::section {{ background: {t['button']}; color: {t['text']}; border: 0;
    border-right: 1px solid {t['window']}; padding: 3px 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['button']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['accent']}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: {t['button']}; border-radius: 2px; }}
QSlider::sub-page:vertical, QSlider::add-page:vertical {{ background: transparent; }}
QSlider::handle:vertical {{ background: {t['accent']}; height: 12px; margin: 0 -5px; border-radius: 6px; }}
QToolTip {{ background: {t['button']}; color: {t['text']}; border: 1px solid {t['accent']}; }}
QListWidget#albumgrid::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
"""


def apply_skin(app: QApplication, name: str, directory: Path | None = None) -> dict:
    """Apply skin by name (falls back to Dark). Returns the resolved tokens (incl. 'accent')."""
    skins = all_skins(directory)
    t = skins.get(name) or skins[DEFAULT]
    if app.style().objectName().lower() != "fusion":   # re-setting the style would dangle cached QStyle refs
        app.setStyle("Fusion")
    c = QColor
    p = QPalette()
    R = QPalette.ColorRole
    p.setColor(R.Window, c(t["window"])); p.setColor(R.WindowText, c(t["text"]))
    p.setColor(R.Base, c(t["base"])); p.setColor(R.AlternateBase, c(t["alt"]))
    p.setColor(R.Text, c(t["text"])); p.setColor(R.Button, c(t["button"])); p.setColor(R.ButtonText, c(t["text"]))
    p.setColor(R.Highlight, c(t["highlight"])); p.setColor(R.HighlightedText, c(t["hl_text"]))
    p.setColor(R.ToolTipBase, c(t["button"])); p.setColor(R.ToolTipText, c(t["text"]))
    p.setColor(R.Link, c(t["accent"])); p.setColor(R.BrightText, c("#ffffff"))
    dis = c(t["text"]); dis.setAlpha(110)
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, dis)
    ph = c(t["text"]); ph.setAlpha(120)
    p.setColor(R.PlaceholderText, ph)
    app.setPalette(p)
    app.setStyleSheet(build_qss(t) + "\n" + t.get("qss", ""))
    return t
