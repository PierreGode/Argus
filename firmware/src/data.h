#pragma once
#include <Arduino.h>

struct UsageData {
    // ---- Rate-limit snapshot (from API headers) ----
    float session_pct;       // 5-hour window utilization (0-100)
    int session_reset_mins;  // minutes until session resets
    float weekly_pct;        // 7-day window utilization (0-100)
    int weekly_reset_mins;   // minutes until weekly resets
    char status[16];         // "allowed" or "limited"
    bool ok;                 // data parse succeeded
    bool valid;              // false until first successful parse

    // ---- "Today" page (aggregated from ~/.claude/projects/**/*.jsonl) ----
    float cost_today;        // USD spent today
    float cost_week;         // USD spent in the last 7 days
    uint8_t opus_pct;        // share of today's tokens that ran on Opus (0-100)
    uint8_t sonnet_pct;      // ... on Sonnet
    uint8_t haiku_pct;       // ... on Haiku
    uint8_t cache_hit_pct;   // cache_read / (cache_read + cache_creation + input) for today
    uint32_t tokens_today;   // total tokens consumed today (input + output + cache_*)
    uint16_t sessions_today; // number of distinct conversation files touched today
    char project[28];        // basename of most-recently-active project

    // ---- GitHub page (optional — daemon only populates if a PAT is set) ----
    uint16_t github_issues;  // open issues assigned to the user
    uint16_t github_prs;     // open PRs awaiting the user's review or owned by them
    bool github_enabled;     // true when the daemon has a PAT and is polling GH

    // ---- Copilot page (requires Copilot Business org admin scope on the PAT) ----
    bool copilot_enabled;    // daemon has the org configured + last seat lookup ok
    char copilot_status[12]; // "active" | "idle" | "inactive" | "off"
    char copilot_when[16];   // "5 min ago" / "2 hours ago" / "3 days ago" / "—"
    char copilot_editor[16]; // "VS Code" / "JetBrains" / "Neovim" / "" if unknown

    // Premium-request usage block. Sourced from the GitHub Enterprise
    // billing endpoint via copilot_stats.fetch_premium_usage(). Only
    // populated when an enterprise slug is configured AND the PAT has
    // billing-read scope on it; otherwise copilot_premium_ok = false
    // and the firmware hides the panel.
    bool     copilot_premium_ok;
    float    copilot_premium_pct;       // 0.0 .. 100.0+
    uint16_t copilot_premium_used;      // requests this month
    uint16_t copilot_premium_allowance; // monthly cap (300 / 1000 / 1500)
    char     copilot_top_model[32];     // e.g. "Claude Opus 4.6"

    // ---- Enabled-apps CSV ("usage,today,github,copilot") ----
    // Daemon publishes the user's chosen visibility list every payload. UI
    // hides screens not in the list from the cycle. Empty / missing means
    // "all on" so a fresh device with no daemon shows everything.
    char enabled_apps[64];

    // ---- Display ----
    uint8_t brightness;      // 0-100 (software dim overlay; 100 = full bright)

    // ---- Auto-focus ----
    // Daemon sets this to a screen name ("github", "today", "usage", …) when
    // something noteworthy changed (new PR, etc.). Firmware switches to that
    // screen once and then waits for the daemon to set it again. Empty string
    // means "no auto-focus this poll".
    char focus_screen[12];

    // ---- Splash mood + events strip ----
    // The daemon computes one "mood" name per poll based on which cap is
    // highest and whether a new GitHub event just landed; the splash
    // module locks the sprite to that expression instead of cycling
    // randomly. Names match the sprite catalog in argus_sprites.h:
    //   happy / looking / flirt / buffeld / surprised / angry.
    // Empty string = use the rate-group default (legacy behavior).
    char mood[12];

    // Event strip rendered below the sprite. Daemon ships a short list of
    // one-line strings ("Claude weekly: 87% · resets in 2d", "New PR · …",
    // …); the splash rotates through them every few seconds. Sized for
    // ~40-char lines × up to 6 events — fits comfortably inside the BLE
    // payload budget.
    static const uint8_t EVENTS_MAX = 6;
    static const uint8_t EVENT_LEN  = 56;
    char    events[EVENTS_MAX][EVENT_LEN];
    uint8_t events_count;
};
