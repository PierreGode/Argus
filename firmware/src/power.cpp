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
    {
        char d[80];
        pmu_ok = true;
        power_diag(d, sizeof(d));
        Serial.printf("PMU: %s\n", d);
    }

    // Turn on the measurements we read below. Without these the gauge / voltage
    // registers stay zeroed.
    pmu.enableBattDetection();
    pmu.enableBattVoltageMeasure();
    pmu.enableVbusVoltageMeasure();
    pmu.enableSystemVoltageMeasure();

    // Never let the PMU latch the whole board off on a brief rail dip. With no
    // battery fitted the AXP2101 holds VSYS at only ~3.67 V, so the 3.3 V buck
    // has almost no headroom and a load spike (radio + flash write + display)
    // trips the "DCDC below 85%" protection — the board then stays dark until
    // someone re-powers it (power-off cause bit 'v' in power_diag()). With the
    // turn-off disabled, the worst case is an ESP32 brownout reset, which
    // recovers by itself. Over-voltage and over-temperature shutdown stay on.
    // These bits reset when the PMU loses power, so set them on every boot.
    pmu.disableDC1LowVoltageTurnOff();
    pmu.disableDC2LowVoltageTurnOff();
    pmu.disableDC3LowVoltageTurnOff();
    pmu.disableDC4LowVoltageTurnOff();
    pmu.disableDC5LowVoltageTurnOff();

    // DLDO1 powers the LCD backlight on this board. It's on by default (EFUSE),
    // but a prior power-save sleep followed by a soft reset would leave it off
    // (the PMU isn't reset by ESP.restart), so force it on at boot.
    pmu.enableDLDO1();
}

void power_tick(void) {}

// One-line PMU diagnostic: why the AXP2101 last cut power, and the supply
// voltages now. The power-off bits latch in the PMU across ESP32 resets, so
// this still tells the story after the board has been powered back on.
//   off bits: T=over-temperature V=DCDC over-voltage v=DCDC under-voltage
//             B=VBUS over-voltage S=VSYS under-voltage K=power key held
//             W=software power-off P=power source pulled down
void power_diag(char* out, size_t len) {
    if (!pmu_ok) { snprintf(out, len, "pmu absent"); return; }
    char off[9];
    int n = 0;
    if (pmu.isOverTemperatureOffSource()) off[n++] = 'T';
    if (pmu.isDcOverVoltageOffSource())   off[n++] = 'V';
    if (pmu.isDcUnderVoltageOffSource())  off[n++] = 'v';
    if (pmu.isVbusOverVoltageOffSource()) off[n++] = 'B';
    if (pmu.isVsysUnderVoltageOffSource())off[n++] = 'S';
    if (pmu.isPwronAlwaysLowOffSource())  off[n++] = 'K';
    if (pmu.isSwConfigOffSource())        off[n++] = 'W';
    if (pmu.isPwrSourcePullDown())        off[n++] = 'P';
    off[n] = '\0';
    // ilim / vlim are the raw input-limit register codes (see XPowersParams.hpp:
    // ilim 0..5 = 100/500/900/1000/1500/2000 mA, vlim 0..15 = 3.88 V + 80 mV steps).
    snprintf(out, len, "off=%s vbus=%umV vsys=%umV batt=%d ilim=%u vlim=%u",
             n ? off : "-", (unsigned)pmu.getVbusVoltage(),
             (unsigned)pmu.getSystemVoltage(), pmu.isBatteryConnect() ? 1 : 0,
             (unsigned)pmu.getVbusCurrentLimit(), (unsigned)pmu.getVbusVoltageLimit());
}

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
