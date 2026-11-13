#include "overlay.h"
#include <Arduino.h>
#include <lvgl.h>
#include "theme.h"

LV_FONT_DECLARE(font_tiempos_34);
LV_FONT_DECLARE(font_styrene_48);
LV_FONT_DECLARE(font_styrene_28);
LV_FONT_DECLARE(font_styrene_24);
LV_FONT_DECLARE(font_styrene_20);

#define SCR 480

// A tap that was already in flight when the prompt popped up must not count as
// an answer — ignore presses for this long after it appears.
#define PROMPT_ARM_MS 700

// ---------- permission prompt ----------
static lv_obj_t* pr_root;
static lv_obj_t* pr_project;
static lv_obj_t* pr_branch;
static lv_obj_t* pr_tool;
static lv_obj_t* pr_detail;
static lv_obj_t* pr_bar;

static char     pr_id[16];
static bool     pr_showing = false;
static uint32_t pr_shown_ms = 0;
static uint32_t pr_timeout_ms = 0;
static const char* pr_result = nullptr;     // set by a button, taken by main loop
static char     pr_result_id[16];

// ---------- ota ----------
static lv_obj_t* ota_root;
static lv_obj_t* ota_title;
static lv_obj_t* ota_pct;
static lv_obj_t* ota_bar;

// ---------- stale ----------
static lv_obj_t* stale_pill;
static int       stale_shown = -1;

static lv_obj_t* make_fullscreen(void) {
    lv_obj_t* o = lv_obj_create(lv_screen_active());
    lv_obj_remove_style_all(o);
    lv_obj_set_size(o, SCR, SCR);
    lv_obj_set_pos(o, 0, 0);
    lv_obj_set_style_bg_color(o, THEME_BG, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(o, LV_OBJ_FLAG_CLICKABLE);   // swallow taps meant for the screen below
    lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    return o;
}

static lv_obj_t* make_label(lv_obj_t* parent, const lv_font_t* font, lv_color_t col,
                            const char* text) {
    lv_obj_t* l = lv_label_create(parent);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, col, 0);
    return l;
}

static void finish_prompt(const char* decision) {
    if (!pr_showing) return;
    strlcpy(pr_result_id, pr_id, sizeof(pr_result_id));
    pr_result = decision;
    pr_showing = false;
    lv_obj_add_flag(pr_root, LV_OBJ_FLAG_HIDDEN);
}

static void prompt_btn_cb(lv_event_t* e) {
    if (!pr_showing) return;
    if (millis() - pr_shown_ms < PROMPT_ARM_MS) return;
    finish_prompt((const char*)lv_event_get_user_data(e));
}

static lv_obj_t* make_button(lv_obj_t* parent, int x, int w, const char* text,
                             lv_color_t bg, const char* decision) {
    lv_obj_t* b = lv_obj_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_pos(b, x, 352);
    lv_obj_set_size(b, w, 96);
    lv_obj_set_style_bg_color(b, bg, 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_70, LV_STATE_PRESSED);
    lv_obj_set_style_radius(b, 14, 0);
    lv_obj_clear_flag(b, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(b, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(b, prompt_btn_cb, LV_EVENT_CLICKED, (void*)decision);
    lv_obj_t* l = make_label(b, &font_styrene_28, THEME_TEXT, text);
    lv_obj_center(l);
    return b;
}

void overlay_init(void) {
    // ---- stale pill (created first so the full-screen overlays sit above it) ----
    stale_pill = make_label(lv_screen_active(), &font_styrene_20, THEME_ACCENT, "");
    lv_obj_set_style_bg_color(stale_pill, THEME_PANEL, 0);
    lv_obj_set_style_bg_opa(stale_pill, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(stale_pill, LV_RADIUS_CIRCLE, 0);
    // Full content width, so it cleanly replaces a screen's footer line rather
    // than half-covering it.
    lv_obj_set_width(stale_pill, SCR - 40);
    lv_obj_set_style_text_align(stale_pill, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_pad_ver(stale_pill, 8, 0);
    lv_obj_add_flag(stale_pill, LV_OBJ_FLAG_IGNORE_LAYOUT);
    lv_obj_align(stale_pill, LV_ALIGN_BOTTOM_MID, 0, -12);
    lv_obj_add_flag(stale_pill, LV_OBJ_FLAG_HIDDEN);

    // ---- permission prompt ----
    pr_root = make_fullscreen();

    lv_obj_t* title = make_label(pr_root, &font_tiempos_34, THEME_TEXT, "Allow this?");
    lv_obj_set_pos(title, 24, 22);

    // Which session is asking: the repo, in the accent colour so it is the
    // first thing read when several sessions are running, with its branch
    // underneath on the tool row.
    pr_project = make_label(pr_root, &font_styrene_24, THEME_ACCENT, "");
    lv_obj_set_size(pr_project, 232, 30);             // one line; overflow ends in "..."
    lv_label_set_long_mode(pr_project, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(pr_project, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_align(pr_project, LV_ALIGN_TOP_RIGHT, -24, 30);

    pr_branch = make_label(pr_root, &font_styrene_20, THEME_DIM, "");
    lv_obj_set_size(pr_branch, 250, 26);
    lv_label_set_long_mode(pr_branch, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(pr_branch, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_align(pr_branch, LV_ALIGN_TOP_RIGHT, -24, 82);

    pr_tool = make_label(pr_root, &font_styrene_24, THEME_TEXT, "");
    lv_obj_set_style_bg_color(pr_tool, THEME_BAR_BG, 0);
    lv_obj_set_style_bg_opa(pr_tool, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(pr_tool, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_pad_hor(pr_tool, 14, 0);
    lv_obj_set_style_pad_ver(pr_tool, 4, 0);
    lv_obj_set_pos(pr_tool, 20, 76);

    lv_obj_t* box = lv_obj_create(pr_root);
    lv_obj_remove_style_all(box);
    lv_obj_set_pos(box, 20, 120);
    lv_obj_set_size(box, 440, 208);
    lv_obj_set_style_bg_color(box, THEME_PANEL, 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(box, 8, 0);
    lv_obj_clear_flag(box, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(box, LV_OBJ_FLAG_CLICKABLE);

    pr_detail = make_label(box, &font_styrene_24, THEME_TEXT, "");
    lv_obj_set_pos(pr_detail, 16, 12);
    lv_obj_set_size(pr_detail, 408, 178);            // 7 lines; overflow ends in "..."
    lv_label_set_long_mode(pr_detail, LV_LABEL_LONG_DOT);

    // Countdown — shrinks to nothing when the question goes back to the terminal.
    pr_bar = lv_bar_create(pr_root);
    lv_obj_set_pos(pr_bar, 20, 336);
    lv_obj_set_size(pr_bar, 440, 6);
    lv_bar_set_range(pr_bar, 0, 1000);
    lv_obj_set_style_bg_color(pr_bar, THEME_BAR_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(pr_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(pr_bar, THEME_DIM, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(pr_bar, LV_OPA_COVER, LV_PART_INDICATOR);

    make_button(pr_root, 20,  150, "Deny",  THEME_RED,    "deny");
    make_button(pr_root, 185, 110, "Skip",  THEME_BAR_BG, "skip");
    make_button(pr_root, 310, 150, "Allow", THEME_GREEN,  "allow");

    // ---- ota progress ----
    ota_root = make_fullscreen();
    ota_title = make_label(ota_root, &font_tiempos_34, THEME_TEXT, "Updating firmware");
    lv_obj_align(ota_title, LV_ALIGN_TOP_MID, 0, 130);
    ota_pct = make_label(ota_root, &font_styrene_48, THEME_TEXT, "0%");
    lv_obj_align(ota_pct, LV_ALIGN_TOP_MID, 0, 200);
    ota_bar = lv_bar_create(ota_root);
    lv_obj_set_size(ota_bar, 360, 16);
    lv_obj_align(ota_bar, LV_ALIGN_TOP_MID, 0, 280);
    lv_bar_set_range(ota_bar, 0, 100);
    lv_obj_set_style_bg_color(ota_bar, THEME_BAR_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(ota_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(ota_bar, THEME_ACCENT, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(ota_bar, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_t* hint = make_label(ota_root, &font_styrene_20, THEME_DIM, "Keep the device powered");
    lv_obj_align(hint, LV_ALIGN_TOP_MID, 0, 320);
}

void overlay_tick(void) {
    if (!pr_showing) return;
    uint32_t el = millis() - pr_shown_ms;
    if (el >= pr_timeout_ms) {
        // The daemon has already handed the question back to the terminal by
        // now; just get out of the way (no answer is sent).
        pr_showing = false;
        lv_obj_add_flag(pr_root, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_bar_set_value(pr_bar, 1000 - (int)((uint64_t)el * 1000 / pr_timeout_ms), LV_ANIM_OFF);
}

void prompt_show(const char* id, const char* tool, const char* detail,
                 const char* project, const char* branch, int timeout_s) {
    if (timeout_s < 3) timeout_s = 3;
    strlcpy(pr_id, id ? id : "", sizeof(pr_id));
    lv_label_set_text(pr_tool, (tool && tool[0]) ? tool : "Tool");
    lv_label_set_text(pr_detail, detail ? detail : "");
    lv_label_set_text(pr_project, project ? project : "");
    lv_label_set_text(pr_branch, branch ? branch : "");
    // The branch shares its row with the tool pill. A long tool name (MCP
    // tools) needs that room more, so the branch gives way to it.
    lv_obj_update_layout(pr_tool);
    if (lv_obj_get_width(pr_tool) > 180) lv_obj_add_flag(pr_branch, LV_OBJ_FLAG_HIDDEN);
    else                                 lv_obj_clear_flag(pr_branch, LV_OBJ_FLAG_HIDDEN);
    lv_bar_set_value(pr_bar, 1000, LV_ANIM_OFF);
    pr_timeout_ms = (uint32_t)timeout_s * 1000UL;
    pr_shown_ms = millis();
    pr_result = nullptr;
    pr_showing = true;
    lv_obj_clear_flag(pr_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(pr_root);
}

void prompt_cancel(const char* id) {
    if (!pr_showing) return;
    if (id && id[0] && strcmp(id, pr_id) != 0) return;
    pr_showing = false;
    lv_obj_add_flag(pr_root, LV_OBJ_FLAG_HIDDEN);
}

bool prompt_active(void) { return pr_showing; }

void prompt_skip(void) { finish_prompt("skip"); }

bool prompt_take_result(char* id_out, int id_len, const char** decision) {
    if (!pr_result) return false;
    strlcpy(id_out, pr_result_id, id_len);
    *decision = pr_result;
    pr_result = nullptr;
    return true;
}

void overlay_ota_show(int pct, const char* msg) {
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    lv_label_set_text(ota_title, msg ? msg : "Updating firmware");
    lv_label_set_text_fmt(ota_pct, "%d%%", pct);
    lv_bar_set_value(ota_bar, pct, LV_ANIM_OFF);
    lv_obj_clear_flag(ota_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(ota_root);
}

void overlay_ota_hide(void) {
    lv_obj_add_flag(ota_root, LV_OBJ_FLAG_HIDDEN);
}

void overlay_set_stale(int mins) {
    if (mins == stale_shown) return;
    stale_shown = mins;
    if (mins < 0) {
        lv_obj_add_flag(stale_pill, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    if (mins < 60)        lv_label_set_text_fmt(stale_pill, "No update for %dm", mins);
    else if (mins < 1440) lv_label_set_text_fmt(stale_pill, "No update for %dh %dm", mins / 60, mins % 60);
    else                  lv_label_set_text_fmt(stale_pill, "No update for %dd", mins / 1440);
    lv_obj_clear_flag(stale_pill, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(stale_pill);
}
