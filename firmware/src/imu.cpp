#include "imu.h"
#include <Arduino.h>

// Stub — LCD-4B board has no QMI8658 IMU
void imu_init(void) {}
void imu_tick(void) {}
uint8_t imu_get_rotation(void) { return 0; }
