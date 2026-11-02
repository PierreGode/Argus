"""The Klar-lampan (LilyGO T-Dongle S3) as an output.

USB-CDC loop: auto-detect the lamp (handshake-classified by `usb_arbiter`),
confirm it, then push the focused session's state whenever it changes (plus a
periodic heartbeat so the lamp knows the daemon is alive). Reads the lamp's
button events back. Reconnects on its own, so unplug/replug needs no restart and
never disturbs the Argus output.

Framing mirrors the Argus serial path: newline-terminated JSON, written in small
chunks so a pre-issue-19 firmware RX ring can't overflow. See PROTOCOL.md.
"""

from __future__ import annotations

import json
import time

import tray_ui
import usb_arbiter
from session_state import MODE_WORK, MODE_WAIT, MODE_DONE, MODE_REST
from .base import Output, RunContext

BAUD = 115200
SERIAL_CHUNK = 48
INTER_CHUNK_DELAY = 0.02
TICK = 0.1               # inner-loop cadence (also stop/disconnect responsiveness)
HEARTBEAT = 10.0         # resend unchanged state at least this often
RECONNECT_DELAY = 2.0
HANDSHAKE_TIMEOUT = 1.0


def _open_serial(port: str, baud: int):
    """Open DTR/RTS-low so the ESP32-S3 native USB doesn't auto-reset on open
    (same trick run_serial uses for Argus)."""
    import serial
    ser = serial.Serial(None, baud, timeout=0, dsrdtr=False, rtscts=False)
    ser.port = port
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def _demo_snapshot() -> dict:
    """Synthesize a rotating session so `--demo` shows the lamp without hooks."""
    phase = int(time.time() / 4) % 4
    mode = [MODE_WORK, MODE_WAIT, MODE_DONE, MODE_REST][phase]
    return {
        "sid": "demo",
        "repo": "Argus",
        "mode": mode,
        "tok": 1200 + phase * 600,
        "ctx": 20 + phase * 20,
        "cost": round(0.42 + phase * 0.37, 3),
        "n": 2,
        "fan": 1 if mode == MODE_DONE else 0,
    }


class KlarLampOutput(Output):
    name = "klar"

    def enabled(self, cfg: dict) -> bool:
        return bool(cfg.get("klar_lamp_enabled", True))

    def run(self, ctx: RunContext) -> None:
        log = ctx.log
        stop = ctx.stop_event
        demo = bool(getattr(ctx.args, "demo", False))

        while not stop.is_set():
            if not self.enabled(tray_ui.load_config()):
                self._wait(stop, RECONNECT_DELAY)
                continue

            port = usb_arbiter.find_klar_port()
            if not port:
                tray_ui.set_status("warn", "Klar — no lamp on USB")
                self._wait(stop, RECONNECT_DELAY)
                continue

            try:
                ser = _open_serial(port, BAUD)
            except Exception as e:
                log(f"Klar: open {port} failed: {e}")
                self._wait(stop, RECONNECT_DELAY)
                continue

            usb_arbiter.claim(port)
            log(f"Klar: connected on {port}")
            tray_ui.set_status("ok", f"Klar — connected on {port}")
            try:
                self._handshake(ser, log)
                self._serve(ser, ctx, demo)
            except Exception as e:
                log(f"Klar: link error: {e}")
            finally:
                usb_arbiter.release(port)
                try:
                    ser.close()
                except Exception:
                    pass

            if stop.is_set():
                return
            tray_ui.set_status("warn", "Klar — link dropped, reconnecting")
            self._wait(stop, RECONNECT_DELAY)

    # ---- connection lifecycle -------------------------------------------

    def _handshake(self, ser, log) -> None:
        try:
            ser.write(usb_arbiter.ID_QUERY)
            ser.flush()
        except Exception:
            return
        deadline = time.time() + HANDSHAKE_TIMEOUT
        buf = b""
        while time.time() < deadline:
            try:
                data = ser.read(64)
            except Exception:
                return
            if data:
                buf += data
                if b"KLAR" in buf:
                    log(f"Klar: handshake {buf.decode('utf-8', 'replace').strip()}")
                    return
            else:
                time.sleep(0.02)

    def _serve(self, ser, ctx: RunContext, demo: bool) -> None:
        stop = ctx.stop_event
        last_key = None
        last_push = 0.0
        rxbuf = b""
        while not stop.is_set():
            rxbuf = self._drain_input(ser, rxbuf, ctx)

            snap = _demo_snapshot() if demo else ctx.store.snapshot()
            key = tuple(snap[k] for k in ("sid", "repo", "mode", "tok", "ctx", "cost", "n"))
            now = time.time()
            if key != last_key or snap.get("fan") or (now - last_push) >= HEARTBEAT:
                self._write_line(ser, json.dumps(snap, separators=(",", ":")))
                last_key = key
                last_push = now

            time.sleep(TICK)

    # ---- io helpers ------------------------------------------------------

    def _write_line(self, ser, line: str) -> None:
        data = (line + "\n").encode("utf-8")
        for i in range(0, len(data), SERIAL_CHUNK):
            ser.write(data[i:i + SERIAL_CHUNK])
            ser.flush()
            time.sleep(INTER_CHUNK_DELAY)

    def _drain_input(self, ser, rxbuf: bytes, ctx: RunContext) -> bytes:
        """Read any pending bytes and dispatch complete lines (acks, buttons).
        Returns the trailing partial line."""
        try:
            n = ser.in_waiting
        except Exception:
            raise  # treated as a disconnect by the caller
        if n:
            rxbuf += ser.read(n)
        while b"\n" in rxbuf:
            line, rxbuf = rxbuf.split(b"\n", 1)
            self._handle_line(line.decode("utf-8", "replace").strip(), ctx)
        # Guard against an unterminated flood.
        if len(rxbuf) > 512:
            rxbuf = rxbuf[-512:]
        return rxbuf

    def _handle_line(self, line: str, ctx: RunContext) -> None:
        if not line:
            return
        if line == "BTN:NEXT":
            ctx.store.focus_next()
            ctx.log("Klar: button -> next session")
        elif line == "BTN:ACK":
            ctx.store.ack_focused()
            ctx.log("Klar: button -> ack done")
        elif line.startswith("KLAR"):
            pass  # heartbeat/handshake echo
        elif line in ("OK", "ERR"):
            pass  # per-line ack
        else:
            ctx.log(f"Klar: {line}")

    def _wait(self, stop, seconds: float) -> None:
        """Interruptible sleep — returns early when stop fires."""
        stop.wait(seconds)
