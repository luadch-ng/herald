"""Herald GUI.

Loads ui/herald.ui (edit in Qt Designer: `pyside6-designer ui/herald.ui`),
applies the dark theme, and drives one ADC connection per hub on a worker
thread with a live status LED. Multihub by design: each sidebar hub has its
own connection + bot settings + announce rules, persisted to data/hubs.json.
"""

import copy
import html
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QByteArray, QEvent, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QImage, QPainter)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSystemTrayIcon,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from adc import __version__
from adc import config, migrate, updates
from adc.connection import HubConnection, ONLINE, CONNECTING, RECONNECTING, ERROR

def _resource_base():
    """Where read-only bundled resources (ui/, assets/) live. Under a
    PyInstaller build that is the extracted bundle root (sys._MEIPASS); from
    source it is this file's directory."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


HERE = _resource_base()
APP_VERSION = __version__


_PALETTES = {
    "dark": {
        "BG": "#14161c", "FG": "#c7ccd6", "PANEL": "#0f1116", "INPUT_BG": "#0d0f14",
        "BORDER": "#23262f", "BORDER2": "#2a2e3a", "BORDER3": "#363b49",
        "FG_BRIGHT": "#e6e9ef", "FG_MUTED": "#6b7280", "FG_SECONDARY": "#8b93a1",
        "BTN_BG": "#2a2e3a", "BTN_FG": "#dfe3ea", "BTN_HOVER": "#333846",
        "BTN_PRESSED": "#262a34", "SEL_BG": "#1d2029", "SEL_FG": "#ffffff",
        "HOVER_BG": "#191c24", "DIS_FG": "#6b7280", "DIS_BG": "#232734",
        "DIS_BORDER": "#363b49", "LOG_FG": "#a9b0bd", "SCROLL": "#2c313d",
        "ACCENT": "#2563eb", "ACCENT_FG": "#ffffff",
    },
    "light": {
        "BG": "#f5f6f8", "FG": "#2a2e37", "PANEL": "#eceef2", "INPUT_BG": "#ffffff",
        "BORDER": "#d9dce3", "BORDER2": "#cdd1da", "BORDER3": "#c2c7d2",
        "FG_BRIGHT": "#14161c", "FG_MUTED": "#6b7280", "FG_SECONDARY": "#6b7280",
        "BTN_BG": "#e6e9ef", "BTN_FG": "#2a2e37", "BTN_HOVER": "#dce0e7",
        "BTN_PRESSED": "#cfd4dd", "SEL_BG": "#dbe3f4", "SEL_FG": "#14161c",
        "HOVER_BG": "#e7eaf1", "DIS_FG": "#a8adb8", "DIS_BG": "#e9ebef",
        "DIS_BORDER": "#d9dce3", "LOG_FG": "#4a4f5a", "SCROLL": "#c8ccd5",
        "ACCENT": "#2563eb", "ACCENT_FG": "#ffffff",
    },
}


def load_stylesheet(theme="dark"):
    """Read the theme skeleton, resolve the __ASSETS__ path placeholder and
    substitute the __COLOUR__ tokens from the chosen palette (dark/light)."""
    qss = (HERE / "ui" / "theme.qss").read_text(encoding="utf-8")
    qss = qss.replace("__ASSETS__", (HERE / "assets").as_posix())
    palette = _PALETTES.get(theme, _PALETTES["dark"])
    for token in sorted(palette, key=len, reverse=True):   # longest first
        qss = qss.replace(f"__{token}__", palette[token])
    return qss

class WaterWidget(QWidget):
    """Easter egg: renders an SVG with a real-time demoscene water ripple.
    Mouse movement drops ripples onto the surface. Uses numpy for the
    height-field sim; falls back to a static image if numpy is unavailable."""

    def __init__(self, svg_path, height=96, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        try:
            import numpy as np
            self._np = np
        except Exception:
            self._np = None

        r = QSvgRenderer(str(svg_path))
        vs = r.defaultSize()
        w = max(1, int(height * vs.width() / max(1, vs.height())))
        self._W, self._H = w, height
        self.setMinimumSize(w, height)
        self._srcimg = QImage(w, height, QImage.Format.Format_RGBA8888)
        self._srcimg.fill(0)
        p = QPainter(self._srcimg)
        r.render(p)
        p.end()

        if self._np is None:
            return
        np = self._np
        stride = self._srcimg.bytesPerLine()
        raw = np.frombuffer(self._srcimg.constBits(), np.uint8)
        self._src = raw.reshape((height, stride))[:, :w * 4].reshape((height, w, 4)).copy()
        self._h1 = np.zeros((height, w), np.float32)
        self._h2 = np.zeros((height, w), np.float32)
        yy, xx = np.meshgrid(np.arange(height), np.arange(w), indexing="ij")
        self._xx, self._yy = xx.astype(np.int32), yy.astype(np.int32)
        self._out = self._src.copy()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(33)

    def _drop(self, x, y, amp=-210.0):
        if self._np is None:
            return
        if 0 <= x < self._W and 0 <= y < self._H:
            self._h1[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = amp

    def _step(self):
        np = self._np
        h1, h2 = self._h1, self._h2
        n = (np.roll(h1, 1, 0) + np.roll(h1, -1, 0)
             + np.roll(h1, 1, 1) + np.roll(h1, -1, 1)) * 0.5 - h2
        n *= 0.955                       # damping -> ripples fade out
        self._h2, self._h1 = h1, n
        dx = np.roll(n, -1, 1) - np.roll(n, 1, 1)
        dy = np.roll(n, -1, 0) - np.roll(n, 1, 0)
        k = 0.55
        sx = np.clip(self._xx + (dx * k).astype(np.int32), 0, self._W - 1)
        sy = np.clip(self._yy + (dy * k).astype(np.int32), 0, self._H - 1)
        self._out = np.ascontiguousarray(self._src[sy, sx])
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        if self._np is not None:
            buf = self._out.tobytes()
            img = QImage(buf, self._W, self._H, self._W * 4,
                         QImage.Format.Format_RGBA8888)
            p.drawImage(self.rect(), img)
        else:
            p.drawImage(self.rect(), self._srcimg)

    def mouseMoveEvent(self, event):
        if self._np is None:
            return
        pos = event.position()
        x = int(pos.x() * self._W / max(1, self.width()))
        y = int(pos.y() * self._H / max(1, self.height()))
        self._drop(x, y)


LED_COLORS = {
    "idle": "#4b5563",
    CONNECTING: "#f59e0b",
    RECONNECTING: "#f59e0b",
    ONLINE: "#22c55e",
    ERROR: "#ef4444",
}


class ConnWorker(QThread):
    """Runs a HubConnection off the GUI thread, re-emitting its callbacks as
    Qt signals (safe to touch widgets from the slots)."""

    status = Signal(str, str)
    logged = Signal(str)
    info = Signal(str, str)
    announced = Signal(str, str, str, bool)   # name, category, path, is_dir

    def __init__(self, config_dict):
        super().__init__()
        self._conn = HubConnection(
            config_dict,
            on_status=lambda s, d: self.status.emit(s, d),
            on_log=lambda line: self.logged.emit(line),
            on_info=lambda k, v: self.info.emit(k, v),
            on_announce=lambda n, c, p="", d=True: self.announced.emit(n, c, p, d),
        )

    def run(self):
        self._conn.run()

    def stop(self):
        self._conn.stop()


class UpdateChecker(QThread):
    """One-shot GitHub release check off the GUI thread. Emits the result dict
    (or None) - never raises, so a failed check cannot crash the app."""

    done = Signal(object)     # updates.check() result, or None

    def run(self):
        self.done.emit(updates.check())


class Herald(QMainWindow):
    def __init__(self):
        super().__init__()
        self._first_run = not os.path.exists(config.HUBS_FILE)
        self.hubs = config.load_hubs()
        self._settings = config.load_settings()
        # Multi-hub: one connection per hub, keyed by id(hub). Each entry holds
        # its own worker + status/sid/keyprint + log and release buffers, so
        # only the SELECTED hub's state is shown and only the connected hub's
        # editor is locked. (Aybo)
        self._runtime = {}
        self._rule_row = -1
        self._rule_dirty = False       # rule editor has unsaved changes
        self._loading_rule = False     # guard: loading fields must not mark dirty
        self._hub_dirty = False        # Config tab has unsaved changes
        self._loading_hub = False      # guard: loading hub fields must not mark dirty
        self._loaded_hub = None        # hub dict whose rule is in the editor
        self._tray = None              # system-tray icon (set up if available)
        self._tray_hinted = False      # showed the "still running" balloon once
        self._suppress_tray_hide = False  # startup minimize -> taskbar, not tray

        self._ui = QUiLoader().load(str(HERE / "ui" / "herald.ui"))
        self.setCentralWidget(self._ui)
        self.setWindowTitle("Herald")
        self.resize(self._ui.size())
        geo = self._settings.get("window_geometry")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except Exception:
                pass
        self._build_menu()
        self._bind_widgets()
        self.brand_icon.setPixmap(
            QIcon(str(HERE / "assets" / "herald-mark.svg")).pixmap(40, 40))
        # folder icon on the watch-path browse button
        self.btn_browse.setIcon(QIcon(str(HERE / "assets" / "folder.svg")))
        self.btn_browse.setText("")
        # Cached yellow folder icon for the release-log open-folder buttons -
        # built once, reused for every row (a rebuild can re-create thousands).
        self._folder_icon = QIcon(str(HERE / "assets" / "folder-yellow.svg"))
        # Rule + hub action buttons use icons instead of labels (cleaner, fits
        # the narrow sidebar / rule row). Text moves to the tooltip. (Sopor)
        for btn, svg, tip in (
            (self.btn_add_rule, "add.svg", "Add rule"),
            (self.btn_clone_rule, "clone.svg", "Clone rule"),
            (self.btn_del_rule, "trash.svg", "Delete rule"),
            (self.btn_add_hub, "add.svg", "Add hub"),
            (self.btn_clone_hub, "clone.svg", "Clone hub"),
            (self.btn_del_hub, "trash.svg", "Remove hub"),
        ):
            btn.setIcon(QIcon(str(HERE / "assets" / svg)))
            btn.setText("")
            btn.setToolTip(tip)
        # placeholders for the multi-line list fields (QPlainTextEdit)
        self.rule_blacklist.setPlaceholderText(
            "one term per line - skip a release if its name contains any of these")
        self.rule_whitelist.setPlaceholderText(
            "one term per line - only announce if the name contains one (empty = allow all)")
        self._set_led("idle")
        self._install_autoscroll_toggles()
        self._populate_categories()

        for hub in self.hubs:
            self.hub_list.addItem(QListWidgetItem(hub["name"]))
        self.hub_list.currentRowChanged.connect(self._on_hub_selected)
        self.rule_list.currentRowChanged.connect(self._on_rule_selected)

        self.btn_connect.clicked.connect(self._connect)
        self.btn_disconnect.clicked.connect(self._disconnect)
        self.btn_add_hub.clicked.connect(self._add_hub)
        self.btn_clone_hub.clicked.connect(self._clone_hub)
        self.btn_del_hub.clicked.connect(self._del_hub)
        self.btn_save.clicked.connect(self._save_hub)
        self.btn_add_rule.clicked.connect(self._add_rule)
        self.btn_clone_rule.clicked.connect(self._clone_rule)
        self.btn_del_rule.clicked.connect(self._del_rule)
        self.btn_save_rule.clicked.connect(self._save_rule)
        self.btn_browse.clicked.connect(self._browse_path)
        # live-reflect the rule name / active state in the list as you type
        self.rule_fields["rulename"].textChanged.connect(self._on_rule_label_change)
        self.rule_fields["active"].toggled.connect(self._on_rule_label_change)
        # mark the editor dirty on any rule-field edit (for the unsaved-changes prompt)
        for w in self.rule_fields.values():
            if isinstance(w, QCheckBox):
                w.toggled.connect(self._mark_rule_dirty)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self._mark_rule_dirty)
            elif isinstance(w, QComboBox):
                w.currentTextChanged.connect(self._mark_rule_dirty)
            else:
                w.textChanged.connect(self._mark_rule_dirty)
        self.rule_blacklist.textChanged.connect(self._mark_rule_dirty)
        self.rule_whitelist.textChanged.connect(self._mark_rule_dirty)
        # mark the Config tab dirty on any hub-field edit, so Connect / hub-switch
        # can warn about unsaved connection settings (Sopor)
        for w in self.hub_fields.values():
            if isinstance(w, QCheckBox):
                w.toggled.connect(self._mark_hub_dirty)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self._mark_hub_dirty)
            else:
                w.textChanged.connect(self._mark_hub_dirty)

        self.hub_list.setCurrentRow(0)
        self.tabs.setCurrentIndex(0)   # always start on the Status tab
        self._apply_target_os()
        self._setup_tray()
        if self._first_run:
            QTimer.singleShot(0, self._first_run_prompt)
        else:
            # Auto-connect the hubs marked for it, once the window is up. Not on
            # the very first run (only the demo hub exists then). (Sopor)
            QTimer.singleShot(0, self._autoconnect_startup)
        # Notify (never install) if a newer release is on GitHub. Show a cached
        # result instantly (offline-friendly), then refresh in the background,
        # throttled to once/24h. (Aybo)
        self._refresh_update_indicator_from_cache()
        # Not on the very first launch (right after download - no point) and not
        # when the operator opted out.
        if not self._first_run and updates.should_check(self._settings):
            QTimer.singleShot(0, lambda: self._start_update_check(force=False))

    def _build_menu(self):
        # keep a reference so the QMenu is not garbage-collected
        self._file_menu = self.menuBar().addMenu("File")
        act_import = self._file_menu.addAction("Import from old announcer...")
        act_import.triggered.connect(self._import_announcer)
        act_import_cat = self._file_menu.addAction("Import freshstuff categories...")
        act_import_cat.triggered.connect(self._import_categories)
        act_gen_rules = self._file_menu.addAction("Create rules from categories...")
        act_gen_rules.triggered.connect(self._create_rules_from_categories)
        # import/generate actions modify hubs/rules -> locked while connected
        self._lock_when_connected = [act_import, act_import_cat, act_gen_rules]
        self._file_menu.addSeparator()
        act_quit = self._file_menu.addAction("Quit")
        act_quit.triggered.connect(self.close)
        self._settings_act = self.menuBar().addAction("Settings")
        self._settings_act.triggered.connect(self._show_settings)
        # The Clear actions live under a "Logs" submenu to keep the bar tidy.
        # Labels mirror the tab names ("Status" / "Release Log") so the same
        # thing is not called two different words in the tabs and the menu. (Aybo)
        self._logs_menu = self.menuBar().addMenu("Logs")
        self._clear_log_act = self._logs_menu.addAction("Clear status log")
        self._clear_log_act.triggered.connect(self._clear_log)
        self._clear_ann_act = self._logs_menu.addAction("Clear release log")
        self._clear_ann_act.triggered.connect(self._clear_releases)
        # About sits at the very end. (Aybo)
        self._about_act = self.menuBar().addAction("About")
        self._about_act.triggered.connect(self._show_about)
        # Far-right corner of the menu bar: an "Update available" indicator,
        # hidden until a newer release is found. Notify-only - clicking opens the
        # GitHub release page in the browser. (Aybo)
        self._update_url = updates.RELEASES_URL
        self._update_btn = QToolButton()
        self._update_btn.setObjectName("updateCorner")
        self._update_btn.setText("⬆︎  Update available")
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.clicked.connect(self._open_release_page)
        self._update_btn.setVisible(False)
        self.menuBar().setCornerWidget(self._update_btn, Qt.Corner.TopRightCorner)

    def _install_autoscroll_toggles(self):
        """A right-aligned 'Auto-scroll' checkbox above each log (Status +
        Release Log tab), so the operator can stop the view from jumping while
        they read back through a big scan. Choice persists in settings.json.
        (Sopor)"""
        self.chk_autoscroll_log = QCheckBox("Auto-scroll")
        self.chk_autoscroll_log.setToolTip("Follow new log lines to the bottom")
        self.chk_autoscroll_log.setChecked(
            bool(self._settings.get("autoscroll_log", True)))
        self.chk_autoscroll_log.toggled.connect(
            lambda on: self._save_autoscroll("autoscroll_log", on))

        self.chk_autoscroll_rel = QCheckBox("Auto-scroll")
        self.chk_autoscroll_rel.setToolTip("Jump to the newest release (top)")
        self.chk_autoscroll_rel.setChecked(
            bool(self._settings.get("autoscroll_releases", True)))
        self.chk_autoscroll_rel.toggled.connect(
            lambda on: self._save_autoscroll("autoscroll_releases", on))

        # Insert each checkbox row just above its list widget. Look up the
        # layouts on self._ui (the loaded .ui root) to match the file's idiom.
        status_lay = self._ui.findChild(QVBoxLayout, "statusLayout")
        if status_lay is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.chk_autoscroll_log)
            status_lay.insertLayout(status_lay.count() - 1, row)  # before logView
        log_lay = self._ui.findChild(QVBoxLayout, "logLayout")
        if log_lay is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.chk_autoscroll_rel)
            log_lay.insertLayout(0, row)  # above releaseLog

    def _save_autoscroll(self, key, on):
        self._settings[key] = bool(on)
        # A failed persist must not break the toggle: the in-memory setting
        # stays correct and the next successful save catches up. (review)
        try:
            config.save_settings(self._settings)
        except OSError:
            pass

    def _show_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setFixedWidth(430)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Target runtime OS"))
        hint = QLabel(
            "Where the announcer will actually RUN - the machine whose folders "
            "it watches. Set this to the target if you configure here but run "
            "headless (herald_cli.py) somewhere else: it stops path validation "
            "from checking your rules against the wrong filesystem.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8b93a1; font-size:12px;")
        lay.addWidget(hint)

        combo = QComboBox()
        for label, val in (("Auto (this machine)", "auto"),
                           ("Windows", "windows"), ("Linux", "linux")):
            combo.addItem(label, val)
        idx = combo.findData(self._settings.get("target_os", "auto"))
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        lay.addWidget(combo)

        lay.addSpacing(8)
        lay.addWidget(QLabel("Theme"))
        theme_combo = QComboBox()
        for label, val in (("Dark", "dark"), ("Light", "light")):
            theme_combo.addItem(label, val)
        tidx = theme_combo.findData(self._settings.get("theme", "dark"))
        theme_combo.setCurrentIndex(tidx if tidx >= 0 else 0)
        lay.addWidget(theme_combo)

        lay.addSpacing(8)
        chk_startmin = QCheckBox("Start minimized")
        chk_startmin.setChecked(bool(self._settings.get("start_minimized", False)))
        chk_startmin.setToolTip(
            "When Herald launches, start minimized to the taskbar instead of "
            "showing the window. Minimize it again later to tuck it into the "
            "system tray (if available).")
        lay.addWidget(chk_startmin)

        chk_reltips = QCheckBox("Show file paths as tooltips in the Release Log")
        chk_reltips.setChecked(bool(self._settings.get("release_tooltips", True)))
        chk_reltips.setToolTip(
            "Hover a release row to see its full folder path. Turn off to "
            "suppress those tooltips.")
        lay.addWidget(chk_reltips)

        lay.addSpacing(8)
        chk_updates = QCheckBox("Check for updates on startup")
        chk_updates.setChecked(bool(self._settings.get("check_updates", True)))
        chk_updates.setToolTip(
            "Ask GitHub once a day whether a newer Herald release exists and "
            "show an indicator. Notify only - Herald never installs anything.")
        lay.addWidget(chk_updates)
        check_now = QPushButton("Check for updates now")
        check_now.clicked.connect(lambda: self._start_update_check(force=True))
        lay.addWidget(check_now, alignment=Qt.AlignmentFlag.AlignLeft)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)
        save = QPushButton("Save")
        save.clicked.connect(dlg.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            prev_os = self._settings.get("target_os", "auto")
            prev_theme = self._settings.get("theme", "dark")
            prev_tips = self._settings.get("release_tooltips", True)
            new_os = combo.currentData()
            new_theme = theme_combo.currentData()
            self._settings["target_os"] = new_os
            self._settings["theme"] = new_theme
            self._settings["check_updates"] = chk_updates.isChecked()
            self._settings["start_minimized"] = chk_startmin.isChecked()
            self._settings["release_tooltips"] = chk_reltips.isChecked()
            config.save_settings(self._settings)   # persists across restarts
            self._apply_target_os()
            QApplication.instance().setStyleSheet(
                load_stylesheet(new_theme))   # live re-theme
            # A generic confirmation line on every save (Sopor asked for one),
            # plus the specific what-changed lines so the log still says exactly
            # what moved. (Sopor)
            self._log("--- settings saved ---")
            if new_os != prev_os:
                self._log(f"--- target runtime set to '{new_os}' ---")
            if new_theme != prev_theme:
                self._log(f"--- theme changed to '{new_theme}' ---")
            # Tooltip preference changed -> re-render the visible release rows so
            # it applies immediately, not only to rows announced from now on.
            if chk_reltips.isChecked() != prev_tips:
                self._render_release_log()

    def _apply_target_os(self):
        """Adapt the watch-path UI to the configured target runtime: browsing
        local folders only makes sense when we run where we configure."""
        local = config.target_is_local(self._settings)
        target = config.target_os(self._settings)
        self.btn_browse.setEnabled(local)
        self.btn_browse.setToolTip(
            "" if local else
            "Target runtime is a different OS - type the path as it is on that "
            "machine (browsing local folders would be misleading).")
        if local:
            self.rule_fields["path"].setPlaceholderText("")
        elif target == "windows":
            self.rule_fields["path"].setPlaceholderText(r"e.g. C:\Releases\Movies")
        else:
            self.rule_fields["path"].setPlaceholderText("e.g. /mnt/releases/movies")

    def _show_about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("About Herald")
        dlg.setWindowIcon(QIcon(str(HERE / "assets" / "herald-broadcast.svg")))
        dlg.setFixedWidth(390)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(12)

        # easter egg: the logo lives on a water surface - move the mouse over it
        water = WaterWidget(HERE / "assets" / "herald-logo.svg", height=72)
        lay.addWidget(water, alignment=Qt.AlignmentFlag.AlignHCenter)

        version = QLabel(f"Version {APP_VERSION}")
        version.setStyleSheet("color: #8b93a1;")
        lay.addWidget(version)

        seen = self._settings.get("update_latest_seen", "")
        if self._settings.get("check_updates", True) and updates.is_newer(seen, APP_VERSION):
            # href is a fixed constant; the tag is untrusted -> escape it before
            # it goes into this rich-text label.
            upd = QLabel(f'<a href="{updates.RELEASES_URL}">Herald '
                         f'{html.escape(seen)} is available - download</a>')
            upd.setOpenExternalLinks(True)
            upd.setStyleSheet("color: #22c55e;")
            lay.addWidget(upd)

        desc = QLabel("Announces new releases from watched folders to your "
                      "ADC hubs.")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        small = QLabel("Part of the luadch-ng project.  GPLv3.")
        small.setStyleSheet("color: #6b7280; font-size: 12px;")
        lay.addWidget(small)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        buttons.addWidget(close)
        lay.addLayout(buttons)

        self._about_music(True)     # chiptune while the About box is open
        try:
            dlg.exec()
        finally:
            self._about_music(False)

    @staticmethod
    def _about_music(on):
        """Play/stop the bundled keygen chiptune (Windows only; no-op elsewhere)."""
        if sys.platform != "win32":
            return
        try:
            import winsound
            if on:
                winsound.PlaySound(
                    str(HERE / "assets" / "keygen.wav"),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            else:
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    # -- update notification (GitHub releases; notify-only, never installs) --
    def _open_release_page(self):
        QDesktopServices.openUrl(QUrl(self._update_url))

    def _show_update_indicator(self, latest, url):
        """Reveal the far-right 'Update available' menu-bar indicator. Notify-
        only - clicking it opens the release page; Herald never self-installs."""
        self._update_url = url or updates.RELEASES_URL
        self._update_btn.setToolTip(
            f"Herald {latest} is available - you have {APP_VERSION}. "
            "Click to open the release page.")
        self._update_btn.setVisible(True)

    def _refresh_update_indicator_from_cache(self):
        """Show the indicator immediately from the last-seen tag (offline-
        friendly), before any network check runs. Suppressed when the operator
        opted out, so disabling update checks also hides a stale cached nag."""
        if not self._settings.get("check_updates", True):
            return
        seen = self._settings.get("update_latest_seen", "")
        if updates.is_newer(seen, APP_VERSION):
            self._show_update_indicator(seen, updates.RELEASES_URL)

    def _start_update_check(self, force=False):
        if getattr(self, "_update_thread", None) is not None:
            # a check is already running; if THIS request is forced (menu click),
            # promote the in-flight one so its result still shows the dialog
            if force:
                self._update_forced = True
            return
        self._update_forced = force
        self._update_thread = UpdateChecker(self)   # parented -> not GC'd
        self._update_thread.done.connect(self._on_update_result)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.start()

    def _on_update_result(self, info):
        forced = getattr(self, "_update_forced", False)
        self._update_thread = None
        if info is None:
            if forced:
                QMessageBox.information(
                    self, "Check for updates",
                    "Could not check for updates right now - GitHub may be "
                    "unreachable or rate-limited. Try again later.")
            return
        updates.mark_checked(self._settings, info["latest"])
        try:
            config.save_settings(self._settings)
        except OSError:
            pass
        if info["update_available"]:
            self._show_update_indicator(info["latest"], info["url"])
            if forced:
                QMessageBox.information(
                    self, "Update available",
                    f"Herald {info['latest']} is available "
                    f"(you have {info['current']}).")
        elif forced:
            QMessageBox.information(
                self, "Check for updates",
                f"You are up to date (Herald {info['current']}).")

    # -- migration from the old Lua announcer -------------------------------
    def _first_run_prompt(self):
        if self._confirm(
                "Welcome to Herald",
                "Migrate your setup from the old (Lua) announcer?\n\n"
                "You can also do this any time via File -> Import from old announcer."):
            self._import_announcer()
        else:
            QMessageBox.information(
                self, "Herald",
                "No problem. You can import your old announcer any time via\n"
                "File -> Import from old announcer.")
        config.save_hubs(self.hubs)   # persist so the prompt only shows once

    def _import_announcer(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select your old announcer folder (the one containing cfg/)")
        if not folder:
            return
        try:
            hub, summary = migrate.import_announcer(folder)
        except Exception as e:  # noqa: BLE001 - report any parse/read failure
            QMessageBox.warning(self, "Import failed",
                                f"Could not import from that folder:\n\n{e}")
            return
        hub["name"] = self._unique_hub_name(hub["name"])
        self.hubs.append(hub)
        self.hub_list.addItem(QListWidgetItem(hub["name"]))
        self.hub_list.setCurrentRow(len(self.hubs) - 1)
        config.save_hubs(self.hubs)
        QMessageBox.information(self, "Import complete", summary)

    # -- freshstuff categories ----------------------------------------------
    def _populate_categories(self):
        """Fill the rule Category dropdown from the imported list, keeping any
        text currently typed/selected."""
        combo = self.rule_fields["category"]
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(config.load_categories())
        combo.setEditText(current)
        combo.blockSignals(False)

    def _import_categories(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ptx_freshstuff_categories.dat", "",
            "Freshstuff categories (*.dat);;All files (*)")
        if not path:
            return
        try:
            cats = migrate.import_categories(path)
        except Exception as e:  # noqa: BLE001 - report any parse/read failure
            QMessageBox.warning(self, "Import failed",
                                f"Could not read categories from that file:\n\n{e}")
            return
        if not cats:
            QMessageBox.information(self, "No categories",
                                    "That file contained no categories.")
            return
        merged = sorted(set(config.load_categories()) | set(cats))
        config.save_categories(merged)
        self._populate_categories()
        QMessageBox.information(
            self, "Categories imported",
            f"Imported {len(cats)} categories ({len(merged)} available in total).\n\n"
            "This does NOT create rules - it fills the Category dropdown so you "
            "can pick a category when you add a rule.\n\nTo make a rule per "
            "category quickly, use File -> Create rules from categories...")
        self._log(f"--- imported {len(cats)} freshstuff categories ---")

    def _create_rules_from_categories(self):
        """Bulk-create one (inactive) rule per selected category. A rule still
        needs a Folder path - left empty here for the operator to fill in."""
        hub = self._hub
        if hub is None:
            return
        cats = config.load_categories()
        if not cats:
            QMessageBox.information(
                self, "No categories",
                "No categories imported yet.\n\nUse File -> Import freshstuff "
                "categories... first, then try again.")
            return
        # don't lose an in-progress rule edit when the list reloads
        if (self._rule_dirty and self._loaded_hub is not None
                and 0 <= self._rule_row < len(self._loaded_hub["rules"])):
            if self._confirm("Unsaved changes",
                             "Save the current rule's changes first?"):
                self._save_editor_to(self._loaded_hub, self._rule_row)
            self._rule_dirty = False

        chosen = self._pick_categories(cats)
        if not chosen:
            return
        for cat in chosen:
            rule = config.default_rule(cat)
            rule["category"] = cat        # path stays empty, active stays False
            hub["rules"].append(rule)
        config.save_hubs(self.hubs)
        self._reload_rule_list(select_row=len(hub["rules"]) - len(chosen))
        QMessageBox.information(
            self, "Rules created",
            f"Created {len(chosen)} rule(s), all inactive.\n\nSet a Folder path "
            "for each and tick Enabled to activate it.")
        self._log(f"--- created {len(chosen)} rules from categories ---")

    def _pick_categories(self, cats):
        """Checkbox picker; returns the list of chosen category names or None."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Create rules from categories")
        dlg.resize(360, 480)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)
        lay.addWidget(QLabel("Create an inactive rule for each selected category\n"
                             "(set the folder and enable them afterwards):"))
        lst = QListWidget()
        for cat in cats:
            it = QListWidgetItem(cat)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            lst.addItem(it)
        lay.addWidget(lst, 1)

        def set_all(state):
            for i in range(lst.count()):
                lst.item(i).setCheckState(state)
        selrow = QHBoxLayout()
        b_all = QPushButton("Select all")
        b_all.clicked.connect(lambda: set_all(Qt.CheckState.Checked))
        b_none = QPushButton("Select none")
        b_none.clicked.connect(lambda: set_all(Qt.CheckState.Unchecked))
        selrow.addWidget(b_all)
        selrow.addWidget(b_none)
        selrow.addStretch(1)
        lay.addLayout(selrow)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("Create rules")
        ok.clicked.connect(dlg.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return [lst.item(i).text() for i in range(lst.count())
                if lst.item(i).checkState() == Qt.CheckState.Checked]

    def _bind_widgets(self):
        f = self._ui.findChild
        # sidebar + header
        self.brand_icon = f(QLabel, "brandIcon")
        self.hub_list = f(QListWidget, "hubList")
        self.led = f(QLabel, "led")
        self.hub_title = f(QLabel, "hubTitle")
        self.status_headline = f(QLabel, "statusHeadline")
        self.info_sid = f(QLabel, "infoSid")
        self.info_keyprint = f(QLabel, "infoKeyprint")
        self.log_view = f(QPlainTextEdit, "logView")
        self.release_log = f(QListWidget, "releaseLog")
        self.tabs = f(QTabWidget, "tabs")
        # Clear also lives in each log's right-click menu, next to the usual
        # copy / select-all, so it is reachable without going to the menu bar.
        # (Sopor)
        self.log_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_view.customContextMenuRequested.connect(self._log_context_menu)
        self.release_log.setContextMenuPolicy(Qt.CustomContextMenu)
        self.release_log.customContextMenuRequested.connect(self._release_context_menu)
        # buttons
        self.btn_connect = f(QPushButton, "btnConnect")
        self.btn_disconnect = f(QPushButton, "btnDisconnect")
        self.btn_add_hub = f(QPushButton, "btnAddHub")
        self.btn_clone_hub = f(QPushButton, "btnCloneHub")
        self.btn_del_hub = f(QPushButton, "btnDelHub")
        self.btn_save = f(QPushButton, "btnSave")
        self.btn_add_rule = f(QPushButton, "btnAddRule")
        self.btn_clone_rule = f(QPushButton, "btnCloneRule")
        self.btn_del_rule = f(QPushButton, "btnDelRule")
        self.btn_save_rule = f(QPushButton, "btnSaveRule")
        self.btn_browse = f(QPushButton, "btnBrowsePath")
        # hub config form: cfg key -> widget
        self.hub_fields = {
            "name": f(QLineEdit, "cfgName"),
            "host": f(QLineEdit, "cfgHost"),
            "port": f(QSpinBox, "cfgPort"),
            "nick": f(QLineEdit, "cfgNick"),
            "password": f(QLineEdit, "cfgPassword"),
            "keyprint": f(QLineEdit, "cfgKeyprint"),
            "description": f(QLineEdit, "cfgDescription"),
            "botslots": f(QSpinBox, "cfgBotslots"),
            "botshare": f(QSpinBox, "cfgBotshare"),
            "botupload": f(QSpinBox, "cfgBotupload"),
            "sleeptime": f(QSpinBox, "cfgSleeptime"),
            "announceinterval": f(QSpinBox, "cfgAnnounceinterval"),
            "sockettimeout": f(QSpinBox, "cfgSockettimeout"),
        }
        # "Enabled" + "Autoconnect" are added in code (not in the .ui) as the
        # first two connection rows. They gate two DIFFERENT front-ends:
        #  - Enabled: the headless runner (herald_cli.py) starts every enabled hub.
        #  - Autoconnect: the GUI connects every autoconnect hub on startup.
        # They are independent - a hub can be one, both, or neither. (Sopor)
        self.hub_enabled = QCheckBox("Start this hub when running headless (herald_cli.py)")
        self.hub_enabled.setToolTip(
            "Headless runner (herald_cli.py) starts every ENABLED hub. The GUI "
            "ignores this - use Autoconnect below to auto-connect in the GUI.")
        self.hub_autoconnect = QCheckBox("Auto-connect this hub when Herald starts")
        self.hub_autoconnect.setToolTip(
            "When the Herald GUI launches, automatically connect this hub. "
            "Independent of Enabled (which is for the headless runner).")
        conn_form = self._ui.findChild(QFormLayout, "connForm")
        if conn_form is not None:
            conn_form.insertRow(0, "Enabled", self.hub_enabled)
            conn_form.insertRow(1, "Auto-connect", self.hub_autoconnect)
        self.hub_fields["enabled"] = self.hub_enabled
        self.hub_fields["autoconnect"] = self.hub_autoconnect
        self.hub_fields["announceinterval"].setToolTip(
            "How often Herald pings the hub to keep the connection alive - it "
            "does NOT control announce timing. Releases are announced as soon "
            "as they are detected and complete (see the rule's completion "
            "settings), not on this interval.")
        # rules editor
        self.rule_list = f(QListWidget, "ruleList")
        # right-click a rule to reorder / clone / delete it. (Sopor)
        self.rule_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rule_list.customContextMenuRequested.connect(self._rule_context_menu)
        self.rule_fields = {
            "rulename": f(QLineEdit, "cfgRuleName"),
            "active": f(QCheckBox, "cfgRuleActive"),
            "path": f(QLineEdit, "cfgRulePath"),
            "command": f(QLineEdit, "cfgRuleCommand"),
            "category": f(QLineEdit, "cfgRuleCategory"),
            "alibicheck": f(QCheckBox, "cfgRuleAlibi"),
            "alibinick": f(QLineEdit, "cfgRuleAlibiNick"),
            "checkdirs": f(QCheckBox, "cfgCheckdirs"),
            "checkfiles": f(QCheckBox, "cfgCheckfiles"),
            "checkdirsnfo": f(QCheckBox, "cfgCheckdirsnfo"),
            "checkdirssfv": f(QCheckBox, "cfgCheckdirssfv"),
            "checkspaces": f(QCheckBox, "cfgCheckspaces"),
            "skip_hidden": f(QCheckBox, "cfgSkipHidden"),
            "daydirscheme": f(QCheckBox, "cfgDaydir"),
            "zeroday": f(QCheckBox, "cfgZeroday"),
            "checkage": f(QCheckBox, "cfgCheckage"),
            "maxage": f(QSpinBox, "cfgMaxage"),
            "settle_seconds": f(QSpinBox, "cfgSettle"),
        }
        combo = f(QComboBox, "cfgSfvLevel")
        combo.clear()
        for label, val in (("present (fastest)", "present"),
                           ("wait until quiet (recommended)", "size_stable"),
                           ("CRC verify (slow, exact)", "crc")):
            combo.addItem(label, val)
        combo.setToolTip(
            "For a folder that HAS an .sfv, how sure Herald must be it is "
            "finished before announcing:\n"
            "- present: announce as soon as every file the SFV lists exists "
            "(fastest, but a file could still be uploading)\n"
            "- wait until quiet: also wait until nothing in the folder changes "
            "for 'Quiet seconds' (recommended)\n"
            "- CRC verify: also check every file's checksum against the SFV "
            "(exact, but reads every byte - slow on big releases)\n"
            "A folder with NO .sfv always uses the 'Quiet seconds' period "
            "instead.")
        self.rule_fields["sfv_level"] = combo
        self.rule_fields["settle_seconds"].setToolTip(
            "How many seconds a folder must go WITHOUT any change (no new or "
            "modified files) before Herald treats the release as finished and "
            "announces it. Higher = safer against announcing a still-uploading "
            "release; lower = faster. (Was called 'settle seconds'.)")
        # "Scan previous daydir (minutes)" - added in code, placed right after
        # the Zeroday row. daydir mode only: grace window after midnight (#Sopor).
        self.rule_daydir_prev = QSpinBox()
        self.rule_daydir_prev.setRange(0, 720)
        self.rule_daydir_prev.setSuffix(" min")
        self.rule_daydir_prev.setToolTip(
            "Only applies with 'Daydir scheme' + 'Only current daydir' both on: "
            "for this many minutes after midnight, also scan yesterday's daydir, "
            "so a release that landed just before midnight still gets announced "
            "instead of being orphaned in the previous day's folder. 0 = off.")
        comp_form = self._ui.findChild(QFormLayout, "completionForm")
        if comp_form is not None:
            pos = comp_form.getWidgetPosition(self.rule_fields["zeroday"])
            row = pos[0] + 1 if pos and pos[0] >= 0 else comp_form.rowCount()
            comp_form.insertRow(row, "Scan previous daydir", self.rule_daydir_prev)
        self.rule_fields["daydir_prev_minutes"] = self.rule_daydir_prev
        # Category: swap the plain .ui line edit for an EDITABLE dropdown of
        # imported freshstuff categories (File -> Import freshstuff categories).
        # Editable so a custom category can still be typed.
        old_cat = self.rule_fields["category"]
        cat_combo = QComboBox()
        cat_combo.setEditable(True)
        cat_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        cat_combo.lineEdit().setPlaceholderText("e.g. Movies_1080p")
        rule_form = self._ui.findChild(QFormLayout, "ruleForm")
        if rule_form is not None:
            pos = rule_form.getWidgetPosition(old_cat)
            if pos and pos[0] >= 0:
                rule_form.removeWidget(old_cat)
                old_cat.setParent(None)
                old_cat.deleteLater()
                rule_form.setWidget(pos[0], pos[1], cat_combo)
        self.rule_fields["category"] = cat_combo
        self.rule_blacklist = f(QPlainTextEdit, "cfgBlacklist")
        self.rule_whitelist = f(QPlainTextEdit, "cfgWhitelist")

    # -- generic field <-> dict helpers -------------------------------------
    @staticmethod
    def _get(widget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QComboBox):
            # editable combo (category): the free text IS the value; a fixed
            # combo (sfv_level): the value is the selected item's itemData.
            return widget.currentText().strip() if widget.isEditable() else widget.currentData()
        return widget.text().strip()

    @staticmethod
    def _set(widget, value):
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value or 0))
        elif isinstance(widget, QComboBox):
            if widget.isEditable():
                widget.setCurrentText(str(value or ""))
            else:
                idx = widget.findData(value)
                widget.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            widget.setText(str(value or ""))

    def _confirm(self, title, text):
        return QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    # -- hub selection ------------------------------------------------------
    @property
    def _hub(self):
        row = self.hub_list.currentRow()
        return self.hubs[row] if 0 <= row < len(self.hubs) else None

    def _on_hub_selected(self, row):
        """Hub row changed: offer to save an unsaved rule edit (belonging to the
        PREVIOUS hub) before loading the new hub."""
        if row < 0:
            return
        if (self._rule_dirty and self._loaded_hub is not None
                and 0 <= self._rule_row < len(self._loaded_hub["rules"])):
            if self._confirm("Unsaved changes",
                             "The current rule has unsaved changes. Save them "
                             "before switching hubs?"):
                self._save_editor_to(self._loaded_hub, self._rule_row)
            self._rule_dirty = False
        if self._hub_dirty and self._loaded_hub is not None:
            if self._confirm("Unsaved changes",
                             "The Config tab has unsaved changes. Save them "
                             "before switching hubs?"):
                self._save_hub_fields_to(self._loaded_hub)
                try:
                    idx = self.hubs.index(self._loaded_hub)
                    self.hub_list.item(idx).setText(
                        self._loaded_hub["name"] or "Unnamed hub")
                except ValueError:
                    pass
            self._hub_dirty = False
        self._load_hub(row)

    def _load_hub(self, row):
        if not (0 <= row < len(self.hubs)):
            return
        hub = self.hubs[row]
        self._loaded_hub = hub
        self.hub_title.setText(f"{hub['name']}   {hub['host']}:{hub['port']}")
        self._loading_hub = True       # populating fields must not mark dirty
        for key, widget in self.hub_fields.items():
            self._set(widget, hub.get(key))
        self._loading_hub = False
        self._hub_dirty = False        # freshly loaded == clean
        self._reload_rule_list()
        # Show this hub's own connection state (LED/log/buttons) + lock only if
        # it is the connected one.
        self._refresh_connection_ui()

    def _save_hub_fields_to(self, hub):
        """Persist the Config-tab widgets into `hub` (which may be a different
        hub than the currently selected row - used when switching away)."""
        for key, widget in self.hub_fields.items():
            hub[key] = self._get(widget)
        config.save_hubs(self.hubs)

    def _unique_hub_name(self, name, exclude=None):
        """Return `name` made unique among the hubs (excluding the hub at index
        `exclude`), appending ' (2)', ' (3)', ... on a collision. Hub names must
        be unique - among other things the per-hub announced-tracking file is
        keyed by the name, so two hubs sharing a name would share history. (Sopor)"""
        name = (name or "Hub").strip() or "Hub"
        taken = {h["name"] for i, h in enumerate(self.hubs) if i != exclude}
        if name not in taken:
            return name
        i = 2
        while f"{name} ({i})" in taken:
            i += 1
        return f"{name} ({i})"

    def _save_hub(self):
        hub = self._hub
        if hub is None:
            return
        self._save_hub_fields_to(hub)
        row = self.hub_list.currentRow()
        unique = self._unique_hub_name(hub["name"], exclude=row)
        if unique != hub["name"]:
            QMessageBox.information(
                self, "Duplicate hub name",
                f"A hub named '{hub['name']}' already exists - names must be "
                f"unique. Renamed to '{unique}'.")
            hub["name"] = unique
            self._set(self.hub_fields["name"], unique)
            config.save_hubs(self.hubs)
        self.hub_list.item(row).setText(hub["name"] or "Unnamed hub")
        self.hub_title.setText(f"{hub['name']}   {hub['host']}:{hub['port']}")
        self._hub_dirty = False
        self._on_log(hub, f"--- saved hub '{hub['name']}' ---")

    def _add_hub(self):
        hub = config.default_hub()
        hub["name"] = self._unique_hub_name(hub["name"])
        self.hubs.append(hub)
        self.hub_list.addItem(QListWidgetItem(hub["name"]))
        self.hub_list.setCurrentRow(len(self.hubs) - 1)
        self.tabs.setCurrentIndex(1)
        config.save_hubs(self.hubs)

    def _clone_hub(self):
        """Duplicate the selected hub with all its rules/settings - handy when
        hubs share the same categories. The clone gets a unique name and a fresh
        bot identity (so it is not the same CID as the source). (Sopor)"""
        hub = self._hub
        if hub is None:
            return
        # flush unsaved Config-tab edits into the source first (like clone rule),
        # so the clone carries what the user currently sees. A connected hub's
        # fields are locked (never dirty), so this is a no-op there.
        if self._loaded_hub is hub and self._hub_dirty:
            self._save_hub_fields_to(hub)
        clone = copy.deepcopy(hub)
        clone["name"] = self._unique_hub_name(f"{clone.get('name') or 'Hub'} (copy)")
        clone.pop("pid", None)      # fresh identity generated on first connect
        clone.pop("cid", None)
        row = self.hub_list.currentRow() + 1
        self.hubs.insert(row, clone)
        self.hub_list.insertItem(row, QListWidgetItem(clone["name"]))
        self.hub_list.setCurrentRow(row)
        self.tabs.setCurrentIndex(1)
        config.save_hubs(self.hubs)

    def _del_hub(self):
        row = self.hub_list.currentRow()
        if not (0 <= row < len(self.hubs)) or len(self.hubs) <= 1:
            return
        hub = self.hubs[row]
        if self._rt(hub)["worker"] is not None:
            QMessageBox.information(self, "Hub connected",
                                    "Disconnect this hub before removing it.")
            return
        if not self._confirm(
                "Remove hub",
                f"Remove hub '{hub['name']}'?\nIts connection settings and "
                f"{len(hub['rules'])} rule(s) will be permanently deleted."):
            return
        self._runtime.pop(id(hub), None)
        del self.hubs[row]
        self.hub_list.takeItem(row)
        config.save_hubs(self.hubs)

    # -- rules editor -------------------------------------------------------
    def _reload_rule_list(self, select_row=0):
        """Rebuild the rule list and load `select_row`. Signals are blocked
        during the rebuild so the row-change does not re-enter the unsaved-
        changes prompt; the target rule is then loaded explicitly."""
        self.rule_list.blockSignals(True)
        self.rule_list.clear()
        hub = self._hub
        for rule in (hub["rules"] if hub else []):
            mark = "*" if rule.get("active") else " "
            self.rule_list.addItem(QListWidgetItem(f"[{mark}] {rule['rulename'] or '(unnamed)'}"))
        count = self.rule_list.count()
        if count:
            if not (0 <= select_row < count):
                select_row = 0
            self.rule_list.setCurrentRow(select_row)
        self.rule_list.blockSignals(False)
        if count:
            self._load_rule(select_row)
        else:
            # No rules yet: show default_rule() values in the editor rather than
            # the previously-viewed hub's stale rule, so it is a clean starting
            # point and a Save here creates a sensible default, not a copy of
            # another hub's rule. (review)
            self._rule_row = -1
            self._populate_rule_editor(config.default_rule())

    def _mark_rule_dirty(self, *_):
        if not self._loading_rule:
            self._rule_dirty = True

    def _mark_hub_dirty(self, *_):
        if not self._loading_hub:
            self._hub_dirty = True

    def _on_rule_label_change(self, *_):
        """Live-update the selected rule's list label as its name / active
        state is edited (visual only; 'Save rule' persists to disk)."""
        row = self.rule_list.currentRow()
        if not (0 <= row < self.rule_list.count()):
            return
        mark = "*" if self.rule_fields["active"].isChecked() else " "
        name = self.rule_fields["rulename"].text() or "(unnamed)"
        self.rule_list.item(row).setText(f"[{mark}] {name}")

    def _on_rule_selected(self, row):
        """Rule row changed: offer to save unsaved edits on the rule we leave."""
        if row < 0:
            return
        if (self._rule_dirty and self._loaded_hub is not None
                and 0 <= self._rule_row < len(self._loaded_hub["rules"])
                and row != self._rule_row):
            name = self._loaded_hub["rules"][self._rule_row].get("rulename") or "(unnamed)"
            if self._confirm("Unsaved changes",
                             f"Rule '{name}' has unsaved changes. Save them "
                             "before switching?"):
                self._save_editor_to(self._loaded_hub, self._rule_row)
            self._rule_dirty = False
        self._load_rule(row)

    def _load_rule(self, row):
        hub = self._hub
        if hub is None or not (0 <= row < len(hub["rules"])):
            return
        self._rule_row = row
        self._populate_rule_editor(hub["rules"][row])

    def _populate_rule_editor(self, rule):
        """Load a rule dict into the editor widgets. Guarded by `_loading_rule`
        so populating never marks the editor dirty."""
        self._loading_rule = True
        for key, widget in self.rule_fields.items():
            self._set(widget, rule.get(key))
        self.rule_blacklist.setPlainText("\n".join(rule.get("blacklist", [])))
        self.rule_whitelist.setPlainText("\n".join(rule.get("whitelist", [])))
        self._rule_dirty = False
        self._loading_rule = False

    def _rule_problems(self, rule):
        """What an ACTIVE rule still needs to actually announce. The watch-path
        existence check only applies when the target runtime is THIS machine;
        when authoring for another OS the path is a foreign-OS string we can't
        validate here (the runner validates it where it actually runs)."""
        problems = []
        path = (rule.get("path") or "").strip()
        if not path:
            problems.append("a folder path")
        elif config.target_is_local(self._settings) and not Path(path).is_dir():
            problems.append(f"a folder path that exists ('{path}' is not a folder)")
        if not (rule.get("command") or "").strip():
            problems.append("a hub command (e.g. +addrel)")
        if not (rule.get("category") or "").strip():
            problems.append("a category (e.g. Movies_1080p)")
        if not (rule.get("checkdirs") or rule.get("checkfiles")):
            problems.append("'Announce directories' or 'Announce files' enabled")
        return problems

    def _save_editor_to(self, hub, row):
        """Persist the rule editor's contents into hub['rules'][row]. Does NOT
        touch the list selection (so saving never jumps you to another rule)."""
        if hub is None or not (0 <= row < len(hub["rules"])):
            return
        rule = hub["rules"][row]
        for key, widget in self.rule_fields.items():
            rule[key] = self._get(widget)
        rule["blacklist"] = self._lines(self.rule_blacklist)
        rule["whitelist"] = self._lines(self.rule_whitelist)

        problems = self._rule_problems(rule)
        if rule.get("active") and problems:
            rule["active"] = False
            self.rule_fields["active"].setChecked(False)
            QMessageBox.warning(
                self, "Rule incomplete",
                "This rule can't announce yet, so it was saved as inactive.\n\n"
                "Still needed:\n  - " + "\n  - ".join(problems))

        config.save_hubs(self.hubs)
        self._rule_dirty = False

    def _save_rule(self):
        hub = self._hub
        if hub is None:
            return
        if self._loaded_hub is hub and 0 <= self._rule_row < len(hub["rules"]):
            # a rule is selected -> save in place (keeps the selection put)
            self._save_editor_to(hub, self._rule_row)
        else:
            # No rule selected yet (fresh hub, empty rule list): create one FROM
            # the editor's current contents instead of doing nothing, so the user
            # does not have to click "+" first and then re-enter everything. Save
            # the editor into a new default rule, then reload the same values back.
            # (Sopor)
            hub["rules"].append(config.default_rule())
            self._loaded_hub = hub
            self._rule_row = len(hub["rules"]) - 1
            self._save_editor_to(hub, self._rule_row)
            self._reload_rule_list(select_row=self._rule_row)
        name = self._loaded_hub["rules"][self._rule_row].get("rulename") or "(unnamed)"
        self._log(f"--- saved rule '{name}' ---")

    def _add_rule(self):
        hub = self._hub
        if hub is None:
            return
        hub["rules"].append(config.default_rule())
        config.save_hubs(self.hubs)
        self._reload_rule_list(select_row=len(hub["rules"]) - 1)

    def _clone_rule(self):
        # Duplicate the SELECTED rule and drop it in right after, inactive and
        # renamed, so a near-identical rule is a rename away rather than a full
        # re-entry. (Sopor)
        hub = self._hub
        if hub is None or not (0 <= self._rule_row < len(hub["rules"])):
            return
        # Flush any unsaved editor edits into the source row first, so the clone
        # carries what the user currently SEES, not the last-saved rule - the
        # editor holds the live values and hub['rules'] only catches up on save.
        # This is a full save of the source (Clone implies committing its current
        # state), so it runs the same incomplete->inactive guard a normal Save
        # does; that consistency is deliberate. It also prevents losing those
        # edits when _reload_rule_list below reloads the editor onto the clone.
        # (review L2)
        if self._loaded_hub is hub:
            self._save_editor_to(hub, self._rule_row)
        clone = copy.deepcopy(hub["rules"][self._rule_row])
        clone["rulename"] = f"{clone.get('rulename') or '(unnamed)'} (copy)"
        clone["active"] = False
        row = self._rule_row + 1
        hub["rules"].insert(row, clone)
        config.save_hubs(self.hubs)
        self._reload_rule_list(select_row=row)

    def _del_rule(self):
        hub = self._hub
        if hub is None or not (0 <= self._rule_row < len(hub["rules"])):
            return
        name = hub["rules"][self._rule_row].get("rulename") or "(unnamed)"
        if not self._confirm("Delete rule", f"Delete rule '{name}'?"):
            return
        del hub["rules"][self._rule_row]
        config.save_hubs(self.hubs)
        self._reload_rule_list()

    def _move_rule(self, delta):
        """Reorder the selected rule by one position (delta -1 up / +1 down), so
        the operator can arrange rules in their own order. (Sopor)"""
        hub = self._hub
        if hub is None or self._rt(hub)["worker"] is not None:
            return                       # locked while this hub is connected
        row = self._rule_row
        new = row + delta
        if not (0 <= row < len(hub["rules"])) or not (0 <= new < len(hub["rules"])):
            return
        # flush unsaved editor edits into the current row first (same as clone),
        # so a move never loses what is in the editor
        if self._loaded_hub is hub:
            self._save_editor_to(hub, row)
        hub["rules"][row], hub["rules"][new] = hub["rules"][new], hub["rules"][row]
        config.save_hubs(self.hubs)
        self._reload_rule_list(select_row=new)

    def _rule_context_menu(self, pos):
        hub = self._hub
        if hub is None:
            return
        item = self.rule_list.itemAt(pos)
        if item is None:
            return
        row = self.rule_list.row(item)
        if row != self._rule_row:
            self.rule_list.setCurrentRow(row)   # select it (loads into editor)
        connected = self._rt(hub)["worker"] is not None
        last = len(hub["rules"]) - 1
        menu = QMenu(self.rule_list)
        a_up = menu.addAction("Move up", lambda: self._move_rule(-1))
        a_dn = menu.addAction("Move down", lambda: self._move_rule(1))
        menu.addSeparator()
        a_cl = menu.addAction("Clone", self._clone_rule)
        a_del = menu.addAction("Delete", self._del_rule)
        # order changes + clone/delete are edits -> locked while connected
        a_up.setEnabled(not connected and row > 0)
        a_dn.setEnabled(not connected and row < last)
        a_cl.setEnabled(not connected)
        a_del.setEnabled(not connected)
        menu.exec(self.rule_list.mapToGlobal(pos))

    def _browse_path(self):
        start = self.rule_fields["path"].text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose watch folder", start)
        if chosen:
            self.rule_fields["path"].setText(chosen)

    @staticmethod
    def _lines(text_edit):
        return [ln.strip() for ln in text_edit.toPlainText().splitlines() if ln.strip()]

    # -- connection ---------------------------------------------------------
    def _set_led(self, state):
        color = LED_COLORS.get(state, LED_COLORS["idle"])
        self.led.setStyleSheet(f"background-color: {color}; border-radius: 8px;")

    def _rt(self, hub):
        """Per-hub runtime state (worker + status/sid/keyprint + log/release
        buffers), created on first use. Keyed by id(hub) - the hub dicts in
        self.hubs are stable objects for the session."""
        rt = self._runtime.get(id(hub))
        if rt is None:
            rt = {"worker": None, "state": "idle", "detail": "disconnected",
                  "sid": "-", "keyprint": "-", "log": [], "releases": []}
            self._runtime[id(hub)] = rt
        return rt

    def _connect(self):
        hub = self._hub
        if hub is None:
            return
        rt = self._rt(hub)
        if rt["worker"]:          # this hub is already connected
            return
        # Warn if the Config tab has edits that were never saved - otherwise we
        # would silently connect with the last saved settings (Sopor).
        if self._hub_dirty:
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setText("The Config tab has unsaved changes for this hub.")
            box.setInformativeText(
                "Save them and connect with the new settings, or connect with "
                "the last saved settings?")
            save_btn = box.addButton("Save && connect",
                                     QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Connect anyway", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(save_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn:
                return
            if clicked is save_btn:
                self._save_hub()   # writes the editor into `hub` (same object)
        self._connect_hub(hub)

    def _connect_hub(self, hub):
        """Start a worker for `hub` and wire its hub-bound callbacks. Shared by
        the Connect button (selected hub) and startup auto-connect (any hub), so
        the two paths never drift. Only touches the visible UI when `hub` is the
        one on screen; a background hub just updates its own state + sidebar tint."""
        rt = self._rt(hub)
        if rt["worker"]:          # already connected
            return
        # Give the hub a stable PID/CID the first time and PERSIST it, so it
        # reconnects as the SAME bot identity across sessions - and so the
        # CID-keyed announced-dedup file stays stable (an ephemeral per-connect
        # identity would re-announce every release every session).
        if config.ensure_identity(hub):
            config.save_hubs(self.hubs)
        config_dict = {**hub}
        rt["sid"] = "-"
        rt["keyprint"] = "-"
        # Bind the hub to each callback so a background hub's events update its
        # own state, and the visible UI only if that hub is currently selected.
        worker = ConnWorker(config_dict)
        worker.status.connect(lambda s, d, h=hub: self._on_status(h, s, d))
        worker.logged.connect(lambda line, h=hub: self._on_log(h, line))
        worker.info.connect(lambda k, v, h=hub: self._on_info(h, k, v))
        worker.announced.connect(lambda n, c, p, d, h=hub: self._on_announce(h, n, c, p, d))
        worker.finished.connect(lambda h=hub: self._on_finished(h))
        worker.finished.connect(worker.deleteLater)  # defer C++ teardown to the event loop
        rt["worker"] = worker
        worker.start()
        self._on_log(hub, f"--- connecting to {hub['name']} ---")
        if config.target_is_local(self._settings):
            for rulename, path, exists in config.hub_path_status(hub):
                if not exists:
                    self._on_log(hub, f"folder not found: '{path}' "
                                      f"(rule '{rulename}' idles until it appears)")
        self._refresh_hub_list_item(hub)
        if hub is self._hub:
            self._refresh_connection_ui()

    def _autoconnect_startup(self):
        """Connect every hub with the per-hub Autoconnect flag, once the window
        is up. Deferred via singleShot so the UI is shown first. (Sopor)"""
        for hub in self.hubs:
            if hub.get("autoconnect"):
                self._connect_hub(hub)

    def _disconnect(self):
        hub = self._hub
        if hub is None:
            return
        rt = self._rt(hub)
        if rt["worker"]:
            self._on_log(hub, "--- disconnect requested ---")
            rt["worker"].stop()

    def _on_status(self, hub, state, detail):
        rt = self._rt(hub)
        rt["state"], rt["detail"] = state, detail
        if hub is self._hub:
            self._set_led(state)
            self.status_headline.setText(detail)

    def _on_info(self, hub, key, value):
        rt = self._rt(hub)
        if key in ("sid", "keyprint"):
            rt[key] = value
        if hub is self._hub:
            if key == "sid":
                self.info_sid.setText(value)
            elif key == "keyprint":
                self.info_keyprint.setText(value)

    def _release_tooltip(self, path):
        """The hover tooltip for a release row: the full path, or "" when the
        operator turned Release-Log tooltips off. Single source for both the item
        and the row label so the two gates cannot desync. The path ends in a
        remote-crafted release folder name and a tooltip has no PlainText flag
        (Qt auto-detects rich text), so escape it - the same anti-markup intent
        the visible label gets via setTextFormat(PlainText). (review L1)"""
        if not (path and self._settings.get("release_tooltips", True)):
            return ""
        return html.escape(path, quote=False)

    def _make_release_item(self, label, path, folder):
        """A release-log row. The visible text lives in the row WIDGET
        (_attach_release_row), NOT in the item's DisplayRole: setting BOTH drew
        the text twice - the item's own text under the transparent row widget -
        which showed as doubled / "dotted" text across the whole row from 1.9 on.
        So the item carries no display text; the label is kept in a data role
        for Copy, the folder in UserRole for the button/menu, the path as a
        hover tooltip. (Sopor)"""
        it = QListWidgetItem()                             # no DisplayRole text -> no ghost
        it.setData(Qt.ItemDataRole.UserRole + 1, label)   # raw label, for Copy
        if folder:
            it.setData(Qt.ItemDataRole.UserRole, folder)
        it.setToolTip(self._release_tooltip(path))
        return it

    def _attach_release_row(self, item, label, path, folder):
        """Render the row as its OWN widget: [text .......... folder-button].
        The item has no display text (see _make_release_item), so the widget is
        the sole renderer - no doubled text. The folder button appears only when
        there is a folder to open, and opens `folder`. Reachability comes from
        the SCANNER's is_dir (dirent-based), NOT a re-stat here: os.path.isdir on
        an FTP/network mount can return False for a folder the scan enumerated
        fine, which used to leave those rows button-less. (Sopor)"""
        row = QWidget()
        row.setObjectName("releaseRow")
        row.setStyleSheet("#releaseRow{background:transparent;}")  # let selection show
        lay = QHBoxLayout(row)
        # The row carries its own padding (the #releaseLog::item QSS padding is
        # zeroed - it insets and clips a setItemWidget widget). This keeps the
        # item sizeHint (row.sizeHint()) equal to the widget's real height so
        # the label + folder button are never clipped.
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(6)
        lbl = QLabel(label)
        # PlainText: a release name is a remote-crafted folder name; QLabel's
        # default AutoText would render markup (e.g. an <img src=UNC> that leaks
        # a NetNTLM hash on Windows).
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setToolTip(self._release_tooltip(path))
        lay.addWidget(lbl, 1)
        if folder:
            btn = QToolButton()
            btn.setObjectName("releaseFolderBtn")
            # Yellow folder glyph so the open-folder button reads as a folder
            # at a glance (Sopor). Semantic colour - kept literal in both themes,
            # like the green/red Connect buttons. Cached QIcon (self._folder_icon)
            # so a 5000-row rebuild does not reload the SVG per row. (review perf)
            btn.setIcon(self._folder_icon)
            btn.setAutoRaise(True)
            btn.setFixedSize(22, 22)
            btn.setToolTip("Open folder")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, f=folder: self._open_release_folder(f))
            lay.addWidget(btn, 0)
        item.setSizeHint(row.sizeHint())
        self.release_log.setItemWidget(item, row)

    def _on_announce(self, hub, name, category, path="", is_dir=True):
        ts = time.strftime("%H:%M:%S")
        label = f"{ts}   {name}" + (f"   [{category}]" if category else "")
        # The folder to reveal: the release directory itself, or the containing
        # directory for a single-file release. Derived from the scanner's is_dir
        # (dirent-based, works on FTP/network mounts) - NO re-stat here, so it
        # never re-hits a slow NAS on rebuild and never fails on a mount whose
        # per-path stat misbehaves.
        folder = "" if not path else (path if is_dir else os.path.dirname(path))
        rel = self._rt(hub)["releases"]
        rel.insert(0, (label, path, folder))
        if len(rel) > 5000:          # cap per-hub buffer, symmetric with the log
            del rel[5000:]
        if hub is self._hub:
            bar = self.release_log.verticalScrollBar()
            prev = bar.value()
            item = self._make_release_item(label, path, folder)
            self.release_log.insertItem(0, item)
            self._attach_release_row(item, label, path, folder)
            if self.release_log.count() > 5000:
                self.release_log.takeItem(self.release_log.count() - 1)
            # Newest is at the top; auto-scroll means show it, otherwise keep
            # the reader where they were. insertItem(0) pushed every row down by
            # one, so +1 holds the SAME item under the eye (ScrollPerItem). (Sopor)
            if self.chk_autoscroll_rel.isChecked():
                self._autoscroll_release_to_top()
            else:
                bar.setValue(min(prev + 1, bar.maximum()))

    def _on_finished(self, hub):
        rt = self._rt(hub)
        rt["worker"] = None
        rt["state"], rt["detail"] = "idle", "disconnected"
        self._refresh_hub_list_item(hub)
        if hub is self._hub:
            self._refresh_connection_ui()

    def _refresh_connection_ui(self):
        """Sync the LED / status / SID / keyprint / log / release list / buttons
        / editor-lock to the currently SELECTED hub's runtime state."""
        hub = self._hub
        if hub is None:
            return
        rt = self._rt(hub)
        connected = rt["worker"] is not None
        self._set_led(rt["state"])
        self.status_headline.setText(rt["detail"])
        self.info_sid.setText(rt["sid"])
        self.info_keyprint.setText(rt["keyprint"])
        log_bar = self.log_view.verticalScrollBar()
        old_val, old_max = log_bar.value(), log_bar.maximum()
        self.log_view.clear()
        for ts, line in rt["log"]:
            self._render_log_line(ts, line)
        # Position once, after the whole rebuild: bottom when auto-scroll is on,
        # else the reader's relative position (a per-line restore can't - clear()
        # already reset it to the top, so OFF used to yank to the top). (review M1)
        if self.chk_autoscroll_log.isChecked():
            self._autoscroll_log_to_bottom()
        else:
            frac = (old_val / old_max) if old_max else 1.0
            log_bar.setValue(round(frac * log_bar.maximum()))
        self._render_release_log()
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        # Only THIS hub's editor is locked while it is connected; other hubs
        # (and Add hub) stay editable.
        self._set_editing_enabled(not connected)

    def _render_release_log(self):
        """Rebuild the Release-Log list from the SELECTED hub's buffer. Shared by
        the connection-state refresh and a live settings change (the tooltip
        toggle), so the two render paths never drift."""
        self.release_log.clear()
        hub = self._hub
        if hub is None:
            return
        for label, path, folder in self._rt(hub)["releases"]:
            item = self._make_release_item(label, path, folder)
            self.release_log.addItem(item)
            self._attach_release_row(item, label, path, folder)
        # When following, re-assert the newest-at-top view after the rebuild
        # (deferred, same reason as the live path). Pre-fix this path did not
        # scroll the release list at all, so a reconnect / hub-switch left it
        # wherever it happened to land.
        if self.chk_autoscroll_rel.isChecked():
            self._autoscroll_release_to_top()

    def _refresh_hub_list_item(self, hub):
        """Tint a connected hub green in the sidebar so it is obvious which
        hubs are live."""
        try:
            idx = self.hubs.index(hub)
        except ValueError:
            return
        item = self.hub_list.item(idx)
        if item is not None:
            live = self._rt(hub)["worker"] is not None
            item.setForeground(QColor("#22c55e") if live else QColor("#dfe3ea"))

    def _set_editing_enabled(self, enabled):
        """Grey out the SELECTED hub's editing surface while it is connected -
        the running announcer captured that hub + its rules at connect time, so
        edits would only apply on a reconnect. Only this hub is locked: the hub
        LIST and Add hub stay live so other hubs can still be added and edited,
        and each hub locks itself when it connects. (Aybo)"""
        # Config tab of the selected hub + Save/Delete this hub
        for w in self.hub_fields.values():
            w.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)
        self.btn_del_hub.setEnabled(enabled)   # can't delete a connected hub
        # rule editor of the selected hub
        for w in self.rule_fields.values():
            w.setEnabled(enabled)
        # The include/exclude lists are multi-line: lock them READ-ONLY rather
        # than disabled, so a long list is still scrollable and readable while
        # connected (a disabled QPlainTextEdit cannot scroll). (Sopor)
        self.rule_blacklist.setReadOnly(not enabled)
        self.rule_whitelist.setReadOnly(not enabled)
        self.rule_daydir_prev.setEnabled(enabled)
        for b in (self.btn_add_rule, self.btn_clone_rule, self.btn_del_rule,
                  self.btn_save_rule, self.btn_browse):
            b.setEnabled(enabled)
        # config/rule-modifying menu actions (import / generate)
        for act in self._lock_when_connected:
            act.setEnabled(enabled)
        # NOTE: self.hub_list and self.btn_add_hub are deliberately NOT locked.

    # keyword -> colour, checked red first (problems win over anything else).
    _LOG_RED = ("fail", "error", "rejected", "mismatch", "refused", "unable",
                "cannot", "lost", "invalid", "broken")
    _LOG_GREEN = ("success", "complete", "announced", "verified", " active",
                  "logged in", "saved", "tls handshake ok")
    _LOG_ORANGE = ("connecting", "waiting", "detected", "reconnect", "info",
                   "warn", "please", "requested", "disconnect", "closed")

    # Status lines that carry an UNQUOTED release name: classify by the message
    # prefix, never by the name, or the name trips a keyword ("Announced:
    # Broken.Bread" -> red on "broken"; "Detected, waiting for completion:
    # Stuart.Fails..." -> red on "fail"). (Sopor)
    _LOG_NAME_PREFIX = (
        ("announced:", "#22c55e"),
        ("detected, waiting", "#f59e0b"),
    )

    @classmethod
    def _log_color(cls, line):
        low = line.lower()
        for prefix, colour in cls._LOG_NAME_PREFIX:
            if low.startswith(prefix):
                return colour
        # Drop single-quoted spans (release names / paths in "Release: '...'
        # blocked", "Searching in '...'") so their contents don't trip keywords.
        if "'" in low:
            low = "".join(low.split("'")[::2])
        if any(k in low for k in cls._LOG_RED):
            return "#ef4444"
        if any(k in low for k in cls._LOG_GREEN):
            return "#22c55e"
        if any(k in low for k in cls._LOG_ORANGE):
            return "#f59e0b"
        return "#a9b0bd"

    def _render_log_line(self, ts, line):
        # Pure append - no scroll. The CALLER positions the view: the live path
        # (_on_log) and the bulk rebuild (_refresh_connection_ui) each apply the
        # auto-scroll rule ONCE. A per-line rule cannot survive a clear()+rebuild
        # (clear() has already reset the scrollbar to the top). (review M1)
        color = self._log_color(line)
        # Timestamp dimmed so the message keeps its status colour.
        self.log_view.appendHtml(
            f'<span style="color:#6b7280">[{ts}]</span> '
            f'<span style="color:{color}">{html.escape(line)}</span>')

    def _autoscroll_log_to_bottom(self):
        """Scroll the status log to its true bottom, deferred to the next
        event-loop tick. A scroll done inline with appendHtml lands one line
        short when the append arrives on a queued worker signal: the document
        layout (and thus the scrollbar range) has not grown yet, so aiming at
        maximum() hits a stale bottom. singleShot(0) lets the pending layout
        settle first, then setValue(maximum) reaches the real end. Offscreen
        layout is synchronous, which is why this never reproduced in tests and
        only bit on a real display (Sopor)."""
        def _to_end():
            # re-check at fire time: the user may have toggled auto-scroll off
            # between the append and this deferred callback.
            if not self.chk_autoscroll_log.isChecked():
                return
            bar = self.log_view.verticalScrollBar()
            bar.setValue(bar.maximum())
        QTimer.singleShot(0, _to_end)

    def _autoscroll_release_to_top(self):
        """Show the newest release (index 0), deferred for the same reason as
        the log scroll: an inline scrollToTop right after insertItem can aim at
        a not-yet-updated viewport on a queued announce signal."""
        def _to_top():
            if self.chk_autoscroll_rel.isChecked():
                self.release_log.scrollToTop()
        QTimer.singleShot(0, _to_top)

    def _reapply_autoscroll(self):
        """Re-assert both auto-scroll positions once the window is on screen
        again. While Herald is hidden in the tray (or minimized to the taskbar),
        log lines keep arriving but the widgets are not laid out, so every
        deferred scroll aimed at a STALE scrollbar maximum() and got stuck near
        the top; on restore nothing re-ran it. A fresh deferred pass after the
        window is shown - when the layout has finally caught up - lands on the
        real bottom / top. (Sopor)"""
        if self.chk_autoscroll_log.isChecked():
            self._autoscroll_log_to_bottom()
        if self.chk_autoscroll_rel.isChecked():
            self._autoscroll_release_to_top()

    def _on_log(self, hub, line):
        """Store a log line in the hub's own buffer (local system time) and
        render it only if that hub is the one on screen."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        rt = self._rt(hub)
        rt["log"].append((ts, line))
        if len(rt["log"]) > 5000:          # cap per-hub buffer (big first scan)
            del rt["log"][:len(rt["log"]) - 5000]
        if hub is self._hub:
            bar = self.log_view.verticalScrollBar()
            prev = bar.value()
            self._render_log_line(ts, line)
            # Follow the bottom only when auto-scroll is on; otherwise hold the
            # reader's position so a live scan does not yank the view. Use the
            # cursor to reach the true end - bar.maximum() right after appendHtml
            # can be stale (lazy layout), which left the view stuck near the top.
            # (Sopor)
            if self.chk_autoscroll_log.isChecked():
                self._autoscroll_log_to_bottom()
            else:
                bar.setValue(prev)

    def _log(self, line):
        """A UI-general message (save, import, ...) -> the selected hub's log."""
        hub = self._hub
        if hub is not None:
            self._on_log(hub, line)

    def _clear_log(self):
        self.log_view.clear()
        hub = self._hub
        if hub is not None:
            self._rt(hub)["log"].clear()

    def _clear_releases(self):
        self.release_log.clear()
        hub = self._hub
        if hub is not None:
            self._rt(hub)["releases"].clear()

    def _log_context_menu(self, pos):
        # keep the standard copy / select-all entries, append Clear. (Sopor)
        menu = self.log_view.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction("Clear status log", self._clear_log)
        menu.exec(self.log_view.mapToGlobal(pos))

    def _release_context_menu(self, pos):
        menu = QMenu(self.release_log)
        item = self.release_log.itemAt(pos)
        if item is not None:
            folder = item.data(Qt.ItemDataRole.UserRole)   # resolved at announce
            if folder:
                menu.addAction("Open folder",
                               lambda f=folder: self._open_release_folder(f))
            menu.addAction("Copy", lambda: QApplication.clipboard().setText(
                item.data(Qt.ItemDataRole.UserRole + 1) or ""))
            menu.addSeparator()
        menu.addAction("Clear release log", self._clear_releases)
        menu.exec(self.release_log.mapToGlobal(pos))

    def _open_release_folder(self, folder):
        """Open a release's folder in the OS file manager (Explorer on Windows).
        `folder` was resolved from the scanner's dir/file knowledge at announce
        time; open it directly - a since-deleted folder just fails in the OS,
        and we deliberately do NOT re-stat (that is what broke folders on an
        FTP/network mount)."""
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # -- system tray (minimize to the notification area) --------------------
    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(HERE / "assets" / "herald-broadcast.svg"))
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Herald")
        menu = QMenu()
        self._tray_menu = menu   # keep a reference so it is not GC'd
        self._tray_toggle_action = menu.addAction("Show Herald")
        self._tray_toggle_action.triggered.connect(self._tray_toggle)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self._tray_quit)
        # Relabel Show/Hide to match the current window state each time the
        # menu opens, so it never says "Show Herald" while Herald is visible.
        menu.aboutToShow.connect(self._update_tray_menu)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _update_tray_menu(self):
        shown = self.isVisible() and not self.isMinimized()
        self._tray_toggle_action.setText("Hide Herald" if shown else "Show Herald")

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._tray_toggle()

    def _tray_toggle(self):
        # Click the tray icon to peek (show) or hide again - a quick
        # show/hide without touching the window controls (Sopor). Minimized
        # counts as hidden, so a click restores it.
        if self.isVisible() and not self.isMinimized():
            self._hide_to_tray()
        else:
            self._tray_show()

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_quit(self):
        self.close()   # closeEvent handles the quit confirm + teardown

    def _hide_to_tray(self):
        self.hide()
        if not self._tray_hinted:
            self._tray_hinted = True
            self._tray.showMessage(
                "Herald", "Still running in the notification area - "
                "click the icon to show or hide.",
                QSystemTrayIcon.MessageIcon.Information, 4000)

    def showEvent(self, event):
        super().showEvent(event)
        # Restored from the tray (hide -> show): re-assert auto-scroll, which got
        # stuck on a stale scrollbar maximum while the window was hidden. Deferred
        # so the show-triggered relayout settles first. Harmless on the initial
        # show (the logs are empty then). (Sopor)
        QTimer.singleShot(0, self._reapply_autoscroll)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            # minimize -> hide into the tray instead of the taskbar
            if self._tray is not None and self.isMinimized():
                if self._suppress_tray_hide:
                    # The startup "Start minimized" case stays on the taskbar
                    # (always reachable) rather than auto-hiding to a tray that
                    # some Linux desktops report as available but never actually
                    # render - which would leave Herald running + connected with
                    # no way back. A deliberate later minimize still hides to the
                    # tray. (review L3)
                    self._suppress_tray_hide = False
                else:
                    QTimer.singleShot(0, self._hide_to_tray)
            elif (event.oldState() & Qt.WindowState.WindowMinimized
                  and not self.isMinimized()):
                # Restored from a taskbar minimize (no tray hide, so no showEvent):
                # re-assert auto-scroll, stuck on a stale maximum while minimized.
                # (Sopor)
                QTimer.singleShot(0, self._reapply_autoscroll)
        super().changeEvent(event)

    def closeEvent(self, event):
        live = [rt for rt in self._runtime.values() if rt["worker"] is not None]
        text = (f"{len(live)} hub(s) connected. Quit Herald and disconnect?"
                if live else "Quit Herald?")
        if not self._confirm("Quit Herald", text):
            event.ignore()
            return
        # remember window size + position for next launch
        try:
            self._settings["window_geometry"] = bytes(
                self.saveGeometry().toBase64()).decode("ascii")
            config.save_settings(self._settings)
        except Exception:
            pass
        if self._tray is not None:
            self._tray.hide()
        for rt in live:
            rt["worker"].stop()
        # Wait under ONE shared 3s budget, not 3s PER worker: a worker stuck in
        # its initial connect to a dead host may not be interruptible by the
        # cross-thread socket close, and with autoconnect there can be many such
        # workers - a per-worker wait would freeze quit for up to 3s x N. (review L2)
        deadline = time.monotonic() + 3.0
        for rt in live:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms > 0:
                rt["worker"].wait(remaining_ms)
        # Join an in-flight update check too - tearing down a running QThread
        # aborts the process on exit. It only blocks on a bounded HTTP timeout.
        upd = getattr(self, "_update_thread", None)
        if upd is not None and upd.isRunning():
            upd.wait(7000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    icon = QIcon(str(HERE / "assets" / "herald-broadcast.svg"))
    app.setWindowIcon(icon)
    # Apply the persisted theme (settings.json) at startup so the choice
    # survives a restart. (Aybo)
    app.setStyleSheet(load_stylesheet(config.load_settings().get("theme", "dark")))
    win = Herald()
    win.setWindowIcon(icon)
    # Start minimized when the operator asked for it: minimize to the TASKBAR
    # (always reachable), not the tray - see changeEvent / the L3 review note.
    # (Sopor)
    if config.load_settings().get("start_minimized", False):
        win._suppress_tray_hide = True
        win.showMinimized()
    else:
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
