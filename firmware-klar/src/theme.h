// Colors + small formatting helpers for the lamp UI. Argus brand palette:
// dark navy + gold, with per-mode accent colors.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include "klar_state.h"

#define C565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

static const uint16_t COL_BG    = C565(13, 18, 32);     // navy background
static const uint16_t COL_HEAD  = C565(26, 34, 58);
static const uint16_t COL_GOLD  = C565(232, 178, 58);
static const uint16_t COL_WHITE = 0xFFFF;
static const uint16_t COL_GREY  = C565(120, 130, 150);
static const uint16_t COL_DIM   = C565(60, 68, 86);
static const uint16_t COL_BLUE  = C565(58, 134, 255);
static const uint16_t COL_YEL   = C565(255, 210, 58);
static const uint16_t COL_GREEN = C565(53, 199, 89);
static const uint16_t COL_RED   = C565(230, 70, 70);

static inline uint16_t mode_color(uint8_t m) {
    switch (m) {
        case MODE_WORK: return COL_BLUE;
        case MODE_WAIT: return COL_YEL;
        case MODE_DONE: return COL_GREEN;
        default:        return COL_GREY;
    }
}

static inline const char *mode_label(uint8_t m) {
    switch (m) {
        case MODE_WORK: return "WORK";
        case MODE_WAIT: return "WAIT";
        case MODE_DONE: return "DONE";
        default:        return "IDLE";
    }
}

// Which mascot face sprite a mode uses.
static inline const char *mode_face(uint8_t m) {
    switch (m) {
        case MODE_WORK: return "looking";    // focused / analyzing
        case MODE_WAIT: return "surprised";  // looking out, needs me
        case MODE_DONE: return "happy";      // pleased
        default:        return "happy";      // calm (dimmed) at rest
    }
}

static inline void fmt_tokens(uint32_t t, char *buf, size_t n) {
    if (t >= 1000000)   snprintf(buf, n, "%.1fM", t / 1000000.0);
    else if (t >= 1000) snprintf(buf, n, "%.1fk", t / 1000.0);
    else                snprintf(buf, n, "%u", (unsigned)t);
}
