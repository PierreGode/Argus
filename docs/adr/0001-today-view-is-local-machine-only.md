# Today's cost and model split are local-machine-only

Discovered while investigating a Fable/cost bug: the user runs Claude Code on a remote server as well as this machine, and the Usage screen's quota bars stay accurate for that remote usage (they come from Anthropic's account-wide rate-limit headers), but Today's dollar estimate and per-model split do not — they only ever parse `~/.claude/projects/**/*.jsonl` on the daemon's own machine.

We decided to keep this scope rather than build a mechanism to aggregate session logs across machines (SSH pull, a sync agent on the remote host, etc.) — that's a new subsystem, not a bug fix, and wasn't what was asked for. Today's cost/model split (including the new Fable slice) will continue to undercount for anyone who runs Claude Code from more than one machine; only the Usage screen's quota bars are multi-machine-accurate.
