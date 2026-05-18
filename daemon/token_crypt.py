"""GitHub PAT storage backed by the OS-native credential store.

`config.json` never holds the secret itself — it holds a marker like
`keyring:argus-daemon:github_token` that points at an entry in:

  - Windows: Credential Manager      (keyring.backends.Windows)
  - macOS:   Login Keychain          (keyring.backends.macOS)
  - Linux:   Secret Service / KWallet (keyring.backends.SecretService /
             keyring.backends.kwallet) — needs libsecret or KWallet on the box

The encrypt/decrypt API stays the same so callers don't change. Three
backwards-compat paths in decrypt():

  - `dpapi:…`   — legacy Windows-only blob from the pre-keyring release;
                  unwrapped via the bundled DPAPI ctypes shim. Gets
                  promoted to a keyring entry the next time save_config
                  runs.
  - `plain:…`   — explicit marker for "no encryption available, sorry"
                  (e.g. Linux without a keyring backend). decrypt strips
                  the prefix.
  - bare string — legacy plaintext from a config.json that predates this
                  module. Returned verbatim; will be migrated on next save.

If `keyring` is missing or its active backend refuses writes (the "null"
or "fail" backends) we fall back to `plain:` so the daemon keeps working —
just without encryption. The tray UI surfaces this via is_secure().
"""

from __future__ import annotations

import base64
import sys

try:
    import keyring
    import keyring.errors
    _KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    _KEYRING_AVAILABLE = False


_SERVICE = "argus-daemon"
_USER = "github_token"

_PREFIX_KEYRING = "keyring:"
_PREFIX_DPAPI = "dpapi:"
_PREFIX_PLAIN = "plain:"

# Stable entropy for the legacy DPAPI path. Keep in sync with the previous
# release so old config.json files still decrypt during the migration pass.
_DPAPI_ENTROPY = b"argus-daemon:v1:github_pat"


# ----- Active backend detection ------------------------------------------------

def _active_backend_name() -> str:
    if not _KEYRING_AVAILABLE:
        return "missing"
    kr = keyring.get_keyring()
    return f"{kr.__class__.__module__}.{kr.__class__.__name__}"


def _backend_is_secure() -> bool:
    """True when the active keyring backend actually encrypts at rest.
    Returns False for the "fail" / "null" / "chainer-with-no-children"
    backends and for plaintext file fallbacks."""
    if not _KEYRING_AVAILABLE:
        return False
    name = _active_backend_name()
    insecure = ("fail.Keyring", "null.Keyring", "chainer.ChainerBackend")
    if any(name.endswith(b) for b in insecure):
        # Chainer is OK as long as it has at least one secure child.
        if name.endswith("chainer.ChainerBackend"):
            kr = keyring.get_keyring()
            children = getattr(kr, "backends", None) or []
            return any(not c.__class__.__name__.endswith(("Keyring",)) is False
                       and c.__class__.__name__ not in ("Keyring",) for c in children)
        return False
    return True


def is_secure() -> bool:
    """Public flag the tray UI can read to decide whether to show a
    "your token is encrypted" tick or a "no backend — install libsecret"
    warning. We treat the Windows / macOS / Secret Service / KWallet
    backends as secure; everything else as not."""
    if not _KEYRING_AVAILABLE:
        return False
    name = _active_backend_name()
    return any(part in name for part in (
        "Windows.WinVaultKeyring",
        "macOS.Keyring",
        "SecretService.Keyring",
        "kwallet.DBusKeyring",
        "libsecret.Keyring",
    ))


# ----- Legacy DPAPI unwrap (Windows only) -------------------------------------

def _legacy_dpapi_unprotect(b64_blob: str) -> str:
    """Decode a `dpapi:` legacy blob using Win32 CryptUnprotectData. Returns
    "" on any failure — caller treats that as a missing token and the user
    re-enters it via the tray UI."""
    if sys.platform != "win32":
        print("[token_crypt] dpapi:… blob on non-Windows host — unreadable", flush=True)
        return ""

    import ctypes
    import ctypes.wintypes as wt

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wt.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    try:
        ct = base64.b64decode(b64_blob)
    except ValueError:
        return ""

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ct_buf = ctypes.create_string_buffer(ct, len(ct))
    ent_buf = ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY))
    in_blob = DataBlob(len(ct), ctypes.cast(ct_buf, ctypes.POINTER(ctypes.c_byte)))
    ent_blob = DataBlob(len(_DPAPI_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DataBlob()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None, None, 0,
        ctypes.byref(out_blob),
    )
    if not ok:
        print(f"[token_crypt] legacy CryptUnprotectData failed "
              f"(GetLastError={ctypes.get_last_error()}) — token lost", flush=True)
        return ""
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8", errors="replace")
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ----- Public API --------------------------------------------------------------

def encrypt(plaintext: str) -> str:
    """Stash `plaintext` in the OS credential store and return a marker for
    config.json. Empty input clears any existing entry and returns "" so an
    unset token round-trips cleanly through save → load."""
    if not plaintext:
        # Best-effort wipe of any previously-stored value.
        if _KEYRING_AVAILABLE:
            try:
                keyring.delete_password(_SERVICE, _USER)
            except keyring.errors.PasswordDeleteError:
                pass
            except keyring.errors.KeyringError as e:
                print(f"[token_crypt] keyring delete: {e}", flush=True)
        return ""

    if _KEYRING_AVAILABLE and is_secure():
        try:
            keyring.set_password(_SERVICE, _USER, plaintext)
            return f"{_PREFIX_KEYRING}{_SERVICE}:{_USER}"
        except keyring.errors.KeyringError as e:
            print(f"[token_crypt] keyring write failed ({e}) — storing plain", flush=True)

    # Fallback: no usable backend. Surface the prefix so future versions
    # can detect "this token never got encrypted" and prompt the user.
    if not _KEYRING_AVAILABLE:
        print("[token_crypt] WARNING: `keyring` not installed — token stored in plaintext", flush=True)
    elif not is_secure():
        print(f"[token_crypt] WARNING: no secure keyring backend "
              f"({_active_backend_name()}) — token stored in plaintext", flush=True)
    return _PREFIX_PLAIN + plaintext


def decrypt(stored: str) -> str:
    """Inverse of encrypt(). Returns "" rather than raising on failure so
    the daemon treats unreadable tokens as "GitHub disabled" instead of
    crashing — that's the safest interpretation: if we can't recover the
    secret, we shouldn't keep using it."""
    if not stored:
        return ""

    if stored.startswith(_PREFIX_KEYRING):
        if not _KEYRING_AVAILABLE:
            print("[token_crypt] keyring:… marker but `keyring` not installed", flush=True)
            return ""
        # Marker format: keyring:<service>:<user>. Tolerate either field
        # missing — fall back to our defaults if so.
        rest = stored[len(_PREFIX_KEYRING):]
        parts = rest.split(":", 1)
        service = parts[0] or _SERVICE
        user = parts[1] if len(parts) > 1 else _USER
        try:
            val = keyring.get_password(service, user)
            return val or ""
        except keyring.errors.KeyringError as e:
            print(f"[token_crypt] keyring read failed: {e}", flush=True)
            return ""

    if stored.startswith(_PREFIX_DPAPI):
        return _legacy_dpapi_unprotect(stored[len(_PREFIX_DPAPI):])

    if stored.startswith(_PREFIX_PLAIN):
        return stored[len(_PREFIX_PLAIN):]

    # Bare string — legacy plaintext from before any of this shipped.
    return stored


# ----- CLI smoke test ----------------------------------------------------------

if __name__ == "__main__":
    print(f"backend: {_active_backend_name()}  (secure={is_secure()})")
    if len(sys.argv) >= 2 and sys.argv[1] == "--decrypt":
        blob = sys.stdin.read().strip()
        print(repr(decrypt(blob)))
    elif len(sys.argv) >= 2:
        enc = encrypt(sys.argv[1])
        print("stored marker:", enc)
        print("round-trip   :", repr(decrypt(enc)))
    else:
        print("usage: token_crypt.py <plaintext>  |  token_crypt.py --decrypt < blob")
