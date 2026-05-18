"""Fetch GitHub Copilot status + premium-request usage for the desk display.

Two independent reads, both authenticated with the user's existing PAT:

  1. fetch_seat(token, org, login=None)
        GET /orgs/{org}/members/{login}/copilot
        Returns seat status + last activity + editor.

  2. fetch_premium_usage(token, enterprise, login, allowance)
        GET /enterprises/{enterprise}/settings/billing/premium_request/usage
            ?user={login}
        Returns this month's premium-request usage, broken down per
        model, plus a percentage against the plan's monthly allowance.

Both surface a single "no data" object on failure (404 / 403 / missing
config) rather than raising, so the daemon can call them on every poll
and the device degrades gracefully when only one of the two endpoints
is reachable.

Permissions needed on the PAT:
    - `read:org` for fetch_seat (issued by an admin of the target org).
    - Enterprise billing read for fetch_premium_usage. That endpoint is
      enterprise-scoped, so a regular Copilot Business org without an
      enclosing enterprise won't have it — `fetch_premium_usage` returns
      a disabled result in that case and the device just hides the
      premium-request panel.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

import httpx

API = "https://api.github.com"


class CopilotError(RuntimeError):
    pass


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_login(token: str) -> str:
    try:
        resp = httpx.get(f"{API}/user", headers=_headers(token), timeout=15)
    except httpx.HTTPError as e:
        raise CopilotError(f"network: {e}") from e
    if resp.status_code != 200:
        raise CopilotError(f"/user http {resp.status_code}")
    login = resp.json().get("login")
    if not login:
        raise CopilotError("/user returned no login")
    return login


def _parse_iso(s: str) -> datetime | None:
    """Parse GitHub's ISO-8601 timestamps. Tolerates trailing 'Z'."""
    if not s:
        return None
    try:
        # Python 3.11+ accepts 'Z' natively; older versions need a swap.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _format_relative(then: datetime, now: datetime) -> str:
    """Produce a compact relative-time string sized for the firmware's
    16-char copilot_when field. Buckets: seconds / minutes / hours / days."""
    delta = (now - then).total_seconds()
    if delta < 0:
        # Clock skew — pretend it just happened rather than showing nonsense.
        delta = 0
    if delta < 60:
        return "just now"
    if delta < 3600:
        m = int(delta // 60)
        return f"{m} min ago" if m != 1 else "1 min ago"
    if delta < 86400:
        h = int(delta // 3600)
        return f"{h} hours ago" if h != 1 else "1 hour ago"
    d = int(delta // 86400)
    return f"{d} days ago" if d != 1 else "1 day ago"


_EDITOR_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*)")


def _pretty_editor(raw: str) -> str:
    """Map Copilot's "vscode/1.85.0/copilot/1.140.0" style strings to short
    display names. Falls back to the raw prefix if we don't recognize it."""
    if not raw:
        return ""
    m = _EDITOR_RE.match(raw)
    base = (m.group(1) if m else raw).lower()
    return {
        "vscode":     "VS Code",
        "code":       "VS Code",
        "jetbrains":  "JetBrains",
        "intellij":   "JetBrains",
        "pycharm":    "JetBrains",
        "webstorm":   "JetBrains",
        "goland":     "JetBrains",
        "rider":      "JetBrains",
        "neovim":     "Neovim",
        "vim":        "Vim",
        "xcode":      "Xcode",
        "visualstudio": "VS",
        "vs":         "VS",
    }.get(base, base.capitalize())


def fetch(token: str, org: str, login: str | None = None) -> dict:
    """Return {status, when, editor, plan_type} for the user's Copilot seat.

    `status` ∈ {"active","idle","inactive","off"}
    `when`   relative-time string ("5 min ago", "—" if never)
    `editor` short editor name ("VS Code", "JetBrains", …)
    """
    if not token:
        raise CopilotError("no token")
    if not org:
        raise CopilotError("no org configured")

    if not login:
        login = _get_login(token)

    try:
        resp = httpx.get(
            f"{API}/orgs/{org}/members/{login}/copilot",
            headers=_headers(token),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise CopilotError(f"network: {e}") from e

    if resp.status_code == 404:
        # User isn't a Copilot seat in that org (or org doesn't exist /
        # token can't see it). Surface a clean "off" rather than an error.
        return {"status": "off", "when": "—", "editor": "", "plan_type": ""}
    if resp.status_code == 401:
        raise CopilotError("token rejected (401)")
    if resp.status_code == 403:
        raise CopilotError(f"forbidden (403): {resp.json().get('message','')}")
    if resp.status_code >= 400:
        raise CopilotError(f"http {resp.status_code}: {resp.text[:120]}")

    seat = resp.json()
    last_iso = seat.get("last_activity_at")
    editor_raw = seat.get("last_activity_editor") or ""
    plan = seat.get("plan_type") or ""

    last_dt = _parse_iso(last_iso)
    if last_dt is None:
        return {"status": "idle", "when": "—", "editor": "", "plan_type": plan}

    now = datetime.now(timezone.utc)
    delta = (now - last_dt).total_seconds()
    if delta < 300:        # 5 min
        status = "active"
    elif delta < 3600:     # 1 hr
        status = "idle"
    else:
        status = "inactive"

    return {
        "status": status,
        "when":   _format_relative(last_dt, now),
        "editor": _pretty_editor(editor_raw),
        "plan_type": plan,
    }


# ---------------------------------------------------------------------------
# Premium-request usage (enterprise billing endpoint)
# ---------------------------------------------------------------------------

# Per-plan monthly premium-request allowance as documented by GitHub. The
# tray UI lets the user pick which plan they're on; we fall back to the
# Copilot Enterprise default since that's the typical case for orgs that
# even expose this endpoint.
ALLOWANCE_BY_PLAN = {
    "business":   300,
    "enterprise": 1000,
    "pro":        300,
    "pro_plus":   1500,
}

# SKU filter — the endpoint returns rows for several SKUs (Cloud Agent,
# Premium Request, etc.). We only want the row labelled as a premium
# request when computing the percentage.
_PREMIUM_SKU = "Copilot Premium Request"


def fetch_premium_usage(
    token: str,
    enterprise: str,
    login: str | None = None,
    allowance: int | None = None,
) -> dict:
    """Return this month's premium-request usage for `login` inside
    `enterprise`. Shape:

        {
          "available": True,
          "user":      "PierreGode",
          "year":      2026,
          "month":     5,
          "used":      604.0,
          "allowance": 1000,
          "pct":       60.4,
          "overage":   0.0,
          "cost":      0.0,
          "top_model": "Claude Opus 4.6",
          "top_count": 603.0,
          "models":    [(name, count), ...]  # sorted desc
        }

    On missing config / 404 / 403 / network error, returns
    `{"available": False}` so the daemon can include the field
    unconditionally and the device just hides the panel. The error is
    logged via print() so the tray's LIVE LOG surfaces it."""
    if not token or not enterprise:
        return {"available": False}

    if not login:
        try:
            login = _get_login(token)
        except CopilotError as e:
            print(f"[copilot_stats] premium: {e}", flush=True)
            return {"available": False}

    try:
        resp = httpx.get(
            f"{API}/enterprises/{enterprise}/settings/billing/premium_request/usage",
            headers=_headers(token),
            params={"user": login},
            timeout=15,
        )
    except httpx.HTTPError as e:
        print(f"[copilot_stats] premium network error: {e}", flush=True)
        return {"available": False}

    if resp.status_code == 404:
        # Enterprise name wrong, or the enterprise doesn't expose premium
        # billing yet. Quiet: this is the "Copilot Business org with no
        # enclosing enterprise" case.
        return {"available": False}
    if resp.status_code in (401, 403):
        print(f"[copilot_stats] premium {resp.status_code}: "
              f"{resp.json().get('message','')}", flush=True)
        return {"available": False}
    if resp.status_code >= 400:
        print(f"[copilot_stats] premium http {resp.status_code}: "
              f"{resp.text[:120]}", flush=True)
        return {"available": False}

    body = resp.json()
    period = body.get("timePeriod", {}) or {}
    items = body.get("usageItems", []) or []

    # Filter to Premium Request SKU and aggregate per-model.
    premium = [i for i in items if i.get("sku") == _PREMIUM_SKU]
    per_model: dict[str, float] = defaultdict(float)
    for i in premium:
        per_model[i.get("model") or "Unknown"] += float(i.get("grossQuantity") or 0)

    gross_total = sum(float(i.get("grossQuantity") or 0) for i in premium)
    net_total   = sum(float(i.get("netQuantity")   or 0) for i in premium)
    net_cost    = sum(float(i.get("netAmount")     or 0) for i in premium)

    if not allowance or allowance <= 0:
        allowance = ALLOWANCE_BY_PLAN["enterprise"]
    pct = (gross_total / allowance) * 100.0 if allowance else 0.0

    models_sorted = sorted(per_model.items(), key=lambda x: -x[1])
    top_model, top_count = (models_sorted[0] if models_sorted else ("", 0.0))

    return {
        "available": True,
        "user":      body.get("user") or login,
        "year":      int(period.get("year")  or 0),
        "month":     int(period.get("month") or 0),
        "used":      gross_total,
        "allowance": int(allowance),
        "pct":       pct,
        "overage":   net_total,
        "cost":      net_cost,
        "top_model": top_model,
        "top_count": top_count,
        "models":    models_sorted,
    }


if __name__ == "__main__":
    # CLI smoke test: GH_TOKEN=ghp_xxx GH_ORG=myorg python copilot_stats.py
    import os, json, sys
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    org = os.environ.get("GH_ORG")
    if not tok or not org:
        print("set GH_TOKEN and GH_ORG to test", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(fetch(tok, org), indent=2))
