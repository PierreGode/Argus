#include "splash.h"
#include "argus_sprites.h"
#include "theme.h"
#include "usage_rate.h"
#include <Arduino.h>
#include <string.h>
#include <lvgl.h>

// Sprite-driven splash. The 80x80 pixel-art eye renderer is gone; we
// now display one of six Argus mascot expressions, scaled 2x from a
// 240x240 RGB565A8 source so the character fills the 480x480 panel.
//
// The sprite stays statically centered — no bob / sway / float. The
// "movement" comes purely from swapping the face every
// SPLASH_EXPR_INTERVAL_MS based on the current usage-rate group:
//   group 0 idle    → happy, looking
//   group 1 normal  → happy, looking, flirt
//   group 2 active  → flirt, buffeld, surprised
//   group 3 heavy   → surprised, buffeld, angry

LV_FONT_DECLARE(font_styrene_28);
LV_FONT_DECLARE(font_styrene_24);
LV_FONT_DECLARE(font_styrene_20);

#define SPRITE_NATIVE       240
// LVGL scale uses 256 = 1.0x. 870 ≈ 3.4x — what the user picked earlier.
// Do NOT shrink this to "make room" for the events strip; the sprite
// already leaves clean transparent space at the bottom of the panel.
#define SPRITE_SCALE        870
// Baseline Y for the events label. Sits in the transparent gap below
// the character body — bump down a few px if the helmet/body overlaps.
#define EVENTS_STRIP_Y      410
#define SPLASH_EXPR_INTERVAL_MS 6000 // fallback expression cycle when no mood is set
#define SPLASH_EVENT_INTERVAL_MS 4000// time each event line stays on screen

static lv_obj_t      *splash_container = NULL;
static lv_obj_t      *sprite_img       = NULL;
static lv_obj_t      *label_status     = NULL;
static lv_obj_t      *events_label     = NULL;
static lv_image_dsc_t sprite_descs[ARGUS_SPRITE_COUNT];

static int idx_happy = -1, idx_looking = -1, idx_flirt = -1;
static int idx_buffeld = -1, idx_surprised = -1, idx_angry = -1;
static int cur_sprite = 0;
static int mood_sprite = -1;     // sprite index forced by splash_set_mood; -1 = rate-group cycle
static uint32_t last_expr_ms = 0;
static uint32_t last_event_swap_ms = 0;
static uint8_t  rotation_seed = 0;
static bool active = false;

// Event strip storage. Module-owned so callers can pass stack/local
// arrays. EVENT_COUNT / EVENT_LEN match data.h's UsageData layout — keep
// in sync if either side changes.
#define EVENT_COUNT 6
#define EVENT_LEN   56
static char    event_strings[EVENT_COUNT][EVENT_LEN];
static uint8_t event_count    = 0;
static uint8_t cur_event      = 0;
static char    mood_name[12]  = {0};

#define GROUP_COUNT 4
#define GROUP_MAX   3
// Filled at init from the resolved sprite indices.
static int8_t group_sprites[GROUP_COUNT][GROUP_MAX];
static uint8_t group_size[GROUP_COUNT] = {0};

static void init_icon_dsc_rgb565a8(lv_image_dsc_t *dsc, int w, int h, const uint8_t *data) {
    memset(dsc, 0, sizeof(*dsc));
    dsc->header.cf       = LV_COLOR_FORMAT_RGB565A8;
    dsc->header.w        = w;
    dsc->header.h        = h;
    dsc->header.stride   = w * 2;        // RGB565A8 stride counts the color plane only.
    dsc->data_size       = (uint32_t)w * h * 3;  // w*h RGB565 + w*h alpha
    dsc->data            = data;
}

static int find_sprite(const char *name) {
    for (int i = 0; i < ARGUS_SPRITE_COUNT; i++) {
        if (strcmp(argus_sprites[i].name, name) == 0) return i;
    }
    return -1;
}

static void resolve_groups(void) {
    idx_happy     = find_sprite("happy");
    idx_looking   = find_sprite("looking");
    idx_flirt     = find_sprite("flirt");
    idx_buffeld   = find_sprite("buffeld");
    idx_surprised = find_sprite("surprised");
    idx_angry     = find_sprite("angry");

    // Helper to pack up to GROUP_MAX ids into a group, deduping and
    // skipping any sprite that wasn't packed (-1).
    auto pack = [](int g, int a, int b, int c) {
        group_size[g] = 0;
        auto add = [&](int id) {
            if (id < 0) return;
            for (int i = 0; i < group_size[g]; i++) {
                if (group_sprites[g][i] == (int8_t)id) return;
            }
            if (group_size[g] < GROUP_MAX) {
                group_sprites[g][group_size[g]++] = (int8_t)id;
            }
        };
        add(a); add(b); add(c);
    };
    pack(0, idx_happy,     idx_looking,   -1);             // idle: chill / scanning
    pack(1, idx_happy,     idx_looking,   idx_flirt);      // normal: cheerful + working
    pack(2, idx_flirt,     idx_buffeld,   idx_surprised);  // active: getting puzzled
    pack(3, idx_surprised, idx_buffeld,   idx_angry);      // heavy: overloaded
}

static void show_sprite(int idx) {
    if (idx < 0 || idx >= ARGUS_SPRITE_COUNT) return;
    cur_sprite = idx;
    if (sprite_img) {
        lv_image_set_src(sprite_img, &sprite_descs[idx]);
    }
}

void splash_init(lv_obj_t *parent) {
    splash_container = lv_obj_create(parent);
    lv_obj_set_size(splash_container, 480, 480);
    lv_obj_set_pos(splash_container, 0, 0);
    lv_obj_set_style_bg_color(splash_container, THEME_BG, 0);
    lv_obj_set_style_bg_opa(splash_container, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(splash_container, 0, 0);
    lv_obj_set_style_pad_all(splash_container, 0, 0);
    lv_obj_clear_flag(splash_container, LV_OBJ_FLAG_SCROLLABLE);

    // Build LVGL descriptors for every packed sprite.
    for (int i = 0; i < ARGUS_SPRITE_COUNT; i++) {
        init_icon_dsc_rgb565a8(&sprite_descs[i], ARGUS_SPRITE_W, ARGUS_SPRITE_H,
                               argus_sprites[i].data);
    }

    sprite_img = lv_image_create(splash_container);
    lv_image_set_scale(sprite_img, SPRITE_SCALE);
    // The image's transform anchor is its top-left; pivot around the
    // sprite center so it doesn't drift when scaled.
    lv_image_set_pivot(sprite_img, SPRITE_NATIVE / 2, SPRITE_NATIVE / 2);
    // Position so the 2x-scaled sprite is centered on the panel.
    lv_obj_set_pos(sprite_img,
                   (480 - SPRITE_NATIVE) / 2,
                   (480 - SPRITE_NATIVE) / 2);

    // Events strip — sits OVER the bottom of the panel where the sprite
    // already has transparent space. Up to two lines wrap automatically;
    // typical strip is one line so this is rarely-used headroom.
    events_label = lv_label_create(splash_container);
    lv_label_set_text(events_label, "");
    lv_obj_set_style_text_font(events_label, &font_styrene_24, 0);
    lv_obj_set_style_text_color(events_label, lv_color_hex(0xe0d8c8), 0);
    lv_obj_set_style_text_align(events_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(events_label, 440);
    lv_label_set_long_mode(events_label, LV_LABEL_LONG_WRAP);
    lv_obj_align(events_label, LV_ALIGN_TOP_MID, 0, EVENTS_STRIP_Y);

    label_status = lv_label_create(splash_container);
    lv_label_set_text(label_status,
        "no sprites loaded\n\n"
        "run tools/build_argus_sprites.js");
    lv_obj_set_style_text_font(label_status, &font_styrene_28, 0);
    lv_obj_set_style_text_color(label_status, lv_color_hex(0xb0aea5), 0);
    lv_obj_set_style_text_align(label_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(label_status);

    resolve_groups();

    if (ARGUS_SPRITE_COUNT == 0) {
        lv_obj_add_flag(sprite_img, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(label_status, LV_OBJ_FLAG_HIDDEN);
        // Start on `happy` if it's packed; otherwise fall back to the first
        // sprite in the table so the splash still draws something.
        show_sprite(idx_happy >= 0 ? idx_happy : 0);
    }

    lv_obj_add_flag(splash_container, LV_OBJ_FLAG_HIDDEN);
}

void splash_tick(void) {
    if (!active) return;

    // Sprite rotation — only when no daemon-provided mood is locked.
    // Otherwise the sprite stays fixed to that mood until the daemon
    // reports a new one.
    if (mood_sprite < 0 && millis() - last_expr_ms >= SPLASH_EXPR_INTERVAL_MS) {
        splash_pick_for_current_rate();
    }

    // Events strip — advance through the daemon's event list. We rotate
    // even on a 1-event list so the user gets a fresh "this is alive"
    // signal (the label gets re-set, redrawn) without flicker; LVGL
    // dedups identical text under the hood.
    if (event_count > 0 && events_label &&
        millis() - last_event_swap_ms >= SPLASH_EVENT_INTERVAL_MS) {
        cur_event = (cur_event + 1) % event_count;
        lv_label_set_text(events_label, event_strings[cur_event]);
        last_event_swap_ms = millis();
    }
}

void splash_next(void) {
    if (ARGUS_SPRITE_COUNT == 0) return;
    int next = (cur_sprite + 1) % ARGUS_SPRITE_COUNT;
    show_sprite(next);
    last_expr_ms = millis();
    Serial.printf("splash: -> %s\n", argus_sprites[next].name);
}

void splash_pick_for_current_rate(void) {
    if (ARGUS_SPRITE_COUNT == 0) return;
    // Mood lock wins — splash_set_mood() owns the sprite while it's set.
    if (mood_sprite >= 0) {
        show_sprite(mood_sprite);
        last_expr_ms = millis();
        return;
    }
    int g = usage_rate_group();
    if (g < 0 || g >= GROUP_COUNT) g = 0;
    if (group_size[g] == 0) return;

    uint8_t slot = rotation_seed % group_size[g];
    rotation_seed++;
    int idx = (int)group_sprites[g][slot];
    if (idx < 0) return;

    show_sprite(idx);
    last_expr_ms = millis();
}

void splash_set_mood(const char* name) {
    int new_idx = -1;
    if (name && *name) new_idx = find_sprite(name);
    // Dedup — common case: same mood every poll.
    if (new_idx == mood_sprite) return;
    mood_sprite = new_idx;
    if (mood_sprite >= 0) {
        show_sprite(mood_sprite);
        last_expr_ms = millis();
    }
}

void splash_set_events(const char (*events)[56], uint8_t count) {
    if (count > EVENT_COUNT) count = EVENT_COUNT;

    // Has the list actually changed? Cheap byte-compare so a repeated
    // identical payload doesn't reset the rotation timer mid-cycle.
    bool changed = (count != event_count);
    if (!changed) {
        for (uint8_t i = 0; i < count; i++) {
            if (strncmp(event_strings[i], events[i], EVENT_LEN) != 0) {
                changed = true; break;
            }
        }
    }
    if (!changed) return;

    for (uint8_t i = 0; i < count; i++) {
        strlcpy(event_strings[i], events[i], EVENT_LEN);
    }
    event_count = count;
    cur_event = 0;
    last_event_swap_ms = millis();
    if (events_label) {
        lv_label_set_text(events_label, count > 0 ? event_strings[0] : "");
    }
}

bool splash_is_active(void) { return active; }

void splash_show(void) {
    splash_pick_for_current_rate();
    if (splash_container) lv_obj_clear_flag(splash_container, LV_OBJ_FLAG_HIDDEN);
    active = true;
}

void splash_hide(void) {
    if (splash_container) lv_obj_add_flag(splash_container, LV_OBJ_FLAG_HIDDEN);
    active = false;
}

lv_obj_t* splash_get_root(void) {
    return splash_container;
}
