#pragma once

#include <Arduino_GFX_Library.h>
#include <Wire.h>

// ---- Display resolution ----
#define LCD_WIDTH   480
#define LCD_HEIGHT  480

// ---- I2C for GPIO expander (TCA9554) ----
#define EXPANDER_SDA  47
#define EXPANDER_SCL  48
#define EXPANDER_ADDR 0x20

// ---- Touch pins (GT911 via same I2C as expander) ----
#define TP_INT      16
#define TP_RST      -1  // managed by expander

// ---- Global hardware objects (defined in main.cpp) ----
extern Arduino_RGB_Display *gfx;
