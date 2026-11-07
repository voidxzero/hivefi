"""Skins: built-in colour schemes plus user skins (JSON, optional QSS) from ~/.config/hivefi/skins."""
import json
from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .config import config_dir

from .skinpack import SPINDLE_SKINS

# core tokens: window text base alt button highlight hl_text accent
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
# optional richer tokens (gradients, bevels, LCD colours...). Skins that have them get a fully styled UI.
EXTRA_COLORS = ("panel_top", "panel_bot", "border", "hilite", "dim", "accent_text", "btn_top", "btn_bot",
                "title_top", "title_bot", "lcd_bg", "lcd_fg", "lcd_dim", "good", "warn", "bad", "caption")
EXTRA_SETTINGS = ("r", "r_panel", "font", "font_size", "style")


def _mix(a: str, b: str, t: float = 0.5) -> str:
    x, y = QColor(a), QColor(b)
    return QColor(round(x.red() + (y.red() - x.red()) * t), round(x.green() + (y.green() - x.green()) * t),
                  round(x.blue() + (y.blue() - x.blue()) * t)).name()


def _px(v, default=3) -> int:
    try:
        return max(0, min(20, int(str(v).replace("px", "").strip())))
    except (TypeError, ValueError):
        return default


def from_spindle(c: dict, corners: dict | None = None, extra: dict | None = None) -> dict:
    """Spindle skin colours (bg/inset/sel/btn_top...) -> Hivefi tokens."""
    corners = {**(corners or {}), **{k: v for k, v in (extra or {}).items() if k in ("r", "r_panel")}}
    t = dict(window=c["bg"], text=c["text"], base=c["inset"], alt=c.get("alt", c["bg"]),
             button=_mix(c.get("btn_top", c["panel_top"]), c.get("btn_bot", c["panel_bot"])),
             highlight=c["sel"], hl_text=c["sel_text"], accent=c["accent"])
    t.update({k: c[k] for k in EXTRA_COLORS if k in c})
    t["r"], t["r_panel"] = _px(corners.get("r"), 3), _px(corners.get("r_panel"), 4)
    for k in ("font", "font_size", "style"):
        v = (extra or {}).get(k, c.get(k))
        if v:
            t[k] = str(v)
    return t


def _pack() -> dict:
    return {name: from_spindle(c, extra=c) for name, c in SPINDLE_SKINS.items()}


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
            known = {**BUILTIN, **_pack()}
            tokens = dict(known.get(data.get("base", DEFAULT), BUILTIN[DEFAULT]))
            if "spindle_skin" in data or "colors" in data:           # a skin made for Spindle
                cols = {k: v for k, v in (data.get("colors") or {}).items() if QColor(str(v)).isValid()}
                base = dict(known.get(data.get("base", "Modern Night"), known["Modern Night"]))
                spin = {**{k: v for k, v in SPINDLE_SKINS.get(data.get("base", "Modern Night"),
                                                               SPINDLE_SKINS["Modern Night"]).items()}, **cols}
                tokens = from_spindle(spin, data.get("corners"), data)
                tokens = {**base, **tokens}
            tokens.update({k: v for k, v in data.items() if k in TOKENS + EXTRA_COLORS and QColor(str(v)).isValid()})
            tokens.update({k: v for k, v in data.items() if k in EXTRA_SETTINGS and k not in ("r", "r_panel")})
            qss = data.get("qss", "")
            if data.get("qss_file"):
                qss += "\n" + (f.parent / data["qss_file"]).read_text()
            tokens["qss"] = qss
            out[data.get("name", f.stem)] = tokens
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return out


def all_skins(directory: Path | None = None) -> dict:
    return {**BUILTIN, **_pack(), **load_user_skins(directory)}


def import_skin(path: str, directory: Path | None = None) -> str:
    """Validate a skin file (Hivefi or Spindle format) and copy it into the skins folder. Returns its name."""
    f = Path(path)
    data = json.loads(f.read_text())
    if not isinstance(data, dict) or not (data.get("name") or f.stem):
        raise ValueError("not a skin file")
    cols = data.get("colors") if isinstance(data.get("colors"), dict) else data
    if not any(k in cols for k in TOKENS + EXTRA_COLORS + ("bg", "inset", "sel")):
        raise ValueError("this file has no colours in it")
    name = str(data.get("name") or f.stem)[:40]
    dest = (directory or skins_dir()) / (("".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "skin")
                                         .replace(" ", "-").lower() + ".json")
    dest.write_text(json.dumps({**data, "name": name}, indent=2))
    return name


def _grad(a, b, horizontal=False):
    x2, y2 = (1, 0) if horizontal else (0, 1)
    return f"qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, stop:0 {a}, stop:1 {b})"


def build_qss(t: dict) -> str:
    base = f"""
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
    if "btn_top" not in t:                                  # a plain colour skin
        return base
    r, rp = int(t.get("r", 3)), int(t.get("r_panel", 4))
    old = t.get("style") == "classic98"
    bd = t["border"]
    face_raised = "outset" if old else "solid"
    out = base + f"""
QToolBar {{ background: {_grad(t['panel_top'], t['panel_bot'])}; border: 0; border-bottom: 1px solid {bd};
    spacing: 4px; padding: 2px; }}
QMenuBar {{ background: {t['panel_bot']}; color: {t['text']}; }}
QMenuBar::item {{ padding: 4px 8px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
QMenu {{ background: {t['panel_bot']}; color: {t['text']}; border: 1px solid {bd}; }}
QMenu::item {{ padding: 4px 22px; }}
QMenu::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
QMenu::separator {{ height: 1px; background: {bd}; margin: 3px 6px; }}
QPushButton, QToolButton {{ background: {_grad(t['btn_top'], t['btn_bot'])}; color: {t['text']};
    border: {'2px' if old else '1px'} {face_raised} {bd if not old else t['btn_top']}; border-radius: {r}px; padding: 3px 10px;
    min-height: 18px; }}
QToolButton {{ padding: 3px 5px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {t['accent'] if not old else t['btn_top']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t['btn_bot']}; {'border-style: inset;' if old else ''} }}
QPushButton:checked, QToolButton:checked {{ background: {t['accent']}; color: {t.get('accent_text', t['hl_text'])};
    border-color: {t['accent']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t.get('dim', t['text'])}; }}
QLineEdit, QPlainTextEdit, QTextEdit {{ background: {t['base']}; color: {t['text']};
    border: {'2px inset ' + t['btn_top'] if old else '1px solid ' + bd}; border-radius: {r}px; padding: 2px 4px;
    selection-background-color: {t['highlight']}; selection-color: {t['hl_text']}; }}
QListView, QTreeView, QTableView {{ background: {t['base']}; alternate-background-color: {t['alt']};
    border: {'2px inset ' + t['btn_top'] if old else '1px solid ' + bd}; border-radius: {0 if old else r}px; }}
QDockWidget {{ color: {t['text']}; }}
QDockWidget::title {{ background: {_grad(t['title_top'], t['title_bot'])}; color: {t['text']};
    border: 1px solid {bd}; padding: 4px 8px; }}
QHeaderView::section {{ background: {_grad(t['title_top'], t['title_bot'])}; color: {t['text']}; border: 0;
    border-right: 1px solid {bd}; border-bottom: 1px solid {bd}; padding: 3px 6px; }}
QSplitter::handle {{ background: {t['panel_bot']}; }}
QStatusBar {{ background: {t['panel_bot']}; color: {t.get('dim', t['text'])}; }}
QTabBar::tab {{ background: {_grad(t['title_top'], t['title_bot'])}; color: {t['text']}; border: 1px solid {bd};
    padding: 4px 10px; border-top-left-radius: {r}px; border-top-right-radius: {r}px; }}
QTabBar::tab:selected {{ background: {t['panel_top']}; border-bottom: 2px solid {t['accent']}; }}
QScrollBar:vertical {{ background: {t['base']}; width: 13px; margin: 0; }}
QScrollBar:horizontal {{ background: {t['base']}; height: 13px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {_grad(t['btn_top'], t['btn_bot'], True)}; border: 1px solid {bd};
    border-radius: {r}px; min-height: 26px; }}
QScrollBar::handle:horizontal {{ background: {_grad(t['btn_top'], t['btn_bot'])}; border: 1px solid {bd};
    border-radius: {r}px; min-width: 26px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSlider::groove:horizontal, QSlider::groove:vertical {{ background: {t.get('lcd_dim', t['button'])}; }}
QProgressBar {{ background: {t.get('lcd_bg', t['base'])}; border: 1px solid {bd}; border-radius: {r}px;
    color: {t['text']}; text-align: center; }}
QProgressBar::chunk {{ background: {t.get('lcd_fg', t['accent'])}; border-radius: {r}px; }}
"""
    if t.get("font"):
        out += f"\nQWidget {{ font-family: {t['font']}; {'font-size: ' + t['font_size'] + ';' if t.get('font_size') else ''} }}\n"
    return out


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
    p.setColor(R.Mid, c(_mix(t["window"], t["text"], 0.22)))          # unplayed part of the seek bar, bar tracks
    p.setColor(R.Midlight, c(_mix(t["window"], t["text"], 0.12)))
    p.setColor(R.Dark, c(_mix(t["window"], "#000000", 0.45))); p.setColor(R.Shadow, c("#000000"))
    p.setColor(R.Light, c(_mix(t["window"], "#ffffff", 0.35)))
    dis = c(t["text"]); dis.setAlpha(110)
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, dis)
    ph = c(t["text"]); ph.setAlpha(120)
    p.setColor(R.PlaceholderText, ph)
    app.setPalette(p)
    app.setStyleSheet(build_qss(t) + "\n" + t.get("qss", ""))
    return t
