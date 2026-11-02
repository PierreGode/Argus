// Mascot sprite lookup + alpha blit into the canvas framebuffer.
#pragma once
#include <stdint.h>
#include "klar_sprites.h"

const klar_sprite_t *sprite_get(const char *name);

// Blit sprite `s` at (dx,dy). `fade` 0..255 scales the sprite's own alpha
// (255 = full opacity) — used for the rest-mode fade-out. Clipped to the screen.
void sprite_blit(const klar_sprite_t *s, int dx, int dy, uint8_t fade = 255);
