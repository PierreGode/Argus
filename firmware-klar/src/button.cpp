#include <Arduino.h>
#include "button.h"
#include "pins.h"

static const uint32_t DEBOUNCE_MS = 30;
static const uint32_t LONG_MS     = 600;

static bool     s_down       = false;
static uint32_t s_down_at    = 0;
static bool     s_long_fired = false;

void button_begin() {
    pinMode(BTN_PIN, INPUT_PULLUP);  // active low
}

ButtonEvent button_poll() {
    bool pressed = (digitalRead(BTN_PIN) == LOW);
    uint32_t now = millis();

    if (pressed && !s_down) {
        s_down = true;
        s_down_at = now;
        s_long_fired = false;
        return BTN_NONE;
    }

    if (pressed && s_down) {
        // Fire the long press as soon as the threshold is crossed, so holding
        // acknowledges "done" without waiting for release.
        if (!s_long_fired && (now - s_down_at) >= LONG_MS) {
            s_long_fired = true;
            return BTN_LONG;
        }
        return BTN_NONE;
    }

    if (!pressed && s_down) {
        s_down = false;
        uint32_t held = now - s_down_at;
        if (s_long_fired) return BTN_NONE;         // long already reported
        if (held >= DEBOUNCE_MS) return BTN_SHORT;  // debounced short press
    }
    return BTN_NONE;
}
