#pragma once

void power_init(void);
void power_tick(void);
int  power_battery_pct(void);    // 0-100, or -1 if no battery
bool power_is_charging(void);
bool power_pwr_pressed(void);    // true once per AXP2101 PWR button short-press

// LCD backlight power. On the ESP32-S3-Touch-LCD-4B the backlight is fed by the
// AXP2101 DLDO1 rail (not a GPIO), so power-save toggles it via the PMU.
// No-op if the PMU wasn't found.
void power_set_backlight(bool on);

// Fill `out` with a one-line PMU diagnostic (last power-off cause + voltages).
#include <stddef.h>
void power_diag(char* out, size_t len);
