"""Main window + system-tray icon for the Argus daemon (PySide6).

The daemon's actual work (polling, BLE/USB writes) runs in a worker thread.
This module owns the user-facing surface:

  - A QMainWindow with live status, settings (GitHub PAT, brightness,
    transport), and a scrolling log view.
  - A QSystemTrayIcon with Show / Quit menu items. Closing the window
    hides it to the tray rather than quitting.

The worker reloads config on every poll, so changes apply within ~60 s
without restarting the daemon.

If PySide6 is unavailable or the platform has no system tray, `run_tray()`
returns False and the caller should fall back to headless mode.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Callable

import token_crypt  # local module: DPAPI wrapper for the GitHub PAT


# ----- Config file path -----------------------------------------------------

APP_NAME = "Argus"


def config_dir() -> Path:
    """Platform-appropriate per-user config directory.

    Windows: %APPDATA%\\Argus
    macOS:   ~/Library/Application Support/Argus
    Linux:   ~/.config/argus (XDG default)
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME.lower()


def config_path() -> Path:
    return config_dir() / "config.json"


DEFAULTS = {
    "github_token": "",
    "copilot_org": "",         # GitHub org slug for Copilot seat lookup (status / editor / last activity)
    "copilot_enterprise": "",  # GitHub Enterprise slug for premium-request usage endpoint
    "copilot_allowance": 1000, # monthly premium-request allowance per plan (300/1000/300/1500)
    "brightness": 100,         # 10..100 — software dim overlay on the device
    "transport": "ble",        # "ble" | "usb"
    "poll_interval": 60,       # seconds between Anthropic API polls
    "enabled_apps": ["usage", "today", "github", "copilot"],  # which tabs cycle on the device
}

# Premium-request monthly allowance by Copilot plan tier. The dropdown in
# the Copilot settings tab maps to these values. Numbers come from the
# user-provided example script + GitHub's published per-plan quotas.
COPILOT_PLAN_PRESETS = [
    ("Copilot Enterprise (1000)", 1000),
    ("Copilot Business (300)",     300),
    ("Copilot Pro (300)",          300),
    ("Copilot Pro+ (1500)",       1500),
]

# Apps known to the system. The Visibility checkbox in each tab toggles
# membership of `enabled_apps`. Add a new app by appending here (and
# extending the firmware enum + daemon `ALL_APPS`).
APP_REGISTRY = [
    ("usage",   "Usage",   "Claude Code rate-limit + reset countdown."),
    ("today",   "Today",   "Cost, tokens, model split from your local Claude logs."),
    ("github",  "GitHub",  "Open issues + PRs waiting on you."),
    ("copilot", "Copilot", "GitHub Copilot status, last activity, editor."),
]

# Poll-interval presets exposed in the UI. Anything below ~10 s risks Anthropic
# rate-limiting on minimal accounts; anything above 10 min defeats the point of
# a live display.
POLL_INTERVAL_PRESETS = [
    (30,  "30 seconds"),
    (60,  "1 minute"),
    (120, "2 minutes"),
    (300, "5 minutes"),
    (600, "10 minutes"),
]


def _fresh_defaults() -> dict:
    """Return a deep-copy-ish snapshot of DEFAULTS so callers can mutate the
    `enabled_apps` list without scribbling on the module-level defaults."""
    base = dict(DEFAULTS)
    base["enabled_apps"] = list(DEFAULTS["enabled_apps"])
    return base


def load_config() -> dict:
    base = _fresh_defaults()
    path = config_path()
    if not path.exists():
        return base
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if k in DEFAULTS:
                base[k] = v
    except (OSError, json.JSONDecodeError):
        return base

    # Filter enabled_apps against the registry so a stale config (referencing
    # an app we've since removed) doesn't propagate junk into the daemon CSV.
    known = {name for name, _, _ in APP_REGISTRY}
    base["enabled_apps"] = [a for a in base.get("enabled_apps") or [] if a in known]

    # GitHub PAT is stored encrypted-at-rest (DPAPI on Windows). The in-
    # memory cfg always holds the plaintext so the rest of the daemon
    # (github_stats, copilot_stats, tray UI line edits) doesn't have to
    # think about crypto.
    base["github_token"] = token_crypt.decrypt(base.get("github_token", ""))
    return base


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Don't mutate the caller's dict — they're still holding plaintext.
    out = dict(cfg)
    tok = out.get("github_token", "") or ""
    # Treat any value that already carries our storage prefix as
    # already-encoded so we don't double-wrap on a save → load → save
    # round-trip. Anything else (plaintext from the UI line edit, legacy
    # config from before any prefixing, etc.) goes through encrypt(),
    # which pushes it into the OS credential store and returns a marker.
    if not (tok.startswith("keyring:") or tok.startswith("dpapi:") or tok.startswith("plain:")):
        out["github_token"] = token_crypt.encrypt(tok)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


# ----- Windows autostart (HKCU Run key) -------------------------------------
#
# The registry is the source of truth here — we read on UI load and write on
# save. No autostart field is stored in config.json so we don't accidentally
# drift from the actual login behavior.

_RUN_KEY_PATH   = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "Argus"


def autostart_supported() -> bool:
    """Autostart toggling is only wired up on Windows."""
    return sys.platform == "win32"


def _build_autostart_command() -> str:
    """Compose the command line that should run at login.

    Prefers `pythonw.exe` in dev mode so launching at login doesn't pop a
    console window. When packaged with PyInstaller (`sys.frozen`), the exe
    is launched directly.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(pyw):
            exe = pyw

    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if not script:
        # Fallback: launch this module's parent script. Unusual but possible
        # when the daemon is started by some shim that clobbers sys.argv[0].
        script = os.path.abspath(__file__).replace("tray_ui.py", "argus-daemon.py")
    return f'"{exe}" "{script}"'


def is_autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            val, _ = winreg.QueryValueEx(key, _RUN_VALUE_NAME)
            return bool(val)
    except (OSError, FileNotFoundError):
        return False


def set_autostart(enabled: bool) -> bool:
    """Enable or disable login autostart. Returns True on success."""
    if not autostart_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ,
                                  _build_autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ----- Worker → UI plumbing -------------------------------------------------
#
# The daemon's worker thread pushes log lines and status updates through these
# thread-safe primitives. The main window drains them on a QTimer tick — Qt
# widgets must only be touched on the main thread, so we never let the worker
# call into widgets directly.

_LOG_QUEUE: "queue.Queue[str]" = queue.Queue(maxsize=4000)
_STATUS: dict = {"state": "idle", "label": "Starting…"}
_STATUS_LOCK = threading.Lock()


def log_line(msg: str) -> None:
    """Push a log line to the UI. Safe to call from any thread."""
    try:
        _LOG_QUEUE.put_nowait(msg)
    except queue.Full:
        pass


def set_status(state: str, label: str) -> None:
    """Update the header status pill. `state` is one of:
        "ok"         — green dot, connected
        "warn"       — amber dot, scanning/reconnecting
        "err"        — red dot, error
        "idle"       — muted dot, not yet started
    Thread-safe.
    """
    with _STATUS_LOCK:
        _STATUS["state"] = state
        _STATUS["label"] = label


def _snapshot_status() -> dict:
    with _STATUS_LOCK:
        return dict(_STATUS)


# ----- Brand / theme --------------------------------------------------------

BG_DARK     = "#15110d"
BG_CARD     = "#1f1c18"
BG_INPUT    = "#26221d"
TERRA       = "#d97757"
TERRA_HOVER = "#e88864"
TERRA_PRESS = "#c46647"
CREAM       = "#faf9f5"
MUTED       = "#8a857a"
BORDER      = "#332e28"
OK_GREEN    = "#7ab988"
AMBER       = "#e0a96c"
ERR_RED     = "#d96666"


STYLESHEET = f"""
QMainWindow, QWidget#central {{
    background-color: {BG_DARK};
}}

QLabel {{
    color: {CREAM};
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    font-size: 13px;
    background: transparent;
}}

QLabel#brand {{
    color: {TERRA};
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

QLabel#muted {{
    color: {MUTED};
    font-size: 12px;
}}

QLabel#sectionLabel {{
    color: {TERRA};
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
}}

QGroupBox {{
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 11px;
    font-weight: 700;
    color: {TERRA};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 22px 16px 14px 16px;
    background-color: {BG_CARD};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 6px;
    letter-spacing: 1.2px;
}}

QLineEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 13px;
    font-family: "Segoe UI", Arial, sans-serif;
    color: {CREAM};
    selection-background-color: {TERRA};
    selection-color: {BG_DARK};
}}

QLineEdit:focus {{
    border-color: {TERRA};
}}

QPushButton {{
    background-color: {BG_INPUT};
    color: {CREAM};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 13px;
    font-family: "Segoe UI", Arial, sans-serif;
}}

QPushButton:hover {{
    background-color: {BG_CARD};
    border-color: {TERRA};
}}

QPushButton:pressed {{
    background-color: {BG_DARK};
}}

QPushButton:disabled {{
    color: {MUTED};
    border-color: {BORDER};
}}

QPushButton#primary {{
    background-color: {TERRA};
    color: {BG_DARK};
    border: none;
    padding: 10px 30px;
    font-weight: 700;
}}

QPushButton#primary:hover {{
    background-color: {TERRA_HOVER};
}}

QPushButton#primary:pressed {{
    background-color: {TERRA_PRESS};
}}

QRadioButton, QCheckBox {{
    color: {CREAM};
    font-size: 13px;
    background: transparent;
    spacing: 6px;
}}

QRadioButton::indicator, QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}

QSlider::groove:horizontal {{
    border: none;
    height: 6px;
    background: {BG_INPUT};
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {TERRA};
    border-radius: 3px;
}}

QSlider::add-page:horizontal {{
    background: {BG_INPUT};
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background: {CREAM};
    border: 2px solid {TERRA};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 9px;
}}

QSlider::handle:horizontal:hover {{
    background: {TERRA_HOVER};
    border-color: {CREAM};
}}

QPlainTextEdit {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: {CREAM};
    selection-background-color: {TERRA};
    selection-color: {BG_DARK};
}}

QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {CREAM};
    font-size: 13px;
    min-width: 140px;
}}

QComboBox:hover {{
    border-color: {TERRA};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    color: {CREAM};
    border: 1px solid {BORDER};
    selection-background-color: {TERRA};
    selection-color: {BG_DARK};
    padding: 2px;
}}

QMenu {{
    background-color: {BG_CARD};
    color: {CREAM};
    border: 1px solid {BORDER};
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 14px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {TERRA};
    color: {BG_DARK};
}}

QFrame#hsep {{
    background-color: {BORDER};
    max-height: 1px;
}}
"""


# ----- Icon -----------------------------------------------------------------

def _make_app_icon():
    """Return the Argus mascot icon. Prefers the official `happy.png` sprite
    from assets/img/; falls back to a stroked A-mark if the asset isn't
    where we expect (e.g. when the daemon is run from a different cwd).
    """
    from PySide6.QtCore import Qt, QSize, QPointF
    from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QIcon

    # Locate assets/img/happy.png relative to the daemon source tree.
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "assets" / "img" / "happy.png",
        here / "assets" / "img" / "happy.png",
    ):
        if candidate.exists():
            pm = QPixmap(str(candidate))
            if not pm.isNull():
                # Scale down to a typical tray-icon size with smooth filtering.
                return QIcon(pm.scaled(
                    256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation,
                ))

    # Fallback — stroked A so the tray still has a usable icon.
    pm = QPixmap(QSize(64, 64))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(TERRA))
    pen.setWidth(10)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(32, 8), QPointF(10, 56))
    p.drawLine(QPointF(32, 8), QPointF(54, 56))
    p.drawLine(QPointF(20, 38), QPointF(44, 38))
    p.end()
    return QIcon(pm)


# ----- Qt main window -------------------------------------------------------

def _run_qt(on_save: Callable[[dict], None], stop_event: threading.Event) -> bool:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QSystemTrayIcon, QMenu, QWidget,
        QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QGroupBox, QRadioButton, QButtonGroup, QSlider, QPlainTextEdit,
        QFrame, QComboBox, QCheckBox, QTabWidget,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLESHEET)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # No tray surface — refuse to start so the daemon falls back to headless.
        print("[tray] no system tray available on this platform", flush=True)
        return False

    icon = _make_app_icon()

    class Win(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Argus")
            self.setWindowIcon(icon)
            self.setMinimumSize(640, 720)
            self.resize(720, 820)
            self._build_ui(load_config())
            self._wire_drain_timer()

        def _build_ui(self, cfg: dict):
            central = QWidget()
            central.setObjectName("central")
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(24, 20, 24, 18)
            root.setSpacing(12)

            # --- Header --------------------------------------------------
            header = QHBoxLayout()
            header.setSpacing(12)

            brand = QLabel("Argus")
            brand.setObjectName("brand")
            header.addWidget(brand)

            tagline = QLabel("desk-side dev monitor")
            tagline.setObjectName("muted")
            header.addWidget(tagline)

            header.addStretch()

            self.status_dot = QLabel("●")
            self.status_dot.setStyleSheet(f"color: {MUTED}; font-size: 18px;")
            self.status_label = QLabel("Starting…")
            self.status_label.setStyleSheet(f"color: {CREAM}; font-weight: 600;")
            header.addWidget(self.status_dot)
            header.addWidget(self.status_label)
            root.addLayout(header)

            sep = QFrame()
            sep.setObjectName("hsep")
            sep.setFrameShape(QFrame.HLine)
            root.addWidget(sep)

            # --- Tabs: System + one per app -----------------------------
            # System tab holds connection/behavior/display (the always-on
            # settings). Each app gets a dedicated tab with its own
            # "Show on device" checkbox + app-specific config. Adding a new
            # app = append to APP_REGISTRY + add a _build_<app>_tab() below.
            self.chk_apps = {}
            self.tabs = QTabWidget()
            self.tabs.addTab(self._build_system_tab(cfg), "System")
            for name, label, _desc in APP_REGISTRY:
                builder = getattr(self, f"_build_{name}_tab", None)
                page = builder(cfg) if builder else self._build_app_tab(cfg, name)
                self.tabs.addTab(page, label)
            root.addWidget(self.tabs)

            # --- Live log -----------------------------------------------
            log_lbl = QLabel("LIVE LOG")
            log_lbl.setObjectName("sectionLabel")
            root.addWidget(log_lbl)
            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setMaximumBlockCount(2000)
            self.log_view.setPlaceholderText("Waiting for daemon output…")
            root.addWidget(self.log_view, stretch=1)

            # --- Footer -------------------------------------------------
            footer = QHBoxLayout()
            cfg_hint = QLabel(f"Config: {config_path()}")
            cfg_hint.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            footer.addWidget(cfg_hint)
            footer.addStretch()
            self.btn_hide = QPushButton("Hide to tray")
            self.btn_hide.clicked.connect(self.hide)
            self.btn_save = QPushButton("Save")
            self.btn_save.setObjectName("primary")
            self.btn_save.clicked.connect(self._on_save_clicked)
            self.btn_quit = QPushButton("Quit")
            self.btn_quit.clicked.connect(self._on_quit_clicked)
            footer.addWidget(self.btn_hide)
            footer.addWidget(self.btn_save)
            footer.addWidget(self.btn_quit)
            root.addLayout(footer)

        # ----- Tab builders ----------------------------------------------
        # Each app tab packs an "Enable on device" checkbox at the top + an
        # app-specific config widget block. Toggle state lands in
        # self.chk_apps[name] for _on_save_clicked() to read uniformly.

        def _app_visibility_row(self, name: str, cfg: dict) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(10)
            chk = QCheckBox("Show on device")
            chk.setChecked(name in (cfg.get("enabled_apps") or []))
            chk.setToolTip(
                "Unchecked apps are hidden from the device's screen cycle. "
                "Tap the middle button on the device to step through the "
                "screens you've enabled."
            )
            self.chk_apps[name] = chk
            row.addWidget(chk)
            row.addStretch()
            return row

        def _build_system_tab(self, cfg: dict) -> QWidget:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(12)

            # Connection
            conn = QGroupBox("CONNECTION")
            cl = QHBoxLayout()
            cl.setSpacing(24)
            self.rb_ble = QRadioButton("Bluetooth (auto-discover)")
            self.rb_usb = QRadioButton("USB-C serial (auto-detect)")
            grp = QButtonGroup(self)
            grp.addButton(self.rb_ble)
            grp.addButton(self.rb_usb)
            (self.rb_usb if cfg["transport"] == "usb" else self.rb_ble).setChecked(True)
            cl.addWidget(self.rb_ble)
            cl.addWidget(self.rb_usb)
            cl.addStretch()
            conn.setLayout(cl)
            v.addWidget(conn)

            # Behavior
            beh = QGroupBox("BEHAVIOR")
            bl = QHBoxLayout()
            bl.setSpacing(14)
            bl.addWidget(QLabel("Poll every"))
            self.cb_interval = QComboBox()
            current_interval = int(cfg.get("poll_interval", 60))
            selected_row = 0
            for i, (secs, label) in enumerate(POLL_INTERVAL_PRESETS):
                self.cb_interval.addItem(label, userData=secs)
                if secs == current_interval:
                    selected_row = i
            if all(s != current_interval for s, _ in POLL_INTERVAL_PRESETS):
                self.cb_interval.addItem(f"{current_interval} seconds (custom)",
                                         userData=current_interval)
                selected_row = self.cb_interval.count() - 1
            self.cb_interval.setCurrentIndex(selected_row)
            bl.addWidget(self.cb_interval)
            bl.addSpacing(24)

            self.chk_autostart = QCheckBox("Start with Windows")
            if autostart_supported():
                self.chk_autostart.setChecked(is_autostart_enabled())
                self.chk_autostart.setToolTip(
                    "Register the daemon under HKCU\\…\\Run so it launches at login."
                )
            else:
                self.chk_autostart.setEnabled(False)
                self.chk_autostart.setToolTip(
                    "Autostart toggle is only wired up on Windows."
                )
            bl.addWidget(self.chk_autostart)
            bl.addStretch()
            beh.setLayout(bl)
            v.addWidget(beh)

            # Display
            disp = QGroupBox("DISPLAY")
            dl = QHBoxLayout()
            dl.setSpacing(14)
            bright_lbl = QLabel("Brightness")
            bright_lbl.setMinimumWidth(80)
            dl.addWidget(bright_lbl)
            self.sl_bright = QSlider(Qt.Horizontal)
            self.sl_bright.setRange(10, 100)
            self.sl_bright.setValue(int(cfg["brightness"]))
            self.lbl_bright = QLabel(f"{int(cfg['brightness'])} %")
            self.lbl_bright.setStyleSheet(
                f"color: {TERRA}; font-weight: 700; min-width: 56px;"
            )
            self.sl_bright.valueChanged.connect(
                lambda val: self.lbl_bright.setText(f"{val} %")
            )
            dl.addWidget(self.sl_bright, stretch=1)
            dl.addWidget(self.lbl_bright)
            disp.setLayout(dl)
            v.addWidget(disp)

            v.addStretch()
            return page

        def _app_description(self, name: str) -> QLabel:
            desc = next((d for n, _, d in APP_REGISTRY if n == name), "")
            lbl = QLabel(desc)
            lbl.setObjectName("muted")
            lbl.setWordWrap(True)
            return lbl

        def _build_app_tab(self, cfg: dict, name: str) -> QWidget:
            """Generic tab — visibility checkbox + description. Used for apps
            that need no further configuration (Usage, Today)."""
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(12)
            v.addLayout(self._app_visibility_row(name, cfg))
            v.addWidget(self._app_description(name))
            v.addStretch()
            return page

        def _build_usage_tab(self, cfg: dict) -> QWidget:
            return self._build_app_tab(cfg, "usage")

        def _build_today_tab(self, cfg: dict) -> QWidget:
            return self._build_app_tab(cfg, "today")

        def _build_github_tab(self, cfg: dict) -> QWidget:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(12)
            v.addLayout(self._app_visibility_row("github", cfg))
            v.addWidget(self._app_description("github"))

            gh = QGroupBox("Token")
            gl = QVBoxLayout()
            gl.setSpacing(8)
            self.ed_token = QLineEdit(cfg["github_token"])
            self.ed_token.setEchoMode(QLineEdit.Password)
            self.ed_token.setPlaceholderText("ghp_… or github_pat_…")
            gl.addWidget(self.ed_token)
            hint = QLabel(
                "Personal access token with read on issues + pull requests. "
                "Leave blank to disable polling — the screen will still show "
                "if its visibility checkbox is on."
            )
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            gl.addWidget(hint)
            gh.setLayout(gl)
            v.addWidget(gh)
            v.addStretch()
            return page

        def _build_copilot_tab(self, cfg: dict) -> QWidget:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(12)
            v.addLayout(self._app_visibility_row("copilot", cfg))
            v.addWidget(self._app_description("copilot"))

            cp = QGroupBox("Org")
            cl = QVBoxLayout()
            cl.setSpacing(8)
            self.ed_copilot_org = QLineEdit(cfg.get("copilot_org", ""))
            self.ed_copilot_org.setPlaceholderText("github org slug (e.g. my-company)")
            cl.addWidget(self.ed_copilot_org)
            hint = QLabel(
                "Org slug → drives seat status, last activity and editor. "
                "Reuses the GitHub PAT from the GitHub tab; PAT needs "
                "admin / Copilot Business seat-read on this org."
            )
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            cl.addWidget(hint)
            cp.setLayout(cl)
            v.addWidget(cp)

            ent = QGroupBox("Enterprise (premium requests)")
            el = QVBoxLayout()
            el.setSpacing(8)
            self.ed_copilot_enterprise = QLineEdit(cfg.get("copilot_enterprise", ""))
            self.ed_copilot_enterprise.setPlaceholderText("github enterprise slug (e.g. My-Enterprise)")
            el.addWidget(self.ed_copilot_enterprise)

            # Plan / allowance dropdown — picks the monthly premium-request
            # quota the device divides usage against. Keep in sync with
            # copilot_stats.ALLOWANCE_BY_PLAN.
            plan_row = QHBoxLayout()
            plan_row.setSpacing(10)
            plan_lbl = QLabel("Plan")
            plan_lbl.setMinimumWidth(60)
            plan_row.addWidget(plan_lbl)
            self.cb_copilot_plan = QComboBox()
            current_allowance = int(cfg.get("copilot_allowance") or DEFAULTS["copilot_allowance"])
            sel = 0
            for i, (label, alw) in enumerate(COPILOT_PLAN_PRESETS):
                self.cb_copilot_plan.addItem(label, alw)
                if alw == current_allowance:
                    sel = i
            self.cb_copilot_plan.setCurrentIndex(sel)
            plan_row.addWidget(self.cb_copilot_plan, stretch=1)
            el.addLayout(plan_row)

            hint_e = QLabel(
                "Enterprise slug → drives the \"Premium requests X/Y "
                "(NN%)\" display. Needs a PAT with enterprise billing read "
                "access. Leave blank if your Copilot Business org isn't "
                "under an enclosing enterprise — the percentage panel will "
                "just be hidden."
            )
            hint_e.setObjectName("muted")
            hint_e.setWordWrap(True)
            el.addWidget(hint_e)
            ent.setLayout(el)
            v.addWidget(ent)

            v.addStretch()
            return page

        def _wire_drain_timer(self):
            t = QTimer(self)
            t.setInterval(200)
            t.timeout.connect(self._drain)
            t.start()

        def _drain(self):
            # Drain a bounded chunk of log lines so a flood never wedges the UI.
            for _ in range(200):
                try:
                    line = _LOG_QUEUE.get_nowait()
                except queue.Empty:
                    break
                self.log_view.appendPlainText(line)

            s = _snapshot_status()
            color_for = {
                "ok":   OK_GREEN,
                "warn": AMBER,
                "err":  ERR_RED,
                "idle": MUTED,
            }
            self.status_dot.setStyleSheet(
                f"color: {color_for.get(s['state'], MUTED)}; font-size: 18px;"
            )
            self.status_label.setText(s["label"])

        def _on_save_clicked(self):
            interval = int(self.cb_interval.currentData() or 60)
            enabled = [name for name, _, _ in APP_REGISTRY
                       if self.chk_apps[name].isChecked()]
            new_cfg = {
                "github_token":       self.ed_token.text().strip(),
                "copilot_org":        self.ed_copilot_org.text().strip(),
                "copilot_enterprise": self.ed_copilot_enterprise.text().strip(),
                "copilot_allowance":  int(self.cb_copilot_plan.currentData()
                                         or DEFAULTS["copilot_allowance"]),
                "brightness":         int(self.sl_bright.value()),
                "transport":          "usb" if self.rb_usb.isChecked() else "ble",
                "poll_interval":      max(5, interval),
                "enabled_apps":       enabled,
            }
            save_config(new_cfg)
            if autostart_supported():
                ok = set_autostart(self.chk_autostart.isChecked())
                if not ok:
                    self.log_view.appendPlainText(
                        "[ui] failed to update HKCU Run key (autostart unchanged)"
                    )
            on_save(new_cfg)
            self.log_view.appendPlainText("[ui] settings saved")

        def _on_quit_clicked(self):
            stop_event.set()
            app.quit()

        def closeEvent(self, e):
            # Hide to tray instead of quitting. The tray menu has explicit Quit.
            e.ignore()
            self.hide()

    win = Win()

    # System tray icon -------------------------------------------------------
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("Argus daemon")
    menu = QMenu()

    act_show = QAction("Show window", menu)
    def _show():
        win.showNormal()
        win.raise_()
        win.activateWindow()
    act_show.triggered.connect(_show)

    act_quit = QAction("Quit", menu)
    def _do_quit():
        stop_event.set()
        app.quit()
    act_quit.triggered.connect(_do_quit)

    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)

    def _on_activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            _show()
    tray.activated.connect(_on_activated)
    tray.show()

    win.show()
    app.exec()
    return True


def run_tray(on_save: Callable[[dict], None], stop_event: threading.Event) -> bool:
    """Block the calling thread on the Qt event loop.

    Returns False immediately if PySide6 can't be imported or no system tray
    is available. The caller should then fall back to headless poll-only mode.

    on_save is invoked after the user saves new settings. stop_event is set
    when the user picks Quit — the worker thread should monitor it and exit
    cleanly.
    """
    try:
        import PySide6  # noqa: F401
    except ImportError as e:
        print(f"[tray] PySide6 not available ({e}). "
              "Install with: pip install PySide6", flush=True)
        return False

    try:
        return _run_qt(on_save, stop_event)
    except Exception as e:
        print(f"[tray] Qt failed to start: {e}", flush=True)
        return False
