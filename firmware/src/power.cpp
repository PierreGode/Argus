#include "power.h"
#include <Arduino.h>
#include <Wire.h>
#include "display_cfg.h"   // EXPANDER_SDA / EXPANDER_SCL — the shared I2C bus
#include "XPowersLib.h"

// AXP2101 PMU driver. The chip sits on the same I2C bus as the GPIO expander
// and touch controller (Wire is already begun in setup()). When no battery is
// physically connected, power_battery_pct() returns -1 so the UI hides the
// indicator. Compiled for AXP2101 via the -DXPOWERS_CHIP_AXP2101 build flag.

static XPowersAXP2101 pmu;
static bool pmu_ok = false;

void power_init(void) {
    pmu_ok = pmu.begin(Wire, AXP2101_SLAVE_ADDRESS, EXPANDER_SDA, EXPANDER_SCL);
    if (!pmu_ok) {
        Serial.println("PMU: AXP2101 not found — battery indicator disabled");
        return;
    }
    Serial.println("PMU: AXP2101 init OK");

    // Turn on the measurements we read below. Without these the gauge / voltage
    // registers stay zeroed.
    pmu.enableBattDetection();
    pmu.enableBattVoltageMeasure();
    pmu.enableVbusVoltageMeasure();
    pmu.enableSystemVoltageMeasure();

    // DLDO1 powers the LCD backlight on this board. It's on by default (EFUSE),
    // but a prior power-save sleep followed by a soft reset would leave it off
    // (the PMU isn't reset by ESP.restart), so force it on at boot.
    pmu.enableDLDO1();
}

void power_tick(void) {}

int power_battery_pct(void) {
    if (!pmu_ok || !pmu.isBatteryConnect()) return -1;  // no battery present
    int pct = pmu.getBatteryPercent();
    if (pct < 0)   return -1;   // gauge not ready yet
    if (pct > 100) pct = 100;
    return pct;
}

bool power_is_charging(void) {
    return pmu_ok && pmu.isCharging();
}

// LCD backlight on the AXP2101 DLDO1 rail. Disabling it blanks the screen for
// power-save; the panel logic + touch stay powered (other rails), so the device
// keeps running and re-enabling DLDO1 brings the image straight back.
void power_set_backlight(bool on) {
    if (!pmu_ok) return;
    if (on) pmu.enableDLDO1();
    else    pmu.disableDLDO1();
}

// PWR-button IRQ isn't wired on this board; the BOOT button cycles screens.
bool power_pwr_pressed(void) { return false; }
