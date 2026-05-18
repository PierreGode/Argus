#include "power.h"
#include <Arduino.h>

// Stub — LCD-4B board has no AXP2101 PMU
void power_init(void) {}
void power_tick(void) {}
int  power_battery_pct(void) { return 100; }
bool power_is_charging(void) { return false; }
bool power_pwr_pressed(void) { return false; }
