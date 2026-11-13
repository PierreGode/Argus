#pragma once

// Firmware build id, reported to the daemon (`!ver`) so it can tell whether the
// published build differs from what's running. CI stamps it through the
// ARGUS_FW_VERSION environment variable (see platformio.ini and
// .github/workflows/deploy-flasher.yml); local builds leave it empty → "dev".
#ifndef ARGUS_FW_VERSION
#define ARGUS_FW_VERSION ""
#endif

static inline const char* argus_fw_version(void) {
    return (ARGUS_FW_VERSION[0] != '\0') ? ARGUS_FW_VERSION : "dev";
}
