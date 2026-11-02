#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "anim.h"
#include "display.h"
#include "sprite.h"
#include "led.h"
#include "theme.h"
#include "pins.h"

static const uint32_t FRAME_MS  = 33;    // ~30 fps
static const float    ACTIVE_MS = 4000;  // motion "energy" decays over this long

// ---- state ----------------------------------------------------------------
static LampState S;
static bool      have_data   = false;
static uint32_t  mode_since  = 0;
static uint32_t  last_frame  = 0;
static uint32_t  fanfare_until = 0;
static uint32_t  swipe_until = 0;
static float     tok_disp    = 0;
static float     ctx_disp    = 0;
static float     cost_prev   = -1.0f;
static char      repo_prev[25] = "";
static uint8_t   mode_prev   = 255;

// ---- particles (runes / confetti / z) -------------------------------------
#define NP 30
struct P { bool on; float x, y, vx, vy; int16_t life, maxlife; uint16_t col; char ch; bool grav; };
static P parts[NP];

// ---- floating "+cost" -----------------------------------------------------
#define NC 3
struct CF { bool on; float y; int16_t life; char txt[12]; };
static CF cfloat[NC];

static const char RUNES[] = "{}<>/=;+*![]().";

static P *free_part() {
    for (int i = 0; i < NP; i++) if (!parts[i].on) return &parts[i];
    return nullptr;
}

static void reset_parts() { for (int i = 0; i < NP; i++) parts[i].on = false; }

static void spawn_rune() {
    P *p = free_part(); if (!p) return;
    p->on = true; p->grav = false;
    p->x = -6; p->y = 6 + random(0, LCD_H - 16);
    p->vx = 0.6f + random(0, 70) / 100.0f; p->vy = (random(0, 2) ? 0.1f : -0.1f);
    p->maxlife = p->life = 200;
    p->col = (random(0, 3) == 0) ? COL_BLUE : COL_DIM;
    p->ch = RUNES[random(0, sizeof(RUNES) - 1)];
}

static void spawn_z() {
    P *p = free_part(); if (!p) return;
    p->on = true; p->grav = false;
    p->x = 40; p->y = 24;
    p->vx = 0.25f; p->vy = -0.35f;
    p->maxlife = p->life = 120;
    p->col = COL_GREY;
    p->ch = (random(0, 2)) ? 'z' : 'Z';
}

static void burst_confetti() {
    for (int i = 0; i < 18; i++) {
        P *p = free_part(); if (!p) return;
        p->on = true; p->grav = true;
        p->x = 80 + random(-40, 40); p->y = random(-10, 10);
        p->vx = random(-120, 120) / 100.0f; p->vy = random(-10, 40) / 100.0f;
        p->maxlife = p->life = 90;
        const uint16_t cs[] = {COL_GOLD, COL_GREEN, COL_BLUE, COL_YEL, COL_WHITE};
        p->col = cs[random(0, 5)];
        p->ch = 0;  // rect confetti
    }
}

static void spawn_costfloat(float d) {
    for (int i = 0; i < NC; i++) if (!cfloat[i].on) {
        cfloat[i].on = true; cfloat[i].y = 58; cfloat[i].life = 60;
        snprintf(cfloat[i].txt, sizeof(cfloat[i].txt), "+$%.2f", d);
        return;
    }
}

// ---- public API -----------------------------------------------------------

void anim_begin() {
    reset_parts();
    for (int i = 0; i < NC; i++) cfloat[i].on = false;
}

void anim_set_state(const LampState &st, bool fanfare) {
    uint32_t now = millis();
    bool same_session = have_data && (strcmp(st.repo, repo_prev) == 0);

    if (have_data && !same_session) swipe_until = now + 500;            // session changed
    if (same_session && st.cost > cost_prev + 0.0005f)                  // cost grew
        spawn_costfloat(st.cost - cost_prev);

    if (st.mode != mode_prev) {
        mode_since = now;
        reset_parts();
        if (st.mode == MODE_DONE) { fanfare_until = now + 2500; burst_confetti(); }
    }
    if (fanfare) { fanfare_until = now + 2500; burst_confetti(); }

    S = st;
    strncpy(repo_prev, st.repo, sizeof(repo_prev) - 1);
    repo_prev[sizeof(repo_prev) - 1] = 0;
    cost_prev = st.cost;
    mode_prev = st.mode;
    if (!have_data) { tok_disp = st.tok; ctx_disp = st.ctx; }
    have_data = true;
}

// ---- rendering ------------------------------------------------------------

static void step_particles(float dt) {
    for (int i = 0; i < NP; i++) {
        P &p = parts[i];
        if (!p.on) continue;
        p.x += p.vx * dt * 60.0f;
        p.y += p.vy * dt * 60.0f;
        if (p.grav) p.vy += 2.2f * dt;
        if (--p.life <= 0 || p.x < -8 || p.x > LCD_W + 8 || p.y > LCD_H + 8) { p.on = false; continue; }
        // fade to dim near end of life
        uint16_t col = (p.life < p.maxlife / 4) ? COL_DIM : p.col;
        if (p.ch) {
            gfx->setTextSize(1);
            gfx->setTextColor(col);
            gfx->setCursor((int)p.x, (int)p.y);
            gfx->write((uint8_t)p.ch);
        } else {
            gfx->fillRect((int)p.x, (int)p.y, 2, 2, col);
        }
    }
}

static void draw_costfloats(float dt) {
    for (int i = 0; i < NC; i++) {
        if (!cfloat[i].on) continue;
        cfloat[i].y -= 0.5f;
        if (--cfloat[i].life <= 0) { cfloat[i].on = false; continue; }
        uint16_t col = (cfloat[i].life < 15) ? COL_DIM : COL_GREEN;
        gfx->setTextSize(1);
        gfx->setTextColor(col);
        gfx->setCursor(118, (int)cfloat[i].y);
        gfx->print(cfloat[i].txt);
    }
}

static void draw_right_column(uint32_t now) {
    const int rx = 72;
    // repo name (with session-change swipe) + session count.
    int xoff = 0;
    if (now < swipe_until) {
        float p = 1.0f - (swipe_until - now) / 500.0f;
        xoff = (int)((1.0f - p) * 70);
    }
    char repo[13];
    strncpy(repo, S.repo[0] ? S.repo : "no session", sizeof(repo) - 1);
    repo[sizeof(repo) - 1] = 0;
    gfx->setTextSize(1);
    gfx->setTextColor(COL_GOLD);
    gfx->setCursor(rx + xoff, 6);
    gfx->print(repo);
    if (S.n > 1) {
        char c[6]; snprintf(c, sizeof(c), "%ux", S.n);
        gfx->setTextColor(COL_GREY);
        gfx->setCursor(LCD_W - (int)strlen(c) * 6 - 2, 6);
        gfx->print(c);
    }

    // mode word.
    gfx->setTextSize(2);
    gfx->setTextColor(mode_color(S.mode));
    gfx->setCursor(rx, 18);
    gfx->print(mode_label(S.mode));

    // rolling token counter.
    char tb[16]; fmt_tokens((uint32_t)(tok_disp + 0.5f), tb, sizeof(tb));
    gfx->setTextSize(2);
    gfx->setTextColor(COL_WHITE);
    gfx->setCursor(rx, 38);
    gfx->print(tb);

    // cost + context %.
    gfx->setTextSize(1);
    gfx->setTextColor(COL_GOLD);
    gfx->setCursor(rx, 60);
    gfx->printf("$%.2f", S.cost);
    gfx->setTextColor(COL_GREY);
    gfx->setCursor(rx + 54, 60);
    gfx->printf("%u%%", (unsigned)(ctx_disp + 0.5f));
}

static void draw_context_bar() {
    uint8_t c = (uint8_t)(ctx_disp + 0.5f);
    uint16_t col = c < 60 ? COL_GREEN : (c < 85 ? COL_YEL : COL_RED);
    int bx = 2, by = 73, bw = LCD_W - 4, bh = 5;
    gfx->drawRoundRect(bx, by, bw, bh, 2, COL_DIM);
    int fill = (bw - 2) * c / 100;
    if (fill > 0) gfx->fillRoundRect(bx + 1, by + 1, fill, bh - 2, 1, col);
}

static void draw_mascot(uint32_t now, float energy) {
    int fx = 2, fy = 6;
    float bob = sinf(now * 0.004f) * 2.0f;
    uint8_t fade = 255;
    if (S.mode == MODE_REST) {
        float t = (now - mode_since) / 2000.0f; if (t > 1) t = 1;
        fade = 255 - (uint8_t)(t * 150);           // settle dim
    }
    sprite_blit(sprite_get(mode_face(S.mode)), fx, (int)(fy + bob), fade);

    if (S.mode == MODE_WAIT) {
        // waving hand + blinking "?"
        float wave = sinf(now * 0.012f) * 7.0f;
        sprite_blit(sprite_get("hand"), (int)(44 + wave), 40, 255);
        if ((now / 450) % 2 == 0) {
            gfx->setTextSize(2);
            gfx->setTextColor(COL_YEL);
            gfx->setCursor(56, 2);
            gfx->print("?");
        }
    } else if (S.mode == MODE_DONE) {
        // hand rises from below, then holds beside the head.
        float t = (now - mode_since) / 500.0f; if (t > 1) t = 1;
        int hy = (int)(70 - t * 34);
        sprite_blit(sprite_get("hand"), 44, hy, 255);
    }
}

static void update_led(uint32_t now, float energy) {
    switch (S.mode) {
        case MODE_WORK: {
            uint8_t b = 3 + (uint8_t)(5 * (0.5f + 0.5f * sinf(now * 0.006f)));
            led_set(30, 90, 255, b);
            break;
        }
        case MODE_WAIT: {
            uint8_t b = 8 + (uint8_t)(6 * (0.5f + 0.5f * sinf(now * 0.013f)));
            led_set(255, 200, 20, b);
            break;
        }
        case MODE_DONE: {
            uint8_t b = (now < fanfare_until) ? (((now / 150) % 2) ? 14 : 4) : 7;
            led_set(40, 220, 90, b);
            break;
        }
        default:
            led_off();
    }
}

void anim_tick() {
    uint32_t now = millis();
    if (now - last_frame < FRAME_MS) return;
    float dt = (now - last_frame) / 1000.0f;
    if (dt > 0.1f) dt = 0.1f;
    last_frame = now;

    if (!have_data) return;   // boot splash stays until first data

    // ease data numbers toward targets
    float k = dt * 6.0f; if (k > 1) k = 1;
    tok_disp += (S.tok - tok_disp) * k;
    ctx_disp += (S.ctx - ctx_disp) * k;

    float energy = 1.0f - (now - mode_since) / ACTIVE_MS;
    if (energy < 0) energy = 0;

    gfx->fillScreen(COL_BG);
    draw_mascot(now, energy);

    // spawn mode-specific particles (rate tapers with energy).
    if (S.mode == MODE_WORK) {
        if (random(0, 100) < (int)(8 + energy * 30)) spawn_rune();
    } else if (S.mode == MODE_REST) {
        if (random(0, 100) < 3) spawn_z();
    }

    step_particles(dt);
    draw_right_column(now);
    draw_costfloats(dt);
    draw_context_bar();

    display_flush();
    update_led(now, energy);
}

void anim_boot() {
    gfx->fillScreen(COL_BG);
    sprite_blit(sprite_get("happy"), 2, 8, 255);
    gfx->setTextSize(2);
    gfx->setTextColor(COL_GOLD);
    gfx->setCursor(72, 20);
    gfx->print("Klar");
    gfx->setTextSize(1);
    gfx->setTextColor(COL_GREY);
    gfx->setCursor(72, 44);
    gfx->print("waiting for");
    gfx->setCursor(72, 54);
    gfx->print("daemon...");
    display_flush();
    led_off();
}
