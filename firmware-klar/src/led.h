// APA102 single-pixel RGB LED driver (bit-banged, no extra library).
#pragma once
#include <stdint.h>

void led_begin();
// r/g/b are 0..255; brightness is the APA102 global 0..31.
void led_set(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness = 8);
void led_off();
