#include <Arduino.h>
#include <lvgl.h>
#include <ArduinoJson.h>
#include "display_cfg.h"
#include "data.h"
#include "ui.h"
#include "ble.h"
#include "splash.h"
#include "touch.h"
#include "usage_rate.h"
#include "power.h"

// Physical buttons (global, screen-independent):
//   BTN_BACK   (GPIO 0)  — left,  send Space (Claude Code voice mode push-to-talk)
//   BTN_FWD    (GPIO 18) — right, send Shift+Tab (Claude Code mode toggle)
#define BTN_BACK 0

// ---- Hardware objects (ST7701 RGB panel via TCA9554 expander) ----
static Arduino_XCA9554SWSPI *expander = new Arduino_XCA9554SWSPI(
    7 /*SPI_MOSI*/, 0 /*SPI_SCK*/, 2 /*SPI_CS*/, 1 /*SPI_DC*/, &Wire, EXPANDER_ADDR);

static Arduino_ESP32RGBPanel *rgbpanel = new Arduino_ESP32RGBPanel(
    17 /*DE*/, 3 /*VSYNC*/, 46 /*HSYNC*/, 9 /*PCLK*/,
    10 /*B0*/, 11 /*B1*/, 12 /*B2*/, 13 /*B3*/, 14 /*B4*/,
    21 /*G0*/, 8 /*G1*/, 18 /*G2*/, 45 /*G3*/, 38 /*G4*/, 39 /*G5*/,
    40 /*R0*/, 41 /*R1*/, 42 /*R2*/, 2 /*R3*/, 1 /*R4*/,
    1, 10, 8, 50,   // hsync timing (polarity, fp, pw, bp)
    1, 10, 8, 20,   // vsync timing (polarity, fp, pw, bp)
    // Bounce-buffer mode: LCD peripheral DMAs from a small SRAM ring buffer
    // that the CPU refills from PSRAM. Eliminates the horizontal "row chunk"
    // tearing you get when the panel reads PSRAM while LVGL is writing it.
    // Size = 10 scan lines (480*10 = 4800 px = 9600 bytes). Stays inside
    // internal SRAM and gives the driver enough lead time over PSRAM latency.
    0 /*pclk_active_neg*/, GFX_NOT_DEFINED /*prefer_speed*/, false /*useBigEndian*/,
    0 /*de_idle_high*/, 0 /*pclk_idle_high*/,
    480 * 10 /*bounce_buffer_size_px*/);

Arduino_RGB_Display *gfx = new Arduino_RGB_Display(
    480, 480, rgbpanel, 0, true,
    expander, GFX_NOT_DEFINED,
    st7701_type1_init_operations, sizeof(st7701_type1_init_operations));

static UsageData usage = {};

// GT911 reset wiring assumption — Waveshare boards route GT911_RST through the
// TCA9554. Pin 1 is the standard pick; if touch_init fails, try another pin.
// Note: TCA9554 pin 1 is also wired to the ST7701 SPI_DC line, but DC is only
// toggled during display init (st7701_type1_init_operations) and is idle after
// gfx->begin() returns, so it's safe to re-purpose the pin for GT911 reset here.
#define GT911_RST_EXPANDER_PIN 1

// ---- LVGL draw buffers (PSRAM-backed, partial render) ----
#define BUF_LINES 40
static uint16_t *buf1 = nullptr;
static uint16_t *buf2 = nullptr;

// LVGL tick callback
static uint32_t my_tick(void) {
    return millis();
}

// LVGL flush callback — writes partial strips to the RGB panel
static void my_flush_cb(lv_display_t* disp, const lv_area_t* area, uint8_t* px_map) {
    int32_t w = area->x2 - area->x1 + 1;
    int32_t h = area->y2 - area->y1 + 1;
    gfx->draw16bitRGBBitmap(area->x1, area->y1, (uint16_t*)px_map, w, h);
    lv_display_flush_ready(disp);
}

// ---- Power-save state ----
// g_display_on tracks the backlight; g_last_activity_ms is the last time we saw
// a meaningful change / touch / button. note_activity() resets the timer and
// wakes the screen if it was blanked. Defined here so my_touch_cb can swallow
// touches that occur while the screen is off (the touch only wakes it).
static bool     g_display_on = true;
static uint32_t g_last_activity_ms = 0;

static void note_activity() {
    g_last_activity_ms = millis();
    if (!g_display_on) {
        power_set_backlight(true);
        g_display_on = true;
    }
}

// LVGL touch callback — reads from the GT911 driver state updated by touch_poll().
static void my_touch_cb(lv_indev_t* indev, lv_indev_data_t* data) {
    // While the screen is blanked, ignore touches here — the first touch only
    // wakes the display (handled in loop()), it must not also cycle a screen.
    if (g_display_on && touch_is_pressed()) {
        data->point.x = touch_get_x();
        data->point.y = touch_get_y();
        data->state = LV_INDEV_STATE_PRESSED;
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}

// Parse a JSON line into UsageData
static bool parse_json(const char* json, UsageData* out) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, json);
    if (err) {
        Serial.printf("JSON parse error: %s\n", err.c_str());
        return false;
    }

    out->session_pct = doc["s"] | 0.0f;
    out->session_reset_mins = doc["sr"] | -1;
    out->weekly_pct = doc["w"] | 0.0f;
    out->weekly_reset_mins = doc["wr"] | -1;
    strlcpy(out->status, doc["st"] | "unknown", sizeof(out->status));
    out->ok = doc["ok"] | false;

    // Today-page fields. All optional — back-compat with older daemons that
    // only send the rate-limit keys.
    out->cost_today     = doc["c"]  | 0.0f;
    out->cost_week      = doc["cw"] | 0.0f;
    out->opus_pct       = doc["mo"] | 0;
    out->sonnet_pct     = doc["ms"] | 0;
    out->haiku_pct      = doc["mh"] | 0;
    out->fable_pct      = doc["mf"] | 0;
    out->cache_hit_pct  = doc["ch"] | 0;
    out->tokens_today   = doc["tk"] | 0;
    out->sessions_today = doc["se"] | 0;
    strlcpy(out->project, doc["pj"] | "", sizeof(out->project));

    // Today-screen element visibility. Default true so older daemons (which
    // don't send these) keep showing everything.
    out->today_show_cost  = doc["tac"] | true;
    out->today_show_cache = doc["tch"] | true;

    // GitHub & brightness — all optional; defaults keep older daemons working.
    out->github_issues  = doc["gi"] | 0;
    out->github_prs     = doc["gp"] | 0;
    out->github_enabled = doc["ge"] | false;
    out->brightness     = doc["br"] | 100;

    // Power save — disabled by default so older daemons keep the screen on.
    out->power_save      = doc["ps"]  | false;
    out->power_save_secs = doc["pst"] | 300;
    out->changed         = doc["chg"] | false;

    // CI/CD (GitHub Actions). All optional — cie=false hides the data.
    out->ci_enabled = doc["cie"] | false;
    strlcpy(out->ci_status,   doc["cis"] | "none", sizeof(out->ci_status));
    strlcpy(out->ci_repo,     doc["cir"] | "",     sizeof(out->ci_repo));
    strlcpy(out->ci_branch,   doc["cib"] | "",     sizeof(out->ci_branch));
    strlcpy(out->ci_workflow, doc["ciw"] | "",     sizeof(out->ci_workflow));
    out->ci_failing = doc["cif"] | 0;
    out->ci_waiting = doc["ciq"] | 0;

    // Auto-focus request — daemon sets "fc" only on the poll where it detects
    // a noteworthy change. Default empty so non-event payloads don't switch.
    strlcpy(out->focus_screen, doc["fc"] | "", sizeof(out->focus_screen));

    // Copilot fields. All optional — when the user hasn't configured a
    // Copilot org we just leave copilot_enabled=false and the screen shows
    // a hint to set one in the tray app.
    out->copilot_enabled = doc["cp"]  | false;
    strlcpy(out->copilot_status, doc["cps"] | "off", sizeof(out->copilot_status));
    strlcpy(out->copilot_when,   doc["cpw"] | "",    sizeof(out->copilot_when));
    strlcpy(out->copilot_editor, doc["cpe"] | "",    sizeof(out->copilot_editor));

    // AI-credit usage. Set only when the daemon got a 200 from the org
    // billing endpoint. cpu/cpa are integers on the wire; cpp is a
    // 0.1-resolution percent (matches the example script's output).
    out->copilot_premium_ok        = doc["cpr"] | false;
    out->copilot_premium_pct       = doc["cpp"] | 0.0f;
    out->copilot_premium_used      = doc["cpu"] | 0;
    out->copilot_premium_allowance = doc["cpa"] | 0;
    strlcpy(out->copilot_premium_scope, doc["cpsu"] | "org", sizeof(out->copilot_premium_scope));
    strlcpy(out->copilot_top_model, doc["cpm"] | "", sizeof(out->copilot_top_model));

    // Per-app visibility CSV ("usage,today,github,copilot"). Missing /
    // empty = show everything (the boot default).
    strlcpy(out->enabled_apps, doc["apps"] | "", sizeof(out->enabled_apps));

    // Splash mood + events strip.
    strlcpy(out->mood, doc["md"] | "", sizeof(out->mood));
    out->events_count = 0;
    JsonArrayConst evts = doc["evts"].as<JsonArrayConst>();
    if (!evts.isNull()) {
        for (JsonVariantConst v : evts) {
            if (out->events_count >= UsageData::EVENTS_MAX) break;
            const char* s = v.as<const char*>();
            if (!s) continue;
            strlcpy(out->events[out->events_count], s, UsageData::EVENT_LEN);
            out->events_count++;
        }
    }

    // Optional device rename pushed by the daemon ("nm"). Persisted to NVS and
    // applied to BLE here because parse_json is the single chokepoint for both
    // the BLE and USB payloads. ble_set_device_name() no-ops when unchanged.
    const char* nm = doc["nm"] | "";
    if (nm[0] && ble_set_device_name(nm)) {
        ui_update_ble_status(ble_get_state(), ble_get_device_name(), ble_get_mac_address());
    }

    out->valid = true;
    return true;
}

// Serial command buffer (sized to fit JSON usage payloads with headroom; must
// match the BLE reassembly buffer since the same payloads arrive over USB).
#define CMD_BUF_SIZE 1024
static char cmd_buf[CMD_BUF_SIZE];
static int cmd_pos = 0;

// Forward decl — defined after this block so it can call into UI/rate-tracker.
static void handle_usb_usage_json(const char* line);

static void send_screenshot() {
    const uint32_t w = LCD_WIDTH, h = LCD_HEIGHT;
    const uint32_t row_bytes = w * 2;
    const uint32_t buf_size = row_bytes * h;
    uint8_t* sbuf = (uint8_t*)heap_caps_malloc(buf_size, MALLOC_CAP_SPIRAM);
    if (!sbuf) {
        Serial.println("SCREENSHOT_ERR");
        return;
    }

    lv_draw_buf_t draw_buf;
    lv_draw_buf_init(&draw_buf, w, h, LV_COLOR_FORMAT_RGB565, row_bytes, sbuf, buf_size);

    lv_result_t res = lv_snapshot_take_to_draw_buf(lv_screen_active(), LV_COLOR_FORMAT_RGB565, &draw_buf);
    if (res != LV_RESULT_OK) {
        heap_caps_free(sbuf);
        Serial.println("SCREENSHOT_ERR");
        return;
    }

    Serial.printf("SCREENSHOT_START %lu %lu %lu\n", (unsigned long)w, (unsigned long)h, (unsigned long)buf_size);
    Serial.flush();
    Serial.write(sbuf, buf_size);
    Serial.flush();
    Serial.println();
    Serial.println("SCREENSHOT_END");

    heap_caps_free(sbuf);
}

static void check_serial_cmd() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            cmd_buf[cmd_pos] = '\0';
            if (cmd_pos > 0) {
                if (strcmp(cmd_buf, "screenshot") == 0) {
                    send_screenshot();
                } else if (cmd_buf[0] == '{') {
                    handle_usb_usage_json(cmd_buf);
                }
            }
            cmd_pos = 0;
        } else if (cmd_pos < CMD_BUF_SIZE - 1) {
            cmd_buf[cmd_pos++] = c;
        } else {
            // Overflow — drop the line so we don't splice garbage into the next one.
            cmd_pos = 0;
        }
    }
}

// Apply a JSON usage payload received over USB CDC. Mirrors the BLE path in loop().
static void handle_usb_usage_json(const char* line) {
    if (!parse_json(line, &usage)) {
        Serial.println("USB_USAGE_ERR");
        return;
    }
    int g_before = usage_rate_group();
    usage_rate_sample(usage.session_pct);
    int g_after = usage_rate_group();
    if (g_after != g_before) {
        Serial.printf("usage rate: group %d -> %d (s=%.2f%%)\n",
            g_before, g_after, usage.session_pct);
        if (splash_is_active()) splash_pick_for_current_rate();
    }
    ui_update(&usage);
    if (ui_focus_by_name(usage.focus_screen)) {
        Serial.printf("Auto-focused on %s\n", usage.focus_screen);
    }
    if (usage.changed) note_activity();  // wake the screen on real changes
    Serial.println("USB_USAGE_OK");
}

void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.println("{\"ready\":true}");

    // Init I2C for GPIO expander
    Wire.begin(EXPANDER_SDA, EXPANDER_SCL);

    // AXP2101 PMU (battery gauge) on the same I2C bus. No-op if absent.
    power_init();

    // Reset sequence via TCA9554 GPIO expander (same as HuginnESP)
    expander->pinMode(5, OUTPUT);
    expander->pinMode(6, OUTPUT);
    expander->digitalWrite(6, LOW);   // backlight off during reset
    delay(200);
    expander->digitalWrite(5, LOW);   // LCD reset LOW
    delay(200);
    expander->digitalWrite(5, HIGH);  // LCD reset HIGH
    delay(200);

    // Init display
    if (!gfx->begin()) {
        Serial.println("ERROR: gfx->begin() FAILED!");
    } else {
        Serial.println("Display init OK");
    }
    gfx->fillScreen(0x0000);

    // Init LVGL
    lv_init();
    lv_tick_set_cb(my_tick);

    // Allocate PSRAM-backed partial render buffers
    buf1 = (uint16_t*)heap_caps_malloc(LCD_WIDTH * BUF_LINES * 2, MALLOC_CAP_SPIRAM);
    buf2 = (uint16_t*)heap_caps_malloc(LCD_WIDTH * BUF_LINES * 2, MALLOC_CAP_SPIRAM);
    Serial.printf("PSRAM bufs: buf1=%p buf2=%p\n", buf1, buf2);

    lv_display_t* disp = lv_display_create(LCD_WIDTH, LCD_HEIGHT);
    lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_flush_cb(disp, my_flush_cb);
    lv_display_set_buffers(disp, buf1, buf2, LCD_WIDTH * BUF_LINES * 2,
                           LV_DISPLAY_RENDER_MODE_PARTIAL);

    lv_indev_t* indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, my_touch_cb);

    // Init touch controller (GT911 on the expander I2C bus). Best-effort —
    // a probe failure logs over Serial but does not abort boot.
    touch_init(expander, GT911_RST_EXPANDER_PIN, TP_INT);

    // Init BLE data channel
    ble_init();

    // BOOT button (GPIO 0) — cycle screens
    pinMode(BTN_BACK, INPUT_PULLUP);

    // Build dashboard
    ui_init();

    // Show initial BLE status on Bluetooth screen
    ui_update_ble_status(ble_get_state(), ble_get_device_name(), ble_get_mac_address());

    // First battery paint (hidden if no battery is connected).
    ui_set_battery(power_battery_pct(), power_is_charging());

    ui_show_screen(SCREEN_SPLASH);

    Serial.println("Dashboard ready, waiting for data on BLE...");
}

static ble_state_t last_ble_state = BLE_STATE_INIT;

void loop() {
    touch_poll();
    lv_timer_handler();
    ui_tick_anim();
    ble_tick();
    splash_tick();

    // BOOT button (GPIO 0) cycles screens: splash → usage → bluetooth → splash
    // PWRKEY is on AXP2101, not a GPIO — handled separately if needed
    {
        static bool back_was = false;
        bool back_now = (digitalRead(BTN_BACK) == LOW);

        if (back_now && !back_was) {
            // When the screen is blanked, the press only wakes it; otherwise it
            // cycles. Either way it counts as activity.
            if (g_display_on) ui_cycle_screen();
            note_activity();
        }
        back_was = back_now;
    }

    // A touch counts as activity too (and wakes a blanked screen). The actual
    // touch action while awake is handled by LVGL via my_touch_cb.
    {
        static bool touch_was = false;
        bool touch_now = touch_is_pressed();
        if (touch_now && !touch_was) note_activity();
        touch_was = touch_now;
    }

    // Power save: blank the backlight after the configured idle timeout, and
    // make sure it's on whenever power-save is off.
    if (g_last_activity_ms == 0) g_last_activity_ms = millis();
    if (usage.power_save) {
        uint32_t timeout_ms = usage.power_save_secs * 1000UL;
        if (g_display_on && timeout_ms && (millis() - g_last_activity_ms > timeout_ms)) {
            power_set_backlight(false);
            g_display_on = false;
        }
    } else if (!g_display_on) {
        power_set_backlight(true);
        g_display_on = true;
    }

    // Update BLE status on screen when state changes
    ble_state_t bs = ble_get_state();
    if (bs != last_ble_state) {
        last_ble_state = bs;
        ui_update_ble_status(bs, ble_get_device_name(), ble_get_mac_address());
    }

    // Refresh the battery indicator a few times a minute (cheap I2C reads).
    {
        static uint32_t last_batt = 0;
        if (millis() - last_batt > 5000) {
            last_batt = millis();
            power_tick();
            ui_set_battery(power_battery_pct(), power_is_charging());
        }
    }

    // Check for serial commands (screenshot, etc.)
    check_serial_cmd();

    // Process incoming BLE data
    if (ble_has_data()) {
        if (parse_json(ble_get_data(), &usage)) {
            int g_before = usage_rate_group();
            usage_rate_sample(usage.session_pct);
            int g_after = usage_rate_group();
            if (g_after != g_before) {
                Serial.printf("usage rate: group %d -> %d (s=%.2f%%)\n",
                    g_before, g_after, usage.session_pct);
                if (splash_is_active()) splash_pick_for_current_rate();
            }
            ui_update(&usage);
            if (ui_focus_by_name(usage.focus_screen)) {
                Serial.printf("Auto-focused on %s\n", usage.focus_screen);
            }
            if (usage.changed) note_activity();  // wake the screen on real changes
            ble_send_ack();
        } else {
            ble_send_nack();
        }
    }

    delay(5);
}
