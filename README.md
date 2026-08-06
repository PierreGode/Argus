
# Argus

A small ESP32 dashboard that sits on my desk and watches my dev workflow — Claude Code rate limits, GitHub issues and PRs, today's token spend — then auto-switches to whichever screen has news.

<table>
  <tr>
    <td><img width="400" alt="image" src="https://github.com/user-attachments/assets/68f12387-425f-4429-98ae-fcf08741885d" /></td>
    <td><img width="400" alt="image" src="https://github.com/user-attachments/assets/a811385c-829d-47f9-bbc1-4fb89a20e7e8" /></td>
  </tr>
  <tr>
    <td><img width="400" alt="image" src="https://github.com/user-attachments/assets/badafd92-f4e3-4a13-b16a-f82d13852c67" /></td>
    <td><img width="400" alt="image" src="https://github.com/user-attachments/assets/adbd3f8e-8bff-4b01-b44f-19330b06f81c" /></td>
  </tr>
</table>
<img width="500" height="480" alt="windows" src="https://github.com/user-attachments/assets/c9ece339-0eb7-459f-befb-8f420151e486" />


It runs on a [Waveshare ESP32-S3 Smart 86 Box Development Board]([https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm](https://www.waveshare.com/esp32-s3-touch-lcd-4b.htm?srsltid=AfmBOooN9vv19u7xrLtIeogvE-IVSSIeT_IpFkS2dTC-TlfwmNQxaFYp)), pairs with my laptop over Bluetooth (or USB-C), and the splash screen shows an animated mascot whose expression tracks how hard you're pushing your limits.

What Argus shows:

- **Usage** — Claude Code rate limits: the 5-hour session window and 7-day weekly window, with reset countdowns.
- **Today** — today's API-equivalent token cost, the Opus / Sonnet / Haiku / Fable split, cache hit rate and session count, parsed from your local Claude Code logs.
- **GitHub** — open issues assigned to you and PRs awaiting your review, fetched with a PAT (`github_stats.py`).
- **CI/CD** — GitHub Actions status across your recently-pushed repos: pass / fail / running / awaiting-approval, with auto-focus when a run fails or needs your sign-off (`ci_stats.py`).
- **Copilot** — GitHub Copilot seat status (active / idle, editor, last activity) and monthly AI-credit usage for your org or enterprise (`copilot_stats.py`).
- **Auto-focus** — the device jumps to the relevant screen when something changes (e.g. a new PR → GitHub). Manual navigation is preserved between events, so it never feels hostile.
- A **PySide6 tray app** (`tray_ui.py`) with a live log, settings window, and system-tray integration. Edits apply on the next poll (immediately on Save). You choose which screens cycle, toggle power-save, rename the device, and more. Windows is the primary target; macOS / Linux run from source.


## Quick start

> **Platform support:** Argus targets **Windows** — that's the build that's packaged, signed-ready, and tested. macOS and Linux are best-effort: the daemon runs, but there's no installer, so you [run it from source](#run-the-daemon-from-source).

> **Claude login (required for the Usage / Today screens):** install the **Claude desktop app** and sign in with an active subscription. Argus reuses that OAuth login — it reads (and auto-refreshes) the token from `~/.claude/.credentials.json` and parses your local conversation logs from `~/.claude/projects/`. No Anthropic API key needed.

1. **Flash the firmware**: open <https://pierregode.github.io/Argus/> in Chrome / Edge / Opera, plug the board in over USB-C, and click **Flash Argus**. (Prefer building from source? See [Build the firmware](#build-the-firmware-locally).)
2. **Install the daemon** (Windows): download **`Argus-Setup.msi`** from the same page — a proper installer that adds Argus to the Start menu and Apps & features (a portable `.exe` is also linked). On macOS / Linux, [run from source](#run-the-daemon-from-source).
3. **Run it**: it lives in the system tray. Right-click → **Show window** to enter a GitHub token, set brightness, pick BLE / USB-C transport, choose poll interval, enable power-save, or toggle Start with Windows. Settings are saved to `%APPDATA%\Argus\config.json` (or the platform-equivalent) and applied on the next send.

> **Windows install note:** the MSI is per-machine, so it asks for admin rights. These builds are **not signed with a commercial code-signing certificate**, so Windows shows an "unknown publisher" UAC prompt and SmartScreen may warn on first run — choose **More info → Run anyway**. Only a paid OV/EV certificate removes those warnings; a self-signed cert does not.

The device pairs the first time it sees the daemon; from then on it reconnects automatically.

## Screens

The device boots into the splash and stays there until you press the BOOT button, which cycles `Splash → Usage → Today → GitHub → CI → Copilot → Bluetooth`. Screens you've unchecked in the tray app are skipped. Tap the screen anywhere (except the Reset zone on the Bluetooth screen) to flip back to the splash; tap again to dismiss it.

**Usage** shows the 5-hour-window session utilization (`Current`) and the 7-day weekly utilization. Bars turn green / amber / red at 50% / 80%. Reset times count down in minutes/hours. A footer line shows your most recently active project, session count, and — only on days you've used it — Fable's share of today's tokens (local-machine-only, same caveat as the Today screen below).

**Today** shows the API-equivalent cost of today's tokens (labeled "API equiv." — on a Max subscription you don't pay this, but it shows how much the subscription is saving you), the 7-day rolling cost, the Opus / Sonnet / Haiku / Fable token split, cache hit rate, most recently active project, and sessions started today. All of it is parsed from `~/.claude/projects/**/*.jsonl` by the daemon, so it works even when the API is down — but it also means these numbers only cover Claude Code sessions run *on the machine the daemon is running on*. If you also use Claude Code (or Fable) from another machine, its usage won't show up here — only the Usage screen's rate-limit bars above are account-wide.

**GitHub** shows open issues assigned to you and open PRs awaiting your review (or assigned to you). Requires a GitHub PAT in the daemon's tray settings (Issues + Pull requests read scopes). Refreshes every 5 minutes. With no token configured the panels show `No data` and a hint.

**CI/CD** shows the latest GitHub Actions run across your most recently-pushed repos — a headline status (Passing / Failing / Running / Action needed for an environment approval), plus counts of failing and approval-waiting runs. Enable it (and its per-event auto-focus rules) in the GitHub tab; it reuses the same PAT, which needs the **Actions: read** scope.

**Copilot** shows your GitHub Copilot seat status (active / idle / inactive, the editor you last used it in, and how long ago) plus this month's AI-credit usage against the configured allowance. Requires a PAT with Copilot org/enterprise read access and the org (or enterprise) slug set in the tray app; otherwise it shows `No data`.

On the splash, the mascot's expression changes on its own to match your current usage rate, or locks to the "mood" the daemon sends when something needs attention (a maxed-out cap, a fresh PR). A strip below the mascot rotates through recent events — hot rate-limit windows with reset countdowns, new PRs/issues, and Copilot credit burn.

## Auto-focus

Argus tracks event counters between polls. When something noteworthy changes — a new GitHub PR or issue assigned to you, or a CI run that just failed or now needs an environment approval — the daemon adds an `"fc"` target (e.g. `"github"` or `"ci"`) to that single payload and the firmware switches to that screen. The CI triggers are individually toggleable in the tray app.

Behavioral rules:

- The first poll after a daemon restart never triggers a focus (no baseline to compare against, so no spurious switch on reboot).
- Manual navigation is preserved: if you press the BOOT button to move elsewhere, subsequent "no change" polls leave you alone.
- A further event fires another switch — the daemon only sends `fc` on the poll where the delta is detected, not continuously.

The mechanism generalizes to other triggers (rate-limit threshold crossings, etc.) by adding more entries to `_detect_focus()` in `argus-daemon.py`. The supported `fc` values are `splash`, `usage`, `today`, `github`, `ci`, `copilot`, `bluetooth`.

## Hardware

- [Waveshare ESP32-S3 Smart 86 Box]([https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm?&aff_id=149786](https://www.waveshare.com/esp32-s3-touch-lcd-4b.htm)) — Waveshare ESP32-S3 Smart 86 Box Development Board.
- USB-C cable for flashing and charging.
- 3.7V Li-Po battery (MX1.25 2-pin connector, optional).

## Daemon

The daemon polls Anthropic's rate-limit headers, parses local Claude Code conversation logs, polls GitHub for issue/PR counts and Copilot for seat + AI-credit usage, then ships a single JSON payload to the device. Two transports:

| Transport          | When to use            | How to start                                                                                              |
| ------------------ | ---------------------- | --------------------------------------------------------------------------------------------------------- |
| **BLE** (default)  | Wireless, no cable     | `argus-daemon`                                                                                            |
| **USB-C serial**   | Plugged in, no pairing | `argus-daemon --serial` (auto-detects ESP32-S3) or `--serial COM3` to force a specific port               |
| **Demo mode**      | Test the UI            | `--demo` flag — sends randomized payloads, no API key required                                            |
| **Headless**       | systemd / no display   | `--headless` — skip the tray app, run worker on the main thread                                           |

The daemon checks the connection every 2 seconds and reconnects fast if you unplug, walk out of BLE range, or restart the board.

The tray app is the way to run it on Windows. Closing the window hides to tray; right-click the tray icon → Quit to actually exit. The window shows live log output, the current connection status, and exposes every setting: GitHub token (encrypted at rest), Copilot org/enterprise + allowance, CI/CD auto-focus rules, which screens cycle and which Today panels show, brightness, power-save, device rename, transport, poll interval, and Start with Windows.

## Prerequisites

- **Windows** (primary, packaged). macOS / Linux work from source but aren't packaged or regularly tested.
- The **Claude desktop app**, installed and signed in with an active subscription — Argus reads its OAuth token from `~/.claude/.credentials.json`. No Anthropic API key.
- A **GitHub PAT** (optional) for the GitHub / CI / Copilot screens.
- Python 3.11+ with `pip` (only if running the daemon from source).
- [PlatformIO CLI](https://docs.platformio.org/en/latest/core/installation/index.html) (only if building firmware from source).

## Build the firmware locally

```bash
cd firmware
pio run -t upload
```

PlatformIO auto-detects the USB port. On Windows, the COM number can change between plug-ins — `pio device list` will show what to expect.

## Run the daemon from source

This is how you run it on **macOS / Linux** (no packaged installer there), and how to hack on the daemon on any OS:

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

The MAC address is shown on the Bluetooth screen — press the BOOT button to cycle to it.

## Controls

| Input                | Action                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------- |
| **BOOT button** (GPIO 0) | Cycle screens: `Splash → Usage → Today → GitHub → CI → Copilot → Bluetooth` (skips screens disabled in the tray app). Also wakes the screen from power-save. |
| **Tap the screen**   | Flip to the splash and back; tap the Reset zone on the Bluetooth screen to clear BLE bonds |

> The firmware also exposes a BLE HID keyboard service (intended for sending Claude Code shortcuts like Space and Shift+Tab), but the current build doesn't bind it to a button.

## Wire protocol

Both BLE and USB-C carry the same JSON payload, newline-terminated. Over BLE it's streamed to the RX characteristic in ≤240-byte chunks and reassembled on the device (so it isn't capped at the 512-octet single-attribute limit); over USB-C it's one line at 115200 baud.

### BLE characteristics

|                            | UUID                                   |
| -------------------------- | -------------------------------------- |
| **Data Service**           | `4c41555a-4465-7669-6365-000000000001` |
| RX Characteristic (write)  | `4c41555a-4465-7669-6365-000000000002` |
| TX Characteristic (notify) | `4c41555a-4465-7669-6365-000000000003` |
| REQ Characteristic (notify)| `4c41555a-4465-7669-6365-000000000004` |
| **HID Service**            | `00001812-0000-1000-8000-00805f9b34fb` |

### Payload

Keys are short to keep the payload small. It's streamed to the device in newline-terminated chunks and reassembled on the firmware, so it isn't bound by the 512-octet single-write BLE limit. The firmware ignores keys it doesn't recognize, so the two sides can version independently.

```json
{
  "s": 45, "sr": 120, "w": 28, "wr": 7200, "st": "allowed", "ok": true,
  "c": 3.47, "cw": 12.30, "mo": 45, "ms": 40, "mh": 5, "mf": 10,
  "mow": 38, "msw": 42, "mhw": 8, "mfw": 12,
  "ch": 82, "tk": 234567, "se": 3, "pj": "argus",
  "ge": true, "gi": 4, "gp": 2,
  "cie": true, "cis": "fail", "cir": "argus", "cib": "main", "ciw": "build", "cif": 1, "ciq": 0,
  "cp": true, "cps": "active", "cpw": "5 min ago", "cpe": "VS Code",
  "cpr": true, "cpp": 60.4, "cpu": 1812, "cpa": 3000, "cpsu": "org", "cpm": "GPT-5.4",
  "apps": "usage,today,github,copilot", "tac": true, "tch": true,
  "md": "flirt", "evts": ["Claude weekly: 87% - resets in 2d", "New PR - awaiting review (total 2)"],
  "br": 80, "fc": "github", "nm": "Argus Controller",
  "ps": true, "pst": 360, "chg": false
}
```

| Key | Meaning |
| --- | --- |
| `s` / `sr` | session % (5-hour window) / minutes until it resets |
| `w` / `wr` | weekly % / minutes until it resets |
| `st` / `ok` | rate-limit status / poll succeeded |
| `c` / `cw` | USD spent today / in the last 7 days (API-equivalent) |
| `mo` / `ms` / `mh` / `mf` | Opus / Sonnet / Haiku / Fable token share today, % (all four are local-machine-only — see note below) |
| `mow` / `msw` / `mhw` / `mfw` | Opus / Sonnet / Haiku / Fable token share this week (rolling 7 days), % — same local-only computation as `mo`/`ms`/`mh`/`mf`, just windowed weekly; `mfw` is the "Fable weekly %" bar shown on the Today screen and in `claude /usage` |
| `ch` / `tk` / `se` | cache hit rate % / tokens today / distinct sessions today |
| `pj` | most recently active project (basename) |
| `ge` / `gi` / `gp` | GitHub enabled / open issues assigned / open PRs awaiting you |
| `cie` / `cis` | CI enabled / headline run status (`ok`/`fail`/`run`/`wait`/`none`) |
| `cir` / `cib` / `ciw` | CI headline run repo / branch / workflow name |
| `cif` / `ciq` | CI runs failing / waiting on approval (across watched repos) |
| `cp` / `cps` / `cpw` / `cpe` | Copilot enabled / seat status / last-activity / editor |
| `cpr` / `cpp` / `cpu` / `cpa` | AI-credit data present / used % / credits used / monthly allowance |
| `cpsu` / `cpm` | AI-credit scope (`org`/`enterprise`) / top model name |
| `apps` | CSV of screens to show in the device's cycle |
| `tac` / `tch` | Today screen: show the API-equivalent cost panel / the cache-hit panel (model split always shows) |
| `md` / `evts` | splash mascot mood / rotating events strip (array of strings) |
| `br` | display brightness, 10–100 |
| `fc` | auto-focus target — `splash`/`usage`/`today`/`github`/`ci`/`copilot`/`bluetooth`; present only on the poll where a change was detected |
| `nm` | desired BLE device name — the device persists it to NVS and re-advertises under it; lets multiple Argus units coexist without name collisions |
| `ps` / `pst` / `chg` | power-save enabled / idle timeout (s, poll-aware) / "substantive change this poll" wake flag — the device blanks the LCD (AXP2101 DLDO1) after `pst` idle, waking on `chg`, touch, or button |

## Web flasher build pipeline

`.github/workflows/deploy-flasher.yml` runs on every push to `main`:

1. **Build the firmware** with PlatformIO, merge bootloader + partitions + app into a single offset-0 image with `esptool merge_bin`.
2. **Build the daemon** in parallel on Windows, macOS, and Linux runners via PyInstaller, driven by [`daemon/argus-daemon.spec`](daemon/argus-daemon.spec) (embeds the mascot icon + Windows version metadata, bundles assets, windowed on Win/macOS, console on Linux). On Windows it then builds a per-machine **`Argus-Setup.msi`** with the [WiX Toolset](https://wixtoolset.org/) (installed as a `dotnet tool`).
3. **Deploy to GitHub Pages** — the firmware bin, the Windows installer + portable exe, the macOS / Linux binaries, the splash animations, and the flasher HTML all ship together.

The page uses [esp-web-tools](https://esphome.github.io/esp-web-tools/) for the Web Serial flash flow.

### Building the Windows installer locally

The whole EXE + MSI build is one script (run on Windows, PowerShell 7):

```powershell
# from the repo root
pwsh daemon/packaging/windows/build-msi.ps1
# -> dist/argus-daemon.exe  and  dist/Argus-Setup.msi
```

It generates `assets/argus.ico` from the mascot if missing (needs Pillow), builds the EXE from the spec, installs the WiX `dotnet tool` on first run, then emits the MSI. CI uses Python 3.12; if PySide6 lacks wheels for your local Python, pass `-Python "py -3.12"`. The build is **cert-ready**: set `$env:ARGUS_SIGN_CERT` (and `$env:ARGUS_SIGN_PASS`) to a `.pfx` to Authenticode-sign the exe + msi — but without a CA-trusted cert this does not remove the SmartScreen / "unknown publisher" warnings.

Version comes from [`daemon/version.py`](daemon/version.py) (single source of truth, also exposed via `argus-daemon --version`).

## Recompiling fonts

`firmware/src/font_*.c` are LVGL 9 bitmap fonts compiled from the typefaces in `assets/`:

- **Tiempos Text** (titles) — 34, 56 px
- **Styrene B** (numbers, labels, body) — 12, 14, 16, 20, 24, 28, 48 px
- **DejaVu Sans Mono** (mono + spinner) — 18, 32 px

Install the converter and generate each size with `--no-compress` (required for LVGL 9), one invocation at a time:

```bash
npm install -g lv_font_conv

lv_font_conv --font assets/StyreneB-Regular.otf -r 0x20-0x7E \
  --size 24 --format lvgl --bpp 4 --no-compress \
  -o firmware/src/font_styrene_24.c --lv-include "lvgl.h"
```

The mono font also carries spinner glyphs — append `,0xB7,0x2026,0x2722,0x2733,0x2736,0x273B,0x273D` to its range.

**LVGL 9 patch (required):** `lv_font_conv` emits LVGL 8 output, which renders invisible until patched. Remove the `#if LVGL_VERSION_MAJOR >= 8` guards and the `.cache` field, then add `.release_glyph = NULL`, `.kerning = 0`, `.static_bitmap = 0`, `.fallback = NULL`, `.user_data = NULL` to the font struct.

## Splash assets

The device's mascot expressions are generated from `assets/img/*.png`:

```bash
node tools/build_argus_sprites.js   # → firmware/src/argus_sprites.h + docs/img/sprite_*.png
pio run -d firmware -t upload
```

The animated mascot on the web flasher page is built separately:

```bash
node tools/build_web_animations.js  # → docs/splash_animations.json
```

See [tools/README.md](tools/README.md) for details.
