// ST7735 160x80 display, drawn through an in-RAM canvas (flicker-free; ready for
// DMA animation in Step B). Draw into `gfx`, then call display_flush().
#pragma once
#include <Arduino_GFX_Library.h>

extern Arduino_GFX *gfx;   // the canvas you draw into

void display_begin();
void display_flush();
void display_backlight(bool on);
uint16_t *display_fb();   // raw canvas framebuffer (LCD_W*LCD_H, native order)
