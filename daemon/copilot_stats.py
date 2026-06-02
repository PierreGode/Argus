"""Fetch GitHub Copilot status + org AI-credit usage for the desk display.

Two independent reads, both authenticated with the user's existing PAT:

  1. fetch(token, org, login=None)
      GET /orgs/{org}/members/{login}/copilot
      Returns seat status + last activity + editor.

  2. fetch_premium_usage(token, org, allowance)
      GET /organizations/{org}/settings/billing/usage/summary
      GET /organizations/{org}/settings/billing/premium_request/usage
      Returns this month's included AI-credit consumption, any billed
      overage, and the top model contributing to Copilot usage.

Both surface a single "no data" object on failure (404 / 403 / missing
config) rather than raising, so the daemon can call them on every poll
and the device degrades gracefully when only one of the two endpoints
is reachable.

Permissions needed on the PAT:
    - `read:org` for fetch (issued by an admin of the target org).
    - Billing/administration read on the organization for
    fetch_premium_usage.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

import httpx

API = "https://api.github.com"
API_VERSION = "2026-03-10"


class CopilotError(RuntimeError):
    pass


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
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
# Org AI-credit usage (enhanced billing endpoints)
# ---------------------------------------------------------------------------

# GitHub's current promotional org-level included pool for a single
# Copilot Business seat. The tray lets the user override this with the
# actual pooled value for their billing entity.
DEFAULT_INCLUDED_AI_CREDITS = 3000

# SKU filter — the premium-request report still carries the useful model
# breakdown even after the UI moved to AI-credit terminology.
_PREMIUM_SKU = "Copilot Premium Request"


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _copilot_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        product = (item.get("product") or "").lower()
        sku = (item.get("sku") or "").lower()
        if "copilot" in product or "copilot" in sku:
            rows.append(item)
    return rows


def _looks_like_credit_row(item: dict) -> bool:
    unit = (item.get("unitType") or "").lower()
    sku = (item.get("sku") or "").lower()
    return "credit" in unit or "credit" in sku or "ai" in sku


def _select_ai_credit_rows(items: list[dict]) -> list[dict]:
    copilot = _copilot_rows(items)
    if not copilot:
        return []

    explicit = [item for item in copilot if _looks_like_credit_row(item)]
    if explicit:
        return explicit

    metered = []
    for item in copilot:
        unit = (item.get("unitType") or "").lower()
        sku = (item.get("sku") or "").lower()
        if unit in {"seat", "seats", "license", "licenses", "month", "months"}:
            continue
        if "license" in sku:
            continue
        metered.append(item)
    return metered or copilot


def _billing_base_path(scope: str, slug: str) -> str:
    if scope == "enterprise":
        return f"{API}/enterprises/{slug}/settings/billing"
    return f"{API}/organizations/{slug}/settings/billing"


def _fetch_top_model_usage(
    token: str,
    scope: str,
    slug: str,
) -> tuple[str, float, list[tuple[str, float]]]:
    try:
        resp = httpx.get(
            f"{_billing_base_path(scope, slug)}/premium_request/usage",
            headers=_headers(token),
            timeout=15,
        )
    except httpx.HTTPError as e:
        print(f"[copilot_stats] premium breakdown network error: {e}", flush=True)
        return "", 0.0, []

    if resp.status_code in (401, 403):
        print(
            f"[copilot_stats] premium breakdown {resp.status_code}: "
            f"{resp.json().get('message', '')}",
            flush=True,
        )
        return "", 0.0, []
    if resp.status_code == 404:
        return "", 0.0, []
    if resp.status_code >= 400:
        print(
            f"[copilot_stats] premium breakdown http {resp.status_code}: "
            f"{resp.text[:120]}",
            flush=True,
        )
        return "", 0.0, []

    per_model: dict[str, float] = defaultdict(float)
    for item in resp.json().get("usageItems", []) or []:
        if item.get("sku") != _PREMIUM_SKU:
            continue
        model = item.get("model") or "Unknown"
        amount = _as_float(item.get("grossAmount"))
        if amount <= 0.0:
            amount = _as_float(item.get("grossQuantity"))
        per_model[model] += amount

    models_sorted = sorted(per_model.items(), key=lambda row: -row[1])
    top_model, top_value = (models_sorted[0] if models_sorted else ("", 0.0))
    return top_model, top_value, models_sorted


def fetch_premium_usage(
    token: str,
    account_slug: str,
    allowance: int | None = None,
    scope: str = "org",
) -> dict:
    """Return this month's Copilot AI-credit usage for an org or enterprise.

    The function keeps the historical name because the rest of the daemon
    already keys off the flat `cpr/cpp/cpu/cpa/cpm` payload fields.

    Shape:

        {
          "available": True,
          "account":   "my-org-or-enterprise",
          "scope":     "org",
          "year":      2026,
          "month":     6,
          "used":      126.0,
          "allowance": 3000,
          "pct":       4.2,
          "overage":   0.0,
          "cost":      0.0,
          "top_model": "GPT-5.4",
          "top_count": 1.26,
          "models":    [(name, score), ...]  # sorted desc
        }

    `used` represents included AI credits consumed from the pool. When the
    summary response does not expose `discountQuantity`, we fall back to the
    total metered quantity, capped to the configured allowance if one exists.
    `overage` is the billed quantity above the included pool.
    """
    if not token or not account_slug:
        return {"available": False}

    scope = "enterprise" if scope == "enterprise" else "org"

    try:
        resp = httpx.get(
            f"{_billing_base_path(scope, account_slug)}/usage/summary",
            headers=_headers(token),
            params={"product": "Copilot"},
            timeout=15,
        )
    except httpx.HTTPError as e:
        print(f"[copilot_stats] AI usage network error: {e}", flush=True)
        return {"available": False}

    if resp.status_code == 404:
        # Slug wrong, or the billing entity is not on the enhanced billing platform.
        return {"available": False}
    if resp.status_code in (401, 403):
        print(f"[copilot_stats] AI usage {resp.status_code}: "
              f"{resp.json().get('message','')}", flush=True)
        return {"available": False}
    if resp.status_code >= 400:
        print(f"[copilot_stats] AI usage http {resp.status_code}: "
              f"{resp.text[:120]}", flush=True)
        return {"available": False}

    body = resp.json()
    period = body.get("timePeriod", {}) or {}
    items = _select_ai_credit_rows(body.get("usageItems", []) or [])
    if not items:
        return {"available": False}

    gross_total = sum(_as_float(i.get("grossQuantity") or i.get("quantity")) for i in items)
    included_total = sum(_as_float(i.get("discountQuantity")) for i in items)
    overage_total = sum(_as_float(i.get("netQuantity")) for i in items)
    net_cost = sum(_as_float(i.get("netAmount")) for i in items)

    if not allowance or allowance <= 0:
        allowance = DEFAULT_INCLUDED_AI_CREDITS

    if included_total <= 0.0 and gross_total > 0.0:
        included_total = min(gross_total, float(allowance)) if allowance else gross_total
        overage_total = max(overage_total, gross_total - included_total)

    pct = (included_total / allowance) * 100.0 if allowance else 0.0

    top_model, top_count, models_sorted = _fetch_top_model_usage(token, scope, account_slug)

    return {
        "available": True,
        "account": body.get("organization") or body.get("enterprise") or account_slug,
        "scope": scope,
        "year":      int(period.get("year")  or 0),
        "month":     int(period.get("month") or 0),
        "used":      included_total,
        "allowance": int(allowance),
        "pct":       pct,
        "overage":   overage_total,
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
    print(json.dumps(fetch_premium_usage(tok, org), indent=2))
