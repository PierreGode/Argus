# Klar-lampan firmware (LilyGO T-Dongle S3)

The "done lamp" — a second, standalone Argus daemon output that shows the live
state of each Claude Code session (working / waiting-for-me / done / resting).
See [`../PROTOCOL.md`](../PROTOCOL.md) for the wire protocol and the Claude Code
hook setup, and the Argus README for the daemon.

This is a **separate** PlatformIO project from `../firmware/` (the Argus device);
building or flashing one never touches the other.

## Build / flash

```bash
pio run -d firmware-klar                                       # build
pio run -d firmware-klar -t upload --upload-port /dev/ttyACM0  # flash
```

Or flash from the browser: the GitHub Pages site has a **Flash Klar-lampan**
button (esp-web-tools, no local toolchain needed).

The T-Dongle enumerates as a native USB CDC port (ESP32-S3 USB-Serial-JTAG), so
no driver is needed. On first plug it may appear in download mode — a normal
`-t upload` handles it.

## Mascot sprites

The animations use the shared Argus mascot. `src/klar_sprites.h` is **generated
and committed** (like Argus's `argus_sprites.h`); CI does not regenerate it. To
re-pack after changing the source PNGs in `assets/img/`:

```bash
cd tools && npm install            # once (sharp + pngjs)
node tools/build_klar_sprites.js   # -> firmware-klar/src/klar_sprites.h
```

## Hardware (pins in `src/pins.h`)

Verified against LilyGO's `Xinyuan-LilyGO/T-Dongle-S3`
`examples/factory_screen/factory_screen.ino`:

- **LCD** ST7735, 160×80 landscape — MOSI 3, SCLK 5, CS 4, DC 2, RST 1, backlight 38 (active LOW)
- **RGB LED** APA102 — data 40, clock 39
- **Button** BOOT 0 (short press → next session, long press → ack done)

## First-boot tuning

ST7735 0.96" panels have a column/row offset. The defaults in `pins.h`
(`LCD_COL_OFFSET 26`, `LCD_ROW_OFFSET 1`) suit most of these LilyGO units; if you
see a few-pixel gap or wrap at an edge, try `24` / `0`. Flip `LCD_ROTATION`
between `1` and `3` to rotate the image 180° (USB connector on the other side).

## Standalone test (no daemon)

Open the port in a serial monitor at 115200 and paste a state line, e.g.:

```
{"repo":"Argus","mode":"wait","tok":1840,"ctx":42,"cost":1.73,"n":2}
```

The screen and LED update immediately. Sending `ID?` returns `KLAR v1`; pressing
the button prints `BTN:NEXT` / `BTN:ACK`.
