#!/usr/bin/env python3
"""Claude Code hook -> Argus daemon bridge (Klar-lampan + Argus prompts).

Claude Code runs this for the UserPromptSubmit / PreToolUse / Notification /
Stop / SessionEnd events. It reads the hook's JSON from stdin and forwards it as
one line to the daemon's loopback listener (127.0.0.1:<port>), then exits.

For the optional PermissionRequest event it additionally waits for the daemon's
answer: the daemon shows the request on the Argus device with Allow / Deny /
Skip buttons, and a tap there is returned to Claude Code as the decision. No
answer (skipped, timed out, no device, daemon stopped) prints nothing, and
Claude Code asks in the terminal as usual.

Design rules:
  - It NEVER writes to the device/COM port. The daemon owns the port; hooks only
    talk to the daemon.
  - It NEVER fails a hook: a missing/stopped daemon, a closed port, or bad input
    all exit 0 quietly. Only PermissionRequest waits, and only while a daemon
    with a connected device is holding the question (bounded by the daemon's
    `prompt_timeout`).

Install: copy this file next to your Claude settings (e.g. ~/.claude/klar_hook.py)
and point the five hooks at `python ~/.claude/klar_hook.py` (see hooks/
settings.sample.json). Override the port with the KLAR_HOOK_PORT env var if you
changed it in the daemon config.
"""

import json
import os
import socket
import sys

DEFAULT_PORT = 47600

# Upper bound on waiting for a PermissionRequest answer. The daemon answers much
# sooner (its `prompt_timeout`, default 30 s); this only guards a hung daemon.
# Keep the hook's own "timeout" in settings.json above it.
PERMISSION_WAIT_S = 125


def _permission_output(decision: str) -> str:
    """Hook stdout for a PermissionRequest decision ("" for none)."""
    if decision == "allow":
        d = {"behavior": "allow"}
    elif decision == "deny":
        d = {"behavior": "deny", "message": "Denied on the Argus device."}
    else:
        return ""
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PermissionRequest", "decision": d}})


def _read_stdin() -> str:
    """Hook JSON from stdin. The packaged Windows app is a windowed program, so
    Python's sys.stdin/stdout can be None even though Claude Code handed us
    pipes — fall back to the raw Win32 standard handles in that case."""
    if sys.stdin is not None:
        try:
            return sys.stdin.read()
        except Exception:
            pass
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = wintypes.HANDLE
        h = k32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        data = b""
        buf = ctypes.create_string_buffer(65536)
        n = wintypes.DWORD(0)
        while k32.ReadFile(wintypes.HANDLE(h), buf, len(buf), ctypes.byref(n), None) and n.value:
            data += buf.raw[:n.value]
        return data.decode("utf-8", "replace")
    except Exception:
        return ""


def _write_stdout(text: str) -> None:
    if sys.stdout is not None:
        try:
            print(text, flush=True)
            return
        except Exception:
            pass
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = wintypes.HANDLE
        h = k32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        data = (text + "\n").encode("utf-8")
        n = wintypes.DWORD(0)
        k32.WriteFile(wintypes.HANDLE(h), data, len(data), ctypes.byref(n), None)
    except Exception:
        pass


def main() -> int:
    raw = _read_stdin()
    if not raw.strip():
        return 0
    # Validate + compact to a single line (the listener reads one line per event).
    wants_answer = False
    try:
        obj = json.loads(raw)
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        wants_answer = (isinstance(obj, dict)
                        and obj.get("hook_event_name") == "PermissionRequest")
    except Exception:
        # Not JSON we understand — forward the first physical line as-is.
        line = raw.splitlines()[0] + "\n"

    try:
        port = int(os.environ.get("KLAR_HOOK_PORT", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3) as s:
            s.sendall(line.encode("utf-8"))
            if wants_answer:
                s.settimeout(PERMISSION_WAIT_S)
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                if buf.strip():
                    out = _permission_output(
                        str(json.loads(buf.decode("utf-8")).get("decision") or ""))
                    if out:
                        _write_stdout(out)
    except Exception:
        # Daemon not running / lamp not in use — that's fine, do nothing.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
