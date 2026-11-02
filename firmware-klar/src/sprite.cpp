#include <string.h>
#include "sprite.h"
#include "display.h"
#include "pins.h"

const klar_sprite_t *sprite_get(const char *name) {
    for (int i = 0; i < KLAR_SPRITE_COUNT; i++) {
        if (!strcmp(klar_sprites[i].name, name)) return &klar_sprites[i];
    }
    return nullptr;
}

// Blend fg over bg by alpha a (0..255), both RGB565.
static inline uint16_t blend565(uint16_t bg, uint16_t fg, uint8_t a) {
    uint8_t br = (bg >> 11) & 0x1F, bgc = (bg >> 5) & 0x3F, bb = bg & 0x1F;
    uint8_t fr = (fg >> 11) & 0x1F, fgc = (fg >> 5) & 0x3F, fb = fg & 0x1F;
    uint8_t r = br + (((fr - br) * a) >> 8);
    uint8_t g = bgc + (((fgc - bgc) * a) >> 8);
    uint8_t b = bb + (((fb - bb) * a) >> 8);
    return (r << 11) | (g << 5) | b;
}

void sprite_blit(const klar_sprite_t *s, int dx, int dy, uint8_t fade) {
    if (!s) return;
    uint16_t *fb = display_fb();
    if (!fb) return;
    const int W = LCD_W, H = LCD_H;
    const int sw = s->w, sh = s->h;
    const uint8_t *color = s->data;                 // sw*sh * 2 bytes, little-endian
    const uint8_t *alpha = s->data + (sw * sh * 2);  // sw*sh bytes

    for (int y = 0; y < sh; y++) {
        int py = dy + y;
        if (py < 0 || py >= H) continue;
        for (int x = 0; x < sw; x++) {
            int px = dx + x;
            if (px < 0 || px >= W) continue;
            int i = y * sw + x;
            uint16_t a = alpha[i];
            if (fade != 255) a = (a * fade) >> 8;
            if (!a) continue;
            uint16_t sc = (uint16_t)color[i * 2] | ((uint16_t)color[i * 2 + 1] << 8);
            uint16_t *d = &fb[py * W + px];
            *d = (a >= 255) ? sc : blend565(*d, sc, (uint8_t)a);
        }
    }
}
