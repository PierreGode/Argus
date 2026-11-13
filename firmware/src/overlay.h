#pragma once
#include <stdint.h>

// Overlays drawn above every screen: the Claude Code permission prompt, the
// firmware-update progress screen, and the "data is stale" pill. They are
// children of the active screen (raised to the foreground when shown) rather
// than of LVGL's top layer, so the `screenshot` serial command captures them.

void overlay_init(void);
void overlay_tick(void);

// ---- Permission prompt ----
// Shows the tool + what it wants to do with Deny / Skip / Allow buttons.
// "Skip" hands the question back to the terminal. `project` is the repo the
// asking session is in and `branch` its branch (either may be ""), so prompts
// from several sessions can be told apart.
void prompt_show(const char* id, const char* tool, const char* detail,
                 const char* project, const char* branch, int timeout_s);
void prompt_cancel(const char* id);     // id NULL/"" cancels whatever is showing
bool prompt_active(void);
void prompt_skip(void);                 // BOOT button while a prompt is up
// Returns true once when the user answered; fills id + decision
// ("allow" / "deny" / "skip").
bool prompt_take_result(char* id_out, int id_len, const char** decision);

// ---- Firmware update progress ----
void overlay_ota_show(int pct, const char* msg);   // msg NULL = "Updating firmware"
void overlay_ota_hide(void);

// ---- Stale-data pill ----
// mins < 0 hides it; otherwise shows "No update for <N>" at the bottom edge.
void overlay_set_stale(int mins);
