#pragma once
#include <stdint.h>

enum ble_state_t {
    BLE_STATE_INIT,
    BLE_STATE_ADVERTISING,
    BLE_STATE_CONNECTED,
    BLE_STATE_DISCONNECTED,
};

void ble_init(void);
void ble_tick(void);
ble_state_t ble_get_state(void);
const char* ble_get_device_name(void);
const char* ble_get_mac_address(void);
void ble_clear_bonds(void);

// Persist a new advertised BLE name to NVS and apply it. The new name is used
// by the GATT Device Name characteristic immediately and by advertising on the
// next advertising cycle (i.e. after the current central disconnects). Returns
// true only if the name actually changed (so the caller can refresh the BT screen).
bool ble_set_device_name(const char* name);

bool ble_has_data(void);
const char* ble_get_data(void);
void ble_send_ack(void);
void ble_send_nack(void);
void ble_request_refresh(void);

// BLE HID keyboard
void ble_keyboard_press(uint8_t key, uint8_t modifier);
void ble_keyboard_release(void);
