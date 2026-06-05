# Splash & sprite tools

Generators for the mascot art shown on the device and on the web flasher page.

## Device mascot sprites — `build_argus_sprites.js`

```bash
node build_argus_sprites.js
```

Resizes the expression PNGs in `assets/img/` and emits two artifacts:

- `firmware/src/argus_sprites.h` — RGB565A8 blobs (planar: `w*h` RGB565 pixels then `w*h` alpha bytes), 240×240 each. The firmware feeds these to `lv_image` and upscales 2× to fill the 480×480 panel.
- `docs/img/sprite_*.png` — the same downscaled PNGs for the GitHub Pages hero.

**This is what drives the splash on the device.**

## Web flasher animation — `build_web_animations.js`

```bash
node build_web_animations.js
```

Compacts the per-animation source JSONs in `claudepix_data/*.json` into a single `docs/splash_animations.json` that the web flasher page plays. Frames are encoded as 400-character strings of digits `0`..`9` indexing each animation's palette.

## Legacy: claudepix scrape/convert

`scrape_claudepix.js` fetches 20×20 pixel-art animation data from a public site into `claudepix_data/*.json`; `convert_to_c.js` turns it into `firmware/src/splash_animations.h`. The firmware no longer uses these — the splash is sprite-based. `claudepix_data/` is kept only as the source for `build_web_animations.js`.

## License note

The scraper hits a public site without a stated license. Confirm reuse is appropriate for your case before redistributing the output.
