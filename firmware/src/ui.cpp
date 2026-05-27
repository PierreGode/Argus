#include "ui.h"
#include "splash.h"
#include <lvgl.h>
#include "icons.h"
#include "display_cfg.h"

// Custom fonts (scaled for 314 PPI, ~1.9x from original 165 PPI)
LV_FONT_DECLARE(font_tiempos_56);
LV_FONT_DECLARE(font_styrene_48);
LV_FONT_DECLARE(font_styrene_28);
LV_FONT_DECLARE(font_styrene_24);
LV_FONT_DECLARE(font_styrene_20);

// Anthropic brand palette — design tokens live in theme.h
#include "theme.h"
#define COL_BG        THEME_BG
#define COL_PANEL     THEME_PANEL
#define COL_TEXT      THEME_TEXT
#define COL_DIM       THEME_DIM
#define COL_ACCENT    THEME_ACCENT
#define COL_GREEN     THEME_GREEN
#define COL_AMBER     THEME_AMBER
#define COL_RED       THEME_RED
#define COL_BAR_BG    THEME_BAR_BG

// ---- Layout constants for 480x480 (scaled for 2.16" high-DPI + rounded corners) ----
#define SCR_W         480
#define SCR_H         480
#define MARGIN        20    // wider margin for rounded display corners
#define TITLE_Y       30
#define CONTENT_Y     100
#define CONTENT_W     (SCR_W - 2 * MARGIN)   // 440

// ---- Usage screen widgets ----
static lv_obj_t* usage_container;
static lv_obj_t* lbl_title;
static lv_obj_t* bar_session;
static lv_obj_t* lbl_session_pct;
static lv_obj_t* lbl_session_label;
static lv_obj_t* lbl_session_reset;
static lv_obj_t* bar_weekly;
static lv_obj_t* lbl_weekly_pct;
static lv_obj_t* lbl_weekly_label;
static lv_obj_t* lbl_weekly_reset;
// Bottom strip of the Claude Usage screen. Used to host the spinner +
// "Thinking…" rotating verb; now repurposed as the session-footer text
// ("<project> - N sessions") moved over from the Today screen.
static lv_obj_t* lbl_usage_footer;

// ---- GitHub screen widgets ----
static lv_obj_t* github_container;
static lv_obj_t* lbl_gh_issues;
static lv_obj_t* lbl_gh_prs;
static lv_obj_t* lbl_gh_status;

// ---- Brightness dim overlay (full-screen semi-transparent rect on top) ----
// Sits above every screen, intercepts no events, opacity is set from the
// daemon's brightness field. 0 = pitch black, 100 = invisible.
static lv_obj_t* dim_overlay;

// ---- Today screen widgets ----
static lv_obj_t* today_container;
static lv_obj_t* lbl_today_cost;
static lv_obj_t* lbl_today_week;
static lv_obj_t* lbl_today_cache_pct;
static lv_obj_t* bar_today_cache;
static lv_obj_t* lbl_today_models;

// ---- Bluetooth screen widgets ----
static lv_obj_t* ble_container;
static lv_obj_t* copilot_container;
// Copilot screen — single big "Premium requests / NN.N%" panel, top-model
// line, and a single-row status strip at the bottom.
static lv_obj_t* lbl_cp_premium_pct;     // huge "60.4%"
static lv_obj_t* lbl_cp_premium_counts;  // "604 / 1000 this month"
static lv_obj_t* lbl_cp_top_model;       // "Claude Opus 4.6"
static lv_obj_t* lbl_cp_strip;           // bottom row "VS Code · 5 min ago"
static lv_obj_t* lbl_cp_hint;            // fallback hint when nothing is wired
static lv_obj_t* lbl_ble_status;
static lv_obj_t* lbl_ble_device;
static lv_obj_t* lbl_ble_mac;

// ---- Shared ----
static screen_t current_screen = SCREEN_USAGE;

static lv_color_t pct_color(float pct) {
    if (pct >= 80.0f) return COL_RED;
    if (pct >= 50.0f) return COL_AMBER;
    return COL_GREEN;
}

static void format_reset_time(int mins, char* buf, size_t len) {
    if (mins < 0) {
        snprintf(buf, len, "---");
    } else if (mins < 60) {
        snprintf(buf, len, "Resets in %dm", mins);
    } else if (mins < 1440) {
        snprintf(buf, len, "Resets in %dh %dm", mins / 60, mins % 60);
    } else {
        snprintf(buf, len, "Resets in %dd %dh", mins / 1440, (mins % 1440) / 60);
    }
}

// Forward decls — callbacks defined near ui_show_screen below
static void global_click_cb(lv_event_t* e);
static void ble_reset_click_cb(lv_event_t* e);

static lv_obj_t* make_panel(lv_obj_t* parent, int x, int y, int w, int h) {
    lv_obj_t* panel = lv_obj_create(parent);
    lv_obj_set_pos(panel, x, y);
    lv_obj_set_size(panel, w, h);
    lv_obj_set_style_bg_color(panel, COL_PANEL, 0);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(panel, 8, 0);
    lv_obj_set_style_border_width(panel, 0, 0);
    lv_obj_set_style_pad_left(panel, 16, 0);
    lv_obj_set_style_pad_right(panel, 16, 0);
    lv_obj_set_style_pad_top(panel, 12, 0);
    lv_obj_set_style_pad_bottom(panel, 12, 0);
    lv_obj_clear_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    // Bubble click events up to the screen / usage_container so a tap anywhere
    // on the panel fires the global click handler.
    lv_obj_add_flag(panel, LV_OBJ_FLAG_EVENT_BUBBLE);
    return panel;
}

static lv_obj_t* make_bar(lv_obj_t* parent, int x, int y, int w, int h) {
    lv_obj_t* bar = lv_bar_create(parent);
    lv_obj_set_pos(bar, x, y);
    lv_obj_set_size(bar, w, h);
    lv_bar_set_range(bar, 0, 100);
    lv_bar_set_value(bar, 0, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(bar, COL_BAR_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(bar, 6, LV_PART_MAIN);
    lv_obj_set_style_bg_color(bar, COL_GREEN, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(bar, 6, LV_PART_INDICATOR);
    return bar;
}

static void init_icon_dsc(lv_image_dsc_t* dsc, int w, int h, const uint16_t* data) {
    dsc->header.w = w;
    dsc->header.h = h;
    dsc->header.cf = LV_COLOR_FORMAT_RGB565;
    dsc->header.stride = w * 2;
    dsc->data = (const uint8_t*)data;
    dsc->data_size = w * h * 2;
}

// RGB565A8: planar — w*h RGB565 pixels followed by w*h alpha bytes.
// Stride is RGB565-only (w*2); LVGL infers alpha plane location from header.
static void init_icon_dsc_rgb565a8(lv_image_dsc_t* dsc, int w, int h, const uint8_t* data) {
    dsc->header.w = w;
    dsc->header.h = h;
    dsc->header.cf = LV_COLOR_FORMAT_RGB565A8;
    dsc->header.stride = w * 2;
    dsc->data = data;
    dsc->data_size = w * h * 3;
}

static lv_obj_t* make_pill(lv_obj_t* parent, const char* text) {
    lv_obj_t* lbl = lv_label_create(parent);
    lv_label_set_text(lbl, text);
    lv_obj_set_style_text_font(lbl, &font_styrene_28, 0);
    lv_obj_set_style_text_color(lbl, COL_TEXT, 0);
    lv_obj_set_style_bg_color(lbl, COL_BAR_BG, 0);
    lv_obj_set_style_bg_opa(lbl, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(lbl, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_pad_left(lbl, 18, 0);
    lv_obj_set_style_pad_right(lbl, 18, 0);
    lv_obj_set_style_pad_top(lbl, 6, 0);
    lv_obj_set_style_pad_bottom(lbl, 6, 0);
    return lbl;
}

// ======== Usage Screen (480x480) ========

#define PANEL_H     150
#define PANEL_GAP   16

// One Session/Weekly panel: big % label, pill on the right, bar, reset label.
// Pill y=1: symmetric inside the panel — panel-outer-top → pill-top equals
// pill-bottom → bar-top (pill height 42 + panel pad_top 12 + bar y=56).
static void make_usage_panel(lv_obj_t* parent, int y, const char* pill_text,
                             lv_obj_t** out_pct, lv_obj_t** out_pill,
                             lv_obj_t** out_bar, lv_obj_t** out_reset) {
    lv_obj_t* panel = make_panel(parent, MARGIN, y, CONTENT_W, PANEL_H);

    *out_pct = lv_label_create(panel);
    lv_label_set_text(*out_pct, "No data");
    lv_obj_set_style_text_font(*out_pct, &font_styrene_48, 0);
    lv_obj_set_style_text_color(*out_pct, COL_TEXT, 0);
    lv_obj_set_pos(*out_pct, 0, 0);

    *out_pill = make_pill(panel, pill_text);
    lv_obj_align(*out_pill, LV_ALIGN_TOP_RIGHT, 0, 1);

    *out_bar = make_bar(panel, 0, 56, CONTENT_W - 32, 24);

    *out_reset = lv_label_create(panel);
    lv_label_set_text(*out_reset, "");
    lv_obj_set_style_text_font(*out_reset, &font_styrene_28, 0);
    lv_obj_set_style_text_color(*out_reset, COL_DIM, 0);
    lv_obj_set_pos(*out_reset, 0, 94);
}

static void init_usage_screen(lv_obj_t* scr) {
    usage_container = lv_obj_create(scr);
    lv_obj_set_size(usage_container, SCR_W, SCR_H);
    lv_obj_set_pos(usage_container, 0, 0);
    lv_obj_set_style_bg_opa(usage_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(usage_container, 0, 0);
    lv_obj_set_style_pad_all(usage_container, 0, 0);
    lv_obj_clear_flag(usage_container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(usage_container, global_click_cb, LV_EVENT_CLICKED, NULL);

    lbl_title = lv_label_create(usage_container);
    lv_label_set_text(lbl_title, "Claude Usage");
    lv_obj_set_style_text_font(lbl_title, &font_tiempos_56, 0);
    lv_obj_set_style_text_color(lbl_title, COL_TEXT, 0);
    lv_obj_align(lbl_title, LV_ALIGN_TOP_MID, 16, TITLE_Y);

    make_usage_panel(usage_container, CONTENT_Y, "Current",
                     &lbl_session_pct, &lbl_session_label,
                     &bar_session, &lbl_session_reset);
    make_usage_panel(usage_container, CONTENT_Y + PANEL_H + PANEL_GAP, "Weekly",
                     &lbl_weekly_pct, &lbl_weekly_label,
                     &bar_weekly, &lbl_weekly_reset);

    // Session footer — was on the Today screen as "<project> - N sessions".
    // The spinner + verb animation that used to live here is gone (the user
    // didn't want it). Same style as the old Today footer.
    lbl_usage_footer = lv_label_create(usage_container);
    lv_label_set_text(lbl_usage_footer, "");
    lv_obj_set_style_text_font(lbl_usage_footer, &font_styrene_24, 0);
    lv_obj_set_style_text_color(lbl_usage_footer, COL_DIM, 0);
    lv_obj_align(lbl_usage_footer, LV_ALIGN_BOTTOM_MID, 0, -20);
}

// ======== Today Screen (480x480) ========
//
// Layout mirrors Usage: two 150px panels + footer text. Panel 1 = cost today,
// Panel 2 = cache hit % with a bar, footer line shows model split + project +
// session count. Data flows in via the same ui_update() pipeline.
static void init_today_screen(lv_obj_t* scr) {
    today_container = lv_obj_create(scr);
    lv_obj_set_size(today_container, SCR_W, SCR_H);
    lv_obj_set_pos(today_container, 0, 0);
    lv_obj_set_style_bg_opa(today_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(today_container, 0, 0);
    lv_obj_set_style_pad_all(today_container, 0, 0);
    lv_obj_clear_flag(today_container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(today_container, global_click_cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t* lbl_title = lv_label_create(today_container);
    lv_label_set_text(lbl_title, "Today");
    lv_obj_set_style_text_font(lbl_title, &font_tiempos_56, 0);
    lv_obj_set_style_text_color(lbl_title, COL_TEXT, 0);
    lv_obj_align(lbl_title, LV_ALIGN_TOP_MID, 16, TITLE_Y);

    // Panel 1 — cost today
    lv_obj_t* p_cost = make_panel(today_container, MARGIN, CONTENT_Y, CONTENT_W, PANEL_H);
    lbl_today_cost = lv_label_create(p_cost);
    lv_label_set_text(lbl_today_cost, "No data");
    lv_obj_set_style_text_font(lbl_today_cost, &font_styrene_48, 0);
    lv_obj_set_style_text_color(lbl_today_cost, COL_TEXT, 0);
    lv_obj_set_pos(lbl_today_cost, 0, 0);

    // Labeled "API equiv." because on a Max subscription you didn't actually
    // spend this — it's the pay-as-you-go API equivalent of today's tokens.
    lv_obj_t* pill_cost = make_pill(p_cost, "API equiv.");
    lv_obj_align(pill_cost, LV_ALIGN_TOP_RIGHT, 0, 1);

    lbl_today_week = lv_label_create(p_cost);
    lv_label_set_text(lbl_today_week, "");
    lv_obj_set_style_text_font(lbl_today_week, &font_styrene_28, 0);
    lv_obj_set_style_text_color(lbl_today_week, COL_DIM, 0);
    lv_obj_set_pos(lbl_today_week, 0, 94);

    // Panel 2 — cache hit rate
    lv_obj_t* p_cache = make_panel(today_container, MARGIN,
                                   CONTENT_Y + PANEL_H + PANEL_GAP,
                                   CONTENT_W, PANEL_H);
    lbl_today_cache_pct = lv_label_create(p_cache);
    lv_label_set_text(lbl_today_cache_pct, "No data");
    lv_obj_set_style_text_font(lbl_today_cache_pct, &font_styrene_48, 0);
    lv_obj_set_style_text_color(lbl_today_cache_pct, COL_TEXT, 0);
    lv_obj_set_pos(lbl_today_cache_pct, 0, 0);

    lv_obj_t* pill_cache = make_pill(p_cache, "Cache");
    lv_obj_align(pill_cache, LV_ALIGN_TOP_RIGHT, 0, 1);

    bar_today_cache = make_bar(p_cache, 0, 56, CONTENT_W - 32, 24);

    lbl_today_models = lv_label_create(p_cache);
    lv_label_set_text(lbl_today_models, "");
    lv_obj_set_style_text_font(lbl_today_models, &font_styrene_24, 0);
    lv_obj_set_style_text_color(lbl_today_models, COL_DIM, 0);
    lv_obj_set_pos(lbl_today_models, 0, 94);

    // (Session-footer line moved to the Claude Usage screen — see
    // init_usage_screen's lbl_usage_footer.)

    lv_obj_add_flag(today_container, LV_OBJ_FLAG_HIDDEN);
}

// ======== GitHub Screen (480x480) ========
//
// Two big-number panels (Issues / Review queue), plus a status row beneath.
// When the daemon hasn't been configured with a PAT, all panels show "—" and
// the status reads "Set a token in the daemon settings". Otherwise the
// numbers reflect issues assigned to the user and PRs awaiting their review.
static void init_github_screen(lv_obj_t* scr) {
    github_container = lv_obj_create(scr);
    lv_obj_set_size(github_container, SCR_W, SCR_H);
    lv_obj_set_pos(github_container, 0, 0);
    lv_obj_set_style_bg_opa(github_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(github_container, 0, 0);
    lv_obj_set_style_pad_all(github_container, 0, 0);
    lv_obj_clear_flag(github_container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(github_container, global_click_cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t* lbl_title = lv_label_create(github_container);
    lv_label_set_text(lbl_title, "GitHub");
    lv_obj_set_style_text_font(lbl_title, &font_tiempos_56, 0);
    lv_obj_set_style_text_color(lbl_title, COL_TEXT, 0);
    lv_obj_align(lbl_title, LV_ALIGN_TOP_MID, 16, TITLE_Y);

    // Panel 1 — issues assigned to the user
    lv_obj_t* p_issues = make_panel(github_container, MARGIN, CONTENT_Y, CONTENT_W, PANEL_H);
    lbl_gh_issues = lv_label_create(p_issues);
    lv_label_set_text(lbl_gh_issues, "No data");
    lv_obj_set_style_text_font(lbl_gh_issues, &font_styrene_48, 0);
    lv_obj_set_style_text_color(lbl_gh_issues, COL_TEXT, 0);
    lv_obj_set_pos(lbl_gh_issues, 0, 0);

    lv_obj_t* pill_issues = make_pill(p_issues, "Issues");
    lv_obj_align(pill_issues, LV_ALIGN_TOP_RIGHT, 0, 1);

    lv_obj_t* sub_issues = lv_label_create(p_issues);
    lv_label_set_text(sub_issues, "Assigned to you");
    lv_obj_set_style_text_font(sub_issues, &font_styrene_28, 0);
    lv_obj_set_style_text_color(sub_issues, COL_DIM, 0);
    lv_obj_set_pos(sub_issues, 0, 94);

    // Panel 2 — PRs awaiting review / owned by user
    lv_obj_t* p_prs = make_panel(github_container, MARGIN,
                                  CONTENT_Y + PANEL_H + PANEL_GAP,
                                  CONTENT_W, PANEL_H);
    lbl_gh_prs = lv_label_create(p_prs);
    lv_label_set_text(lbl_gh_prs, "No data");
    lv_obj_set_style_text_font(lbl_gh_prs, &font_styrene_48, 0);
    lv_obj_set_style_text_color(lbl_gh_prs, COL_TEXT, 0);
    lv_obj_set_pos(lbl_gh_prs, 0, 0);

    lv_obj_t* pill_prs = make_pill(p_prs, "PRs");
    lv_obj_align(pill_prs, LV_ALIGN_TOP_RIGHT, 0, 1);

    lv_obj_t* sub_prs = lv_label_create(p_prs);
    lv_label_set_text(sub_prs, "Awaiting your review");
    lv_obj_set_style_text_font(sub_prs, &font_styrene_28, 0);
    lv_obj_set_style_text_color(sub_prs, COL_DIM, 0);
    lv_obj_set_pos(sub_prs, 0, 94);

    lbl_gh_status = lv_label_create(github_container);
    lv_label_set_text(lbl_gh_status, "Set a token in the daemon settings");
    lv_obj_set_style_text_font(lbl_gh_status, &font_styrene_24, 0);
    lv_obj_set_style_text_color(lbl_gh_status, COL_DIM, 0);
    lv_obj_align(lbl_gh_status, LV_ALIGN_BOTTOM_MID, 0, -20);

    lv_obj_add_flag(github_container, LV_OBJ_FLAG_HIDDEN);
}

// ======== Copilot Screen (480x480) ========
//
// One-panel layout: big status word (ACTIVE / IDLE / INACTIVE / OFF), relative
// "last seen" line, and the editor that was last in use. Powered by the
// daemon's "cp_*" payload fields; if Copilot polling is disabled in the tray
// app the panel falls back to a hint pointing the user at the settings.

static void init_copilot_screen(lv_obj_t* scr) {
    copilot_container = lv_obj_create(scr);
    lv_obj_set_size(copilot_container, SCR_W, SCR_H);
    lv_obj_set_pos(copilot_container, 0, 0);
    lv_obj_set_style_bg_opa(copilot_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(copilot_container, 0, 0);
    lv_obj_set_style_pad_all(copilot_container, 0, 0);
    lv_obj_clear_flag(copilot_container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(copilot_container, global_click_cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t* lbl_title = lv_label_create(copilot_container);
    lv_label_set_text(lbl_title, "Copilot");
    lv_obj_set_style_text_font(lbl_title, &font_tiempos_56, 0);
    lv_obj_set_style_text_color(lbl_title, COL_TEXT, 0);
    lv_obj_align(lbl_title, LV_ALIGN_TOP_MID, 16, TITLE_Y);

    // Big Premium-requests panel — "Usage" pill on the right, "Premium
    // requests" subtitle, huge percentage, "X / Y this month" counter.
    lv_obj_t* p_premium = make_panel(copilot_container, MARGIN, CONTENT_Y,
                                     CONTENT_W, PANEL_H + 60);

    lv_obj_t* pill_usage = make_pill(p_premium, "Usage");
    lv_obj_align(pill_usage, LV_ALIGN_TOP_RIGHT, 0, 1);

    lv_obj_t* sub_premium = lv_label_create(p_premium);
    lv_label_set_text(sub_premium, "Premium requests");
    lv_obj_set_style_text_font(sub_premium, &font_styrene_24, 0);
    lv_obj_set_style_text_color(sub_premium, COL_DIM, 0);
    lv_obj_set_pos(sub_premium, 0, 0);

    lbl_cp_premium_pct = lv_label_create(p_premium);
    lv_label_set_text(lbl_cp_premium_pct, "—");
    lv_obj_set_style_text_font(lbl_cp_premium_pct, &font_tiempos_56, 0);
    lv_obj_set_style_text_color(lbl_cp_premium_pct, COL_TEXT, 0);
    lv_obj_set_pos(lbl_cp_premium_pct, 0, 32);

    lbl_cp_premium_counts = lv_label_create(p_premium);
    lv_label_set_text(lbl_cp_premium_counts, "");
    lv_obj_set_style_text_font(lbl_cp_premium_counts, &font_styrene_24, 0);
    lv_obj_set_style_text_color(lbl_cp_premium_counts, COL_DIM, 0);
    lv_obj_set_pos(lbl_cp_premium_counts, 0, 130);

    // Top-model row — just under the panel.
    lbl_cp_top_model = lv_label_create(copilot_container);
    lv_label_set_text(lbl_cp_top_model, "");
    lv_obj_set_style_text_font(lbl_cp_top_model, &font_styrene_28, 0);
    lv_obj_set_style_text_color(lbl_cp_top_model, COL_ACCENT, 0);
    lv_obj_align(lbl_cp_top_model, LV_ALIGN_TOP_MID, 0,
                 CONTENT_Y + PANEL_H + 60 + 14);

    // Bottom strip — compact "<status> · <editor> · <when>".
    lbl_cp_strip = lv_label_create(copilot_container);
    lv_label_set_text(lbl_cp_strip, "");
    lv_obj_set_style_text_font(lbl_cp_strip, &font_styrene_20, 0);
    lv_obj_set_style_text_color(lbl_cp_strip, COL_DIM, 0);
    lv_obj_align(lbl_cp_strip, LV_ALIGN_BOTTOM_MID, 0, -50);

    // Fallback hint — sits in the same row as the strip when nothing is
    // wired up. ui_update() flips between strip and hint via text + opa.
    lbl_cp_hint = lv_label_create(copilot_container);
    lv_label_set_text(lbl_cp_hint, "Set an org in the daemon settings");
    lv_obj_set_style_text_font(lbl_cp_hint, &font_styrene_24, 0);
    lv_obj_set_style_text_color(lbl_cp_hint, COL_DIM, 0);
    lv_obj_align(lbl_cp_hint, LV_ALIGN_BOTTOM_MID, 0, -20);

    lv_obj_add_flag(copilot_container, LV_OBJ_FLAG_HIDDEN);
}

// ======== Bluetooth Screen (480x480) ========

static void init_bluetooth_screen(lv_obj_t* scr) {
    ble_container = lv_obj_create(scr);
    lv_obj_set_size(ble_container, SCR_W, SCR_H);
    lv_obj_set_pos(ble_container, 0, 0);
    lv_obj_set_style_bg_opa(ble_container, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(ble_container, 0, 0);
    lv_obj_set_style_pad_all(ble_container, 0, 0);
    lv_obj_clear_flag(ble_container, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(ble_container, global_click_cb, LV_EVENT_CLICKED, NULL);

    // Title
    lv_obj_t* lbl_ble_title = lv_label_create(ble_container);
    lv_label_set_text(lbl_ble_title, "Bluetooth");
    lv_obj_set_style_text_font(lbl_ble_title, &font_tiempos_56, 0);
    lv_obj_set_style_text_color(lbl_ble_title, COL_TEXT, 0);
    lv_obj_align(lbl_ble_title, LV_ALIGN_TOP_MID, 16, TITLE_Y);

    // Info panel (taller for 480x480)
    lv_obj_t* p_info = make_panel(ble_container, MARGIN, CONTENT_Y, CONTENT_W, 160);

    // Bluetooth icon + status row
    static lv_image_dsc_t icon_bt_dsc;
    init_icon_dsc(&icon_bt_dsc, ICON_BLUETOOTH_W, ICON_BLUETOOTH_H, icon_bluetooth_data);

    lv_obj_t* bt_img = lv_image_create(p_info);
    lv_image_set_src(bt_img, &icon_bt_dsc);
    lv_obj_set_pos(bt_img, 0, 0);

    lbl_ble_status = lv_label_create(p_info);
    lv_label_set_text(lbl_ble_status, "Initializing...");
    lv_obj_set_style_text_font(lbl_ble_status, &font_styrene_48, 0);
    lv_obj_set_style_text_color(lbl_ble_status, COL_DIM, 0);
    lv_obj_set_pos(lbl_ble_status, 56, 2);

    lbl_ble_device = lv_label_create(p_info);
    lv_label_set_text(lbl_ble_device, "Device: ---");
    lv_obj_set_style_text_font(lbl_ble_device, &font_styrene_28, 0);
    lv_obj_set_style_text_color(lbl_ble_device, COL_DIM, 0);
    lv_obj_set_pos(lbl_ble_device, 0, 64);

    lbl_ble_mac = lv_label_create(p_info);
    lv_label_set_text(lbl_ble_mac, "Address: ---");
    lv_obj_set_style_text_font(lbl_ble_mac, &font_styrene_28, 0);
    lv_obj_set_style_text_color(lbl_ble_mac, COL_DIM, 0);
    lv_obj_set_pos(lbl_ble_mac, 0, 100);

    // Reset Bluetooth tap zone with trash icon
    int reset_y = CONTENT_Y + 160 + 16;
    lv_obj_t* reset_zone = lv_obj_create(ble_container);
    lv_obj_set_pos(reset_zone, MARGIN, reset_y);
    lv_obj_set_size(reset_zone, CONTENT_W, 110);
    lv_obj_set_style_bg_color(reset_zone, COL_PANEL, 0);
    lv_obj_set_style_bg_opa(reset_zone, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(reset_zone, 8, 0);
    lv_obj_set_style_border_width(reset_zone, 0, 0);
    lv_obj_set_style_pad_column(reset_zone, 14, 0);
    lv_obj_set_flex_flow(reset_zone, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(reset_zone, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_clear_flag(reset_zone, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(reset_zone, ble_reset_click_cb, LV_EVENT_CLICKED, NULL);

    static lv_image_dsc_t icon_trash_dsc;
    init_icon_dsc(&icon_trash_dsc, ICON_TRASH2_W, ICON_TRASH2_H, icon_trash2_data);
    lv_obj_t* trash_img = lv_image_create(reset_zone);
    lv_image_set_src(trash_img, &icon_trash_dsc);

    lv_obj_t* reset_lbl = lv_label_create(reset_zone);
    lv_label_set_text(reset_lbl, "Reset Bluetooth");
    lv_obj_set_style_text_font(reset_lbl, &font_styrene_28, 0);
    lv_obj_set_style_text_color(reset_lbl, COL_DIM, 0);

    // Start hidden
    lv_obj_add_flag(ble_container, LV_OBJ_FLAG_HIDDEN);
}

// ======== Public API ========

void ui_init(void) {
    lv_obj_t* scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, COL_BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

    init_usage_screen(scr);
    init_today_screen(scr);
    init_github_screen(scr);
    init_copilot_screen(scr);
    init_bluetooth_screen(scr);
    splash_init(scr);

    // Splash is touch-toggled — tap anywhere on the splash dismisses it
    if (splash_get_root()) {
        lv_obj_add_event_cb(splash_get_root(), global_click_cb, LV_EVENT_CLICKED, NULL);
    }

    // Brightness dim overlay — top-most layer. Click events pass through so
    // the dimmer doesn't eat taps on the screens below.
    dim_overlay = lv_obj_create(scr);
    lv_obj_set_size(dim_overlay, SCR_W, SCR_H);
    lv_obj_set_pos(dim_overlay, 0, 0);
    lv_obj_set_style_bg_color(dim_overlay, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(dim_overlay, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(dim_overlay, 0, 0);
    lv_obj_set_style_pad_all(dim_overlay, 0, 0);
    lv_obj_clear_flag(dim_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(dim_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(dim_overlay, LV_OBJ_FLAG_IGNORE_LAYOUT);
}

void ui_update(const UsageData* data) {
    if (!data->valid) {
        // No payload received yet — show "No data" instead of fake zeros so it's
        // obvious the daemon hasn't talked to us. Bars stay at 0.
        lv_label_set_text(lbl_session_pct,     "No data");
        lv_label_set_text(lbl_session_reset,   "");
        lv_bar_set_value(bar_session, 0, LV_ANIM_OFF);
        lv_label_set_text(lbl_weekly_pct,      "No data");
        lv_label_set_text(lbl_weekly_reset,    "");
        lv_bar_set_value(bar_weekly, 0, LV_ANIM_OFF);

        lv_label_set_text(lbl_today_cost,      "No data");
        lv_label_set_text(lbl_today_week,      "");
        lv_label_set_text(lbl_today_cache_pct, "No data");
        lv_bar_set_value(bar_today_cache, 0, LV_ANIM_OFF);
        lv_label_set_text(lbl_today_models,    "");
        lv_label_set_text(lbl_usage_footer,    "");

        lv_label_set_text(lbl_gh_issues,       "No data");
        lv_label_set_text(lbl_gh_prs,          "No data");
        lv_label_set_text(lbl_gh_status,       "Waiting for daemon");
        lv_label_set_text(lbl_cp_premium_pct,    "—");
        lv_label_set_text(lbl_cp_premium_counts, "");
        lv_label_set_text(lbl_cp_top_model,      "");
        lv_label_set_text(lbl_cp_strip,          "");
        lv_label_set_text(lbl_cp_hint,           "Waiting for daemon");
        return;
    }

    int s_pct = (int)(data->session_pct + 0.5f);

    // Usage screen
    lv_label_set_text_fmt(lbl_session_pct, "%d%%", s_pct);
    lv_bar_set_value(bar_session, s_pct, LV_ANIM_ON);
    lv_obj_set_style_bg_color(bar_session, pct_color(data->session_pct), LV_PART_INDICATOR);

    char buf[48];
    format_reset_time(data->session_reset_mins, buf, sizeof(buf));
    lv_label_set_text(lbl_session_reset, buf);

    int w_pct = (int)(data->weekly_pct + 0.5f);
    lv_label_set_text_fmt(lbl_weekly_pct, "%d%%", w_pct);
    lv_bar_set_value(bar_weekly, w_pct, LV_ANIM_ON);
    lv_obj_set_style_bg_color(bar_weekly, pct_color(data->weekly_pct), LV_PART_INDICATOR);

    format_reset_time(data->weekly_reset_mins, buf, sizeof(buf));
    lv_label_set_text(lbl_weekly_reset, buf);

    // ---- Today screen ----
    // Cost — show 3 decimals under a dollar (you can spot a $0.12 day), 2
    // otherwise. NOTE: LVGL's lv_label_set_text_fmt does NOT support %f unless
    // LV_USE_FLOAT is enabled in the build flags, so we route float formatting
    // through libc snprintf and then set the label text directly.
    char cost_buf[24];
    if (data->cost_today < 1.0f) {
        snprintf(cost_buf, sizeof(cost_buf), "$%.3f", data->cost_today);
    } else {
        snprintf(cost_buf, sizeof(cost_buf), "$%.2f", data->cost_today);
    }
    lv_label_set_text(lbl_today_cost, cost_buf);

    char week_buf[24];
    snprintf(week_buf, sizeof(week_buf), "Week: $%.2f", data->cost_week);
    lv_label_set_text(lbl_today_week, week_buf);

    int cache_pct = data->cache_hit_pct;
    if (cache_pct > 100) cache_pct = 100;
    lv_label_set_text_fmt(lbl_today_cache_pct, "%d%%", cache_pct);
    lv_bar_set_value(bar_today_cache, cache_pct, LV_ANIM_ON);
    // Cache hit colors are inverted from utilization — more is better. Green at
    // >=70%, amber at 30..69, red below that.
    lv_color_t cache_col = COL_GREEN;
    if (cache_pct < 30)      cache_col = COL_RED;
    else if (cache_pct < 70) cache_col = COL_AMBER;
    lv_obj_set_style_bg_color(bar_today_cache, cache_col, LV_PART_INDICATOR);

    lv_label_set_text_fmt(lbl_today_models,
                          "Opus %d%%  Sonnet %d%%  Haiku %d%%",
                          (int)data->opus_pct,
                          (int)data->sonnet_pct,
                          (int)data->haiku_pct);

    char footer[64];
    const char* proj = (data->project[0] != '\0') ? data->project : "(no project)";
    if (data->sessions_today == 1) {
        snprintf(footer, sizeof(footer), "%s  -  1 session", proj);
    } else {
        snprintf(footer, sizeof(footer), "%s  -  %u sessions", proj, (unsigned)data->sessions_today);
    }
    lv_label_set_text(lbl_usage_footer, footer);

    // ---- GitHub screen ----
    if (data->github_enabled) {
        lv_label_set_text_fmt(lbl_gh_issues, "%u", (unsigned)data->github_issues);
        lv_label_set_text_fmt(lbl_gh_prs,    "%u", (unsigned)data->github_prs);
        unsigned total = (unsigned)data->github_issues + (unsigned)data->github_prs;
        if (total == 0) {
            lv_label_set_text(lbl_gh_status, "Nothing waiting");
        } else {
            lv_label_set_text(lbl_gh_status, "Refreshed just now");
        }
    } else {
        lv_label_set_text(lbl_gh_issues, "No data");
        lv_label_set_text(lbl_gh_prs,    "No data");
        lv_label_set_text(lbl_gh_status, "Set a token in the daemon settings");
    }

    // ---- Copilot screen ----
    // Big panel: "Premium requests" with a huge percentage and an
    // "X / Y this month" counter. Top-model row underneath. Bottom strip
    // (one line) packs the status / editor / last-seen info that used to
    // get its own panel.
    if (data->copilot_premium_ok) {
        char pct_buf[16];
        snprintf(pct_buf, sizeof(pct_buf), "%.1f%%", data->copilot_premium_pct);
        lv_label_set_text(lbl_cp_premium_pct, pct_buf);

        char cnt_buf[32];
        snprintf(cnt_buf, sizeof(cnt_buf), "%u / %u this month",
                 (unsigned)data->copilot_premium_used,
                 (unsigned)data->copilot_premium_allowance);
        lv_label_set_text(lbl_cp_premium_counts, cnt_buf);

        lv_label_set_text(lbl_cp_top_model,
                          data->copilot_top_model[0] ? data->copilot_top_model : "");
    } else {
        lv_label_set_text(lbl_cp_premium_pct,    "—");
        lv_label_set_text(lbl_cp_premium_counts, "");
        lv_label_set_text(lbl_cp_top_model,      "");
    }

    if (data->copilot_enabled) {
        // Compact one-line strip — "<status> · <editor> · <when>" with
        // empty fields dropped. Uppercase the status word.
        char strip[64] = {0};
        size_t off = 0;
        if (data->copilot_status[0]) {
            for (size_t i = 0; i < sizeof(strip) - 1 - off && data->copilot_status[i]; i++) {
                char c = data->copilot_status[i];
                if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
                strip[off++] = c;
            }
        }
        if (data->copilot_editor[0]) {
            off += snprintf(strip + off, sizeof(strip) - off, "%s%s",
                            off ? "  |  " : "", data->copilot_editor);
        }
        if (data->copilot_when[0]) {
            off += snprintf(strip + off, sizeof(strip) - off, "%s%s",
                            off ? "  |  " : "", data->copilot_when);
        }
        lv_label_set_text(lbl_cp_strip, strip);
        lv_label_set_text(lbl_cp_hint,  "");
    } else {
        lv_label_set_text(lbl_cp_strip, "");
        lv_label_set_text(lbl_cp_hint,
                          data->copilot_premium_ok
                              ? ""
                              : "Set an org in the daemon settings");
    }

    // ---- Per-app visibility ----
    // Daemon pushes the user's tray checkboxes on every poll; the call is
    // a no-op when the CSV is unchanged so this is cheap on the hot path.
    ui_set_enabled_apps(data->enabled_apps);

    // ---- Splash mood + events strip ----
    // splash_set_mood / splash_set_events both dedup internally, so
    // calling them on every poll is cheap.
    splash_set_mood(data->mood);
    splash_set_events(data->events, data->events_count);

    // ---- Brightness overlay ----
    // The daemon sends 0..100 (0 = blackout, 100 = full bright). Translate to
    // LVGL's 0..255 opacity scale; we cap at ~85% opaque so a slider tug to 0
    // doesn't pitch the screen to fully unrecoverable black.
    if (dim_overlay) {
        uint8_t br = data->brightness > 100 ? 100 : data->brightness;
        if (br == 0) br = 100;  // treat unset (zero/missing) as full bright
        uint32_t dim_opa = ((100 - br) * 215) / 100;  // 0..215 (LV_OPA_TRANSP..~85%)
        lv_obj_set_style_bg_opa(dim_overlay, (lv_opa_t)dim_opa, 0);
    }
}

void ui_tick_anim(void) {
    // The Claude Usage screen's spinner + rotating-verb animation has been
    // removed; the bottom strip now hosts the static session-footer text
    // updated in ui_update(). This hook is kept so main.cpp's loop() can
    // continue to call it unchanged — a future per-screen tick (e.g.
    // pulse the BLE icon while scanning) can drop in here.
}

static screen_t prev_non_splash_screen = SCREEN_USAGE;

// ---- Per-app visibility ----
// Splash + Bluetooth are system screens — always cyclable. The rest are
// "apps" the user can show/hide from the tray window. Default = all on, so
// a fresh boot with no daemon shows everything. Updated by
// ui_set_enabled_apps() on every payload.
static bool screen_enabled[SCREEN_COUNT] = {
    true,   // SPLASH
    true,   // USAGE
    true,   // TODAY
    true,   // GITHUB
    true,   // COPILOT
    true,   // BLUETOOTH
};
static char enabled_apps_cache[64] = {0};  // last CSV we've seen — for dedup

static bool app_screen(screen_t s) {
    return s != SCREEN_SPLASH && s != SCREEN_BLUETOOTH;
}

static screen_t name_to_screen(const char* tok, size_t len) {
    auto eq = [&](const char* lit) {
        return strlen(lit) == len && strncmp(tok, lit, len) == 0;
    };
    if (eq("usage"))   return SCREEN_USAGE;
    if (eq("today"))   return SCREEN_TODAY;
    if (eq("github"))  return SCREEN_GITHUB;
    if (eq("copilot")) return SCREEN_COPILOT;
    return SCREEN_COUNT;
}

void ui_set_enabled_apps(const char* csv) {
    // Null / empty = enable everything (the boot default).
    if (!csv || !*csv) {
        if (enabled_apps_cache[0] == '\0') return;
        enabled_apps_cache[0] = '\0';
        for (int i = 0; i < SCREEN_COUNT; i++) screen_enabled[i] = true;
        return;
    }
    if (strncmp(csv, enabled_apps_cache, sizeof(enabled_apps_cache)) == 0) return;
    strlcpy(enabled_apps_cache, csv, sizeof(enabled_apps_cache));

    // Start with all "app" screens off; the CSV turns them back on. Splash
    // and Bluetooth stay enabled regardless.
    for (int i = 0; i < SCREEN_COUNT; i++) {
        screen_enabled[i] = !app_screen((screen_t)i);
    }
    const char* p = csv;
    while (*p) {
        while (*p == ',' || *p == ' ') p++;
        const char* tok = p;
        while (*p && *p != ',') p++;
        size_t len = (size_t)(p - tok);
        if (len > 0) {
            screen_t s = name_to_screen(tok, len);
            if (s != SCREEN_COUNT) screen_enabled[s] = true;
        }
    }

    // If the user just hid the screen we're showing, slide to the next
    // enabled one so they don't get stuck looking at a now-hidden app.
    if (!screen_enabled[current_screen]) ui_cycle_screen();
}

// LVGL handles click debouncing internally. Screen-level handler fires when
// no child consumed the event (children only consume if they have their own
// event callback, e.g. the Reset Bluetooth zone, which absorbs taps inside its
// own bounds). A tap anywhere else cycles to the next screen — same behavior
// as the middle hardware button.
static void global_click_cb(lv_event_t* e) {
    (void)e;
    ui_cycle_screen();
}

static void ble_reset_click_cb(lv_event_t* e) {
    (void)e;
    ble_clear_bonds();
}

void ui_show_screen(screen_t screen) {
    lv_obj_add_flag(usage_container,   LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(today_container,   LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(github_container,  LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(copilot_container, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ble_container,     LV_OBJ_FLAG_HIDDEN);
    splash_hide();

    switch (screen) {
    case SCREEN_SPLASH:     splash_show(); break;
    case SCREEN_USAGE:      lv_obj_clear_flag(usage_container,   LV_OBJ_FLAG_HIDDEN); break;
    case SCREEN_TODAY:      lv_obj_clear_flag(today_container,   LV_OBJ_FLAG_HIDDEN); break;
    case SCREEN_GITHUB:     lv_obj_clear_flag(github_container,  LV_OBJ_FLAG_HIDDEN); break;
    case SCREEN_COPILOT:    lv_obj_clear_flag(copilot_container, LV_OBJ_FLAG_HIDDEN); break;
    case SCREEN_BLUETOOTH:  lv_obj_clear_flag(ble_container,     LV_OBJ_FLAG_HIDDEN); break;
    default: break;
    }

    // Keep the dim overlay above whatever screen we just revealed.
    if (dim_overlay) lv_obj_move_foreground(dim_overlay);

    if (screen != SCREEN_SPLASH) prev_non_splash_screen = screen;
    current_screen = screen;
}

// Cycle order: SPLASH → USAGE → TODAY → GITHUB → COPILOT → BLUETOOTH → SPLASH.
// Skip any "app" screen whose enabled bit is clear so the user only sees
// the apps they've checked in the tray window.
static screen_t cycle_next_after(screen_t s) {
    switch (s) {
        case SCREEN_SPLASH:    return SCREEN_USAGE;
        case SCREEN_USAGE:     return SCREEN_TODAY;
        case SCREEN_TODAY:     return SCREEN_GITHUB;
        case SCREEN_GITHUB:    return SCREEN_COPILOT;
        case SCREEN_COPILOT:   return SCREEN_BLUETOOTH;
        case SCREEN_BLUETOOTH: return SCREEN_SPLASH;
        default:               return SCREEN_USAGE;
    }
}

void ui_cycle_screen(void) {
    screen_t next = cycle_next_after(current_screen);
    // Walk forward over disabled apps. Bound the walk by SCREEN_COUNT in
    // case every app got unchecked (we'll just land on Bluetooth or Splash).
    for (int safety = 0; safety < SCREEN_COUNT && !screen_enabled[next]; safety++) {
        next = cycle_next_after(next);
    }
    ui_show_screen(next);
}

void ui_toggle_splash(void) {
    if (current_screen == SCREEN_SPLASH) ui_show_screen(prev_non_splash_screen);
    else                                  ui_show_screen(SCREEN_SPLASH);
}

screen_t ui_get_current_screen(void) {
    return current_screen;
}

bool ui_focus_by_name(const char* name) {
    if (!name || !*name) return false;
    screen_t target;
    if      (strcmp(name, "splash")    == 0) target = SCREEN_SPLASH;
    else if (strcmp(name, "usage")     == 0) target = SCREEN_USAGE;
    else if (strcmp(name, "today")     == 0) target = SCREEN_TODAY;
    else if (strcmp(name, "github")    == 0) target = SCREEN_GITHUB;
    else if (strcmp(name, "copilot")   == 0) target = SCREEN_COPILOT;
    else if (strcmp(name, "bluetooth") == 0) target = SCREEN_BLUETOOTH;
    else return false;
    if (current_screen == target) return false;
    ui_show_screen(target);
    return true;
}

void ui_update_ble_status(ble_state_t state, const char* name, const char* mac) {
    switch (state) {
    case BLE_STATE_CONNECTED:
        lv_label_set_text(lbl_ble_status, "Connected");
        lv_obj_set_style_text_color(lbl_ble_status, COL_GREEN, 0);
        break;
    case BLE_STATE_ADVERTISING:
        lv_label_set_text(lbl_ble_status, "Advertising...");
        lv_obj_set_style_text_color(lbl_ble_status, COL_AMBER, 0);
        break;
    case BLE_STATE_DISCONNECTED:
        lv_label_set_text(lbl_ble_status, "Disconnected");
        lv_obj_set_style_text_color(lbl_ble_status, COL_RED, 0);
        break;
    default:
        lv_label_set_text(lbl_ble_status, "Initializing...");
        lv_obj_set_style_text_color(lbl_ble_status, COL_DIM, 0);
        break;
    }

    if (name) {
        static char nbuf[48];
        snprintf(nbuf, sizeof(nbuf), "Device: %s", name);
        lv_label_set_text(lbl_ble_device, nbuf);
    }
    if (mac) {
        static char mbuf[48];
        snprintf(mbuf, sizeof(mbuf), "Address: %s", mac);
        lv_label_set_text(lbl_ble_mac, mbuf);
    }
}

