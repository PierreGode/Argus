"""Per-session Claude Code status, fed by Claude Code hooks.

This module is the data model behind the "Klar-lampan" output. The Argus device
shows *account-wide* usage (polled from the Anthropic API); the lamp instead
shows the live state of each Claude Code **session** on this machine, learned
from hooks:

    UserPromptSubmit / PreToolUse  -> working  (blue)
    Notification                   -> waiting  (yellow, "needs me")
    Stop                           -> done     (green, fanfare)
    SessionEnd / idle / ack        -> resting  (off)

Hooks are short-lived separate processes. They never touch the device — they
send one JSON line to a loopback TCP server owned by the daemon (see
`start_hook_listener`), which calls `SessionStore.handle_event`. The daemon's
output threads read `SessionStore.snapshot()` and push it to the lamp.

Token / cost / context numbers come from the session's `transcript_path`
(the same `.jsonl` format `claude_logs.py` already parses), read incrementally
so a long session isn't re-scanned on every tool call.
"""

from __future__ import annotations

import json
import os
import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Reuse the Argus cost model rather than reimplementing it.
from claude_logs import classify_model, _line_cost

# --- Modes (wire values; the firmware switches on these) --------------------
MODE_WORK = "work"
MODE_WAIT = "wait"
MODE_DONE = "done"
MODE_REST = "rest"

# Priority for auto-focus: a session that needs me wins, then one that's
# actively working, then a freshly-finished one, then idle.
_MODE_PRIORITY = {MODE_WAIT: 3, MODE_WORK: 2, MODE_DONE: 1, MODE_REST: 0}

# Timeouts (seconds).
WORK_IDLE_TO_REST = 900    # a work/wait session with no events for this long -> rest (abandoned)
DONE_LINGER       = 600    # a done session auto-rests after this long if not acked
SESSION_FORGET    = 3600   # drop a resting session entirely after this long with no events

# Nominal context window for the context-fill bar. Tokens in the latest
# assistant message (input + cache) approximate how full the window is.
CONTEXT_WINDOW_TOKENS = 200_000

# Default loopback port the hooks talk to. Overridable via config / env.
DEFAULT_HOOK_PORT = 47600


def _hook_event_to_mode(event: str) -> str | None:
    """Map a Claude Code hook event name to a mode, or None to leave as-is."""
    if event in ("UserPromptSubmit",):
        return MODE_WORK
    if event in ("PreToolUse", "PostToolUse"):
        return MODE_WORK
    if event in ("Notification", "PermissionRequest"):
        return MODE_WAIT
    if event in ("Stop",):
        return MODE_DONE
    if event in ("SessionEnd",):
        return MODE_REST
    return None


@dataclass
class _Session:
    sid: str
    repo: str = ""
    mode: str = MODE_WORK
    tok: int = 0            # tokens in the latest assistant response
    ctx: int = 0           # context fill, 0..100
    cost: float = 0.0      # cumulative session cost, USD
    first_seen: float = field(default_factory=time.time)
    last_event: float = field(default_factory=time.time)
    mode_since: float = field(default_factory=time.time)
    fanfare_pending: bool = False   # consumed once by snapshot() on entering done
    # Incremental transcript reader state:
    _tx_offset: int = 0
    _tx_path: str = ""

    def to_wire(self) -> dict:
        return {
            "sid": self.sid[:8],
            "repo": self.repo[:24],
            "mode": self.mode,
            "tok": int(self.tok),
            "ctx": int(self.ctx),
            "cost": round(self.cost, 3),
        }


class SessionStore:
    """Thread-safe store of per-session state. Written by the hook listener
    thread, read by the output threads. A monotonically increasing `version`
    plus a Condition lets an output block until something changes (so the lamp
    pushes on change, not on a busy-loop)."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log or (lambda m: None)
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._sessions: dict[str, _Session] = {}
        self._order: list[str] = []        # stable insertion order for button cycling
        self._manual_sid: str | None = None  # button-selected focus; None = auto
        self._version = 0

    # ---- ingest ----------------------------------------------------------

    def handle_event(self, evt: dict) -> None:
        """Apply one hook event. `evt` is the raw JSON a Claude Code hook emits
        on stdin: has at least `hook_event_name`, `session_id`; usually also
        `cwd` and `transcript_path`."""
        event = evt.get("hook_event_name") or evt.get("hook_event") or ""
        sid = evt.get("session_id") or evt.get("sessionId") or ""
        if not sid:
            return
        now = time.time()
        with self._cond:
            s = self._sessions.get(sid)
            if s is None:
                s = _Session(sid=sid)
                self._sessions[sid] = s
                self._order.append(sid)
            s.last_event = now

            cwd = evt.get("cwd") or evt.get("workspace") or ""
            if cwd:
                s.repo = Path(cwd).name

            tx = evt.get("transcript_path") or ""
            if tx:
                self._ingest_transcript(s, tx)

            new_mode = _hook_event_to_mode(event)
            if new_mode and new_mode != s.mode:
                s.mode = new_mode
                s.mode_since = now
                if new_mode == MODE_DONE:
                    s.fanfare_pending = True
                # A session that needs me should grab the lamp even if the user
                # had manually parked focus elsewhere.
                if new_mode == MODE_WAIT:
                    self._manual_sid = None

            self._bump()

    def _ingest_transcript(self, s: _Session, path: str) -> None:
        """Read new assistant lines from the session transcript and update
        tok/ctx/cost. First read covers the whole file (full session cost);
        later reads only cover bytes appended since."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if s._tx_path != path:
            # New/rotated transcript for this session — start over.
            s._tx_path = path
            s._tx_offset = 0
            s.cost = 0.0
        if size < s._tx_offset:        # truncated
            s._tx_offset = 0
            s.cost = 0.0
        if size == s._tx_offset:
            return
        try:
            with open(path, "rb") as f:
                f.seek(s._tx_offset)
                chunk = f.read()
                s._tx_offset = f.tell()
        except OSError:
            return
        for raw in chunk.splitlines():
            raw = raw.strip()
            if not raw or raw[:1] != b"{":
                continue
            try:
                evt = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if evt.get("type") != "assistant":
                continue
            msg = evt.get("message") or {}
            usage = msg.get("usage") or {}
            model = msg.get("model") or evt.get("model") or ""
            _family, pricing = classify_model(model)
            cost = evt.get("costUSD")
            if not isinstance(cost, (int, float)):
                cost = _line_cost(usage, pricing)
            s.cost += cost
            inp = usage.get("input_tokens", 0) or 0
            out = usage.get("output_tokens", 0) or 0
            cw = usage.get("cache_creation_input_tokens", 0) or 0
            cr = usage.get("cache_read_input_tokens", 0) or 0
            s.tok = inp + out + cw + cr
            ctx_tokens = inp + cr + cw
            s.ctx = max(0, min(100, round(100 * ctx_tokens / CONTEXT_WINDOW_TOKENS)))

    # ---- housekeeping ----------------------------------------------------

    def _sweep(self, now: float | None = None) -> None:
        """Expire idle/stale sessions. Caller holds the lock."""
        now = now or time.time()
        changed = False
        for sid in list(self._order):
            s = self._sessions.get(sid)
            if s is None:
                continue
            if s.mode in (MODE_WORK, MODE_WAIT) and now - s.last_event > WORK_IDLE_TO_REST:
                s.mode = MODE_REST
                s.mode_since = now
                changed = True
            elif s.mode == MODE_DONE and now - s.mode_since > DONE_LINGER:
                s.mode = MODE_REST
                s.mode_since = now
                changed = True
            if s.mode == MODE_REST and now - s.last_event > SESSION_FORGET:
                self._sessions.pop(sid, None)
                self._order.remove(sid)
                if self._manual_sid == sid:
                    self._manual_sid = None
                changed = True
        if changed:
            self._bump()

    def _active_ids(self) -> list[str]:
        """Sessions worth showing/counting — anything not forgotten. Caller
        holds the lock."""
        return [sid for sid in self._order if sid in self._sessions]

    def _pick_focus(self) -> _Session | None:
        """Caller holds the lock. Honor a live manual selection, else auto-pick
        the highest-priority (most recently active on ties) session."""
        ids = self._active_ids()
        if not ids:
            return None
        if self._manual_sid and self._manual_sid in self._sessions:
            return self._sessions[self._manual_sid]
        self._manual_sid = None
        return max(
            (self._sessions[sid] for sid in ids),
            key=lambda s: (_MODE_PRIORITY.get(s.mode, 0), s.last_event),
        )

    # ---- read side -------------------------------------------------------

    def snapshot(self) -> dict:
        """Return the wire payload for the currently focused session plus the
        active-session count. Consumes the one-shot fanfare flag."""
        with self._cond:
            self._sweep()
            focus = self._pick_focus()
            # n = sessions currently working or waiting (truly active).
            n = sum(
                1 for sid in self._active_ids()
                if self._sessions[sid].mode in (MODE_WORK, MODE_WAIT)
            )
            if focus is None:
                return {"sid": "", "repo": "", "mode": MODE_REST,
                        "tok": 0, "ctx": 0, "cost": 0.0, "n": 0, "fan": 0}
            wire = focus.to_wire()
            wire["n"] = n
            wire["fan"] = 1 if focus.fanfare_pending else 0
            focus.fanfare_pending = False
            return wire

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def wait_for_change(self, since: int, timeout: float) -> int:
        """Block up to `timeout` s until `version` advances past `since`.
        Returns the current version. Lets an output push on change instead of
        polling."""
        with self._cond:
            if self._version == since:
                self._cond.wait(timeout)
            return self._version

    def _bump(self) -> None:
        self._version += 1
        self._cond.notify_all()

    # ---- button actions from the lamp ------------------------------------

    def focus_next(self) -> None:
        """Short press: move the manual focus to the next session in order."""
        with self._cond:
            ids = self._active_ids()
            if not ids:
                return
            cur = self._manual_sid or (self._pick_focus().sid if self._pick_focus() else None)
            if cur in ids:
                nxt = ids[(ids.index(cur) + 1) % len(ids)]
            else:
                nxt = ids[0]
            self._manual_sid = nxt
            self._bump()

    def ack_focused(self) -> None:
        """Long press: acknowledge the focused 'done' session to rest."""
        with self._cond:
            focus = self._pick_focus()
            if focus is not None:
                focus.mode = MODE_REST
                focus.mode_since = time.time()
                focus.fanfare_pending = False
                self._manual_sid = None
                self._bump()


# --- Loopback TCP hook listener --------------------------------------------

def start_hook_listener(store: SessionStore, port: int = DEFAULT_HOOK_PORT,
                        log: Callable[[str], None] | None = None,
                        responder: Callable[[dict, Callable[[], bool]], str] | None = None):
    """Start a loopback TCP server that feeds `store`. Each connection delivers
    one hook event as a JSON line. Returns the server (call .shutdown() to stop)
    or None if the port couldn't be bound. Runs on a daemon thread.

    `responder`, when given, is asked to decide `PermissionRequest` events:
    it gets the event plus a `cancelled()` probe (true once the hook process
    has gone away, e.g. the user answered in the terminal) and returns
    "allow" / "deny" / "". The answer goes back on the same connection as one
    JSON line, `{"decision": "..."}`, which the hook script is waiting for."""
    log = log or (lambda m: None)

    class _Handler(socketserver.StreamRequestHandler):
        timeout = 2

        def handle(self):
            try:
                data = self.rfile.readline(65536)
            except OSError:
                return
            if not data:
                return
            try:
                evt = json.loads(data.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                return
            if isinstance(evt, dict):
                try:
                    store.handle_event(evt)
                except Exception as e:   # never let a bad event kill the listener
                    log(f"hook event error: {e}")
                if responder and evt.get("hook_event_name") == "PermissionRequest":
                    self._respond(evt)

        def _peer_gone(self) -> bool:
            # The hook sends nothing after its one line, so the socket only
            # turns readable when the hook process exits and closes it.
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable:
                    return self.connection.recv(1, socket.MSG_PEEK) == b""
            except (OSError, ValueError):
                return True
            return False

        def _respond(self, evt: dict) -> None:
            try:
                decision = responder(evt, self._peer_gone) or ""
            except Exception as e:
                log(f"permission responder error: {e}")
                decision = ""
            try:
                self.wfile.write(
                    (json.dumps({"decision": decision}) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                pass   # hook already gone — nothing to tell

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        srv = _Server(("127.0.0.1", port), _Handler)
    except OSError as e:
        log(f"hook listener could not bind 127.0.0.1:{port}: {e}")
        return None
    threading.Thread(target=srv.serve_forever, name="klar-hook-listener",
                     daemon=True).start()
    log(f"hook listener on 127.0.0.1:{port}")
    return srv
