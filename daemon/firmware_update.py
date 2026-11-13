"""Where firmware images for updates-over-the-link come from.

Two sources:
  - the build published by the project's GitHub Pages flasher (`version.json`
    there names the build id and the app image), and
  - a local .bin — either PlatformIO's app image (`firmware.bin`) or the merged
    factory image the web flasher uses, from which the app part is cut out.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://pierregode.github.io/Argus/"

_APP_OFFSET = 0x10000          # where the app sits inside a merged image
_PART_TABLE_OFFSET = 0x8000
_ESP_IMAGE_MAGIC = 0xE9
_CHIP_ID_ESP32S3 = 9


class FirmwareError(Exception):
    pass


def app_image(data: bytes) -> bytes:
    """Validate `data` as an ESP32-S3 app image, cutting it out of a merged
    factory image if that's what was given. Raises FirmwareError otherwise —
    this is the guard against pushing an arbitrary file at the device."""
    if len(data) > _APP_OFFSET + 24 and \
            data[_PART_TABLE_OFFSET:_PART_TABLE_OFFSET + 2] == b"\xaa\x50":
        data = data[_APP_OFFSET:]
    if len(data) < 24 or data[0] != _ESP_IMAGE_MAGIC:
        raise FirmwareError("not an ESP32 firmware image")
    (chip_id,) = struct.unpack_from("<H", data, 12)
    if chip_id != _CHIP_ID_ESP32S3:
        raise FirmwareError(f"image is for a different chip (id {chip_id}), not ESP32-S3")
    return data


def load_file(path: str) -> bytes:
    try:
        return app_image(Path(path).expanduser().read_bytes())
    except OSError as e:
        raise FirmwareError(f"can't read {path}: {e}")


def published(base_url: str = DEFAULT_BASE_URL, timeout: float = 20) -> dict:
    """Return {"build", "url", "md5"} for the published firmware."""
    base = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/"
    try:
        resp = httpx.get(base + "version.json", timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FirmwareError(f"can't reach {base}: {e}")
    if resp.status_code != 200:
        raise FirmwareError(f"version.json: HTTP {resp.status_code}")
    try:
        d = resp.json()
        build = str(d["build"])
    except (ValueError, KeyError, TypeError):
        raise FirmwareError("version.json has no build id")
    ota = d.get("ota")
    if not ota:
        raise FirmwareError("the published site carries no update image yet "
                            "(it appears after the next deploy)")
    return {"build": build, "url": base + str(ota).lstrip("/"),
            "md5": str(d.get("ota_md5") or "")}


def download(info: dict, timeout: float = 120) -> bytes:
    try:
        resp = httpx.get(info["url"], timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FirmwareError(f"download failed: {e}")
    if resp.status_code != 200:
        raise FirmwareError(f"download failed: HTTP {resp.status_code}")
    data = resp.content
    if info.get("md5") and hashlib.md5(data).hexdigest() != info["md5"].lower():
        raise FirmwareError("downloaded image is corrupt (checksum mismatch)")
    return app_image(data)
