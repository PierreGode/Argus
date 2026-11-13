"""Claude plan-usage lookup — the numbers behind `claude /usage`.

The rate-limit headers on /v1/messages only carry the two account-wide windows
(5-hour session and 7-day weekly). The per-model weekly limit (e.g. the "Fable"
bar in `claude /usage`) is only exposed by the OAuth usage endpoint, so we read
it from there. Subscription (OAuth) tokens only — API keys have no plan limits.

`fetch()` returns a normalized dict:

    {
      "session": {"pct": 18, "reset": 1794560400} | None,   # 5-hour window
      "weekly":  {"pct": 79, "reset": 1794661200} | None,   # 7-day, all models
      "scoped":  {"name": "Fable", "pct": 69, "reset": ...} | None,
    }

`reset` is an epoch timestamp (0 when unknown), so callers can derive a fresh
countdown from a cached reading.
"""
from __future__ import annotations

from datetime import datetime

import httpx

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Model the device's third Usage panel prefers when an account carries more
# than one model-scoped weekly limit.
PREFERRED_MODEL = "fable"


class PlanUsageError(Exception):
    pass


def _epoch(iso) -> int:
    if not iso or not isinstance(iso, str):
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _pct(v) -> int | None:
    try:
        return max(0, int(round(float(v))))
    except (TypeError, ValueError):
        return None


def parse(d: dict) -> dict:
    """Normalize the endpoint's JSON. Tolerates both the `limits` list and the
    older flat `five_hour` / `seven_day` / `seven_day_<model>` objects."""
    out: dict = {"session": None, "weekly": None, "scoped": None}
    scoped: list[dict] = []

    for lim in d.get("limits") or []:
        if not isinstance(lim, dict):
            continue
        pct = _pct(lim.get("percent"))
        if pct is None:
            continue
        entry = {"pct": pct, "reset": _epoch(lim.get("resets_at"))}
        kind = lim.get("kind")
        if kind == "session":
            out["session"] = entry
        elif kind == "weekly_all":
            out["weekly"] = entry
        elif kind == "weekly_scoped":
            model = ((lim.get("scope") or {}).get("model") or {})
            name = (model.get("display_name") or "").strip()
            if name:
                scoped.append({"name": name, **entry})

    # Flat-object fallbacks (utilization is already 0-100 here).
    def _flat(key: str) -> dict | None:
        o = d.get(key)
        if not isinstance(o, dict):
            return None
        pct = _pct(o.get("utilization"))
        if pct is None:
            return None
        return {"pct": pct, "reset": _epoch(o.get("resets_at"))}

    if out["session"] is None:
        out["session"] = _flat("five_hour")
    if out["weekly"] is None:
        out["weekly"] = _flat("seven_day")
    if not scoped:
        for key, name in (("seven_day_opus", "Opus"), ("seven_day_sonnet", "Sonnet")):
            e = _flat(key)
            if e is not None:
                scoped.append({"name": name, **e})

    if scoped:
        preferred = [s for s in scoped if PREFERRED_MODEL in s["name"].lower()]
        # Otherwise surface whichever scoped limit is closest to its cap.
        out["scoped"] = preferred[0] if preferred else max(scoped, key=lambda s: s["pct"])
    return out


def fetch(token: str, timeout: float = 20) -> dict:
    """GET the usage endpoint and return the normalized dict. Raises
    PlanUsageError on any transport / HTTP / parse failure."""
    try:
        resp = httpx.get(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise PlanUsageError(f"request failed: {e}")
    if resp.status_code != 200:
        raise PlanUsageError(f"HTTP {resp.status_code}")
    try:
        d = resp.json()
    except ValueError as e:
        raise PlanUsageError(f"bad JSON: {e}")
    if not isinstance(d, dict):
        raise PlanUsageError("unexpected response shape")
    return parse(d)
