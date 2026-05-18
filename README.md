# Argus

A small ESP32 dashboard that sits on my desk and watches my dev workflow — Claude Code rate limits, GitHub issues and PRs, today's token spend — then auto-switches to whichever screen has news.

It runs on a [ESP32-S3 Smart 86 Box Development Board touch LCD ](https://www.waveshare.com/esp32-s3-touch-lcd-4b.htm?srsltid=AfmBOoqCfMzBwrAlBizVvAYwWNn8y5nF47A394HkxzyLU1cAYydvO8_g), pairs with my laptop over Bluetooth (or USB-C), and the splash screen plays pixel-art Clawd animations that get busier when your usage rate climbs. The two side buttons send Space and Shift+Tab over BLE HID for Claude Code's voice-mode and mode-toggle shortcuts.
  


What Argus Offers:

- A live **GitHub screen** — open issues assigned to you and PRs awaiting your review, fetched with a PAT (`github_stats.py`).
- **Auto-focus**: the device automatically switches to the relevant screen when something changes. New PR? The screen jumps to GitHub. Manual navigation is preserved between events so it doesn't feel hostile.
- A **PySide6 tray app** for Windows / macOS / Linux (`tray_ui.py`) — proper main window with live log, brand-styled QSS theme, system-tray integration. Replaces the original tkinter settings dialog.
- **Immediate push on Save** — when you change settings, a wake-event drops the daemon out of its inter-poll sleep so the new config hits the device in ~2 seconds, not up to a minute.
- Explicit **"No data"** placeholders instead of fake `$0.00` / `0%` / `Opus 0% Sonnet 0% Haiku 0%` defaults — the screen now clearly shows when nothing has been received yet.
- A **poll-interval picker** (30 s / 1 m / 2 m / 5 m / 10 m) and a **Start with Windows** checkbox in the tray app.
- The BLE device name changed to `Argus Controller` and the project filenames / config paths follow the new branding (see [Migration](#migration-from-clawdmeter)).


## Quick start

1. **Flash the firmware**: open <https://pierregode.github.io/Argus/> in Chrome / Edge / Opera, plug the board in over USB-C, click **Flash Argus**. (Pages deployment needs to be set up against the renamed repo — see the workflows in `.github/`. Until then, build from source — see [Build the firmware](#build-the-firmware-locally).)
2. **Download the daemon** from the same page (Windows `.exe`, macOS, or Linux binary).
3. **Run it**: it lives in the system tray. Right-click → **Show window** to enter a GitHub token, set brightness, pick BLE / USB-C transport, choose poll interval, or toggle Start with Windows. Settings are saved to `%APPDATA%\Argus\config.json` (or the platform-equivalent) and applied on the next send.

The device pairs the first time it sees the daemon; from then on it reconnects automatically.

## Screens

The device boots into the splash and stays there until you press the middle (PWR) button, which cycles `Splash → Usage → Today → GitHub → Bluetooth`. Tap the screen anywhere (except the Reset zone on the Bluetooth screen) to flip back to the splash; tap again to dismiss it.


**Usage** shows the 5-hour-window session utilization (`Current`) and the 7-day weekly utilization. Bars turn green / amber / red at 50% / 80%. Reset times count down in minutes/hours.

**Today** shows the pay-as-you-go API-equivalent cost of today's tokens (labeled "API equiv." — on a Max subscription you don't actually pay this, but it's a useful measure of how much value the subscription is saving you), the 7-day rolling cost, the Opus / Sonnet / Haiku token split, your cache hit rate, the most recently active project, and how many sessions you've started today. All of it is parsed from `~/.claude/projects/**/*.jsonl` by the daemon, so it works even when the API is down.

**GitHub** shows the count of open issues assigned to you and open PRs awaiting your review (or assigned to you). Requires a GitHub PAT in the daemon's tray settings (Issues + Pull requests read scopes). Refreshes every 5 minutes; well under the 5000/hr search-API limit. With no token configured the panels show `No data` and a hint.

While the splash is up, the middle button cycles animations instead of screens. The firmware also auto-rotates every 20 s within the current usage-rate group, so a long stretch on the splash isn't just one Clawd on loop.

## Auto-focus

Argus tracks event counters between polls. When something noteworthy changes — currently the trigger is a new GitHub PR or new issue assigned to you — the daemon adds `"fc": "github"` to that single payload and the firmware switches to the GitHub screen.

Behavioral rules:

- The first poll after a daemon restart never triggers a focus (no baseline to compare against, so no spurious switch on reboot).
- Manual navigation is preserved: if you press the middle button to move elsewhere, subsequent "no change" polls leave you alone.
- A further event fires another switch — the daemon only sends `fc` on the poll where the delta is detected, not continuously.

The mechanism generalizes to other triggers (rate-limit threshold crossings, etc.) by adding more entries to `_detect_focus()` in `argus-daemon.py`. The five supported `fc` values are `splash`, `usage`, `today`, `github`, `bluetooth`.

## Hardware

- [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm?&aff_id=149786) — ESP32-S3R8, 2.16" 480×480 AMOLED (CO5300 QSPI), CST9220 cap touch, AXP2101 PMU + Li-Po battery, QMI8658 IMU.
- USB-C cable for flashing and charging.
- 3.7V Li-Po battery (MX1.25 2-pin connector, optional).

## Daemon

The daemon does four things: poll Anthropic's rate-limit headers, parse local Claude Code conversation logs, poll GitHub for issue/PR counts, and ship a single JSON payload to the device. Two transports:

| Transport          | When to use            | How to start                                                                                              |
| ------------------ | ---------------------- | --------------------------------------------------------------------------------------------------------- |
| **BLE** (default)  | Wireless, no cable     | `argus-daemon`                                                                                            |
| **USB-C serial**   | Plugged in, no pairing | `argus-daemon --serial` (auto-detects ESP32-S3) or `--serial COM3` to force a specific port               |
| **Demo mode**      | Test the UI            | `--demo` flag — sends randomized payloads, no API key required                                            |
| **Headless**       | systemd / no display   | `--headless` — skip the tray app, run worker on the main thread                                           |

The daemon checks the connection every 2 seconds and reconnects fast if you unplug, walk out of BLE range, or restart the board.

The tray app is the recommended way to run it on Windows / macOS. Closing the window hides to tray; right-click the tray icon → Quit to actually exit. The window shows live log output, the current connection status, and exposes all settings (token, brightness, transport, poll interval, autostart).

## Prerequisites

- Linux / macOS / Windows
- [PlatformIO CLI](https://docs.platformio.org/en/latest/core/installation/index.html) (for building firmware from source)
- Python 3.11+ with `pip`
- Claude Code with an active subscription

## Build the firmware locally

```bash
cd firmware
pio run -t upload
```

PlatformIO auto-detects the USB port. On Windows, the COM number can change between plug-ins — `pio device list` will show what to expect.

## Run the daemon from source

```bash
pip install -r daemon/requirements.txt
python daemon/argus-daemon.py             # BLE
python daemon/argus-daemon.py --serial    # USB-C, auto-detect
python daemon/argus-daemon.py --demo      # fake data, no API key needed
python daemon/argus-daemon.py --headless  # no tray UI
```

To install as a systemd user service on Linux:

```bash
./install.sh
systemctl --user start argus-daemon
```

Logs: `journalctl --user -u argus-daemon -f`

On macOS, `./install-mac.sh` sets up a LaunchAgent under `~/Library/LaunchAgents/com.user.argus-daemon.plist`.

## Bluetooth pairing

After flashing, the device advertises as **Argus Controller**. The daemon discovers and connects to it by name automatically on first run — no manual pairing required on Windows or macOS.

On Linux you may need to allow it once:

```bash
bluetoothctl scan le
bluetoothctl pair F4:12:FA:C0:8F:E5    # use your device's MAC
bluetoothctl trust F4:12:FA:C0:8F:E5
```

The MAC address is shown on the Bluetooth screen — press the middle (PWR) button to cycle to it.

## Physical buttons

The board has three side buttons. Left and right do the same thing on every screen; the middle button is screen-aware.

| Button           | GPIO         | Function                                                                                 |
| ---------------- | ------------ | ---------------------------------------------------------------------------------------- |
| **Left**         | GPIO 0       | Hold to send Space (Claude Code voice-mode push-to-talk)                                 |
| **Middle** (PWR) | AXP2101 PKEY | Cycle screens (Splash → Usage → Today → GitHub → Bluetooth); cycles animations on splash |
| **Right**        | GPIO 18      | Press to send Shift+Tab (Claude Code mode toggle)                                        |

Space and Shift+Tab go out as standard BLE HID keyboard reports, so they trigger in whatever window has focus on the paired host — not just Claude Code.

## Wire protocol

Both BLE and USB-C carry the same JSON payload. Over BLE it's a single GATT write; over USB-C it's a newline-terminated line at 115200 baud.

### BLE characteristics

|                            | UUID                                   |
| -------------------------- | -------------------------------------- |
| **Data Service**           | `4c41555a-4465-7669-6365-000000000001` |
| RX Characteristic (write)  | `4c41555a-4465-7669-6365-000000000002` |
| TX Characteristic (notify) | `4c41555a-4465-7669-6365-000000000003` |
| REQ Characteristic (notify)| `4c41555a-4465-7669-6365-000000000004` |
| **HID Service**            | `00001812-0000-1000-8000-00805f9b34fb` |

### Payload

```json
{
  "s": 45, "sr": 120, "w": 28, "wr": 7200,
  "st": "allowed", "ok": true,
  "c": 3.47, "cw": 12.30,
  "mo": 45, "ms": 50, "mh": 5,
  "ch": 82, "tk": 234567, "se": 3,
  "pj": "argus",
  "ge": true, "gi": 4, "gp": 2,
  "br": 80,
  "fc": "github"
}
```

| Key                    | Meaning                                                                |
| ---------------------- | ---------------------------------------------------------------------- |
| `s`                    | session % (5-hour window)                                              |
| `sr`                   | minutes until session resets                                           |
| `w`                    | weekly %                                                               |
| `wr`                   | minutes until weekly resets                                            |
| `st`                   | rate-limit status                                                      |
| `ok`                   | poll succeeded                                                         |
| `c`                    | USD spent today (API-equivalent)                                       |
| `cw`                   | USD spent in the last 7 days                                           |
| `mo` / `ms` / `mh`     | Opus / Sonnet / Haiku token share, %                                   |
| `ch`                   | cache hit rate, %                                                      |
| `tk`                   | tokens consumed today (sum of all categories)                          |
| `se`                   | distinct sessions today                                                |
| `pj`                   | most recently active project (basename)                                |
| `ge`                   | GitHub enabled (token is configured)                                   |
| `gi`                   | open issues assigned to the user                                       |
| `gp`                   | open PRs awaiting the user (review or owned)                           |
| `br`                   | display brightness, 10–100                                             |
| `fc`                   | **(new in Argus)** auto-focus target — `splash` / `usage` / `today` / `github` / `bluetooth`; present only on the poll where a change was detected |

The firmware ignores keys it doesn't recognize, so older daemons (rate-limit fields only) still work.

## Web flasher build pipeline

`.github/workflows/deploy-flasher.yml` runs on every push to `main`:

1. **Build the firmware** with PlatformIO, merge bootloader + partitions + app into a single offset-0 image with `esptool merge_bin`.
2. **Build the daemon** in parallel on Windows, macOS, and Linux runners via PyInstaller.
3. **Deploy to GitHub Pages** — the firmware bin, the three daemon binaries, the splash animations, and the flasher HTML all ship together.

The page uses [esp-web-tools](https://esphome.github.io/esp-web-tools/) for the Web Serial flash flow.

## Migration from Clawdmeter

If you previously ran upstream Clawdmeter on this hardware:

- **BLE device name changed** — the firmware now advertises as `Argus Controller`. Old Clawdmeter daemons won't find it; flash this firmware *and* run the Argus daemon.
- **Config path changed** — `%APPDATA%\Argus\config.json` on Windows, `~/Library/Application Support/Argus/config.json` on macOS, `~/.config/argus/config.json` on Linux. If you want your saved token / brightness / transport back, copy the old `Clawdmeter` file across; otherwise re-enter in the tray app.
- **HKCU Run key renamed** — old autostart entry was `Clawdmeter`, new one is `Argus`. Toggle the "Start with Windows" checkbox in the tray app once, or remove the old key manually: `reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Clawdmeter /f`.
- **Web flasher firmware binary** renamed from `clawdmeter-esp32s3.bin` to `argus-esp32s3.bin`.

## Recompiling fonts

The `firmware/src/font_*.c` files are pre-compiled LVGL bitmap fonts at ~1.9× the original Panlee 165 PPI sizing to match the 314 PPI of the 2.16" AMOLED.

```bash
npm install -g lv_font_conv
```

Generate each one (one at a time — `lv_font_conv` doesn't like loop-driven invocations) with `--no-compress` (required for LVGL 9):

```bash
# Tiempos Text (titles, 56px)
lv_font_conv --font assets/TiemposText-400-Regular.otf -r 0x20-0x7E \
  --size 56 --format lvgl --bpp 4 --no-compress \
  -o firmware/src/font_tiempos_56.c --lv-include "lvgl.h"

# Styrene B (numbers 48, panel labels 28, small text 24, minimal 20)
for size in 48 28 24 20; do
  lv_font_conv --font assets/StyreneB-Regular.otf -r 0x20-0x7E \
    --size $size --format lvgl --bpp 4 --no-compress \
    -o firmware/src/font_styrene_${size}.c --lv-include "lvgl.h"
done

# DejaVu Sans Mono (32px, with spinner Unicode chars)
lv_font_conv --font assets/DejaVuSansMono.ttf \
  -r 0x20-0x7E,0xB7,0x2026,0x2722,0x2733,0x2736,0x273B,0x273D \
  --size 32 --format lvgl --bpp 4 --no-compress \
  -o firmware/src/font_mono_32.c --lv-include "lvgl.h"
```

**Important:** `lv_font_conv` v1.5.3 outputs LVGL 8 format. Each generated file must be patched for LVGL 9 compatibility:

1. Remove `#if LVGL_VERSION_MAJOR >= 8` guards around `font_dsc` and the font struct.
2. Remove the `.cache` field from `font_dsc`.
3. Add `.release_glyph = NULL`, `.kerning = 0`, `.static_bitmap = 0` to the font struct.
4. Add `.fallback = NULL`, `.user_data = NULL` to the font struct.

Without these patches, fonts compile but render as invisible.

## Converting Lucide icons

The UI uses a small set of [Lucide](https://lucide.dev) icons (bluetooth + battery states) converted to RGB565 / RGB565A8 C arrays for LVGL.

```bash
node tools/png_to_lvgl.js assets/icon_bluetooth_48.png icon_bluetooth_data ICON_BLUETOOTH_WIDTH ICON_BLUETOOTH_HEIGHT
```

Default tint is white (`0xFFFFFF`); Lucide PNGs ship as black-on-transparent and would render invisible against the dark UI without it. Pass `--no-tint` for pre-coloured artwork like the logo. Battery icons use RGB565A8 (alpha plane) so they blend cleanly over the splash; the rest are baked RGB565 over the panel colour. Paste the converter output into `firmware/src/icons.h`.

## Splash animations

The animations come from [claudepix.vercel.app](https://claudepix.vercel.app), [@amaanbuilds](https://x.com/amaanbuilds)'s library of Clawd sprites. `tools/scrape_claudepix.js` evaluates the site's JavaScript in a Node VM to pull out frame data and palettes, then `tools/convert_to_c.js` turns everything into RGB565 C arrays and writes `firmware/src/splash_animations.h`. A third script bundles them for the web-flasher hero canvas.

To re-pull (e.g. when the source library updates):

```bash
node tools/scrape_claudepix.js
node tools/convert_to_c.js                 # firmware/src/splash_animations.h
node tools/build_web_animations.js         # docs/splash_animations.json
pio run -d firmware -t upload
```

See `tools/README.md` for details.

## credit

Argus is inspired from **[HermannBjorgvin/Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter)
