# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Argus daemon.

Produces a single-file binary for the host platform:
  - Windows: windowed (no console), embedded mascot .ico, full VSVersionInfo
             resource so Explorer/Properties -> Details shows ProductName,
             Company, and version.
  - macOS:   windowed.
  - Linux:   console (keeps useful stdout logs; no GUI-bundling distinction).

Build (from the repo root or the daemon/ dir):
    pyinstaller daemon/argus-daemon.spec --noconfirm

Bundles assets/argus.ico and assets/img/happy.png so the tray/window icon
resolves at runtime via tray_ui.resource_path() (which checks sys._MEIPASS).
"""
import os
import sys

# SPECPATH is injected by PyInstaller and points at this file's directory
# (daemon/). The repo root is one level up.
DAEMON_DIR = SPECPATH
REPO_ROOT = os.path.dirname(DAEMON_DIR)
ASSETS = os.path.join(REPO_ROOT, "assets")
ICON = os.path.join(ASSETS, "argus.ico")

# Read the single-source version for the embedded Windows resource.
sys.path.insert(0, DAEMON_DIR)
try:
    from version import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "0.0.0"

is_windows = sys.platform.startswith("win")
is_linux = sys.platform.startswith("linux")

datas = [
    (ICON, "assets"),
    (os.path.join(ASSETS, "img", "happy.png"), os.path.join("assets", "img")),
]

# ---- Windows version resource (Properties -> Details) ----------------------
version_info = None
if is_windows:
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
        StringStruct, VarFileInfo, VarStruct,
    )

    parts = [int(p) for p in (APP_VERSION.split(".") + ["0", "0", "0", "0"])[:4]]
    vtuple = tuple(parts)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=vtuple, prodvers=vtuple,
            mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "PierreGode"),
                StringStruct("FileDescription",
                             "Argus desktop daemon — Claude Code usage monitor"),
                StringStruct("FileVersion", APP_VERSION),
                StringStruct("InternalName", "argus-daemon"),
                StringStruct("LegalCopyright", "© 2026 PierreGode"),
                StringStruct("OriginalFilename", "argus-daemon.exe"),
                StringStruct("ProductName", "Argus"),
                StringStruct("ProductVersion", APP_VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

a = Analysis(
    [os.path.join(DAEMON_DIR, "argus-daemon.py")],
    pathex=[DAEMON_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "claude_logs", "github_stats", "copilot_stats",
        "tray_ui", "token_crypt", "version",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="argus-daemon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=is_linux,            # console on Linux, windowed elsewhere
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if is_windows else None,
    version=version_info,        # None on non-Windows
)
