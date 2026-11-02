#include <Arduino.h>
#include "led.h"
#include "pins.h"

static void apa_write(uint8_t b) {
    for (int i = 0; i < 8; i++) {
        digitalWrite(LED_DATA, (b & 0x80) ? HIGH : LOW);
        digitalWrite(LED_CLK, HIGH);
        b <<= 1;
        digitalWrite(LED_CLK, LOW);
    }
}

void led_begin() {
    pinMode(LED_DATA, OUTPUT);
    pinMode(LED_CLK, OUTPUT);
    digitalWrite(LED_DATA, LOW);
    digitalWrite(LED_CLK, LOW);
    led_off();
}

void led_set(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness) {
    if (brightness > 31) brightness = 31;
    // Start frame: 32 bits of 0.
    for (int i = 0; i < 4; i++) apa_write(0x00);
    // LED frame: 111b + 5-bit global brightness, then B, G, R.
    apa_write(0xE0 | brightness);
    apa_write(b);
    apa_write(g);
    apa_write(r);
    // End frame: >= n/2 clock edges; 4 bytes is plenty for one LED.
    for (int i = 0; i < 4; i++) apa_write(0xFF);
}

void led_off() {
    led_set(0, 0, 0, 0);
}
