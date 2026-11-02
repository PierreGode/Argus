# Klar-lampan protocol

The **Klar-lampan** ("done lamp") is a second, independent daemon **output** — a
LilyGO T-Dongle S3 that shows the live state of each Claude Code **session** on
this machine (working / waiting-for-me / done / resting), with a mascot and an
RGB LED. It is separate from the Argus device: Argus shows account-wide API
usage; the lamp shows per-session status. Either, both, or neither can be
connected — the daemon runs regardless.

This document is the contract between the daemon and the lamp firmware, and
between Claude Code hooks and the daemon.

```
Claude Code hooks ──(loopback TCP, JSON line)──► daemon ──(USB CDC, JSON line)──► lamp
       ▲                                                                            │
       └──────────────────── button events (BTN:*) ◄───────────────────────────────┘
```

Hooks **never** touch the USB/COM port — the daemon owns it. Hooks only talk to
the daemon.

---

## 1. Hooks → daemon (loopback TCP)

The daemon listens on `127.0.0.1:<klar_hook_port>` (default **47600**, set in the
daemon config as `klar_hook_port`). Each hook invocation opens a connection,
sends **one line** — the raw JSON Claude Code passes the hook on stdin — and
closes. One event per connection.

`klar_hook.py` (shipped in `daemon/`) is the bridge. It reads stdin, forwards the
line, and exits 0 on any error, so a stopped daemon never blocks or fails a hook.
Override the port with the `KLAR_HOOK_PORT` env var.

### Hook event → mode

| Claude Code hook event | Lamp mode | LED |
|---|---|---|
| `UserPromptSubmit` | `work` | blue |
| `PreToolUse` (and `PostToolUse`) | `work` (refresh activity) | blue |
| `Notification` | `wait` — needs me | **yellow (most eye-catching)** |
| `Stop` | `done` (fires one-shot fanfare) | green |
| `SessionEnd` | `rest` | off |
| *(idle timeout / long-press ack)* | `done`/`work` → `rest` | off |

The daemon keys state by `session_id`, reads `cwd` for the repo name, and reads
the tail of `transcript_path` (same `.jsonl` format `claude_logs.py` parses) for
last-response tokens, cumulative session cost, and a context-fill estimate.

### Minimal `settings.json`

One command handles all five events — Claude Code puts `hook_event_name` in the
stdin JSON, so the daemon routes it. See `hooks/settings.sample.json`.

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python ~/.claude/klar_hook.py" }] }],
    "PreToolUse":       [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python ~/.claude/klar_hook.py" }] }],
    "Notification":     [{ "hooks": [{ "type": "command", "command": "python ~/.claude/klar_hook.py" }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "python ~/.claude/klar_hook.py" }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "python ~/.claude/klar_hook.py" }] }]
  }
}
```

Copy `daemon/klar_hook.py` to `~/.claude/klar_hook.py` (or point the command at
its path). On Windows use `python` / the full interpreter path as appropriate.

---

## 2. Daemon ↔ lamp (USB CDC serial)

- **115200 baud**, 8N1, native USB-Serial (the T-Dongle enumerates as a CDC port;
  no driver needed).
- **Line framed:** every message ends with `\n`.
- **Chunked writes:** the daemon writes in ≤48-byte chunks with a short pause, so a
  small firmware RX ring can't overflow mid-line (mirrors the Argus serial path).
- The port is opened with **DTR/RTS held low** so the ESP32-S3 doesn't auto-reset
  on connect.

### 2a. Handshake / discovery

Both the lamp and an Argus-on-USB are ESP32-S3 boards (USB VID `0x303A`), so the
daemon disambiguates by handshake:

| Direction | Line | Meaning |
|---|---|---|
| daemon → device | `ID?` | "who are you?" |
| lamp → daemon | `KLAR v1` | "I'm a Klar-lampan, protocol v1" |

An Argus device ignores `ID?` (its parser only acts on `screenshot` or `{…}`), so
silence / any non-`KLAR` reply ⇒ not a lamp. The daemon caches this per port and
never re-probes a port an output already owns.

### 2b. Daemon → lamp: state push

One compact JSON object per line. Pushed when the focused session's state
changes, and at least every ~10 s as a heartbeat.

```json
{"sid":"ab12","repo":"Argus","mode":"wait","tok":1840,"ctx":42,"cost":1.73,"n":2,"fan":0}
```

| Key | Type | Meaning |
|---|---|---|
| `sid` | string | session id, truncated to 8 chars (display/debug) |
| `repo` | string | repo name (basename of the session's `cwd`), ≤24 chars |
| `mode` | string | `work` \| `wait` \| `done` \| `rest` |
| `tok` | int | total tokens in the **latest** assistant response |
| `ctx` | int | context fill, `0..100` (% of a 200k window) |
| `cost` | float | cumulative session cost, USD |
| `n` | int | number of sessions currently working or waiting |
| `fan` | int | `1` on the single push where the session just entered `done` (play the fanfare), else `0` |

The firmware **must ignore unknown keys** so the two sides can version
independently (same rule as the Argus payload). All keys are optional on the
wire; treat a missing key as "unchanged / zero".

### 2c. Lamp → daemon

| Line | Meaning |
|---|---|
| `KLAR v1` | handshake reply |
| `OK` / `ERR` | optional per-line ack of a state push |
| `BTN:NEXT` | **short press** — focus the next session |
| `BTN:ACK` | **long press** — acknowledge the focused `done` session to `rest` |

---

## 3. Versioning

The handshake carries a protocol version (`KLAR v1`). Within a major version,
both sides add keys/lines additively and ignore anything they don't recognize.
Bump the version only for a breaking change to the framing or existing semantics.
