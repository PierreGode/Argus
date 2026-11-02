#include <Arduino.h>
#include "display.h"
#include "pins.h"

static Arduino_DataBus *bus    = nullptr;
static Arduino_GFX     *panel  = nullptr;   // the physical ST7735
static Arduino_Canvas  *canvas = nullptr;   // RAM framebuffer blitted to panel

Arduino_GFX *gfx = nullptr;

void display_backlight(bool on) {
    pinMode(LCD_BL, OUTPUT);
    // Backlight is active LOW on the T-Dongle S3.
    digitalWrite(LCD_BL, on ? LOW : HIGH);
}

void display_begin() {
    bus = new Arduino_ESP32SPI(LCD_DC, LCD_CS, LCD_SCLK, LCD_MOSI,
                               GFX_NOT_DEFINED /*miso*/, FSPI);
    panel = new Arduino_ST7735(
        bus, LCD_RST, LCD_ROTATION, true /*ips*/,
        LCD_NATIVE_W, LCD_NATIVE_H,
        LCD_COL_OFFSET, LCD_ROW_OFFSET,
        LCD_COL_OFFSET, LCD_ROW_OFFSET);

    canvas = new Arduino_Canvas(LCD_W, LCD_H, panel);
    gfx = canvas;
    canvas->begin();            // also brings up the panel
    canvas->fillScreen(0x0000);   // black
    canvas->flush();
    display_backlight(true);
}

void display_flush() {
    if (canvas) canvas->flush();
}

uint16_t *display_fb() {
    return canvas ? canvas->getFramebuffer() : nullptr;
}
