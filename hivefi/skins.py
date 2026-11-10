"""Skins: built-in colour schemes plus user skins (JSON, optional QSS) from ~/.config/hivefi/skins."""
import json
from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .config import cache_dir, config_dir

from .skinpack import SPINDLE_SKINS

# core tokens: window text base alt button highlight hl_text accent
BUILTIN = {
    "Dark": dict(window="#252529", text="#dcdcdc", base="#1c1c1f", alt="#222226", button="#303036",
                 highlight="#1e78be", hl_text="#ffffff", accent="#4fc3f7"),
    "MusicBee": dict(window="#1b1b1b", text="#dcdcdc", base="#161616", alt="#161616", button="#262626",
                     highlight="#2a2a2a", hl_text="#ffffff", accent="#1fa8e8", style="musicbee", dim="#6b6b6b",
                     border="#262626", panel_top="#1b1b1b", panel_bot="#1b1b1b", r=0, r_panel=0),
    "MusicBee Green": dict(window="#1a1a1a", text="#dcdcdc", base="#161616", alt="#161616", button="#262626",
                           highlight="#5fb720", hl_text="#ffffff", accent="#6cc424", style="musicbee", dim="#6b6b6b",
                           border="#262626", panel_top="#1a1a1a", panel_bot="#1a1a1a", r=0, r_panel=0),
    "MusicBee Blue": dict(window="#0b1730", text="#e2e8f4", base="#0a1226", alt="#0e1b38", button="#14264a",
                          highlight="#1b3a73", hl_text="#ffffff", accent="#4da3ff", style="musicbee", dim="#7d8bab",
                          border="#14264a", panel_top="#0b1730", panel_bot="#0b1730", r=0, r_panel=0),
    "MusicBee Glow": dict(window="#b3c4c4", text="#2f343a", base="#c2ccc8", alt="#c2ccc8", button="#9db0b2",
                          highlight="#7f9396", hl_text="#ffffff", accent="#2f5568", style="musicbee", glow=True,
                          dim="#59656b", border="#9db0b2", panel_top="#b3c4c4", panel_bot="#b3c4c4", r=12, r_panel=14),
    "Neomorphic": dict(window="#e0e5ec", text="#44495a", base="#e6ebf2", alt="#dde2e9", button="#e0e5ec",
                       highlight="#cfd8e6", hl_text="#2f3547", accent="#5b7cfa", style="neo", dim="#8d96a8",
                       border="#c3cad6", panel_top="#e0e5ec", panel_bot="#e0e5ec", btn_top="#e0e5ec",
                       btn_bot="#e0e5ec", hilite="#ffffff", accent_text="#ffffff", r=12, r_panel=14),
    "Neomorphic Dark": dict(window="#2b2f36", text="#c9ced8", base="#2e323a", alt="#2a2e35", button="#2b2f36",
                            highlight="#3a404b", hl_text="#ffffff", accent="#6c8cff", style="neo", dim="#7b8392",
                            border="#23262c", panel_top="#2b2f36", panel_bot="#2b2f36", btn_top="#2b2f36",
                            btn_bot="#2b2f36", hilite="#3a3f49", accent_text="#ffffff", r=12, r_panel=14),
    "Winamp Classic": dict(window="#2b2c38", text="#00e000", base="#000000", alt="#000000", button="#3b3d4e",
                           highlight="#0000c6", hl_text="#ffffff", accent="#00e000", style="winamp",
                           dim="#008800", border="#12121a", panel_top="#4a4c60", panel_bot="#262733",
                           btn_top="#52546a", btn_bot="#30313f", hilite="#6a6c84", title_top="#5a5c78",
                           title_bot="#2a2b3a", lcd_bg="#000000", lcd_fg="#00e000", lcd_dim="#004400",
                           accent_text="#000000", caption="#d6d6e0", r=0, r_panel=0),
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
EXTRA_SETTINGS = ("r", "r_panel", "font", "font_size", "style", "category")


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


def _is_dark(color: str) -> bool:
    return QColor(color).lightness() < 128


def complete_tokens(t: dict) -> dict:
    """A copy of the skin with every rich token filled in. Plain colour skins (Dark, AMOLED, Nord, user skins with
    only the core colours...) only define window/text/base/button/highlight/accent; the panel, button, border and
    title colours are derived from those so they get the same fully styled UI as the skin-pack skins."""
    t = dict(t)
    dark = _is_dark(t["window"])
    w, tx, btn = t["window"], t["text"], t["button"]
    acc = QColor(t["accent"])
    lum = 0.299 * acc.red() + 0.587 * acc.green() + 0.114 * acc.blue()
    derived = dict(
        panel_top=_mix(w, tx, 0.05) if dark else _mix(w, "#ffffff", 0.45),
        panel_bot=w,
        border=_mix(w, tx, 0.20 if dark else 0.24),
        hilite=_mix(btn, "#ffffff", 0.10 if dark else 0.6),
        dim=_mix(tx, w, 0.42),
        accent_text="#111111" if lum > 150 else "#ffffff",
        btn_top=_mix(btn, tx, 0.08) if dark else _mix(btn, "#ffffff", 0.55),
        btn_bot=btn,
        title_top=_mix(w, tx, 0.08 if dark else 0.05),
        title_bot=_mix(w, tx, 0.05 if dark else 0.03),
        lcd_bg=t["base"], lcd_fg=t["accent"], lcd_dim=_mix(t["base"], tx, 0.18),
        good="#5cb85c", warn="#e0a030", bad="#d9534f", caption=tx,
    )
    for k, v in derived.items():
        t.setdefault(k, v)
    t.setdefault("r", 4); t.setdefault("r_panel", 6)
    return t


_GLYPHS = {"check": "M3 7.4l2.6 2.6L11 4.4", "down": "M3.5 5.5L7 9l3.5-3.5", "up": "M3.5 8.5L7 5l3.5 3.5"}


def _glyph(name: str, color: str) -> str:
    """A small stroke glyph (check mark, arrows) in `color` as an SVG file for QSS: sub-controls styled with QSS
    lose the style's own drawing, so check marks and combo/spin arrows have to be supplied as images."""
    c = QColor(color).name()
    try:
        d = cache_dir() / "skin"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}-{c[1:]}.svg"
        if not f.exists():
            f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                         f'<path d="{_GLYPHS[name]}" fill="none" stroke="{c}" stroke-width="1.8" '
                         'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        return f.as_posix()
    except OSError:
        return ""


def _musicbee_qss(t: dict) -> str:
    """MusicBee's flat look: no frames, gradients or bevels; plain-text headers, thin scrollbars, upper-case tabs."""
    w, tx, ac, dim = t["window"], t["text"], t["accent"], t["dim"]
    return f"""
QToolBar {{ background: {w}; border: 0; spacing: 4px; padding: 2px 6px; }}
QToolBar QToolButton, QToolBar QPushButton {{ background: transparent; border: 0; padding: 4px 8px; color: {tx}; }}
QToolBar QToolButton:hover, QToolBar QPushButton:hover {{ background: {t['highlight']}; }}
QToolBar QToolButton:checked, QToolBar QPushButton:checked {{ background: transparent; color: {ac}; }}
QToolBar QToolButton:!checked:checkable {{ border: 0; padding: 4px 8px; }}
QToolBar QLineEdit {{ background: transparent; border: 0; border-bottom: 1px solid #333; border-radius: 0;
    padding: 3px 4px; color: {tx}; }}
QToolBar QLineEdit:focus {{ border-bottom: 1px solid {ac}; }}
QPushButton#btn_shuffle:checked, QPushButton#btn_repeat:checked {{ background: transparent; border: 0; }}
QToolButton::menu-button {{ border: 0; background: transparent; width: 10px; }}
QMenuBar {{ background: {w}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t['highlight']}; color: #ffffff; }}
QMenu {{ background: #202020; border: 1px solid #333; border-radius: 0; }}
QListView, QTreeView, QTableView {{ background: {w}; alternate-background-color: {w}; border: 0; border-radius: 0;
    outline: 0; }}
QTreeView::item:selected, QListView::item:selected, QTreeView::item:hover, QListView::item:hover {{
    background: {t['highlight']}; color: #ffffff; }}
QTableView {{ selection-background-color: {t['highlight']}; selection-color: #ffffff; }}
QHeaderView::section {{ background: {w}; color: {tx}; border: 0; border-bottom: 1px solid #2a2a2a; padding: 4px 6px; }}
QDockWidget::title {{ background: {w}; border: 0; color: {tx}; padding: 4px 6px; font-weight: bold; }}
QSplitter::handle {{ background: {w}; }}
QStatusBar {{ background: {w}; color: {dim}; }}
QStatusBar::item {{ border: 0; }}
QScrollBar:vertical {{ width: 6px; background: transparent; }}
QScrollBar:horizontal {{ height: 6px; background: transparent; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: #3a3a3a; border: 0; border-radius: 0; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: #555; }}
QTabBar {{ background: {w}; }}
QTabBar::tab {{ background: transparent; border: 0; border-radius: 0; padding: 6px 12px; color: {tx};
    text-transform: uppercase; }}
QTabBar::tab:selected {{ background: transparent; border: 0; color: {ac}; font-weight: bold; }}
QTabBar::tab:hover {{ color: #ffffff; }}
QSlider::groove:horizontal {{ height: 2px; background: #444; border-radius: 0; }}
QSlider::sub-page:horizontal {{ background: {tx}; border-radius: 0; }}
QSlider::handle:horizontal {{ background: {tx}; width: 6px; height: 10px; margin: -5px 0; border-radius: 0; }}
QListWidget#albumgrid {{ background: {w}; border: 0; }}
QListWidget#albumgrid::item, QListWidget#albumgrid::item:selected, QListWidget#albumgrid::item:hover {{
    background: transparent; }}
"""


# the groups of the View -> Skin menu, in order; user skins can name one with "category" (anything else: "Custom")
CATEGORIES = {
    "Flat": ["MusicBee", "MusicBee Glow", "MusicBee Green", "MusicBee Blue", "Dark", "Light", "AMOLED", "High Contrast", "Slate Light", "Paper"],
    "Neomorphic": ["Neomorphic", "Neomorphic Dark"],
    "Classic": ["Winamp Classic", "Classic", "Classic 98", "Amp Classic", "Chrome Deck", "Olive Noir"],
    "Modern": ["Modern Night", "Luna Midnight", "Aqua Deck", "Ice Blue", "Emerald Core", "Vapor Dusk", "Midnight",
               "Sunset", "Cyberpunk", "Forest"],
    "Colour Schemes": ["Nord", "Solarized Dark", "Solarized Light", "Dracula", "Gruvbox Dark", "Tokyo Night",
                       "Catppuccin Mocha", "Rose Pine", "One Dark", "Monokai"],
}


def grouped(skin_map: dict | None = None) -> dict:
    """{category: [skin names]} for the skin menu; skins not listed above go to the category in their file or 'Custom'."""
    skin_map = skin_map or all_skins()
    out = {c: [n for n in names if n in skin_map] for c, names in CATEGORIES.items()}
    placed = {n for names in out.values() for n in names}
    for n, t in skin_map.items():
        if n not in placed:
            out.setdefault(str(t.get("category") or "Custom"), []).append(n)
    return {c: names for c, names in out.items() if names}


def _tabs_qss(t: dict, r) -> str:
    """The library tabs of the gradient skins: flat labels, the open one lifted with an accent line (no boxes), and
    flat Back / Forward / + buttons beside them."""
    hover = _mix(t['panel_top'], t['text'], 0.10)
    return f"""
QTabBar#library_tabs {{ background: transparent; }}
QTabBar#library_tabs::tab {{ background: transparent; color: {t['dim']}; border: 0; border-bottom: 2px solid transparent;
    padding: 6px 6px 4px 14px; margin: 2px 1px 0 1px; border-top-left-radius: {r}px; border-top-right-radius: {r}px; }}
QTabBar#library_tabs::tab:hover {{ background: {hover}; color: {t['text']}; }}
QTabBar#library_tabs::tab:selected {{ background: {t['panel_top']}; color: {t['text']};
    border-bottom: 2px solid {t['accent']}; }}
QToolBar QToolButton#tab_close {{ color: {t['dim']}; }}
QToolBar QToolButton#tab_close:hover {{ color: {t['text']}; }}
QToolBar QToolButton#new_tab, QToolBar QToolButton#nav_back, QToolBar QToolButton#nav_fwd {{ background: transparent;
    border: 0; border-radius: {r}px; padding: 4px 8px; color: {t['text']}; }}
QToolBar QToolButton#new_tab {{ font-size: 16px; font-weight: bold; padding: 2px 18px 2px 8px; }}
QToolBar QToolButton#new_tab:hover, QToolBar QToolButton#nav_back:hover, QToolBar QToolButton#nav_fwd:hover,
QToolBar QToolButton#new_tab::menu-button:hover {{ background: {hover}; }}
QToolBar QToolButton#nav_back:disabled, QToolBar QToolButton#nav_fwd:disabled {{ color: {_mix(t['panel_top'], t['dim'], 0.5)}; }}
QToolBar QToolButton#new_tab::menu-button {{ border: 0; background: transparent; width: 16px;
    border-top-right-radius: {r}px; border-bottom-right-radius: {r}px; }}
QToolBar QToolButton#new_tab::menu-arrow {{ image: url("{_glyph('down', t['dim'])}"); width: 9px; height: 9px; }}
"""


def _glow_qss(t: dict) -> str:
    """MusicBee with the cover's colours blurred behind everything (the window paints it): every panel is see-through,
    tabs are pills, the player bar is a dark glass strip."""
    tx = t["text"]
    return f"""
QMainWindow, QDockWidget, QDockWidget > QWidget, QAbstractScrollArea, QAbstractScrollArea > QWidget,
QToolBar, QStatusBar, QMenuBar, QTabBar, QStackedWidget, QSplitter {{ background: transparent; }}
QListView, QTreeView, QTableView, QListWidget#albumgrid {{ background: transparent; alternate-background-color: transparent; }}
QTreeView::item:selected, QListView::item:selected, QTreeView::item:hover, QListView::item:hover {{
    background: rgba(255,255,255,70); color: {tx}; border-radius: 8px; }}
QTableView {{ selection-background-color: rgba(255,255,255,90); selection-color: {tx}; }}
QHeaderView::section {{ background: transparent; color: {tx}; border-bottom: 1px solid rgba(0,0,0,40); }}
QTabBar::tab {{ background: rgba(70,85,95,120); color: {tx}; border-radius: 12px; margin: 3px 0 3px 6px; padding: 3px 12px 3px 14px; }}
QTabBar::tab:selected {{ background: rgba(60,75,88,190); color: #ffffff; }}
QToolBar#tb_controls, QToolBar#tb_volume, QToolBar#tb_seek {{ background: rgba(255,255,255,70); }}
QLineEdit {{ background: rgba(255,255,255,70); border: 0; border-radius: 12px; padding: 3px 10px; color: {tx}; }}
QMenu {{ background: #cdd6d2; color: {tx}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: rgba(60,75,88,160); color: #ffffff; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: rgba(60,75,88,140); border-radius: 3px; }}
"""


def _neo_qss(t: dict) -> str:
    """Soft UI: raised controls (light edge top-left, shadow edge bottom-right) and pressed / input fields inset."""
    w, tx, ac = t["window"], t["text"], t["accent"]
    hi, sh = t["hilite"], t["border"]
    r = int(t.get("r", 12))
    raised = (f"border: 2px solid {w}; border-top-color: {hi}; border-left-color: {hi}; "
              f"border-right-color: {sh}; border-bottom-color: {sh}; border-radius: {r}px;")
    inset = (f"border: 2px solid {w}; border-top-color: {sh}; border-left-color: {sh}; "
             f"border-right-color: {hi}; border-bottom-color: {hi}; border-radius: {r}px;")
    return f"""
QToolBar {{ background: {w}; border: 0; spacing: 8px; padding: 4px 8px; }}
QPushButton, QToolButton {{ background: {w}; color: {tx}; {raised} padding: 4px 12px; }}
QToolBar QToolButton {{ background: {w}; {raised} padding: 4px 8px; }}
QPushButton:hover, QToolButton:hover {{ color: {ac}; }}
QPushButton:pressed, QToolButton:pressed, QPushButton:checked, QToolButton:checked, QToolBar QToolButton:checked {{
    background: {w}; color: {ac}; {inset} }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{ background: {t['base']}; color: {tx}; {inset} padding: 3px 8px; }}
QListView, QTreeView, QTableView {{ background: {t['base']}; alternate-background-color: {t['base']}; {inset} outline: 0; }}
QTreeView::item:selected, QListView::item:selected {{ background: {t['highlight']}; color: {t['hl_text']};
    border-radius: 8px; }}
QTableView {{ selection-background-color: {t['highlight']}; selection-color: {t['hl_text']}; }}
QHeaderView::section {{ background: {w}; color: {t['dim']}; border: 0; padding: 5px 8px; }}
QDockWidget::title {{ background: {w}; border: 0; color: {t['dim']}; padding: 6px 8px; }}
QMenuBar, QStatusBar, QTabBar {{ background: {w}; }}
QMenu {{ background: {w}; {raised} padding: 6px; }}
QMenu::item {{ border-radius: 8px; }}
QMenu::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
QTabBar::tab {{ background: {w}; border: 0; padding: 6px 14px; border-radius: {r}px; color: {tx}; }}
QTabBar::tab:selected {{ {inset} color: {ac}; font-weight: bold; }}
QSlider::groove:horizontal {{ height: 8px; background: {w}; {inset} border-radius: 4px; }}
QSlider::sub-page:horizontal {{ background: {ac}; border-radius: 4px; }}
QSlider::handle:horizontal {{ background: {w}; {raised} width: 14px; height: 14px; margin: -5px 0; border-radius: 8px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {sh}; border-radius: 5px; min-height: 26px; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {sh}; border-radius: 5px; min-width: 26px; }}
QListWidget#albumgrid {{ background: {w}; border: 0; }}
"""


def _winamp_qss(t: dict) -> str:
    """Winamp 2.x look: bevelled metal chrome, black lists with green text, blue selection, LCD-style time."""
    return f"""
QToolBar {{ border: 1px solid #000; }}
QToolBar QToolButton, QPushButton {{ border: 1px solid #000; border-top-color: {t['hilite']}; border-left-color: {t['hilite']};
    color: {t['caption']}; padding: 3px 8px; }}
QToolBar QToolButton:checked, QPushButton:checked {{ background: #1f2030; border-top-color: #000; border-left-color: #000;
    border-right-color: {t['hilite']}; border-bottom-color: {t['hilite']}; color: {t['accent']}; }}
QTableView, QListView, QTreeView {{ background: #000000; alternate-background-color: #000000; color: #00ff00;
    selection-background-color: #0000c6; selection-color: #ffffff; border: 2px solid #000; outline: 0; }}
QTreeView::item:selected, QListView::item:selected {{ background: #0000c6; color: #ffffff; }}
QHeaderView::section {{ color: {t['caption']}; }}
QLabel {{ color: {t['caption']}; }}
QMenuBar {{ color: {t['caption']}; }} QMenu {{ color: {t['caption']}; }}
QSlider::groove:horizontal {{ height: 6px; background: #000; border: 1px solid {t['hilite']}; border-radius: 0; }}
QSlider::sub-page:horizontal {{ background: #00a000; border-radius: 0; }}
QSlider::handle:horizontal {{ background: {_grad(t['btn_top'], t['btn_bot'])}; border: 1px solid #000; width: 12px;
    height: 10px; margin: -5px 0; border-radius: 0; }}
QStatusBar {{ color: {t['caption']}; }}
"""


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
    t = complete_tokens(t)
    r, rp = int(t.get("r", 3)), int(t.get("r_panel", 4))
    old = t.get("style") == "classic98"
    bd = t["border"]
    face_raised = "outset" if old else "solid"
    hover_bg = _mix(t["base"], t["text"], 0.07)
    out = base + f"""
QToolBar {{ background: {_grad(t['panel_top'], t['panel_bot'])}; border: 0; border-bottom: 1px solid {bd};
    spacing: 4px; padding: 2px; }}
QMenuBar {{ background: {t['panel_bot']}; color: {t['text']}; }}
QMenuBar::item {{ padding: 4px 8px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
QMenu {{ background: {t['panel_bot']}; color: {t['text']}; border: 1px solid {bd}; }}
QMenu::item {{ padding: 4px 22px; }}
QMenu::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
QMenu::item:disabled {{ color: {t['dim']}; }}
QMenu::separator {{ height: 1px; background: {bd}; margin: 3px 6px; }}
QPushButton, QToolButton {{ background: {_grad(t['btn_top'], t['btn_bot'])}; color: {t['text']};
    border: {'2px' if old else '1px'} {face_raised} {bd if not old else t['btn_top']}; border-radius: {r}px; padding: 3px 10px;
    min-height: 18px; }}
QToolButton {{ padding: 3px 5px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {t['accent'] if not old else t['btn_top']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t['btn_bot']}; {'border-style: inset;' if old else ''} }}
QPushButton:checked, QToolButton:checked {{ background: {t['accent']}; color: {t['accent_text']};
    border-color: {t['accent']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['dim']}; }}
QPushButton#btn_shuffle, QPushButton#btn_repeat {{ padding: 3px 6px; }}
QPushButton#btn_shuffle:checked, QPushButton#btn_repeat:checked {{ background: {_mix(t['btn_bot'], t['accent'], 0.18)};
    border-color: {t['accent']}; }}
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
QStatusBar {{ background: {t['panel_bot']}; color: {t['dim']}; }}
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
QSlider::groove:horizontal, QSlider::groove:vertical {{ background: {t['lcd_dim']}; }}
QProgressBar {{ background: {t['lcd_bg']}; border: 1px solid {bd}; border-radius: {r}px;
    color: {t['text']}; text-align: center; }}
QProgressBar::chunk {{ background: {t['lcd_fg']}; border-radius: {r}px; }}
"""
    if not old:                                             # modern touches; Classic 98 keeps its bevels
        out += f"""
QToolBar {{ spacing: 6px; padding: 3px 6px; }}
QToolBar QToolButton {{ background: transparent; border: 1px solid transparent; padding: 4px; }}
QToolBar QToolButton:hover {{ background: {_grad(t['btn_top'], t['btn_bot'])}; border-color: {bd}; }}
QToolBar QToolButton:pressed {{ background: {t['btn_bot']}; }}
QToolBar QToolButton:checked {{ background: {t['accent']}; color: {t['accent_text']}; border-color: {t['accent']};
    padding: 4px 10px; }}
QToolBar QToolButton:!checked:checkable {{ padding: 4px 10px; border-color: {bd}; }}
QToolBar QLineEdit {{ padding: 4px 8px; min-height: 18px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {t['accent']}; }}
QMenu {{ padding: 4px; border-radius: {rp}px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: {r}px; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {hover_bg}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
QHeaderView::section {{ padding: 4px 6px; }}
QDockWidget::title {{ padding: 5px 8px; }}
QScrollBar:vertical {{ width: 11px; background: transparent; }}
QScrollBar:horizontal {{ height: 11px; background: transparent; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {_mix(t['base'], t['text'], 0.22)};
    border: 2px solid transparent; background-clip: padding; border-radius: 5px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {_mix(t['base'], t['text'], 0.38)}; }}
QToolTip {{ background: {t['panel_top']}; border: 1px solid {bd}; padding: 4px 6px; }}
QStatusBar QPushButton, QStatusBar QToolButton {{ padding: 2px 8px; }}
QListWidget#albumgrid::item {{ padding: 0; }}
{_tabs_qss(t, r) if not old else ''}
QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{ background: {t['base']}; color: {t['text']};
    border: 1px solid {bd}; border-radius: {r}px; padding: 3px 6px; min-height: 18px; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {_mix(bd, t['accent'], 0.5)}; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t['accent']}; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QComboBox::down-arrow {{ image: url("{_glyph('down', t['text'])}"); width: 12px; height: 12px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QDateEdit::up-button, QDateEdit::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{
    border: 0; background: transparent; width: 16px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow, QTimeEdit::up-arrow {{
    image: url("{_glyph('up', t['text'])}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow, QTimeEdit::down-arrow {{
    image: url("{_glyph('down', t['text'])}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {t['panel_bot']}; color: {t['text']}; border: 1px solid {bd};
    selection-background-color: {t['highlight']}; selection-color: {t['hl_text']}; outline: 0; }}
QCheckBox::indicator, QRadioButton::indicator, QGroupBox::indicator {{ width: 14px; height: 14px;
    background: {t['base']}; border: 1px solid {_mix(bd, t['text'], 0.25)}; border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover, QGroupBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked, QGroupBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']};
    image: url("{_glyph('check', t['accent_text'])}"); }}
QCheckBox::indicator:indeterminate {{ background: {_mix(t['base'], t['accent'], 0.5)}; border-color: {t['accent']}; }}
QRadioButton::indicator:checked {{ border-color: {t['accent']}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
    fx:0.5, fy:0.5, stop:0 {t['accent']}, stop:0.45 {t['accent']}, stop:0.55 {t['base']}, stop:1 {t['base']}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {t['window']}; border-color: {bd}; }}
QGroupBox {{ border: 1px solid {bd}; border-radius: {rp}px; margin-top: 10px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QListWidget#albumgrid::item:selected {{ background: {t['highlight']}; color: {t['hl_text']}; }}
"""
    if t.get("style") == "musicbee":
        out += _musicbee_qss(t)
        if t.get("glow"):
            out += _glow_qss(t)
    elif t.get("style") == "neo":
        out += _neo_qss(t)
    elif t.get("style") == "winamp":
        out += _winamp_qss(t)
    if t.get("font"):
        out += f"\nQWidget {{ font-family: {t['font']}; {'font-size: ' + t['font_size'] + ';' if t.get('font_size') else ''} }}\n"
    return out


def apply_skin(app: QApplication, name: str, directory: Path | None = None) -> dict:
    """Apply skin by name (falls back to Dark). Returns the resolved tokens (incl. 'accent')."""
    skins = all_skins(directory)
    t = complete_tokens(skins.get(name) or skins[DEFAULT])
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
