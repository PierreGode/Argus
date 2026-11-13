"""Install the Argus hook into Claude Code's user settings.

Claude Code only shows permission prompts on the device if a `PermissionRequest`
hook forwards them to the daemon. This writes that hook into
`~/.claude/settings.json` so nobody has to edit JSON by hand:

  - packaged app:  the hook command is the app itself, `"<exe>" --hook`
    (no Python needed on the machine);
  - from source:   `"<python>" "<this folder>/klar_hook.py"`.

Only the PermissionRequest event is installed. It fires once per permission
prompt, so starting the app for it costs nothing noticeable; the Klar-lampan's
per-tool-call events stay a manual opt-in (hooks/settings.sample.json).

The existing file is backed up to `settings.json.argus-bak` first, other hooks
are left alone, and re-running replaces our own entry instead of adding a
second one.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

EVENT = "PermissionRequest"
HOOK_TIMEOUT_S = 130
_MARKERS = ("klar_hook.py", "--hook")     # how we recognise our own entry


class HookInstallError(Exception):
    pass


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hook_command() -> str:
    """The command Claude Code should run. Forward slashes + quotes, so it
    survives both cmd.exe and a POSIX shell (Git Bash eats backslashes)."""
    def q(p) -> str:
        return '"' + str(p).replace("\\", "/") + '"'
    if getattr(sys, "frozen", False):
        return f"{q(sys.executable)} --hook"
    return f"{q(sys.executable)} {q(Path(__file__).resolve().parent / 'klar_hook.py')}"


def _is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks") or []:
        cmd = str((h or {}).get("command") or "") if isinstance(h, dict) else ""
        if any(m in cmd for m in _MARKERS):
            return True
    return False


def is_installed(path: Path | None = None) -> bool:
    path = path or settings_path()
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return any(_is_ours(e) for e in (d.get("hooks") or {}).get(EVENT) or [])
    except (OSError, ValueError, AttributeError):
        return False


def install(path: Path | None = None) -> str:
    """Add / refresh the hook. Returns the command that was written."""
    path = path or settings_path()
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as e:
            raise HookInstallError(f"can't read {path}: {e} - not touching it")
        if not isinstance(data, dict):
            raise HookInstallError(f"{path} is not a JSON object - not touching it")
        try:
            shutil.copy2(path, path.with_name(path.name + ".argus-bak"))
        except OSError as e:
            raise HookInstallError(f"can't back up {path}: {e}")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HookInstallError(f"'hooks' in {path} has an unexpected shape")
    entries = hooks.get(EVENT)
    if not isinstance(entries, list):
        entries = []
    cmd = hook_command()
    entries = [e for e in entries if not _is_ours(e)]
    entries.append({"matcher": "*", "hooks": [
        {"type": "command", "command": cmd, "timeout": HOOK_TIMEOUT_S}]})
    hooks[EVENT] = entries
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".argus-tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        raise HookInstallError(f"can't write {path}: {e}")
    return cmd
