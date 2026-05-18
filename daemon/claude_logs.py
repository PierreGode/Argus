"""Aggregate today's Claude Code usage from local ~/.claude/projects logs.

Claude Code persists every assistant turn as one line in a `.jsonl` file under
`~/.claude/projects/<encoded-cwd>/<session>.jsonl`. Each assistant line carries
a token-usage block and a model name; some versions also carry a precomputed
`costUSD` field. We aggregate this into a compact dict that gets merged into
the wire payload alongside the rate-limit numbers.

We compute "today" against the user's local timezone — that's what matters for
a desk display. "Week" is a rolling 7-day window from now.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

# USD per 1M tokens. Cache write is the same as input on current Anthropic
# pricing for 5m TTL; we use the documented numbers below. Easy to update
# in one place if rates move.
PRICING = {
    # Claude 4.x family
    "opus-4":   {"in": 15.00, "out": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "sonnet-4": {"in":  3.00, "out": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "haiku-4":  {"in":  1.00, "out":  5.00, "cache_write":  1.25, "cache_read": 0.10},
    # Older 3.x — kept so cost stays sane if your logs span a model transition.
    "opus-3":   {"in": 15.00, "out": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "sonnet-3": {"in":  3.00, "out": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "haiku-3":  {"in":  0.80, "out":  4.00, "cache_write":  1.00, "cache_read": 0.08},
}

def classify_model(model: str) -> tuple[str, dict]:
    """Return (family, pricing) for a model id. Family is one of opus/sonnet/haiku/other."""
    if not model:
        return "other", PRICING["sonnet-4"]
    m = model.lower()
    if "opus" in m:
        return "opus", PRICING["opus-4" if "-4" in m else "opus-3"]
    if "sonnet" in m:
        return "sonnet", PRICING["sonnet-4" if "-4" in m else "sonnet-3"]
    if "haiku" in m:
        return "haiku", PRICING["haiku-4" if "-4" in m else "haiku-3"]
    return "other", PRICING["sonnet-4"]


@dataclass
class Aggregates:
    cost_today: float = 0.0
    cost_week: float = 0.0
    tokens_today: int = 0
    cache_read_today: int = 0
    cache_creation_today: int = 0
    input_today: int = 0
    by_model_today: dict[str, int] = field(default_factory=lambda: {
        "opus": 0, "sonnet": 0, "haiku": 0, "other": 0,
    })
    sessions_today: set[str] = field(default_factory=set)
    latest_project: str = ""
    latest_project_ts: float = 0.0


def _parse_timestamp(ts: str) -> float | None:
    if not ts:
        return None
    try:
        # ISO-8601, ending in Z or +HH:MM
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _line_cost(usage: dict, pricing: dict) -> float:
    inp   = usage.get("input_tokens", 0) or 0
    out   = usage.get("output_tokens", 0) or 0
    cw    = usage.get("cache_creation_input_tokens", 0) or 0
    cr    = usage.get("cache_read_input_tokens", 0) or 0
    return (
        inp * pricing["in"]
        + out * pricing["out"]
        + cw  * pricing["cache_write"]
        + cr  * pricing["cache_read"]
    ) / 1_000_000.0


def _project_basename(jsonl_path: Path) -> str:
    """Recover something readable from the encoded project dir name.

    ~/.claude/projects/<dir>/<session>.jsonl — <dir> is the cwd with '/' or '\'
    replaced by '-'. We don't try to reverse the encoding exactly; just take
    the trailing segment which is usually the repo name.
    """
    parent = jsonl_path.parent.name
    # Trim leading dashes from path encoding, then take the last segment.
    parts = [p for p in parent.split("-") if p]
    if not parts:
        return parent[:24]
    return parts[-1][:24]


def aggregate(claude_dir: Path | None = None, now: float | None = None) -> Aggregates:
    """Walk ~/.claude/projects/**/*.jsonl and aggregate the last 7 days."""
    if claude_dir is None:
        claude_dir = Path.home() / ".claude" / "projects"
    if now is None:
        now = time.time()

    # Local midnight as today's cutoff.
    today_start = datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    week_start = now - 7 * 86400

    agg = Aggregates()
    if not claude_dir.exists():
        return agg

    # Skip files whose mtime is older than the 7-day window — they can't
    # contribute to today or this week.
    for jsonl in claude_dir.rglob("*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime < week_start:
            continue

        try:
            f = jsonl.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue

        contributed_today = False
        with f:
            for line in f:
                line = line.strip()
                if not line or line[0] != "{":
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "assistant":
                    continue

                ts = _parse_timestamp(evt.get("timestamp", ""))
                if ts is None or ts < week_start:
                    continue

                msg = evt.get("message") or {}
                usage = msg.get("usage") or {}
                model = msg.get("model") or evt.get("model") or ""
                family, pricing = classify_model(model)

                # Prefer logged cost if present; else compute from tokens.
                cost = evt.get("costUSD")
                if not isinstance(cost, (int, float)):
                    cost = _line_cost(usage, pricing)

                agg.cost_week += cost
                if ts >= today_start:
                    agg.cost_today += cost
                    inp = usage.get("input_tokens", 0) or 0
                    out = usage.get("output_tokens", 0) or 0
                    cw  = usage.get("cache_creation_input_tokens", 0) or 0
                    cr  = usage.get("cache_read_input_tokens", 0) or 0
                    total = inp + out + cw + cr
                    agg.tokens_today += total
                    agg.input_today += inp
                    agg.cache_creation_today += cw
                    agg.cache_read_today += cr
                    agg.by_model_today[family] += total

                    sid = evt.get("sessionId") or jsonl.stem
                    agg.sessions_today.add(sid)
                    contributed_today = True

                    cwd = evt.get("cwd")
                    if ts > agg.latest_project_ts:
                        agg.latest_project_ts = ts
                        if cwd:
                            agg.latest_project = Path(cwd).name[:24]
                        else:
                            agg.latest_project = _project_basename(jsonl)

        # If the file had today activity but no cwd field, still register the project.
        if contributed_today and not agg.latest_project:
            agg.latest_project = _project_basename(jsonl)

    return agg


def to_payload_fields(agg: Aggregates) -> dict:
    """Convert aggregates to the short-key form that ships over BLE/serial."""
    counted = sum(agg.by_model_today.values())
    if counted > 0:
        opus_pct   = round(100 * agg.by_model_today["opus"]   / counted)
        sonnet_pct = round(100 * agg.by_model_today["sonnet"] / counted)
        haiku_pct  = round(100 * agg.by_model_today["haiku"]  / counted)
    else:
        opus_pct = sonnet_pct = haiku_pct = 0

    cache_denom = agg.input_today + agg.cache_creation_today + agg.cache_read_today
    cache_pct = round(100 * agg.cache_read_today / cache_denom) if cache_denom else 0

    return {
        "c":  round(agg.cost_today, 3),
        "cw": round(agg.cost_week, 2),
        "mo": opus_pct,
        "ms": sonnet_pct,
        "mh": haiku_pct,
        "ch": cache_pct,
        "tk": agg.tokens_today,
        "se": len(agg.sessions_today),
        "pj": agg.latest_project,
    }


if __name__ == "__main__":
    # CLI: `python claude_logs.py` prints today's stats as JSON for debugging.
    import sys
    fields = to_payload_fields(aggregate())
    json.dump(fields, sys.stdout, indent=2)
    sys.stdout.write("\n")
