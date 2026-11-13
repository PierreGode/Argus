#pragma once
#include <stddef.h>
#include <stdint.h>

// Firmware update received through the daemon link (USB serial or BLE).
//
//   daemon:  !ota <size> <md5hex>
//   device:  !ota ready <block>          (or  !ota err <reason>)
//   daemon:  <size> raw bytes, in blocks of at most <block>
//   device:  !ota ack <received>         (USB only, after every block)
//   device:  !ota ok   → reboots into the new image
//            !ota err <reason>  → keeps running the current image
//
// The image is written to the inactive app slot and only made bootable after
// the MD5 matches, so a dropped link or a corrupt transfer leaves the running
// firmware untouched.

#define OTA_USB_BLOCK 1024

// Start an update. Returns false (and fills err) if the slot can't be opened.
bool ota_begin(size_t size, const char* md5_hex, const char** err);
bool ota_active(void);
size_t ota_remaining(void);
size_t ota_received(void);
int  ota_progress_pct(void);

// Feed raw image bytes. Returns:  0 = more expected, 1 = finished and verified
// (caller should reboot), -1 = failed (err filled, update aborted).
int ota_feed(const uint8_t* data, size_t len, const char** err);

// Abort when the sender has gone quiet. Returns true if it just timed out.
bool ota_check_timeout(void);
void ota_abort(void);
