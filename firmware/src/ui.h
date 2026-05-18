#pragma once
#include "data.h"
#include "ble.h"

enum screen_t {
    SCREEN_SPLASH,
    SCREEN_USAGE,
    SCREEN_TODAY,
    SCREEN_GITHUB,
    SCREEN_COPILOT,
    SCREEN_BLUETOOTH,
    SCREEN_COUNT,
};

void ui_init(void);
void ui_update(const UsageData* data);
void ui_tick_anim(void);
void ui_show_screen(screen_t screen);
void ui_cycle_screen(void);
void ui_toggle_splash(void);
screen_t ui_get_current_screen(void);
void ui_update_ble_status(ble_state_t state, const char* name, const char* mac);

// Set the visible-apps list from a CSV like "usage,today,github,copilot".
// Apps NOT in the list are hidden from the cycle on the device but remain
// available to ui_focus_by_name (the daemon's focus-request flow still
// reaches the user even if they hid the app — surfacing a fresh PR is
// more important than honoring a soft preference). Splash + Bluetooth
// are system screens and always cyclable. NULL or empty CSV means
// "enable everything" (used as the boot-time default before the first
// daemon payload arrives).
void ui_set_enabled_apps(const char* csv);

// Auto-focus: switch to the named screen if it's a known name and we're not
// already on it. Returns true if a switch happened (so callers can log it).
// Unknown / empty names are a silent no-op.
bool ui_focus_by_name(const char* name);
