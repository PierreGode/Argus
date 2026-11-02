// Shared lamp state (the parsed daemon state push) + mode enum.
#pragma once
#include <stdint.h>
#include <string.h>

enum Mode { MODE_WORK = 0, MODE_WAIT, MODE_DONE, MODE_REST };

struct LampState {
    char     repo[25] = "";
    uint8_t  mode     = MODE_REST;
    uint32_t tok      = 0;
    uint8_t  ctx      = 0;     // 0..100
    float    cost     = 0.0f;
    uint8_t  n        = 0;     // active session count
};

inline uint8_t mode_from_str(const char *m) {
    if (!m) return MODE_REST;
    if (!strcmp(m, "work")) return MODE_WORK;
    if (!strcmp(m, "wait")) return MODE_WAIT;
    if (!strcmp(m, "done")) return MODE_DONE;
    return MODE_REST;
}
