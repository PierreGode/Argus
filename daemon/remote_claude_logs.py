"""Fold Claude Code usage from remote SSH hosts into the Today-screen stats.

The local "Today" numbers (claude_logs.py) only see sessions run on the
machine the daemon itself is on. Many people run Claude Code mostly on
remote Linux boxes over SSH — this module extends that view by running
claude_logs.py *on each configured host* and merging the raw counts back in,
rather than trying to sync log files:

    ssh -o BatchMode=yes <host> python3 - --raw < claude_logs.py

No rsync, no deployment step, no per-file sync state — the current daemon
build's copy of claude_logs.py is piped over stdin every time, so there's
nothing to install or keep updated on the remote hosts beyond python3.

Hosts are SSH config aliases (~/.ssh/config) the user already has working
key-based auth for — this never touches a password. BatchMode=yes makes a
host with no loaded key (or that needs a password) fail fast instead of
hanging the poll loop on a prompt nobody can answer.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from claude_logs import Aggregates, aggregate_from_dict, merge

# How long a single host gets to connect + run + return before this refresh
# cycle gives up on it. Generous because a cold SSH connection to a rarely-
# used box (DNS, TCP handshake, host-key check) can take a few seconds.
HOST_TIMEOUT = 12.0

# How often to actually SSH out. Fetching on every poll (as often as every
# 5-30s, per poll_interval) would mean an SSH round-trip to every host that
# often; this caches the merged result and only refreshes every
# REFRESH_INTERVAL seconds, same idea as the rate-limit cache in the daemon.
REFRESH_INTERVAL = 300.0

def _resolve_script_path() -> Path:
    """Locate claude_logs.py on disk so its bytes can be piped over SSH.

    Under PyInstaller, Python modules are compiled into the frozen bundle and
    have no source file to read at `__file__` — claude_logs.py has to be
    bundled separately as a data file (see argus-daemon.spec's `datas`) so it
    lands, as a real file, in the onefile extraction dir (sys._MEIPASS).
    Running from source, it's just the file next to this one."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "claude_logs.py"
    return Path(__file__).resolve().parent / "claude_logs.py"


_SCRIPT_PATH = _resolve_script_path()

_cache_lock = threading.Lock()
_cached_agg: Aggregates | None = None
_cached_at: float = 0.0

# On Windows, spawning a console program (ssh.exe) from a windowed/no-console
# app (the PyInstaller-built tray daemon) briefly flashes a console window per
# call unless we explicitly ask CreateProcess not to allocate one. No-op on
# other platforms — CREATE_NO_WINDOW only exists in the Windows subprocess API.
_NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _fetch_host(host: str, script_bytes: bytes, log: Callable[[str], None]) -> Aggregates | None:
    """Run claude_logs.py --raw on `host` over SSH. Returns None on any
    failure (unreachable, no key loaded, remote python3 missing, etc.) so
    the caller can tell "this host didn't answer" apart from "it answered
    with zero activity today"."""
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             host, "python3", "-", "--raw"],
            input=script_bytes,
            capture_output=True,
            timeout=HOST_TIMEOUT,
            creationflags=_NO_WINDOW_FLAGS,
        )
    except subprocess.TimeoutExpired:
        log(f"remote Claude logs: {host} timed out after {HOST_TIMEOUT:.0f}s")
        return None
    except OSError as e:
        log(f"remote Claude logs: {host} failed to launch ssh: {e}")
        return None

    if proc.returncode != 0:
        stderr_lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = stderr_lines[-1] if stderr_lines else f"exit {proc.returncode}"
        log(f"remote Claude logs: {host} failed — {detail}")
        return None

    try:
        return aggregate_from_dict(json.loads(proc.stdout))
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log(f"remote Claude logs: {host} returned unparseable output ({e})")
        return None


def fetch_all(
    hosts: list[str],
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[Aggregates, bool]:
    """SSH into every host in parallel and merge their raw Aggregates.

    Returns (merged, any_host_reachable) — the bool lets callers distinguish
    "every host is down, keep showing what we last had" from "hosts answered
    and nobody used Claude Code there today"."""
    merged = Aggregates()
    if not hosts:
        return merged, True

    script_bytes = _SCRIPT_PATH.read_bytes()
    any_ok = False
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futures = [pool.submit(_fetch_host, h, script_bytes, log) for h in hosts]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                any_ok = True
                merged = merge(merged, result)
    return merged, any_ok


def get_cached(
    hosts: list[str],
    now: float | None = None,
    refresh_interval: float = REFRESH_INTERVAL,
    log: Callable[[str], None] = lambda _msg: None,
) -> Aggregates:
    """Return the merged remote Aggregates, refreshing at most once every
    `refresh_interval` seconds so build_payload() doesn't SSH out on every
    poll tick. Returns an empty Aggregates immediately if `hosts` is empty.
    If a refresh finds every host unreachable, keeps serving the last
    successful result instead of zeroing the Today screen out."""
    global _cached_agg, _cached_at

    if not hosts:
        return Aggregates()

    now = now if now is not None else time.time()
    with _cache_lock:
        if _cached_agg is not None and now - _cached_at < refresh_interval:
            return _cached_agg

    fresh, any_ok = fetch_all(hosts, log)
    with _cache_lock:
        if any_ok or _cached_agg is None:
            _cached_agg = fresh
        _cached_at = now
        return _cached_agg
