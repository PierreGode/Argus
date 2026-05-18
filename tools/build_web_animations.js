#!/usr/bin/env node
// Compact the per-animation source JSONs from `tools/claudepix_data/*.json`
// into a single `docs/splash_animations.json` that the web flasher page loads.
//
// Frames are encoded as 400-character strings of digits "0".."9" indexing
// into the palette, which compresses better than nested arrays. The web
// renderer treats palette entry "transparent" as the page background.
//
// Run once after re-scraping or whenever palettes change:
//   node tools/build_web_animations.js

const fs = require("node:fs");
const path = require("node:path");

const SRC_DIR = path.join(__dirname, "claudepix_data");
const OUT_FILE = path.join(__dirname, "..", "docs", "splash_animations.json");

function loadAnim(file) {
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    const frames = data.frames.map((fr) => ({
        h: fr.hold,
        // Flatten 20x20 → 400-char string of digits.
        g: fr.grid.flat().join(""),
    }));
    return {
        name: data.name,
        category: data.category,
        palette: data.palette,
        frames,
    };
}

const out = [];
for (const entry of fs.readdirSync(SRC_DIR)) {
    if (entry === "_index.json" || !entry.endsWith(".json")) continue;
    out.push(loadAnim(path.join(SRC_DIR, entry)));
}

fs.mkdirSync(path.dirname(OUT_FILE), { recursive: true });
fs.writeFileSync(OUT_FILE, JSON.stringify(out));
console.log(`wrote ${out.length} animations to ${OUT_FILE} (${fs.statSync(OUT_FILE).size} bytes)`);
