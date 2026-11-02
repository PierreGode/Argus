// BOOT-button short/long press detection (active low, debounced).
#pragma once

enum ButtonEvent { BTN_NONE, BTN_SHORT, BTN_LONG };

void button_begin();
// Call every loop; returns a one-shot event when a press completes (short) or
// crosses the long-press threshold (long).
ButtonEvent button_poll();
