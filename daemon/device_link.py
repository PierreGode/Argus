"""Daemon <-> Argus command channel.

Besides the periodic usage payload (one JSON line), the daemon and the device
exchange short command lines. Every command starts with '!' in both directions
and on both transports (USB serial lines / BLE: written to RX, notified on TX):

    daemon -> device                      device -> daemon
    !ver                                  !ver <build id>
    !pr {"id","t","d","p","to"}           !pr <id> allow|deny|skip
    !prx <id>                             (prompt withdrawn)
    !ota <size> <md5>                     !ota ready <block> | !ota err <why>
    <raw image bytes>                     !ota ack <received>   (USB only)
                                          !ota ok | !ota err <why>

Firmware that predates this ignores '!' lines (USB) or nacks them (BLE) without
touching the display, so `!ver` doubles as the capability probe: nothing else is
sent until the device has answered it.

`DeviceLink` is the thread-safe meeting point. The transport loop (which owns
the port / BLE client) calls attach/detach/pop_outgoing/feed_line/take_ota;
everything else (hook listener, tray, CLI) calls send/request_prompt/submit_ota.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


class OtaError(Exception):
    pass


class OtaJob:
    """One firmware image on its way to the device."""

    def __init__(self, data: bytes, label: str = ""):
        self.data = data
        self.size = len(data)
        self.md5 = hashlib.md5(data).hexdigest()
        self.label = label
        self.sent = 0
        self.ok = False
        self.error = ""
        self.done = threading.Event()

    @property
    def pct(self) -> int:
        return int(self.sent * 100 / self.size) if self.size else 0

    def finish(self, ok: bool, error: str = "") -> None:
        self.ok = ok
        self.error = error
        self.done.set()


class DeviceLink:
    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log or (lambda m: None)
        self._lock = threading.Lock()
        self._outbox: list[str] = []
        self._ota: OtaJob | None = None
        self._ota_running = False
        self._prompt_lock = threading.Lock()   # one prompt on the device at a time
        self._pending: tuple[str, threading.Event, list[str]] | None = None
        self.connected = False
        self.kind = ""
        self.fw_version: str | None = None

    # ---- transport side --------------------------------------------------

    def attach(self, kind: str) -> None:
        with self._lock:
            self.connected = True
            self.kind = kind
            self.fw_version = None
            self._outbox = ["!ver"]

    def detach(self) -> None:
        with self._lock:
            self.connected = False
            self.fw_version = None
            self._outbox = []
            pending = self._pending
        if pending:
            pending[1].set()      # release a hook waiting on a device that left

    def pop_outgoing(self) -> list[str]:
        with self._lock:
            out, self._outbox = self._outbox, []
        return out

    def feed_line(self, line: str) -> None:
        """One '!' line from the device."""
        parts = line.split()
        if not parts:
            return
        if parts[0] == "!ver" and len(parts) >= 2:
            first = self.fw_version is None
            self.fw_version = parts[1]
            if first:
                self._log(f"Device firmware: {parts[1]}")
        elif parts[0] == "!pr" and len(parts) >= 3:
            with self._lock:
                pending = self._pending
            if pending and pending[0] == parts[1]:
                pending[2][0] = parts[2]
                pending[1].set()

    def take_ota(self) -> OtaJob | None:
        with self._lock:
            job, self._ota = self._ota, None
            if job:
                self._ota_running = True
        return job

    def ota_finished(self) -> None:
        with self._lock:
            self._ota_running = False

    # ---- client side -----------------------------------------------------

    @property
    def ready(self) -> bool:
        """Connected to firmware that understands commands, and not mid-update."""
        return self.connected and self.fw_version is not None and not self._ota_running

    def send(self, line: str) -> None:
        with self._lock:
            if self.connected:
                self._outbox.append(line)

    def submit_ota(self, job: OtaJob) -> None:
        with self._lock:
            if not (self.connected and self.fw_version is not None):
                raise OtaError("device not connected, or its firmware predates "
                               "updates over the daemon link (flash it once by cable)")
            if self._ota is not None or self._ota_running:
                raise OtaError("a firmware update is already in progress")
            self._ota = job

    def request_prompt(self, info: dict, timeout: float,
                       cancelled: Callable[[], bool] = lambda: False) -> str:
        """Show a permission prompt on the device and wait for the answer.
        Returns "allow", "deny", or "" (no decision — skipped, timed out, no
        device, or the asker went away), in which case Claude Code falls back to
        its normal terminal prompt."""
        deadline = time.time() + timeout
        if not self.ready:
            return ""
        # Another session's prompt may be on screen; queue behind it, but never
        # past our own deadline.
        if not self._prompt_lock.acquire(timeout=max(0.0, timeout - 3)):
            return ""
        try:
            remaining = deadline - time.time()
            if remaining < 3 or not self.ready or cancelled():
                return ""
            pid = uuid.uuid4().hex[:8]
            ev = threading.Event()
            result = [""]
            with self._lock:
                self._pending = (pid, ev, result)
            msg = dict(info)
            msg["id"] = pid
            msg["to"] = int(remaining)
            self.send("!pr " + json.dumps(msg, separators=(",", ":")))
            while time.time() < deadline:
                if ev.wait(0.25):
                    break
                if cancelled() or not self.connected:
                    break
            if result[0] not in ("allow", "deny", "skip"):
                self.send("!prx " + pid)      # take it off the screen
            return result[0] if result[0] in ("allow", "deny") else ""
        finally:
            with self._lock:
                self._pending = None
            self._prompt_lock.release()


# ---- permission-prompt summary ---------------------------------------------

# The device's detail box holds roughly 190 characters. Stay well under it so
# the "(+N more)" marker is always on screen instead of clipped off the end.
_DETAIL_MAX = 150


def _clean(text: str, limit: int) -> str:
    """Device fonts are ASCII; flatten whitespace and mark anything cut off so a
    long command can't hide its tail behind the edge of the screen."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = "".join(c if 32 <= ord(c) < 127 else "?" for c in text)
    if len(text) > limit:
        cut = len(text) - limit
        text = text[:limit] + f" ...(+{cut} more)"
    return text


def prompt_info(evt: dict) -> dict:
    """Reduce a PermissionRequest hook event to what fits on the device:
    t = tool name, d = what it wants to do, p = project (cwd basename)."""
    tool = str(evt.get("tool_name") or "Tool")
    ti = evt.get("tool_input")
    if not isinstance(ti, dict):
        ti = {}
    if tool == "Bash" and ti.get("command"):
        detail = ti["command"]
    elif ti.get("file_path"):
        detail = ti["file_path"]
    elif ti.get("notebook_path"):
        detail = ti["notebook_path"]
    elif ti.get("url"):
        detail = ti["url"]
    elif ti.get("query"):
        detail = ti["query"]
    elif ti:
        detail = json.dumps(ti, separators=(",", ":"), ensure_ascii=True)
    else:
        detail = "(no details)"
    cwd = evt.get("cwd") or ""
    return {
        "t": _clean(tool, 36),
        "d": _clean(detail, _DETAIL_MAX),
        "p": _clean(Path(cwd).name if cwd else "", 28),
    }


# ---- firmware transfer -------------------------------------------------------

def _ota_reason(line: str) -> str:
    """The reason from an '!ota err <reason>' line ('' for anything else)."""
    return line[len("!ota err"):].strip() if line.startswith("!ota err") else ""


def _progress_logger(job: OtaJob, log: Callable[[str], None]):
    state = {"last": -10}

    def tick():
        if job.pct >= state["last"] + 10:
            state["last"] = job.pct - job.pct % 10
            log(f"Firmware update: {job.pct}%")
    return tick


def ota_over_serial(ser, job: OtaJob, log: Callable[[str], None]) -> None:
    """Blocking transfer on an open pyserial port. Raises OtaError on failure;
    on success the device reboots (the caller should reconnect)."""
    buf = bytearray()

    def expect(timeout: float) -> str:
        end = time.time() + timeout
        while time.time() < end:
            while b"\n" in buf:
                raw, _, rest = bytes(buf).partition(b"\n")
                buf[:] = rest
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("!ota"):
                    return line
            chunk = ser.read(ser.in_waiting or 1)   # port timeout bounds this
            if chunk:
                buf.extend(chunk)
        raise OtaError("device stopped responding")

    ser.reset_input_buffer()
    ser.write(f"!ota {job.size} {job.md5}\n".encode())
    ser.flush()
    line = expect(15)
    if not line.startswith("!ota ready"):
        raise OtaError(_ota_reason(line) or "device refused the update")
    try:
        block = max(64, int(line.split()[2]))
    except (IndexError, ValueError):
        block = 512

    tick = _progress_logger(job, log)
    for off in range(0, job.size, block):
        part = job.data[off:off + block]
        ser.write(part)
        ser.flush()
        job.sent = off + len(part)
        tick()
        if job.sent < job.size:
            line = expect(20)
            if not line.startswith("!ota ack"):
                raise OtaError(_ota_reason(line) or "transfer rejected")
    line = expect(60)
    if not line.startswith("!ota ok"):
        raise OtaError(_ota_reason(line) or "verification failed")


async def ota_over_ble(client, rx_uuid: str, job: OtaJob, lines, chunk: int,
                       log: Callable[[str], None]) -> None:
    """Async transfer over a connected bleak client. `lines` is an asyncio.Queue
    fed with the device's '!ota ...' notifications. Every chunk is a
    write-with-response, which the firmware only acknowledges once the chunk is
    flashed — that is the flow control."""
    import asyncio

    async def expect(timeout: float) -> str:
        try:
            return await asyncio.wait_for(lines.get(), timeout)
        except asyncio.TimeoutError:
            raise OtaError("device stopped responding")

    while not lines.empty():
        lines.get_nowait()
    await client.write_gatt_char(
        rx_uuid, f"!ota {job.size} {job.md5}\n".encode(), response=True)
    line = await expect(15)
    if not line.startswith("!ota ready"):
        raise OtaError(_ota_reason(line) or "device refused the update")

    tick = _progress_logger(job, log)
    for off in range(0, job.size, chunk):
        part = job.data[off:off + chunk]
        await client.write_gatt_char(rx_uuid, part, response=True)
        job.sent = off + len(part)
        tick()
        if not lines.empty():
            line = lines.get_nowait()
            if line.startswith("!ota ok"):
                return
            raise OtaError(_ota_reason(line) or "transfer rejected")
    line = await expect(60)
    if not line.startswith("!ota ok"):
        raise OtaError(_ota_reason(line) or "verification failed")
