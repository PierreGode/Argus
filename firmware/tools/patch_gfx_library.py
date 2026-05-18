"""
Patches the moononournation GFX-for-Arduino library so the ESP32 RGB panel
driver enables `bb_invalidate_cache` when bounce-buffer mode is in use.

The library hardcodes `bb_invalidate_cache = false` in
`src/databus/Arduino_ESP32RGBPanel.cpp`. With PSRAM-backed framebuffers,
this leaves stale cached pixels visible as horizontal glitch lines on the
left side of the panel (the bounce buffer's leading edge of each refill).
Flipping the flag to `true` makes the driver invalidate cache before each
bounce-buffer DMA so the LCD sees freshly written PSRAM pixels.

Idempotent — safe to re-run on every build. Invoked via `extra_scripts` in
platformio.ini's pre: phase so the patch is reapplied after any
`pio lib update` that re-fetches the library.
"""
from pathlib import Path

Import("env")  # noqa: F821 — provided by PlatformIO SCons context

OLD = ".bb_invalidate_cache = false,"
NEW = ".bb_invalidate_cache = true,"

target = Path(env["PROJECT_LIBDEPS_DIR"]) / env["PIOENV"] / \
    "GFX Library for Arduino" / "src" / "databus" / "Arduino_ESP32RGBPanel.cpp"

if not target.exists():
    print(f"[patch_gfx_library] target not yet present: {target}")
else:
    text = target.read_text(encoding="utf-8")
    if NEW in text:
        print("[patch_gfx_library] already patched")
    elif OLD in text:
        target.write_text(text.replace(OLD, NEW), encoding="utf-8")
        print(f"[patch_gfx_library] patched {target.name}")
    else:
        print(f"[patch_gfx_library] WARNING: neither '{OLD}' nor '{NEW}' "
              f"found in {target}; library structure may have changed")
