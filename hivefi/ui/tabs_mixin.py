"""Tabs above the library, as in MusicBee: each tab is its own view of the library (source, search, Genre /
Artist / Album selection, sort order, Columns or Albums browser, Details or Album and Tracks list, selection and
scroll position). One track list is shared: switching tabs stores the view of the tab being left and puts back the
view of the tab being opened. The tabs are remembered with the session."""
import re

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QMenu, QTabBar, QToolButton

from .pages import PAGE_TITLES

DEFAULT_TAB = {"source": ["all", None]}


class TabsMixin:
    def _build_tabs(self):
        tb = self._make_toolbar("Tabs", "tb_tabs")
        self.tab_bar = QTabBar(); self.tab_bar.setObjectName("library_tabs")
        self.tab_bar.setDocumentMode(True); self.tab_bar.setTabsClosable(True); self.tab_bar.setMovable(True)
        self.tab_bar.setExpanding(False); self.tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tab_bar.setUsesScrollButtons(True); self.tab_bar.setDrawBase(False)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._tab_menu)
        self.tab_bar.currentChanged.connect(self._tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._close_tab)
        self.tab_bar.tabMoved.connect(self._tab_moved)
        self.tab_bar.tabBarDoubleClicked.connect(lambda i: i < 0 and self._new_tab())
        self.tab_bar.installEventFilter(self)
        self.btn_new_tab = QToolButton(); self.btn_new_tab.setText("+"); self.btn_new_tab.setAutoRaise(True); self.btn_new_tab.setObjectName("new_tab")
        self.btn_new_tab.setToolTip("New tab (Ctrl+T); the arrow opens the Visualizer or Now Playing page")
        self.btn_new_tab.clicked.connect(lambda: self._new_tab())
        nm = QMenu(self.btn_new_tab)
        nm.addAction("New Tab", lambda: self._new_tab())
        nm.addAction("Visualizer", lambda: self._open_page("visualizer"))
        nm.addAction("Now Playing", lambda: self._open_page("nowplaying"))
        self.btn_new_tab.setMenu(nm); self.btn_new_tab.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._nav_back: list[dict] = []; self._nav_fwd: list[dict] = []; self._nav_lock = False
        self.btn_back = QToolButton(); self.btn_back.setText("←"); self.btn_back.setAutoRaise(True)
        self.btn_fwd = QToolButton(); self.btn_fwd.setText("→"); self.btn_fwd.setAutoRaise(True)
        self.btn_back.setToolTip("Back"); self.btn_fwd.setToolTip("Forward")
        self.btn_back.setObjectName("nav_back"); self.btn_fwd.setObjectName("nav_fwd")
        self.btn_back.clicked.connect(lambda: self._nav_go(-1)); self.btn_fwd.clicked.connect(lambda: self._nav_go(1))
        self._nav_buttons()
        tb.addWidget(self.btn_back); tb.addWidget(self.btn_fwd)
        tb.addWidget(self.tab_bar); tb.addWidget(self.btn_new_tab)
        self.tabs: list[dict | None] = [None]            # None = the live view of the current tab
        self._tab_cur = 0
        self._tab_lock = True
        self._add_tab("Music")
        self._tab_lock = False
        self._update_close_buttons()

    # ------------------------------------------------------------------ back / forward
    def _nav_buttons(self):
        self.btn_back.setEnabled(bool(self._nav_back)); self.btn_fwd.setEnabled(bool(self._nav_fwd))

    def _nav_remember(self):
        """Call before the view changes (another source, artist, jump): Back returns to what was shown."""
        if getattr(self, "_nav_lock", True) or not hasattr(self, "model"):
            return
        self._nav_back.append(self._view_state()); del self._nav_back[:-50]
        self._nav_fwd.clear(); self._nav_buttons()

    def _nav_go(self, step):
        src, dst = (self._nav_back, self._nav_fwd) if step < 0 else (self._nav_fwd, self._nav_back)
        if not src:
            return
        dst.append(self._view_state())
        state = src.pop()
        self._nav_lock = True
        try:
            self._apply_view_state(state)
        finally:
            self._nav_lock = False
        self._nav_buttons(); self._update_tab_label()

    # ------------------------------------------------------------------ labels
    def _source_label(self, src):
        src = tuple(src) if src else ("all", None)
        if src and src[0] == "folder" and isinstance(src[1], list):
            src = ("folder", tuple(src[1]))
        it = self._items.get(src) if hasattr(self, "_items") else None
        if it is not None:
            text = it.text(0).replace("  ☁", "").replace("★ ", "")
            return re.sub(r"\s+\(\d+\)$", "", text).strip() or "Music"
        if src[0] == "folder":
            prefix = src[1][2] if len(src[1]) > 2 else ""
            return prefix.rsplit("/", 1)[-1] or "Folders"
        return "Music"

    def _tab_label(self, state=None):
        if state is not None and state.get("page") in PAGE_TITLES:
            return PAGE_TITLES[state["page"]]
        if state is None:
            src, search = self.source, self.search.text().strip()
        else:
            src, search = state.get("source"), (state.get("search") or "").strip()
        name = self._source_label(src)
        return f"{name}: {search}" if search else name

    def _update_tab_label(self):
        if getattr(self, "tab_bar", None) is not None and not self._tab_lock:
            i = self._tab_cur
            if 0 <= i < len(self.tabs) and self.tabs[i] is not None and self.tabs[i].get("page"):
                return                                             # a page tab keeps its name
            if 0 <= i < self.tab_bar.count():
                label = self._tab_label()
                self.tab_bar.setTabText(i, label if len(label) <= 40 else label[:39] + "…")
                self.tab_bar.setTabToolTip(i, label)

    def _add_tab(self, label):
        """Add a tab with a small flat ✕ in the text colour (the style's own close icon is a grey or red box)."""
        i = self.tab_bar.addTab(label)
        b = QToolButton(self.tab_bar); b.setText("✕"); b.setAutoRaise(True); b.setToolTip("Close tab (Ctrl+W)")
        b.setObjectName("tab_close"); b.setCursor(Qt.CursorShape.ArrowCursor); b.setFixedSize(18, 18)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setStyleSheet("QToolButton { border: 0; border-radius: 9px; background: transparent; padding: 0; font-size: 10px; }"
                        "QToolButton:hover { background: rgba(128,128,128,90); }")
        b.clicked.connect(lambda _c=False, b=b: self._close_tab_of(b))
        self.tab_bar.setTabButton(i, QTabBar.ButtonPosition.RightSide, b)
        return i

    def _close_tab_of(self, button):
        for i in range(self.tab_bar.count()):
            if self.tab_bar.tabButton(i, QTabBar.ButtonPosition.RightSide) is button:
                self._close_tab(i); return

    def _update_close_buttons(self):
        many = self.tab_bar.count() > 1
        side = QTabBar.ButtonPosition.RightSide
        for i in range(self.tab_bar.count()):
            b = self.tab_bar.tabButton(i, side)
            if b is not None:
                b.setVisible(many)

    # ------------------------------------------------------------------ switching
    def _tab_changed(self, i):
        if self._tab_lock or i < 0 or i == self._tab_cur:
            return
        if 0 <= self._tab_cur < len(self.tabs) and self.tabs[self._tab_cur] is None:
            self.tabs[self._tab_cur] = self._view_state()          # remember the tab we leave (a page tab is its own state)
        self._tab_cur = i
        state = self.tabs[i] or DEFAULT_TAB
        if not state.get("page"):
            self.tabs[i] = None
        self._apply_view_state(state)
        self._update_tab_label()

    def _new_tab(self, state=None):
        """Open a tab (All Music by default, or a copy of a view) and switch to it."""
        state = dict(state or DEFAULT_TAB)
        self._tab_lock = True
        i = self._add_tab(self._tab_label(state))
        self.tabs.append(state)
        self._tab_lock = False
        self._update_close_buttons()
        self.tab_bar.setCurrentIndex(i)
        return i

    def _open_page(self, kind):
        """Open (or switch to) a whole-window page tab: 'visualizer' or 'nowplaying'."""
        for i, t in enumerate(self.tabs):
            if t is not None and t.get("page") == kind:
                self.tab_bar.setCurrentIndex(i); return i
        return self._new_tab({"page": kind})

    def _close_tab(self, i=None):
        i = self._tab_cur if i is None or isinstance(i, bool) else i
        n = self.tab_bar.count()
        if n <= 1 or not 0 <= i < n:
            return
        if i == self._tab_cur:                                     # go to a neighbour first, then drop this one
            self.tab_bar.setCurrentIndex(i + 1 if i + 1 < n else i - 1)
        self._tab_lock = True
        self.tab_bar.removeTab(i)
        del self.tabs[i]
        self._tab_cur = self.tab_bar.currentIndex()
        self._tab_lock = False
        self._update_close_buttons()

    def _close_other_tabs(self, keep):
        if keep != self._tab_cur:
            self.tab_bar.setCurrentIndex(keep)
        for i in reversed(range(self.tab_bar.count())):
            if i != self._tab_cur:
                self._close_tab(i)

    def _tab_moved(self, frm, to):
        self.tabs.insert(to, self.tabs.pop(frm))
        self._tab_cur = self.tab_bar.currentIndex()

    def _tab_menu(self, pos):
        i = self.tab_bar.tabAt(pos)
        m = QMenu(self)
        m.addAction("New Tab", lambda: self._new_tab())
        if i >= 0:
            m.addAction("Duplicate Tab", lambda: self._new_tab(self._view_state() if i == self._tab_cur else self.tabs[i]))
            if self.tab_bar.count() > 1:
                m.addSeparator()
                m.addAction("Close Tab", lambda: self._close_tab(i))
                m.addAction("Close Other Tabs", lambda: self._close_other_tabs(i))
        m.exec(self.tab_bar.mapToGlobal(pos))

    def _tab_middle_click(self, e):
        """Middle-click a tab to close it (as in browsers and MusicBee)."""
        if e.button() == Qt.MouseButton.MiddleButton:
            i = self.tab_bar.tabAt(e.position().toPoint())
            if i >= 0:
                self._close_tab(i)
                return True
        return False

    def eventFilter(self, obj, e):
        if obj is getattr(self, "tab_bar", None) and e.type() == QEvent.Type.MouseButtonRelease \
                and self._tab_middle_click(e):
            return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------------ session
    def _tabs_state(self):
        tabs = [t if t is not None else self._view_state() for t in self.tabs]
        return {"tabs": tabs, "tab": self._tab_cur}

    def _restore_tabs(self, s):
        """Rebuild the tab bar from a saved session (the current tab's view is restored by the caller)."""
        tabs = s.get("tabs")
        cur = s.get("tab", 0)
        if not isinstance(tabs, list) or len(tabs) < 2 or not isinstance(cur, int) or not 0 <= cur < len(tabs):
            self._update_tab_label(); return
        self._tab_lock = True
        while self.tab_bar.count():
            self.tab_bar.removeTab(0)
        self.tabs = []
        for k, t in enumerate(tabs):
            t = t if isinstance(t, dict) else dict(DEFAULT_TAB)
            label = self._tab_label(t)
            self._add_tab(label if len(label) <= 40 else label[:39] + "…")
            self.tabs.append(None if k == cur and not t.get("page") else t)
        self.tab_bar.setCurrentIndex(cur)
        self._tab_cur = cur
        self._tab_lock = False
        self._update_close_buttons()
        self._update_tab_label()
