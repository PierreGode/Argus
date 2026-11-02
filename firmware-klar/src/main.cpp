// Klar-lampan firmware — LilyGO T-Dongle S3.
//
// USB CDC line protocol (see PROTOCOL.md):
//   daemon -> lamp:  "ID?"              -> we reply "KLAR v1"
//                    "{...json...}"     -> state push; we render it, reply "OK"
//   lamp -> daemon:  "BTN:NEXT" (short press), "BTN:ACK" (long press)
//
// Step A: static per-mode screen + LED. Step B adds animations.
#include <Arduino.h>
#include <ArduinoJson.h>

#include "pins.h"
#include "display.h"
#include "led.h"
#include "button.h"
#include "anim.h"
#include "klar_state.h"

static LampState g_state;

// Line reassembly. The daemon streams in <=48-byte chunks terminated by '\n';
// accumulate until the newline (same approach as the Argus serial path).
#define RX_BUF 512
static char   rxbuf[RX_BUF];
static size_t rxpos = 0;

static void handle_line(const char *line) {
    if (line[0] == '\0') return;

    if (!strcmp(line, "ID?")) {
        Serial.println("KLAR v1");     // handshake: identify as the lamp
        return;
    }
    if (line[0] == '{') {
        JsonDocument doc;
        if (deserializeJson(doc, line)) {   // parse error
            Serial.println("ERR");
            return;
        }
        strlcpy(g_state.repo, doc["repo"] | "", sizeof(g_state.repo));
        g_state.mode = mode_from_str(doc["mode"] | "rest");
        g_state.tok  = doc["tok"].as<uint32_t>();
        g_state.ctx  = (uint8_t)(doc["ctx"] | 0);
        g_state.cost = doc["cost"] | 0.0f;
        g_state.n    = (uint8_t)(doc["n"] | 0);
        bool fan     = ((int)(doc["fan"] | 0)) != 0;   // one-shot fanfare trigger

        anim_set_state(g_state, fan);
        Serial.println("OK");
        return;
    }
    // Unknown line — ignore (forward-compatible).
}

static void read_serial() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            rxbuf[rxpos] = '\0';
            if (rxpos > 0) handle_line(rxbuf);
            rxpos = 0;
        } else if (rxpos < RX_BUF - 1) {
            rxbuf[rxpos++] = c;
        } else {
            rxpos = 0;   // overflow — drop the line
        }
    }
}

void setup() {
    Serial.begin(115200);
    Serial.setRxBufferSize(2048);     // headroom for the state line (issue-19 lesson)

    display_begin();
    led_begin();
    button_begin();

    anim_begin();
    anim_boot();

    // Announce ourselves so a daemon that opened the port before probing can
    // classify us immediately.
    Serial.println("KLAR v1");
}

void loop() {
    read_serial();

    ButtonEvent e = button_poll();
    if (e == BTN_SHORT)      Serial.println("BTN:NEXT");
    else if (e == BTN_LONG)  Serial.println("BTN:ACK");

    anim_tick();   // self-paces to ~30 fps
    delay(2);
}
