# Argus

Daemon + firmware pair that surfaces Claude Code usage, cost, GitHub, CI, and Copilot stats on a desk-side ESP32 display. This file is the glossary for the domain concepts around usage/cost reporting — not a spec, not an implementation log.

## Language

**Usage screen (quota bars)**:
The firmware's `SCREEN_USAGE` panel showing "Current" (5h rolling), "Weekly" and — when the plan has one — the **model-scoped weekly limit** ("Fable weekly") quota-used percentages. The first two come from Anthropic's account-wide rate-limit HTTP headers, the third from the OAuth usage endpoint; all are accurate regardless of which machine issued the request.
_Avoid_: "Claude Usage view" (user-facing name, not a code identifier)

**Model-scoped weekly limit**:
A weekly quota that applies to one model only (currently Fable), shown as its own bar in `claude /usage` and as the third Usage-screen panel. It is a quota percentage, not a token share — do not confuse it with the Fable slice of the **model split** below, which is a local-machine-only share of tokens.
_Avoid_: "Fable usage" (ambiguous between the limit and the token share), "Fable %"

**Today's cost / model split**:
The `SCREEN_TODAY` panel's dollar estimate ("API equiv.") and per-model percentage breakdown (Opus/Sonnet/Haiku, soon +Fable). Both are computed by the daemon by parsing `~/.claude/projects/**/*.jsonl` on the machine the daemon runs on and pricing each line via the **model bucket** it falls into.
_Avoid_: "the cost", "usage stats" (ambiguous with the quota bars above)

**Local-machine-only scope**:
The known limitation that Today's cost/model split only reflects Claude Code sessions whose JSONL transcripts land on the daemon's own machine. Usage run on a remote server (e.g. over SSH) is invisible to this pipeline, even though it's fully visible to the Usage screen's quota bars (which come from account-wide rate-limit headers, not local files). See `docs/adr/0001-today-view-is-local-machine-only.md`.

**Model bucket**:
The daemon's `classify_model()` mapping from a session's raw `model` id string to a pricing category (`opus` / `sonnet` / `haiku` / `fable` / `other`) and a per-1M-token price. Buckets are what the Today screen's percentage split is computed over.
_Avoid_: "model type", "pricing tier"

**`other` bucket**:
The catch-all model bucket for any model id `classify_model()` doesn't recognize. Tracked internally for cost totals but never broken out as its own line in the UI — unlike the named buckets (opus/sonnet/haiku/fable), which each get a visible slice.
