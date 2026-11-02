// LilyGO T-Dongle S3 pin map.
// Verified against Xinyuan-LilyGO/T-Dongle-S3 examples/factory_screen/factory_screen.ino.
#pragma once

// ST7735 LCD over SPI (SPI2_HOST / FSPI on the S3). MISO unused.
#define LCD_MOSI   3
#define LCD_SCLK   5
#define LCD_CS     4
#define LCD_DC     2
#define LCD_RST    1
#define LCD_BL    38   // backlight — ACTIVE LOW (on = 0)

// Panel geometry. Native 80x160 portrait; we use it as 160x80 landscape.
#define LCD_NATIVE_W  80
#define LCD_NATIVE_H  160
#define LCD_W        160
#define LCD_H         80

// ST7735 0.96" column/row offsets. Commonly 26/1 for this LilyGO panel; some
// IPS units want 24/0. Tune on first flash if there's a pixel gap or wrap.
#define LCD_COL_OFFSET 26
#define LCD_ROW_OFFSET  1
// Rotation that yields 160x80 landscape. Flip to 3 to turn the image 180°
// (choose whichever puts the USB connector where you want it).
#define LCD_ROTATION    1

// APA102 RGB LED (2-wire SPI, bit-banged).
#define LED_DATA  40   // DI
#define LED_CLK   39   // CI

// Button (BOOT) — active LOW.
#define BTN_PIN    0
