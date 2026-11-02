// Animation engine for the lamp. Owns the whole frame: mascot sprite + per-mode
// motion, data widgets, and the RGB LED. Short bursts of motion on change, then
// it settles so the lamp doesn't nag in peripheral vision.
#pragma once
#include "klar_state.h"

void anim_begin();
void anim_boot();                                     // splash until first data
void anim_set_state(const LampState &st, bool fanfare);
void anim_tick();                                     // call every loop; self-paces
