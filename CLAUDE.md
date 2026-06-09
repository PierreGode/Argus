# Argus — project context

ESP32-S3 firmware for **Argus**, a desk-side dev monitor on a **Waveshare ESP32-S3-Touch-AMOLED-2.16** board (480×480 square AMOLED). Connects to a host daemon over BLE; the daemon polls the Anthropic API for Claude Code usage, GitHub for issue/PR counts, and GitHub Copilot for seat status + AI-credit usage, then pushes a JSON payload to the device. The device cycles through Splash / Usage / Today / GitHub / Copilot / Bluetooth screens and can auto-focus on a screen when something noteworthy changes (e.g. a new PR).

Argus began as a fork of an upstream ESP32 usage-monitor project, then was renamed and reworked. Leftover third-party library strings in build artifacts (e.g. `.pio/` libdeps) are upstream content and not load-bearing.

This file is for future Claude Code sessions to bootstrap quickly. Read this first.

## Hardware (critical pins)

- Display: **CO5300** AMOLED via QSPI (CS=12, SCLK=38, SDIO0..3=4..7, RST=2)
- Touch: **CST9220** via I2C (SDA=15, SCL=14, INT=11, addr=0x5A)
- PMU: **AXP2101** on same I2C bus (addr=0x34) — battery, USB VBUS, PWR button IRQ
- IMU: **QMI8658** on same I2C bus (addr=0x6B) — accelerometer for auto-rotation
- Button: **BOOT** (GPIO 0) → cycle screens. The AXP2101 PWR key (`power_pwr_pressed()`) is a stub and the BLE HID keyboard service (`ble_keyboard_press`) is defined but unbound — no other button is wired in the current build.

## Architecture

```text
main.cpp        — setup(), loop(), BOOT-button polling (cycle screens), rotation flash, JSON payload parsing
display_cfg.h   — pin defines, extern object decls
ui.{h,cpp}      — 6 screens (splash, usage, today, github, copilot, bluetooth); splash is touch-toggled, others cycled via mid button. Daemon can hide screens from the cycle via the enabled-apps CSV.
splash.{h,cpp}  — sprite-driven splash: 6 mascot expressions (240×240 RGB565A8, 2× upscale to 480×480), mood-locked or cycled by usage-rate group, plus a rotating events strip
usage_rate.{h,cpp} — maps the current caps to a 0..3 usage-rate group used by the splash
imu.{h,cpp}     — accelerometer-driven rotation tracker (returns 0..3)
power.{h,cpp}   — AXP2101 wrapper (battery %, charging, VBUS, PWR button)
touch.{h,cpp}   — minimal tap detector → ui_toggle_splash() (Usage/Splash) or ble_clear_bonds() (BT reset zone)
ble.{h,cpp}     — NimBLE peripheral: custom data service + HID keyboard
data.h          — UsageData struct (full wire payload after parsing)
theme.h         — shared colors / layout constants
icons.h         — icon arrays. Battery (5×) are RGB565A8 with alpha; rest are raw RGB565.
argus_sprites.h — generated mascot sprite blobs (RGB565A8); do not hand-edit. From tools/build_argus_sprites.js.
font_*.c        — pre-compiled LVGL 9 bitmap fonts (Tiempos 34/56, Styrene 12/14/16/20/24/28/48, Mono 18/32)
```

## Build / flash

```bash
pio run -d firmware                                       # build
pio run -d firmware -t upload --upload-port /dev/ttyACM0  # flash (binary path uses USB JTAG)
```

`/home/hermann/.platformio/penv/bin/pio` if `pio` isn't on PATH.

Device shows up as `/dev/ttyACM0` (Espressif USB JTAG/serial debug unit). No boot-mode gymnastics needed — direct flash works.

## QA your own UI changes — don't ask the user

The firmware ships a `screenshot` serial command that dumps the LVGL framebuffer over `/dev/ttyACM0`. `./screenshot.sh out.png /dev/ttyACM0` captures a 480×480 PNG. **Use this on every UI iteration** — Read the PNG with the Read tool, verify the change visually, iterate.

The boot screen is `SCREEN_SPLASH` and only advances on a physical button press, so a fresh flash will sit on the splash. To screenshot the screen you're actually editing without asking the user to press a button, **temporarily change the default boot screen** in `main.cpp` (search for `ui_show_screen(SCREEN_SPLASH);`) to `SCREEN_USAGE` / `SCREEN_TODAY` / `SCREEN_GITHUB` / `SCREEN_COPILOT` / `SCREEN_BLUETOOTH`, do your iteration, then revert before committing.

## Critical gotchas

1. **CO5300 cannot rotate.** Its MADCTL only supports axis flips, not column/row exchange. Rotation is done by **CPU pixel remapping in `my_flush_cb`** in main.cpp. We use **PARTIAL render mode with strip rotation** (small 480×40 strips, fast). On rotation change → AMOLED brightness flash → force redraw.
2. **OPI PSRAM** required: `board_build.arduino.memory_type = qio_opi` in platformio.ini. Without this, `MALLOC_CAP_SPIRAM` returns NULL and the screen is black.
3. **pioarduino platform required.** GFX Library for Arduino needs Arduino Core 3.x (`esp32-hal-periman.h`), not the 2.x that standard `espressif32` ships. We pin `pioarduino/platform-espressif32` 55.03.38-1.
4. **LVGL 9 font patching.** `lv_font_conv` outputs LVGL 8 format. Must remove `#if LVGL_VERSION_MAJOR >= 8` guards, drop `.cache` field, add `.release_glyph`, `.kerning`, `.static_bitmap`, `.fallback`, `.user_data`. Without patching, fonts render invisible.
5. **Touch reading must be centralized.** CST9220's `getPoint()` does a full I2C transaction. Calling it from multiple places consumed each other's data and broke input. `touch_read()` is called once per loop in main.cpp; both LVGL `my_touch_cb` and `touch.cpp` read from shared `touch_pressed/touch_x/touch_y` state.
6. **CO5300 needs even-aligned flush regions.** `rounder_cb` enforces this.
7. **Touch `setSwapXY(true)` and `setMirrorXY(true, false)`** are the empirically-correct values for default rotation 0. IMU rotation logic doesn't change touch mapping (it does CPU-side rotation of the rendered pixels, so LVGL still thinks the display is portrait at 0°).
8. **LVGL RGB565A8 is planar.** `w*h` RGB565 pixels followed by `w*h` alpha bytes; `data_size = w*h*3`, `stride = w*2`. Use `init_icon_dsc_rgb565a8()` for icons that overlap non-uniform backgrounds (e.g. battery over splash). Lucide source PNGs are black-on-transparent — converter must tint to white or icons render invisible. See `tools/png_to_lvgl.js`.

## Icons

`tools/png_to_lvgl.js <input.png> <symbol> [W_MACRO] [H_MACRO] [--tint=RRGGBB | --no-tint]` converts an alpha PNG to RGB565A8. Default tint is white (`0xFFFFFF`) — necessary for Lucide PNGs. Splice output into `firmware/src/icons.h` and use `init_icon_dsc_rgb565a8()` in ui.cpp. Currently only the 5 battery icons use this format; the rest are still raw RGB565 baked over the panel background, fine because they live inside opaque zones.

## Splash

The firmware splash shows one of **6 mascot expressions** (happy / looking / flirt / buffeld / surprised / angry) at 240×240 RGB565A8, scaled 2× to fill the panel. The face is locked to the daemon's `mood` when one is sent, otherwise it cycles within the current usage-rate group. The old 20×20 pixel-art animation engine has been **removed from the firmware**. Sprites are generated from `assets/img/*.png`:

```bash
node tools/build_argus_sprites.js   # → firmware/src/argus_sprites.h  +  docs/img/sprite_*.png
```

The legacy claudepix 20×20 animations now only feed the **web flasher hero** on the GitHub Pages site (`tools/build_web_animations.js` → `docs/splash_animations.json`), not the device.

## User profile / preferences

See `~/.claude/projects/.../memory/` files for persistent context (user is an embedded-beginner senior dev, brand-conscious, prefers iterative UI refinement, dislikes me authoring my own art when third-party assets are intended). Always read those memory files at session start.

## Daemon / host side

The primary daemon is **`daemon/argus-daemon.py`** — a cross-platform PySide6 tray app + worker thread. It polls the Anthropic API (rate-limit headers), parses `~/.claude/projects/**/*.jsonl` for today's stats, polls GitHub (`github_stats.py`) and Copilot (`copilot_stats.py`), then ships one JSON line per poll. Settings live in `%APPDATA%/Argus/config.json` (Windows), `~/Library/Application Support/Argus/config.json` (macOS), or `~/.config/argus/config.json` (Linux); the GitHub token is encrypted at rest (`token_crypt.py`). `tray_ui.py` holds the window/config code; `version.py` is the single source of truth for the version.

`daemon/argus-daemon.sh` is the original Linux/systemd bash daemon (run via `systemctl --user start argus-daemon`); it predates the Python port and only does Claude rate limits.

**The wire payload** is built in `build_payload()` and parsed in `main.cpp`'s `parse_payload()`. Keys are short to fit the BLE MTU — `s`/`sr`/`w`/`wr` (caps), `c`/`cw`/`mo`/`ms`/`mh`/`ch`/`tk`/`se`/`pj` (today), `ge`/`gi`/`gp` (GitHub), `cp`/`cps`/`cpw`/`cpe`/`cpr`/`cpp`/`cpu`/`cpa`/`cpsu`/`cpm` (Copilot), `apps` (enabled-screens CSV), `tac`/`tch` (Today screen: show cost panel / cache panel), `md`/`evts` (splash mood + events strip), `br` (brightness), `fc` (one-shot auto-focus), `nm` (BLE device-name override — firmware persists it to NVS and re-advertises, so multiple Argus units don't all collide on `"Argus Controller"`). The firmware ignores unknown keys, so the two sides can version independently. The full table lives in README.md.

**Discovery & resilience:** connects by name (`"Argus Controller"`), caches the resolved MAC, and on connect failure drops the cache (and removes the device from bluez on Linux) so the next scan won't re-pick a dead MAC. ESP32 BLE addresses are factory-burned per-chip, so swapping any board invalidates the cache. The inner loop wakes every ~5s to detect disconnects fast and polls when the interval elapses OR when the ESP fires a refresh request.

**GATT characteristics on service `4c41555a-...0001`:**

- `...0002` RX — daemon writes the JSON usage payload here.
- `...0003` TX — firmware notifies ack/nack (daemon doesn't subscribe).
- `...0004` REQ — firmware fires a `0x01` notify in `onSubscribe` if it hasn't received data yet, asking the daemon to push immediately.
