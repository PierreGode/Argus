#!/usr/bin/env node
/**
 * Resizes the Argus mascot expression PNGs in `assets/img/` to a
 * firmware-friendly size, then emits two artifacts:
 *
 *   1. firmware/src/argus_sprites.h
 *      RGB565A8 binary blobs (planar: w*h RGB565 then w*h alpha bytes)
 *      for each expression — what LVGL's lv_image_dsc_t expects.
 *
 *   2. docs/img/sprite_<name>.png
 *      The same downscaled PNGs, served by the GitHub Pages hero.
 *
 * The sprite is drawn at 240x240 native and the firmware uses
 * lv_image_set_scale(img, 512) to upscale 2x to 480x480 on the panel,
 * so the in-flash footprint is one-quarter of the panel resolution.
 *
 * Usage:
 *   node tools/build_argus_sprites.js
 */

const fs = require("node:fs");
const path = require("node:path");
const sharp = require("sharp");
const { PNG } = require("pngjs");

const SRC_DIR = path.resolve(__dirname, "..", "assets", "img");
const OUT_FW = path.resolve(__dirname, "..", "firmware", "src", "argus_sprites.h");
const OUT_WEB_DIR = path.resolve(__dirname, "..", "docs", "img");

const TARGET = 240;  // panel is 480x480; firmware scales 2x

// Expression name → source PNG → identifier suffix.
// Adding a new face = drop a *.png into assets/img/ and append here. The
// firmware's resolve_groups() will pick it up if you map it into a group.
const SPRITES = [
  { name: "happy",     file: "happy.png" },
  { name: "looking",   file: "looking.png" },
  { name: "flirt",     file: "flirt.png" },
  { name: "buffeld",   file: "buffeld.png" },
  { name: "surprised", file: "surprised.png" },
  { name: "angry",     file: "angry.png" },
];

function rgb565(r, g, b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

// The source PNGs are flat 3-channel images on a near-white background
// (no alpha), so we floodfill from the four corners and erase every
// connected near-white pixel. Whites that are part of the character
// (helmet highlights, the dot in the eyes) are surrounded by dark navy
// and never reached by the floodfill — they stay opaque.
function keyOutBackground(rgba, w, h, threshold = 220) {
  const total = w * h;
  const visited = new Uint8Array(total);
  const stack = [];
  const tryPush = (x, y) => {
    if (x < 0 || x >= w || y < 0 || y >= h) return;
    const idx = y * w + x;
    if (visited[idx]) return;
    const o = idx * 4;
    if (Math.min(rgba[o], rgba[o + 1], rgba[o + 2]) < threshold) return;
    visited[idx] = 1;
    stack.push(x, y);
  };
  tryPush(0, 0);
  tryPush(w - 1, 0);
  tryPush(0, h - 1);
  tryPush(w - 1, h - 1);
  while (stack.length) {
    const y = stack.pop();
    const x = stack.pop();
    tryPush(x + 1, y);
    tryPush(x - 1, y);
    tryPush(x, y + 1);
    tryPush(x, y - 1);
  }
  for (let i = 0; i < total; i++) {
    if (visited[i]) rgba[i * 4 + 3] = 0;
  }
}

async function resizeToBuffer(srcPath) {
  // Pull raw RGBA at the target resolution so we can chroma-key the
  // background before re-encoding. `ensureAlpha()` adds an opaque alpha
  // channel when the source has none (which is the common case here).
  const { data, info } = await sharp(srcPath)
    .resize(TARGET, TARGET, {
      fit: "contain",
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });

  keyOutBackground(data, info.width, info.height);

  // Re-encode to PNG (preserves the new alpha channel) so the file we drop
  // into docs/img is web-ready.
  return await sharp(data, {
    raw: { width: info.width, height: info.height, channels: 4 },
  })
    .png()
    .toBuffer();
}

function pngBufferToRgb565a8(buf) {
  const png = PNG.sync.read(buf);
  const w = png.width, h = png.height;
  if (w !== TARGET || h !== TARGET) {
    throw new Error(`expected ${TARGET}x${TARGET}, got ${w}x${h}`);
  }
  const total = w * h;
  const color = Buffer.alloc(total * 2);
  const alpha = Buffer.alloc(total);
  for (let i = 0; i < total; i++) {
    const r = png.data[i * 4 + 0];
    const g = png.data[i * 4 + 1];
    const b = png.data[i * 4 + 2];
    const a = png.data[i * 4 + 3];
    const c = rgb565(r, g, b);
    color[i * 2 + 0] = c & 0xFF;
    color[i * 2 + 1] = (c >> 8) & 0xFF;
    alpha[i] = a;
  }
  return Buffer.concat([color, alpha]);
}

function emitArray(name, blob) {
  const total = blob.length;
  const symbol = `argus_sprite_${name}_data`;
  let out = `static const uint8_t ${symbol}[${total}] = {\n    `;
  const lines = [];
  for (let i = 0; i < total; i += 16) {
    const row = [];
    for (let j = 0; j < 16 && i + j < total; j++) {
      row.push("0x" + blob[i + j].toString(16).padStart(2, "0").toUpperCase());
    }
    lines.push(row.join(", "));
  }
  out += lines.join(",\n    ");
  out += "\n};\n";
  return { out, symbol, total };
}

async function main() {
  if (!fs.existsSync(SRC_DIR)) {
    throw new Error(`Missing source dir ${SRC_DIR}`);
  }
  fs.mkdirSync(OUT_WEB_DIR, { recursive: true });

  let header = "";
  header += "// =====================================================================\n";
  header += "// Generated by tools/build_argus_sprites.js. Do not edit by hand.\n";
  header += `// ${SPRITES.length} expression sprites at ${TARGET}x${TARGET}, RGB565A8.\n`;
  header += "// Layout per blob: w*h RGB565 pixels (little-endian) followed by w*h\n";
  header += "// alpha bytes. The firmware feeds each into init_icon_dsc_rgb565a8()\n";
  header += "// and renders via lv_image with a 2x scale to fill the 480x480 panel.\n";
  header += "// =====================================================================\n";
  header += "#pragma once\n#include <stdint.h>\n\n";
  header += `#define ARGUS_SPRITE_W ${TARGET}\n`;
  header += `#define ARGUS_SPRITE_H ${TARGET}\n\n`;

  const entries = [];
  for (const sp of SPRITES) {
    const srcPath = path.join(SRC_DIR, sp.file);
    if (!fs.existsSync(srcPath)) {
      console.warn(`SKIP ${sp.name}: missing ${srcPath}`);
      continue;
    }
    const resized = await resizeToBuffer(srcPath);
    const blob = pngBufferToRgb565a8(resized);
    const { out, symbol, total } = emitArray(sp.name, blob);
    header += out + "\n";
    entries.push({ name: sp.name, symbol, total });

    // Also copy a 240x240 PNG to docs/img for the web hero.
    fs.writeFileSync(path.join(OUT_WEB_DIR, `sprite_${sp.name}.png`), resized);
    console.log(`packed ${sp.name}  ${(total / 1024).toFixed(1)} KB  (${srcPath})`);
  }

  // Index struct so the firmware can walk sprites by name.
  header += "typedef struct {\n";
  header += "    const char *name;\n";
  header += "    const uint8_t *data;\n";
  header += "    uint32_t size;\n";
  header += "} argus_sprite_def_t;\n\n";
  header += `#define ARGUS_SPRITE_COUNT ${entries.length}\n`;
  header += "static const argus_sprite_def_t argus_sprites[ARGUS_SPRITE_COUNT] = {\n";
  for (const e of entries) {
    header += `    {"${e.name}", ${e.symbol}, ${e.total}},\n`;
  }
  header += "};\n";

  fs.writeFileSync(OUT_FW, header);
  console.log(`\nWrote ${OUT_FW}  (${(header.length / 1024).toFixed(1)} KB source)`);
  console.log(`Wrote ${OUT_WEB_DIR}/sprite_*.png`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
