#!/usr/bin/env python3
"""Claude Code hook -> Argus daemon bridge for the Klar-lampan.

Claude Code runs this for the UserPromptSubmit / PreToolUse / Notification /
Stop / SessionEnd events. It reads the hook's JSON from stdin and forwards it as
one line to the daemon's loopback listener (127.0.0.1:<port>), then exits.

Design rules:
  - It NEVER writes to the device/COM port. The daemon owns the port; hooks only
    talk to the daemon.
  - It NEVER blocks or fails a hook: a missing/stopped daemon, a closed port, or
    bad input all exit 0 quietly, so Claude Code is never slowed or interrupted.

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


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw.strip():
        return 0
    # Validate + compact to a single line (the listener reads one line per event).
    try:
        obj = json.loads(raw)
        line = json.dumps(obj, separators=(",", ":")) + "\n"
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
    except Exception:
        # Daemon not running / lamp not in use — that's fine, do nothing.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
