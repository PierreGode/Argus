#include "ota.h"
#include <Arduino.h>
#include <Update.h>

static bool     s_active = false;
static size_t   s_size = 0;
static size_t   s_received = 0;
static uint32_t s_last_data_ms = 0;

// No bytes for this long while an update is open → give up and resume normal
// operation (the daemon went away, or the cable/link dropped).
#define OTA_IDLE_TIMEOUT_MS 20000

bool ota_begin(size_t size, const char* md5_hex, const char** err) {
    if (s_active) Update.abort();
    s_active = false;
    if (size == 0) { *err = "empty image"; return false; }
    if (!md5_hex || strlen(md5_hex) != 32) { *err = "bad md5"; return false; }
    if (!Update.begin(size, U_FLASH)) { *err = Update.errorString(); return false; }
    if (!Update.setMD5(md5_hex)) { Update.abort(); *err = "bad md5"; return false; }
    s_size = size;
    s_received = 0;
    s_last_data_ms = millis();
    s_active = true;
    return true;
}

bool   ota_active(void)    { return s_active; }
size_t ota_remaining(void) { return s_active ? s_size - s_received : 0; }
size_t ota_received(void)  { return s_received; }

int ota_progress_pct(void) {
    if (!s_active || s_size == 0) return 0;
    return (int)((uint64_t)s_received * 100 / s_size);
}

int ota_feed(const uint8_t* data, size_t len, const char** err) {
    if (!s_active) { *err = "not started"; return -1; }
    if (len > s_size - s_received) len = s_size - s_received;
    if (len && Update.write((uint8_t*)data, len) != len) {
        *err = Update.errorString();
        Update.abort();
        s_active = false;
        return -1;
    }
    s_received += len;
    s_last_data_ms = millis();
    if (s_received < s_size) return 0;

    s_active = false;
    if (!Update.end(false)) {       // verifies the MD5 and marks the slot bootable
        *err = Update.errorString();
        return -1;
    }
    return 1;
}

bool ota_check_timeout(void) {
    if (!s_active) return false;
    if (millis() - s_last_data_ms < OTA_IDLE_TIMEOUT_MS) return false;
    ota_abort();
    return true;
}

void ota_abort(void) {
    if (s_active) Update.abort();
    s_active = false;
}
