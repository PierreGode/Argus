"""Tell the Klar-lamp apart from an Argus device on USB.

Both boards are ESP32-S3 and enumerate under Espressif's VID (0x303A), so a port
scan alone can't say which is which. This module opens each candidate port once,
sends the ``ID?`` handshake, and classifies it:

    reply starts with "KLAR"  -> the lamp      ("klar")
    anything else / silence   -> not the lamp  ("other")  (e.g. an Argus device)

The Argus firmware silently ignores any serial line that isn't ``screenshot`` or
a ``{...}`` JSON payload, so probing an Argus port with ``ID?`` is a no-op for it
(no reset, no output) — the port is opened with DTR/RTS held low, exactly like
``run_serial`` does, so the probe never triggers the ESP32-S3 auto-reset.

Classification is cached per device path and is sticky while the device stays
plugged in, so steady-state operation never re-opens a port an output already
owns. A port that disappears and comes back is re-probed.

Shared by the lamp output (``find_klar_port``) and the Argus USB discovery
(``is_klar`` — the one small change to Argus's ``find_serial_port``).
"""

from __future__ import annotations

import sys
import threading
import time

_ESP_VID = 0x303A
_ESP_PIDS = (0x1001, 0x4001)

ID_QUERY = b"ID?\n"
KLAR_REPLY_PREFIX = "KLAR"

_lock = threading.Lock()
_cache: dict[str, str] = {}      # device path -> "klar" | "other"
_owned: set[str] = set()         # ports an output currently holds open


def espressif_ports() -> list[str]:
    """Device paths of attached ESP32-S3-class USB serial ports."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    out: list[str] = []
    for p in list_ports.comports():
        if p.vid == _ESP_VID and p.pid in _ESP_PIDS:
            out.append(p.device)
            continue
        desc = ((p.description or "") + " " + (p.manufacturer or "")).lower()
        if "espressif" in desc or "usb jtag" in desc:
            out.append(p.device)
    return out



def open_no_reset(port: str, baud: int = 115200, timeout: float | None = 1):
    """Open an ESP32-S3 native-USB serial port without rebooting the board.

    The USB-Serial-JTAG resets the chip on an RTS-high / DTR-low combination,
    and which open sequence produces that edge differs by OS:

      - Windows: pyserial asserts DTR while opening, so the lines must be
        cleared *before* open() (construct closed, set dtr/rts False, open).
      - Linux: the kernel raises both lines on open; pre-clearing makes
        pyserial then drop DTR before RTS, which is exactly the reset
        combination — measured on a Pi: that sequence reboots the board on
        every open, a plain open never does. So on Linux just open normally and
        leave the lines alone.

    Every serial open in the daemon goes through here (Argus transport, lamp
    transport, the ID? probe) so none of them reboot a board another transport
    — e.g. Argus over BLE while it sits on USB power — is talking to.
    """
    import serial
    if sys.platform.startswith("linux"):
        return serial.Serial(port, baud, timeout=timeout, dsrdtr=False, rtscts=False)
    ser = serial.Serial(None, baud, timeout=timeout, dsrdtr=False, rtscts=False)
    ser.port = port
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def _probe(port: str, baud: int = 115200, timeout: float = 0.8) -> str:
    """Open `port` DTR/RTS-low, send ID?, and classify from the reply.
    Returns "klar" or "other". Never resets the board."""
    import serial
    try:
        ser = open_no_reset(port, baud, timeout)
    except (serial.SerialException, OSError):
        return "other"
    try:
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        ser.write(ID_QUERY)
        ser.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = ser.readline().decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith(KLAR_REPLY_PREFIX):
                return "klar"
            # A non-KLAR line is enough to call it "other" — but keep reading a
            # touch in case the first line was stale buffered output.
        return "other"
    except (serial.SerialException, OSError):
        return "other"
    finally:
        try:
            ser.close()
        except Exception:
            pass


def _classify(port: str) -> str:
    """Return the cached kind for `port`, probing once if unknown. Owned ports
    are never re-opened. Caller must NOT hold `_lock`."""
    with _lock:
        if port in _owned:
            return _cache.get(port, "klar")  # owned by the lamp loop
        cached = _cache.get(port)
        if cached is not None:
            return cached
    kind = _probe(port)           # probe outside the lock (it does I/O)
    with _lock:
        # Don't clobber a value set while we were probing.
        _cache.setdefault(port, kind)
        return _cache[port]


def _forget_absent(present: set[str]) -> None:
    with _lock:
        for gone in [p for p in _cache if p not in present]:
            if gone not in _owned:
                _cache.pop(gone, None)


def find_klar_port() -> str | None:
    """First attached port that handshakes as the lamp and isn't already owned.
    Returns None when no lamp is present."""
    ports = espressif_ports()
    _forget_absent(set(ports))
    for port in ports:
        with _lock:
            if port in _owned:
                continue
        if _classify(port) == "klar":
            return port
    return None


def is_klar(port: str) -> bool:
    """True if `port` is the lamp. Used by Argus's USB discovery to skip it."""
    return _classify(port) == "klar"


def claim(port: str) -> None:
    with _lock:
        _owned.add(port)
        _cache[port] = "klar"


def release(port: str) -> None:
    with _lock:
        _owned.discard(port)
