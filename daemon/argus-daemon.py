#!/usr/bin/env python3
"""Argus daemon (Windows/cross-platform).

Reads Claude Code API key, polls Anthropic usage headers + GitHub counts,
sends JSON to the ESP32 "Argus Controller" over either:
  - BLE GATT (default), or
  - USB CDC serial (--serial COM3 / --serial /dev/ttyACM0)

Dependencies: pip install bleak httpx pyserial PySide6
"""

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

from claude_logs import aggregate as aggregate_today_stats
from claude_logs import to_payload_fields as today_payload_fields
from claude_logs import merge as merge_aggregates
import remote_claude_logs
import github_stats
import copilot_stats
import ci_stats
import tray_ui
from version import __version__

# ---- BLE UUIDs (must match firmware ble.cpp) ----
SERVICE_UUID    = "4c41555a-4465-7669-6365-000000000001"
RX_CHAR_UUID    = "4c41555a-4465-7669-6365-000000000002"
DEVICE_NAME     = "Argus Controller"
POLL_INTERVAL   = 60   # seconds between API polls
RECONNECT_DELAY = 2    # seconds between reconnect attempts
TICK            = 2    # seconds between connection-health checks inside the poll window
BLE_FALLBACK_AFTER = 3 # consecutive BLE failures under a custom name before probing the default name

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    # Push to the tray UI's log view (no-op when the UI isn't running).
    try:
        tray_ui.log_line(line)
    except Exception:
        pass

def read_credentials() -> dict:
    """Read the OAuth block from ~/.claude/.credentials.json.

    Returns the dict that holds at least 'accessToken' (and usually
    'refreshToken'/'expiresAt' for subscription OAuth tokens). Raises if the
    file is missing or has no usable token.
    """
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        raise FileNotFoundError(f"Credentials not found at {cred_path}")
    with open(cred_path) as f:
        data = json.load(f)
    # Subscription OAuth format (claudeAiOauth.accessToken + expiresAt).
    oauth = data.get("claudeAiOauth", {})
    if isinstance(oauth, dict) and oauth.get("accessToken"):
        return oauth
    # Legacy flat format.
    if data.get("accessToken"):
        return {"accessToken": data["accessToken"]}
    raise ValueError("No accessToken in credentials file")


def read_token() -> str:
    """Read just the OAuth access token (used once at startup for validation)."""
    return read_credentials()["accessToken"]


# Last access token we handed to poll_usage(). Tracked so we can log (once) when
# Claude Code rotates the on-disk token, and fall back to it if a later disk read
# fails transiently.
_last_token_seen: str | None = None

# Claude Code's public OAuth client. These let the daemon refresh an expired
# access token itself (using the refresh token already in .credentials.json)
# when Claude Code isn't running to do it. Verified against the documented
# Claude Code OAuth flow; if Anthropic ever changes them, refresh just fails
# safe (we fall back to the on-disk token, same as before this feature).
OAUTH_TOKEN_URL    = "https://console.anthropic.com/v1/oauth/token"
OAUTH_CLIENT_ID    = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
# Only refresh inside this margin before expiry, so we rarely race with Claude
# Code's own refresh (refresh tokens rotate on use).
REFRESH_MARGIN_SEC = 300
_refresh_lock = threading.Lock()


def _credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def _write_credentials(new_oauth: dict) -> bool:
    """Atomically merge the refreshed token fields back into .credentials.json,
    preserving every other key (and file permissions). Returns True on success.
    """
    path = _credentials_path()
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        log(f"Credentials write skipped (re-read failed: {e})")
        return False

    if isinstance(data.get("claudeAiOauth"), dict):
        data["claudeAiOauth"].update({
            "accessToken":  new_oauth["accessToken"],
            "refreshToken": new_oauth["refreshToken"],
            "expiresAt":    new_oauth["expiresAt"],
        })
    else:  # legacy flat format
        data["accessToken"] = new_oauth["accessToken"]

    tmp = path.with_suffix(path.suffix + ".argus-tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        try:  # keep 0600 perms on POSIX; no-op effect on Windows
            os.chmod(tmp, os.stat(path).st_mode)
        except OSError:
            pass
        os.replace(tmp, path)  # atomic on same filesystem
        return True
    except Exception as e:
        log(f"Credentials write failed: {e}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _maybe_refresh_token(oauth: dict) -> dict:
    """If the access token is within REFRESH_MARGIN_SEC of expiry (or already
    expired) and a refresh token is present, exchange it for a fresh one and
    persist it. Returns the (possibly updated) oauth dict; on any failure logs
    and returns the input unchanged so the caller still has a token to try.
    """
    exp = oauth.get("expiresAt")
    rt = oauth.get("refreshToken")
    if not rt or not isinstance(exp, (int, float)):
        return oauth
    if int(exp / 1000 - time.time()) > REFRESH_MARGIN_SEC:
        return oauth

    with _refresh_lock:
        # Another thread (or Claude Code) may have refreshed while we waited on
        # the lock — re-read and bail if the on-disk token is now fresh.
        try:
            fresh = read_credentials()
            fexp = fresh.get("expiresAt")
            if isinstance(fexp, (int, float)) and \
               int(fexp / 1000 - time.time()) > REFRESH_MARGIN_SEC:
                return fresh
            rt = fresh.get("refreshToken", rt)
        except Exception:
            pass

        log("OAuth token near expiry — refreshing via refresh_token")
        try:
            resp = httpx.post(
                OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/json",
                         "User-Agent": "anthropic"},
                json={"grant_type": "refresh_token",
                      "refresh_token": rt,
                      "client_id": OAUTH_CLIENT_ID},
                timeout=30,
            )
        except httpx.HTTPError as e:
            log(f"Token refresh request failed: {e} — keeping current token")
            return oauth
        if resp.status_code != 200:
            log(f"Token refresh rejected (HTTP {resp.status_code}) — keeping current token")
            return oauth
        try:
            d = resp.json()
            new_oauth = dict(oauth)
            new_oauth["accessToken"]  = d["access_token"]
            new_oauth["refreshToken"] = d.get("refresh_token", rt)
            new_oauth["expiresAt"]    = int(time.time() * 1000) + int(d["expires_in"]) * 1000
        except Exception as e:
            log(f"Token refresh parse failed: {e} — keeping current token")
            return oauth

        if _write_credentials(new_oauth):
            log(f"OAuth token refreshed — valid for ~{int(d['expires_in']) // 3600}h")
            return new_oauth
        return oauth


def current_token(fallback: str | None = None) -> str:
    """Re-read the OAuth access token from disk on EVERY poll.

    Claude Code rewrites ~/.claude/.credentials.json with a fresh access token
    roughly every ~8h (the old one expires). The daemon used to capture the
    token once at startup and reuse that stale string forever, so after ~8h the
    API stopped returning real rate-limit headers and the device showed 0% until
    a manual restart. Re-reading here means a refresh by Claude Code is picked up
    automatically on the next poll — no restart needed.

    `fallback` (the startup token) is returned only if the disk read fails.
    """
    global _last_token_seen
    try:
        oauth = read_credentials()
    except Exception as e:
        log(f"Token re-read failed ({e}) — reusing last known token")
        return fallback or _last_token_seen or ""

    # Refresh ourselves if the token is about to expire and Claude Code hasn't.
    oauth = _maybe_refresh_token(oauth)

    tok = oauth.get("accessToken", "")
    exp = oauth.get("expiresAt")
    if isinstance(exp, (int, float)):
        secs = int(exp / 1000 - time.time())
        if secs < 0:
            log(f"On-disk OAuth token expired {-secs // 60}m ago — "
                f"run Claude Code to refresh it")
        elif secs < 600:
            log(f"OAuth token expires in {secs // 60}m {secs % 60}s")

    if tok and tok != _last_token_seen:
        if _last_token_seen is not None:
            log("Picked up refreshed OAuth token from disk")
        _last_token_seen = tok
    return tok or fallback or _last_token_seen or ""

# Last successful rate-limit reading. Reused when the API returns a non-2xx
# response (typically 401 after an OAuth token expires, or transient 5xx) so the
# device keeps showing the last known utilisation instead of flapping to 0%
# every poll. Keys mirror the wire shape returned by poll_usage().
_last_usage_cache: dict | None = None


def poll_usage(token: str) -> dict:
    """Make a minimal API call and extract rate-limit headers.

    Returns the rate-limit fields as a dict so callers can merge other data
    (e.g. today's local-log stats) before serializing.

    Anthropic emits rate-limit headers even on some error responses (e.g. 429),
    but NOT on 401 — so we have to distinguish "no headers because the request
    was rejected" from "no headers because no session has started in this 5h
    window". The former reuses the cached last-good values; the latter reports
    0%.
    """
    global _last_usage_cache
    url = "https://api.anthropic.com/v1/messages"
    # Use Bearer auth for OAuth tokens, x-api-key for legacy API keys
    if token.startswith("sk-ant-oat"):
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        headers = {
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "h"}],
    }

    # We only care about response headers, not body
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=30)
    except httpx.HTTPError as e:
        raise RuntimeError(f"HTTP request failed: {e}")
    
    h = resp.headers
    now = int(time.time())
    
    # Log response status for debugging
    if resp.status_code != 200:
        log(f"API status {resp.status_code} (headers still useful)")

    # Try unified headers first (Claude Code Max subscriptions)
    s5h_util = h.get("anthropic-ratelimit-unified-5h-utilization")
    tok_limit_hdr = h.get("anthropic-ratelimit-tokens-limit")
    if s5h_util is not None:
        # Unified rate limits (subscription-based)
        s5h_util  = float(s5h_util)
        s5h_reset = int(h.get("anthropic-ratelimit-unified-5h-reset", "0"))
        s7d_util  = float(h.get("anthropic-ratelimit-unified-7d-utilization", "0"))
        s7d_reset = int(h.get("anthropic-ratelimit-unified-7d-reset", "0"))
        status    = h.get("anthropic-ratelimit-unified-5h-status", "unknown")
        sp = int(s5h_util * 100)
        sr = max(0, int((s5h_reset - now) / 60))
        wp = int(s7d_util * 100)
        wr = max(0, int((s7d_reset - now) / 60))
    elif tok_limit_hdr is not None:
        # Standard rate limits (API key based) — show token usage percentage
        tok_limit = int(tok_limit_hdr)
        tok_remain = int(h.get("anthropic-ratelimit-tokens-remaining", "0"))
        req_limit = int(h.get("anthropic-ratelimit-requests-limit", "1"))
        req_remain = int(h.get("anthropic-ratelimit-requests-remaining", "0"))
        tok_reset_str = h.get("anthropic-ratelimit-tokens-reset", "")

        # Calculate usage percentage
        tok_used_pct = int(((tok_limit - tok_remain) / max(tok_limit, 1)) * 100)
        req_used_pct = int(((req_limit - req_remain) / max(req_limit, 1)) * 100)

        # Parse reset time
        sr = 0
        if tok_reset_str:
            try:
                from datetime import datetime, timezone
                reset_dt = datetime.fromisoformat(tok_reset_str.replace("Z", "+00:00"))
                sr = max(0, int((reset_dt.timestamp() - now) / 60))
            except Exception:
                pass

        sp = max(tok_used_pct, req_used_pct)
        wp = sp  # No weekly data for standard API
        wr = 0
        status = "active"
        log(f"Tokens: {tok_remain}/{tok_limit}, Requests: {req_remain}/{req_limit}")
    elif resp.status_code in (401, 403):
        # Auth failed — token expired/revoked. Don't pretend the user is at 0%;
        # serve the last known good values so the panels don't flap. Log
        # loudly because this is something only the user can fix.
        log(
            f"Auth failed (HTTP {resp.status_code}) — Claude Code OAuth token "
            f"likely expired. Run `claude /login` to refresh it."
        )
        if _last_usage_cache is not None:
            return dict(_last_usage_cache)
        sp = sr = wp = wr = 0
        status = "auth-error"
    elif resp.status_code >= 500 or resp.status_code == 429:
        # Transient API issue — reuse cache so the device doesn't blink to 0%
        # during a brief Anthropic outage / rate-limit on the meta endpoint.
        log(f"API status {resp.status_code} with no rate-limit headers — reusing last good values")
        if _last_usage_cache is not None:
            return dict(_last_usage_cache)
        sp = sr = wp = wr = 0
        status = "unavailable"
    else:
        # 2xx with no rate-limit headers — genuine "no session started yet" in
        # the current 5h window. Reporting 0% is the right answer here.
        sp = sr = wp = wr = 0
        status = "idle"
        log("No rate-limit headers in response — reporting 0% (no active session)")

    result = {
        "s": sp, "sr": sr,
        "w": wp, "wr": wr,
        "st": status, "ok": True,
    }
    # Only cache successful reads with real header data — never cache the
    # zero-fallback paths, otherwise a startup glitch would freeze 0% forever.
    if status not in ("auth-error", "unavailable", "idle"):
        _last_usage_cache = dict(result)
    return result


def demo_payload() -> str:
    """Generate fake usage data for testing the UI (rate-limit + Today fields)."""
    import random
    sp = random.randint(20, 80)
    wp = random.randint(10, 60)
    sr = random.randint(30, 280)
    wr = random.randint(500, 9000)
    # Fake Today numbers — split sums to ~100 so the screen looks realistic.
    opus = random.randint(10, 50)
    sonnet = random.randint(10, 100 - opus)
    fable = random.randint(0, max(0, 100 - opus - sonnet))
    haiku = max(0, 100 - opus - sonnet - fable)
    opus_w = random.randint(10, 50)
    sonnet_w = random.randint(10, 100 - opus_w)
    fable_w = random.randint(0, max(0, 100 - opus_w - sonnet_w))
    haiku_w = max(0, 100 - opus_w - sonnet_w - fable_w)
    fields = {
        "s": sp, "sr": sr,
        "w": wp, "wr": wr,
        "st": "active", "ok": True,
        "c":  round(random.uniform(0.5, 8.0), 2),
        "cw": round(random.uniform(5.0, 60.0), 2),
        "mo": opus, "ms": sonnet, "mh": haiku, "mf": fable,
        "mow": opus_w, "msw": sonnet_w, "mhw": haiku_w, "mfw": fable_w,
        "ch": random.randint(40, 95),
        "tk": random.randint(50_000, 800_000),
        "se": random.randint(1, 12),
        "pj": "argus",
        "ge": True,
        "gi": random.randint(0, 7),
        "gp": random.randint(0, 4),
        "br": 100,
        "cp":  True,
        "cps": random.choice(["active", "idle", "inactive"]),
        "cpw": random.choice(["just now", "3 min ago", "12 min ago", "2 hours ago"]),
        "cpe": random.choice(["VS Code", "JetBrains", "Neovim"]),
        "cpr": True,
        "cpu": random.randint(50, 2900),
        "cpa": 3000,
        "cpm": random.choice(["Claude Opus 4.6", "GPT-4o", "Claude Sonnet 4.5"]),
        "apps": "usage,today,github,copilot",
    }
    # Derive the percent from cpu/cpa so demo numbers stay self-consistent.
    fields["cpp"] = round(fields["cpu"] / fields["cpa"] * 100.0, 1)
    # Mood + events strip, computed from the demo fields so the splash
    # actually reacts to demo numbers (e.g. cranks up to "angry" if a
    # cap hit 100%) instead of being stuck on one expression.
    mood, events = _mood_and_events(fields)
    fields["md"]   = mood
    fields["evts"] = events
    return json.dumps(fields, separators=(",", ":"))


# Cache for GitHub fetches so we don't hit the API once per poll if the user
# has a fast tick — refresh every 5 minutes (well under the search-API limit).
_gh_cache = {"at": 0.0, "data": None, "token_hash": None}
_GH_CACHE_TTL = 300

# Same caching strategy for the Copilot seat lookup. The TTL is short (60 s)
# because `last_activity_at` is the headline number and the user actively
# changes it by typing code — we want freshness here, not API frugality.
_cp_cache = {"at": 0.0, "data": None, "key_hash": None}
_CP_CACHE_TTL = 60

# Premium-request usage is a monthly counter, so a much longer TTL is fine
# and saves API calls. 5 min keeps the device "fresh enough" to notice a
# new burst within the next two polls.
_cp_prem_cache = {"at": 0.0, "data": None, "key_hash": None}
_CP_PREM_CACHE_TTL = 300

# CI/CD (GitHub Actions). Short TTL — build state changes fast and the user
# wants to see a red/green flip promptly.
_ci_cache = {"at": 0.0, "data": None, "token_hash": None}
_CI_CACHE_TTL = 60


def force_refresh() -> None:
    """Expire the GitHub / Copilot response caches so the very next poll
    re-fetches from every source. Called when the user presses Save (paired
    with a wake) so the device reflects current data immediately instead of
    showing values cached for up to the per-source TTL (GitHub 5 min, Copilot
    seat 60 s, AI-credits 5 min). The Anthropic rate-limit read already happens
    on every poll, so it needs no invalidation here."""
    _gh_cache["at"] = 0.0
    _cp_cache["at"] = 0.0
    _cp_prem_cache["at"] = 0.0
    _ci_cache["at"] = 0.0

# Snapshot of the last poll's "watch these for changes" counters. None means
# "we haven't observed a baseline yet" — first run after a daemon restart
# never triggers an auto-focus, so we don't yank the screen on every reboot.
_focus_prev = {"gh_issues": None, "gh_prs": None}
# Per-repo signature (run_id, status-code) from the last CI poll. Empty means no
# baseline yet, so the first poll after a restart never fires a CI focus.
_ci_focus_prev: dict = {}


def _detect_focus(fields: dict, cfg: dict) -> str | None:
    """If something noteworthy changed since the last poll, return the screen
    the device should auto-switch to:

        - A workflow needs approval / action  → "ci"   (highest priority)
        - New PR or new issue (count went up)  → "github"
        - A workflow just failed               → "ci"  (if enabled)
        - A workflow just succeeded            → "ci"  (if enabled)

    Updates the baselines in place so the next poll has something to diff.
    """
    # ---- GitHub issue/PR deltas ----
    github_changed = False
    if fields.get("ge"):
        gi = int(fields.get("gi", 0))
        gp = int(fields.get("gp", 0))
        if _focus_prev["gh_issues"] is not None and _focus_prev["gh_prs"] is not None:
            github_changed = gp > _focus_prev["gh_prs"] or gi > _focus_prev["gh_issues"]
        _focus_prev["gh_issues"] = gi
        _focus_prev["gh_prs"]    = gp
    else:
        # GitHub disabled or no token — drop the baseline so re-enabling later
        # doesn't fire a stale "new PR" focus on the first poll back.
        _focus_prev["gh_issues"] = None
        _focus_prev["gh_prs"]    = None

    # ---- CI/CD run transitions ----
    ci_fail = ci_success = ci_action = False
    if fields.get("cie"):
        runs = (_ci_cache.get("data") or {}).get("runs") or []
        seen = set()
        for run in runs:
            repo = run["repo"]
            seen.add(repo)
            sig = (run.get("run_id"), run.get("code"))
            prev = _ci_focus_prev.get(repo)
            if prev is not None and prev != sig:
                if   run["code"] == "fail": ci_fail = True
                elif run["code"] == "ok":   ci_success = True
                elif run["code"] == "wait": ci_action = True
            _ci_focus_prev[repo] = sig
        # Forget repos that dropped out of the watched set.
        for repo in [r for r in _ci_focus_prev if r not in seen]:
            del _ci_focus_prev[repo]
    else:
        _ci_focus_prev.clear()

    # ---- Priority: action needed > new GitHub items > build failed > passed ----
    if ci_action and cfg.get("ci_focus_action", True):
        return "ci"
    if github_changed:
        return "github"
    if ci_fail and cfg.get("ci_focus_fail", True):
        return "ci"
    if ci_success and cfg.get("ci_focus_success", False):
        return "ci"
    return None


def _github_fields(cfg: dict) -> dict:
    """Return {'ge': bool, 'gi': int, 'gp': int} based on the configured PAT.

    Cached by (token, time) so changing the token invalidates the cache, and
    the API gets one call per 5 min regardless of poll interval.
    """
    tok = cfg.get("github_token") or ""
    if not tok:
        return {"ge": False, "gi": 0, "gp": 0}

    now = time.time()
    th = hash(tok)
    if _gh_cache["data"] and _gh_cache["token_hash"] == th and (now - _gh_cache["at"]) < _GH_CACHE_TTL:
        d = _gh_cache["data"]
    else:
        try:
            d = github_stats.fetch(tok)
            _gh_cache.update({"at": now, "data": d, "token_hash": th})
        except github_stats.GitHubError as e:
            log(f"GitHub fetch failed: {e}")
            return {"ge": False, "gi": 0, "gp": 0}

    return {"ge": True, "gi": d["issues"], "gp": d["prs"]}


def _ci_enabled(cfg: dict) -> bool:
    """CI/CD is on when the 'ci' screen is enabled (None = boot default = all)."""
    apps = cfg.get("enabled_apps")
    return apps is None or "ci" in apps


def _ci_fields(cfg: dict) -> dict:
    """Build the CI/CD block. Reuses the GitHub PAT (needs Actions: read).
    Cached on its own short TTL. Returns {'cie': False} when disabled, tokenless,
    or the lookup failed — the firmware then shows a hint on the CI screen."""
    tok = cfg.get("github_token") or ""
    if not tok or not _ci_enabled(cfg):
        return {"cie": False}

    now = time.time()
    th = hash(tok)
    if _ci_cache["data"] and _ci_cache["token_hash"] == th and (now - _ci_cache["at"]) < _CI_CACHE_TTL:
        d = _ci_cache["data"]
    else:
        try:
            d = ci_stats.fetch(tok)
            _ci_cache.update({"at": now, "data": d, "token_hash": th})
        except ci_stats.CIError as e:
            log(f"CI fetch failed: {e}")
            return {"cie": False}

    return {
        "cie": True,
        "cis": d["status"],
        "cir": d["repo"][:27],
        "cib": d["branch"][:23],
        "ciw": d["workflow"][:27],
        "cif": int(d["failing"]),
        "ciq": int(d["waiting"]),
    }


def _copilot_fields(cfg: dict) -> dict:
    """Build the Copilot block of the device payload.

    Two independent reads, both cached separately so a failure on one
    doesn't suppress the other:
      • seat status / last activity / editor — needs `copilot_org`
        • monthly AI-credit usage               — uses `copilot_enterprise` when set,
                                     otherwise `copilot_org`

    The shape we emit on the wire is intentionally flat:
        cp   bool  Copilot section enabled at all (org configured)
        cps  str   status: "active"|"idle"|"inactive"|"off"
        cpw  str   relative time of last activity, e.g. "5 min ago"
        cpe  str   pretty editor name, e.g. "VS Code"
                cpr  bool  AI-credit data available (org billing endpoint + 200)
                cpp  float included AI-credit % used (60.4 → "60.4%")
                cpu  int   included AI credits consumed this month
                cpa  int   configured monthly included AI-credit pool
                cpm  str   top model name, e.g. "GPT-5.4"
    """
    tok = cfg.get("github_token") or ""
    org = cfg.get("copilot_org") or ""
    ent = cfg.get("copilot_enterprise") or ""
    allowance = int(cfg.get("copilot_allowance") or 0)

    if not tok:
        return {"cp": False}

    out: dict = {"cp": False}

    # ---- Seat / status ------------------------------------------------
    if org:
        now = time.time()
        key = hash((tok, org))
        if _cp_cache["data"] and _cp_cache["key_hash"] == key and (now - _cp_cache["at"]) < _CP_CACHE_TTL:
            d = _cp_cache["data"]
        else:
            try:
                d = copilot_stats.fetch(tok, org)
                _cp_cache.update({"at": now, "data": d, "key_hash": key})
            except copilot_stats.CopilotError as e:
                log(f"Copilot seat fetch failed: {e}")
                d = None
        if d is not None:
            out.update({
                "cp":  True,
                "cps": d.get("status", "off"),
                "cpw": d.get("when",   "—"),
                "cpe": d.get("editor", ""),
            })

    # ---- AI-credit usage ---------------------------------------------
    usage_scope = "enterprise" if ent else "org"
    usage_slug = ent or org
    if usage_slug:
        now = time.time()
        key = hash((tok, usage_scope, usage_slug, allowance))
        if _cp_prem_cache["data"] and _cp_prem_cache["key_hash"] == key and (now - _cp_prem_cache["at"]) < _CP_PREM_CACHE_TTL:
            p = _cp_prem_cache["data"]
        else:
            p = copilot_stats.fetch_premium_usage(
                tok,
                usage_slug,
                allowance=allowance or None,
                scope=usage_scope,
            )
            _cp_prem_cache.update({"at": now, "data": p, "key_hash": key})
        if p.get("available"):
            out.update({
                "cp":  True,
                "cpr": True,
                # Round to 0.1 so the wire size stays small and the device
                # rendering matches the example script's "60.4%" format.
                "cpp": round(float(p["pct"]), 1),
                "cpu": int(round(p["used"])),
                "cpa": int(p["allowance"]),
                "cpsu": p.get("scope", "org"),
                "cpm": p.get("top_model", "") or "",
            })

    return out


# Order matters — this is the cycle order on the device. Add new apps here
# (and to the firmware's name_to_screen() table + enum) to grow the list.
ALL_APPS = ["usage", "today", "github", "ci", "copilot"]


def _apps_csv(cfg: dict) -> str:
    """Comma-joined list of enabled apps. Default = everything checked."""
    enabled = cfg.get("enabled_apps")
    if enabled is None:
        return ",".join(ALL_APPS)
    # Preserve cycle order; drop anything the user typed that we don't know.
    return ",".join(a for a in ALL_APPS if a in enabled)


def _format_reset(mins: int) -> str:
    """Compact 'resets in Xh Ym' / 'Xd Yh' / 'Xm' string for the events
    strip. -1 / 0 -> empty so callers can append " - resets in X" safely."""
    if mins is None or mins < 0:
        return ""
    if mins < 60:
        return f"{mins}m"
    if mins < 1440:
        return f"{mins // 60}h {mins % 60}m"
    return f"{mins // 1440}d {(mins % 1440) // 60}h"


# Track the previous payload's counters so we can detect deltas (a new PR
# arriving since the last poll). The "new" flag is sticky for one poll
# cycle so the device has time to surface it.
_events_prev = {"gi": None, "gp": None}
_events_new_seen_at = {"gp": 0.0, "gi": 0.0}
_EVENTS_NEW_WINDOW_S = 3600  # show "New PR/issue" for up to 1 hour


def _mood_and_events(fields: dict) -> tuple[str, list[str]]:
    """Decide which Argus expression to show on the splash and assemble the
    rotating event-text list below it. Priorities, highest-first:

        angry      any cap >= 100% (Claude session / weekly / Copilot AI credits)
        surprised  a new PR or issue arrived in the last hour
        buffeld    any cap >= 80%
        flirt      any cap >= 50%
        looking    any cap >= 25% — quiet but active
        happy      default — under 25%, nothing waiting

    Events strip lists the human-readable lines the device cycles through
    every few seconds: cap warnings (with reset countdown), Copilot
    AI-credit burn, GitHub queues, and any "New X" notices that arrived in
    the last hour.
    """
    s   = float(fields.get("s")  or 0.0)
    w   = float(fields.get("w")  or 0.0)
    sr  = int  (fields.get("sr") or -1)
    wr  = int  (fields.get("wr") or -1)
    cpu = int  (fields.get("cpu") or 0)
    cpa = int  (fields.get("cpa") or 0)
    cpp = float(fields.get("cpp") or 0.0)
    gi  = int  (fields.get("gi") or 0)
    gp  = int  (fields.get("gp") or 0)
    ge  = bool (fields.get("ge"))
    cpr = bool (fields.get("cpr"))

    max_cap = max(s, w, cpp)

    # ---- New-event detection -----------------------------------------
    # We can't see the per-item timestamps yet; "new" is defined as
    # "the count went up since last poll". That lets us flip the
    # surprised expression + a "New PR/issue" line for up to one hour
    # without scraping individual creation dates.
    now = time.time()
    new_pr_recent = False
    new_issue_recent = False
    if ge:
        prev_gi = _events_prev["gi"]
        prev_gp = _events_prev["gp"]
        if prev_gp is not None and gp > prev_gp:
            _events_new_seen_at["gp"] = now
        if prev_gi is not None and gi > prev_gi:
            _events_new_seen_at["gi"] = now
        _events_prev["gp"] = gp
        _events_prev["gi"] = gi
        if now - _events_new_seen_at["gp"] < _EVENTS_NEW_WINDOW_S:
            new_pr_recent = True
        if now - _events_new_seen_at["gi"] < _EVENTS_NEW_WINDOW_S:
            new_issue_recent = True
    else:
        _events_prev["gp"] = None
        _events_prev["gi"] = None

    # ---- Mood --------------------------------------------------------
    if max_cap >= 100.0:
        mood = "angry"
    elif new_pr_recent or new_issue_recent:
        mood = "surprised"
    elif max_cap >= 80.0:
        mood = "buffeld"
    elif max_cap >= 50.0:
        mood = "flirt"
    elif max_cap >= 25.0:
        mood = "looking"
    else:
        mood = "happy"

    # ---- Events strip -----------------------------------------------
    # Order matters — the first entry is the one the user sees first
    # when the splash opens; subsequent entries cycle every few seconds.
    events: list[str] = []

    # Hot caps first.
    if w >= 50.0:
        r = _format_reset(wr)
        events.append(f"Claude weekly: {int(round(w))}%" + (f" - resets in {r}" if r else ""))
    if s >= 50.0:
        r = _format_reset(sr)
        events.append(f"Claude session: {int(round(s))}%" + (f" - resets in {r}" if r else ""))
    if cpr and cpa > 0 and cpp >= 50.0:
        events.append(f"Copilot AI credits: {cpu}/{cpa} ({cpp:.0f}%)")

    # New-event highlights.
    if new_pr_recent:
        events.append(f"New PR - awaiting review (total {gp})")
    if new_issue_recent:
        events.append(f"New issue - assigned to you (total {gi})")

    # Steady-state queues — only when nothing more urgent is on the list.
    if ge and not events:
        if gp or gi:
            events.append(f"GitHub: {gi} issues - {gp} PRs")
    # Always-on Copilot summary if available + not already in events.
    if cpr and cpa > 0 and cpp < 50.0 and len(events) < 4:
        events.append(f"Copilot AI credits: {cpu}/{cpa} ({cpp:.0f}%)")

    if not events:
        events.append("All clear")

    # Cap at 6 entries so we never bloat the BLE payload.
    return mood, events[:6]


# The payload is streamed to the device in chunks and reassembled (see
# _ble_write), so it's no longer bounded by the 512-octet single-attribute
# limit. This is just a backstop kept under the firmware's reassembly buffer
# (BLE_BUF_SIZE = 1024) so a pathologically large payload can't overflow it.
_PAYLOAD_BUDGET = 1000


def _fit_payload(fields: dict) -> str:
    """Serialize `fields`; if it won't fit in one BLE attribute, shed the
    least-important keys (and trim the events strip) until it does. The firmware
    treats every shed key as absent and falls back gracefully, so a CI workflow
    name or a second events line is a far better thing to lose than the whole
    payload."""
    s = json.dumps(fields, separators=(",", ":"))
    if len(s) <= _PAYLOAD_BUDGET:
        return s

    def _evts_to_one():
        e = fields.get("evts")
        if isinstance(e, list) and len(e) > 1:
            fields["evts"] = e[:1]

    # Least-important first. The events strip is cosmetic (rotating splash
    # text), so it goes before the actionable CI detail. CI then degrades
    # workflow → branch → repo, leaving the headline status/counts intact.
    steps = [
        ("cpm",    lambda: fields.pop("cpm", None)),   # Copilot top model (often empty)
        ("evts:1", _evts_to_one),                       # trim events strip to one line
        ("ciw",    lambda: fields.pop("ciw", None)),   # CI workflow name (bottom hint only)
        ("evts",   lambda: fields.pop("evts", None)),  # drop the events strip entirely
        ("cib",    lambda: fields.pop("cib", None)),   # CI branch
        ("cir",    lambda: fields.pop("cir", None)),   # CI repo name
        ("cpw",    lambda: fields.pop("cpw", None)),   # Copilot last-activity
        ("pj",     lambda: fields.pop("pj", None)),    # today's project name
    ]
    dropped = []
    for label, mutate in steps:
        if len(s) <= _PAYLOAD_BUDGET:
            break
        mutate()
        dropped.append(label)
        s = json.dumps(fields, separators=(",", ":"))
    if dropped:
        log(f"payload trimmed to {len(s)}B for BLE (dropped: {', '.join(dropped)})")
    return s


# Signature of the last poll's substantive values, for power-save wake. None =
# no baseline yet.
_last_change_sig = None


def _changed_since_last(fields: dict) -> bool:
    """True when a substantive value changed since the previous poll. Used by the
    device to wake from power-save. Deliberately ignores time-decay fields (reset
    countdowns, "x min ago", the events strip) that tick every poll — otherwise
    an idle device would never sleep. The first poll counts as changed so the
    screen starts on."""
    global _last_change_sig
    sig = (
        fields.get("s"), fields.get("w"),                          # Claude caps
        fields.get("gi"), fields.get("gp"),                        # GitHub issues / PRs
        fields.get("cps"), fields.get("cpu"),                      # Copilot status / credits used
        fields.get("cis"), fields.get("cif"), fields.get("ciq"),   # CI status / failing / waiting
        fields.get("tk"), fields.get("c"),                         # today's tokens / cost
    )
    changed = _last_change_sig is None or sig != _last_change_sig
    _last_change_sig = sig
    return changed


def build_payload(api_token: str) -> str:
    """Combine rate-limit headers, today's local-log stats, GitHub counts, and
    brightness into one JSON line. Reads config.json on every call so the
    tray UI's edits apply at the next poll without a restart."""
    cfg = tray_ui.load_config()

    # Re-read the token from disk every poll so a refresh by Claude Code is
    # picked up without restarting the daemon (api_token is the startup token,
    # used only as a fallback if the disk read fails).
    token = current_token(fallback=api_token)
    fields = poll_usage(token)
    try:
        agg = aggregate_today_stats()
        remote_hosts = cfg.get("remote_claude_hosts") or []
        if remote_hosts:
            agg = merge_aggregates(agg, remote_claude_logs.get_cached(remote_hosts, log=log))
        today = today_payload_fields(agg)
        fields.update(today)
    except Exception as e:
        # Local log parsing must never break the rate-limit display.
        log(f"Today stats failed: {e}")

    fields.update(_github_fields(cfg))
    fields.update(_ci_fields(cfg))
    fields.update(_copilot_fields(cfg))
    fields["br"] = max(10, min(100, int(cfg.get("brightness", 100))))
    fields["apps"] = _apps_csv(cfg)

    # Today-screen element visibility (firmware defaults both to true if absent).
    fields["tac"] = bool(cfg.get("today_show_cost", True))
    fields["tch"] = bool(cfg.get("today_show_cache", True))

    # Power save: master switch + poll-aware idle timeout + a "changed" flag the
    # device uses to wake. Timeout is at least 5 min, or poll_interval + 1 min
    # when polls are slower than that (so a poll always lands before sleep).
    poll = int(cfg.get("poll_interval", POLL_INTERVAL))
    fields["ps"]  = bool(cfg.get("power_save", False))
    fields["pst"] = max(300, poll + 60)
    fields["chg"] = _changed_since_last(fields)

    # Desired BLE name. When a rename is pending we push the target so the device
    # adopts it; otherwise we keep asserting the current name (the firmware no-ops
    # when it already matches). _commit_pending_rename() promotes the target to
    # device_name once a payload carrying it has been delivered.
    device_name = (cfg.get("device_name") or DEVICE_NAME).strip() or DEVICE_NAME
    pending = (cfg.get("pending_name") or "").strip()
    fields["nm"] = pending or device_name

    # Mood-driven splash expression + rotating event strip.
    mood, events = _mood_and_events(fields)
    fields["md"]   = mood
    fields["evts"] = events

    focus = _detect_focus(fields, cfg)
    if focus:
        fields["fc"] = focus
        log(f"Auto-focus → {focus}")

    return _fit_payload(fields)

def configured_device_name() -> str:
    """The BLE name the device currently advertises and we scan for. Falls back
    to the factory default. Note this is intentionally NOT the pending rename
    target — we keep scanning under the current name until a rename is confirmed
    delivered (see _commit_pending_rename)."""
    name = (tray_ui.load_config().get("device_name") or DEVICE_NAME).strip()
    return name or DEVICE_NAME


def _commit_pending_rename() -> None:
    """Promote a pending rename to the live device name. Call this only AFTER a
    payload carrying the pending name (`nm`) has been delivered, so the device
    has actually adopted it. Until then we keep scanning under the old name, so
    a dropped link mid-rename never leaves us hunting for a name no device
    advertises yet."""
    cfg = tray_ui.load_config()
    pending = (cfg.get("pending_name") or "").strip()
    if not pending:
        return
    if pending != (cfg.get("device_name") or "").strip():
        cfg["device_name"] = pending
        log(f"Device renamed to '{pending}' — will reconnect under the new name")
    cfg["pending_name"] = ""
    tray_ui.save_config(cfg)


def _adopt_device_name(name: str) -> None:
    """Persist `name` as the device_name we scan for, clearing any pending
    rename. Used when we reached the device via the default-name fallback (e.g.
    a reflash wiped the persisted rename), so the saved name matches reality and
    we stop burning scans on a name nothing advertises."""
    cfg = tray_ui.load_config()
    if (cfg.get("device_name") or "") != name or (cfg.get("pending_name") or ""):
        prev = cfg.get("device_name") or ""
        cfg["device_name"] = name
        cfg["pending_name"] = ""
        tray_ui.save_config(cfg)
        log(f"Saved device name reset to '{name}' (was '{prev}')")


async def find_device(name: str | None = None):
    """Scan for an Argus BLE device by exact name. Defaults to the configured
    name; callers pass an explicit name for the default-name fallback."""
    from bleak import BleakScanner
    target = name or configured_device_name()
    log(f"Scanning for '{target}'...")
    devices = await BleakScanner.discover(timeout=10)
    for d in devices:
        if d.name and d.name == target:
            log(f"Found: {d.name} [{d.address}]")
            return d
    return None

def find_serial_port() -> str | None:
    """Scan attached USB serial devices for the ESP32-S3 (Espressif VID 0x303A).

    The ESP32-S3 USB JTAG/serial debug unit enumerates as VID 0x303A. PID 0x1001
    is the normal runtime CDC; 0x4001 appears in download mode. We pick the
    first match — if the user has multiple boards plugged in, they should pass
    --serial PORT explicitly.
    """
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == 0x303A and p.pid in (0x1001, 0x4001):
            return p.device
    # Fallback: match on description for boards using a CH343/CP210x bridge that
    # still advertise Espressif in their USB strings.
    for p in list_ports.comports():
        desc = ((p.description or "") + " " + (p.manufacturer or "")).lower()
        if "espressif" in desc or "usb jtag" in desc:
            return p.device
    return None


# The ESP32-S3 HWCDC RX ring defaults to 256 bytes and payloads have grown
# past it (~420 B). 48-byte chunks with a short pause let loop() drain the
# ring between writes. Firmware built after issue #19 also enlarges the ring,
# but chunking keeps the daemon working with boards flashed before that —
# mirroring what the BLE path has always done.
SERIAL_CHUNK = 48
SERIAL_INTER_CHUNK_DELAY = 0.02


def run_serial(port_or_auto: str, baud: int, demo_mode: bool, token,
               stop_event: threading.Event | None = None,
               wake_event: threading.Event | None = None,
               reload_event: threading.Event | None = None):
    """USB CDC transport: write JSON payloads over serial, one line at a time.

    If `port_or_auto == "auto"`, the port is re-resolved on every reconnect
    attempt so plug/unplug works even when the OS assigns a new COM number.

    `stop_event` (optional) lets the tray's Quit menu terminate us cleanly.
    `wake_event` (optional) lets the tray's Save button cut the inter-poll
    wait short so a fresh payload (with the new settings) hits the device
    immediately instead of up to POLL_INTERVAL seconds later.
    """
    import serial  # pyserial — imported lazily so BLE-only users don't need it

    def _should_stop() -> bool:
        # reload_event means "the tray saved a different transport" — treat it
        # like stop so run_worker can re-read the config and switch over.
        return ((stop_event is not None and stop_event.is_set())
                or (reload_event is not None and reload_event.is_set()))

    while not _should_stop():
        port = port_or_auto
        if port == "auto":
            tray_ui.set_status("warn", "USB — scanning for ESP32-S3…")
            port = find_serial_port()
            if not port:
                log(f"No ESP32-S3 on USB (VID 0x303A). Retrying in {RECONNECT_DELAY}s...")
                tray_ui.set_status("warn", f"USB — no ESP32-S3, retrying in {RECONNECT_DELAY}s")
                time.sleep(RECONNECT_DELAY)
                continue
            log(f"Auto-detected ESP32-S3 on {port}")
        try:
            log(f"Opening serial port {port} @ {baud}...")
            # dsrdtr/rtscts off is not enough: pyserial still asserts DTR when
            # the constructor opens the port, and on the ESP32-S3's native
            # USB-Serial-JTAG that edge fires USB_UART_CHIP_RESET — the board
            # reboots the moment we connect. Construct closed, clear the
            # control lines, then open. (Issue #19, cause 3.)
            _ser = serial.Serial(None, baud, timeout=1, dsrdtr=False, rtscts=False)
            _ser.port = port
            _ser.dtr = False
            _ser.rts = False
            _ser.open()
            with _ser as ser:
                log(f"Serial open: {port}")
                tray_ui.set_status("ok", f"USB — connected on {port}")
                while True:
                    try:
                        payload = demo_payload() if demo_mode else build_payload(token)
                        log(f"Sending: {payload}")
                        # A single write of the whole line overflows the
                        # firmware's 256 B HWCDC RX ring; the tail is silently
                        # dropped, no "\n" arrives, and the command never
                        # dispatches. Chunk it. (Issue #19, cause 4, fix B.)
                        data = (payload + "\n").encode("utf-8")
                        for i in range(0, len(data), SERIAL_CHUNK):
                            ser.write(data[i:i + SERIAL_CHUNK])
                            ser.flush()
                            time.sleep(SERIAL_INTER_CHUNK_DELAY)
                        # The firmware acks every payload (USB_USAGE_OK /
                        # USB_USAGE_ERR). Log it — "Sending:" alone looks the
                        # same whether the device parsed the line or never
                        # received it, which made this bug invisible.
                        time.sleep(0.05)
                        if ser.in_waiting:
                            reply = ser.read(ser.in_waiting).decode("utf-8", "replace").strip()
                            if reply:
                                log(f"Device: {reply}")
                        _commit_pending_rename()
                    except httpx.HTTPError as e:
                        log(f"API error: {e}")
                    except (serial.SerialException, OSError) as e:
                        log(f"Serial write error: {e}")
                        break
                    except Exception as e:
                        log(f"Poll error: {e}")

                    # Chunked wait: bail out fast when the cable is unplugged.
                    # pyserial doesn't surface link state directly, so we probe
                    # is_open and read DSR (cleared when CDC peer disappears on
                    # most Windows USB stacks). poll_interval is re-read each
                    # cycle so saved changes take effect on the next send.
                    cur_interval = max(5, int(tray_ui.load_config().get("poll_interval", POLL_INTERVAL)))
                    waited = 0
                    disconnected = False
                    while waited < cur_interval and not _should_stop():
                        # wake_event.wait returns True immediately if Save
                        # set it; otherwise blocks up to TICK seconds.
                        if wake_event is not None and wake_event.wait(TICK):
                            wake_event.clear()
                            log("Wake — pushing payload now")
                            break
                        elif wake_event is None:
                            time.sleep(TICK)
                        waited += TICK
                        if not ser.is_open:
                            disconnected = True
                            break
                        try:
                            # Touching any property on a closed CDC raises OSError
                            # on Windows. We use in_waiting as a cheap probe.
                            _ = ser.in_waiting
                        except (serial.SerialException, OSError):
                            disconnected = True
                            break
                    if _should_stop():
                        return
                    if disconnected:
                        log("Serial link dropped — reconnecting")
                        tray_ui.set_status("warn", "USB — link dropped, reconnecting")
                        break
        except (serial.SerialException, OSError) as e:
            log(f"Serial open failed: {e}")
            tray_ui.set_status("err", f"USB error: {e}")
        if _should_stop():
            return
        log(f"Reconnecting in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)

# Bytes per BLE write. Kept under the smallest common ATT MTU payload
# (MTU 247 → 244 usable) so each chunk is a single write op on any adapter; with
# the usual MTU 517 it's well within one op too. The firmware reassembles chunks
# up to its newline terminator.
_BLE_CHUNK = 240


async def _ble_write(client, payload: str) -> None:
    """Write `payload` to the RX characteristic, newline-terminated, in
    MTU-safe chunks. The firmware accumulates chunks until the '\\n', so the
    payload can exceed the 512-octet single-attribute limit."""
    data = (payload + "\n").encode("utf-8")
    for i in range(0, len(data), _BLE_CHUNK):
        await client.write_gatt_char(RX_CHAR_UUID, data[i:i + _BLE_CHUNK], response=True)


async def run_ble(demo_mode: bool, token,
                  stop_event: threading.Event | None = None,
                  wake_event: threading.Event | None = None,
                  reload_event: threading.Event | None = None):
    from bleak import BleakClient

    def _should_stop() -> bool:
        # reload_event means "the tray saved a different transport" — treat it
        # like stop so run_worker can re-read the config and switch over.
        return ((stop_event is not None and stop_event.is_set())
                or (reload_event is not None and reload_event.is_set()))

    loop = asyncio.get_running_loop()

    async def _wait_or_wake(timeout: float) -> bool:
        """Sleep up to `timeout` seconds. Return True if wake_event fired
        during the wait (and clear it), False otherwise."""
        if wake_event is None:
            await asyncio.sleep(timeout)
            return False
        # Run the threading.Event wait on the default executor so it doesn't
        # block the asyncio loop. Cheap because the executor thread sleeps
        # natively and is released on return.
        fired = await loop.run_in_executor(None, wake_event.wait, timeout)
        if fired:
            wake_event.clear()
        return fired

    fail_count = 0
    while not _should_stop():
        # Find device. After BLE_FALLBACK_AFTER straight failures under a custom
        # name, probe the factory-default name once — recovers from a config that
        # points at a name the device no longer advertises (e.g. a reflash wiped
        # the persisted rename). The probe consumes the streak either way, so we
        # alternate back to the configured name rather than getting stuck.
        target = configured_device_name()
        probe_default = fail_count >= BLE_FALLBACK_AFTER and target != DEVICE_NAME
        scan_name = DEVICE_NAME if probe_default else target
        if probe_default:
            log(f"Can't reach '{target}' after {fail_count} tries — probing default name '{DEVICE_NAME}'")

        tray_ui.set_status("warn", f"BLE — scanning for '{scan_name}'…")
        device = await find_device(scan_name)
        if not device:
            fail_count = 0 if probe_default else fail_count + 1
            log(f"'{scan_name}' not found, retrying in {RECONNECT_DELAY}s...")
            tray_ui.set_status("warn", f"BLE — '{scan_name}' not found, retrying in {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)
            continue

        # Connect and send data
        try:
            async with BleakClient(device.address, timeout=15) as client:
                log(f"Connected to {device.address}")
                tray_ui.set_status("ok", f"BLE — connected to {device.address}")
                if probe_default:
                    # Reached it under the default name → the saved name was stale.
                    _adopt_device_name(DEVICE_NAME)
                fail_count = 0

                # Log discovered services for debugging
                for svc in client.services:
                    log(f"  Service: {svc.uuid}")
                    for char in svc.characteristics:
                        log(f"    Char: {char.uuid} props={char.properties}")

                while client.is_connected:
                    try:
                        payload = demo_payload() if demo_mode else build_payload(token)
                        log(f"Sending: {payload}")
                        # Streamed in newline-terminated chunks and reassembled
                        # on the device, so payloads aren't capped at the
                        # 512-octet single-attribute limit. response=True keeps
                        # the chunks ordered and acknowledged.
                        await _ble_write(client, payload)
                        log("Sent OK")
                        _commit_pending_rename()
                    except httpx.HTTPError as e:
                        log(f"API error: {e}")
                    except Exception as e:
                        log(f"Write error: {e}")
                        break

                    # Chunked wait: sleep TICK seconds at a time and bail out
                    # the moment bleak reports a disconnect, instead of holding
                    # the full interval before noticing. _wait_or_wake also cuts
                    # the wait short when the tray's Save button fires.
                    # poll_interval is re-read each cycle so saved changes
                    # take effect on the next send.
                    cur_interval = max(5, int(tray_ui.load_config().get("poll_interval", POLL_INTERVAL)))
                    waited = 0
                    while waited < cur_interval and not _should_stop():
                        woke = await _wait_or_wake(TICK)
                        waited += TICK
                        if woke:
                            log("Wake — pushing payload now")
                            break
                        if not client.is_connected:
                            log("BLE link dropped — exiting poll loop to reconnect")
                            break
                    if _should_stop():
                        return

        except Exception as e:
            fail_count = 0 if probe_default else fail_count + 1
            log(f"BLE error: {e}")
            tray_ui.set_status("err", f"BLE error: {e}")

        if _should_stop():
            return
        log(f"Disconnected, reconnecting in {RECONNECT_DELAY}s...")
        tray_ui.set_status("warn", f"BLE — disconnected, reconnecting in {RECONNECT_DELAY}s")
        await asyncio.sleep(RECONNECT_DELAY)

def parse_args():
    p = argparse.ArgumentParser(description="Argus daemon")
    p.add_argument("--version", action="version",
                   version=f"Argus daemon {__version__}")
    p.add_argument("--serial", nargs="?", const="auto", metavar="PORT",
                   help="Override transport: use USB CDC serial. Pass nothing to "
                        "auto-detect the ESP32-S3 (VID 0x303A), or a port like "
                        "COM3 / /dev/ttyACM0 to force a specific one. If omitted, "
                        "the transport is read from the saved settings.")
    p.add_argument("--serial-baud", type=int, default=115200,
                   help="Serial baud rate (default 115200, must match firmware).")
    p.add_argument("--demo", action="store_true",
                   help="Send randomized fake data instead of polling Anthropic API.")
    p.add_argument("--headless", action="store_true",
                   help="Skip the tray icon / settings window. Useful for systemd "
                        "services or remote shells with no display.")
    return p.parse_args()


def run_worker(args, token, stop_event: threading.Event,
               wake_event: threading.Event | None = None,
               reload_event: threading.Event | None = None):
    """Run the transport loop. Honors stop_event so the tray's Quit menu can
    terminate us cleanly, and wake_event so Save can push immediately. Picks
    transport from CLI args first, then from the saved config.

    When reload_event fires (the tray saved a different transport), the
    active loop winds down, the config is re-read, and the newly selected
    transport starts — previously the setting was silently ignored until
    the daemon was restarted. (Issue #19, cause 1.)"""
    while True:
        cfg = tray_ui.load_config()
        transport = args.serial or (
            "auto" if cfg.get("transport") == "usb" else None
        )

        if transport:
            log(f"Transport: USB serial ({transport})")
            try:
                run_serial(transport, args.serial_baud, args.demo, token,
                           stop_event=stop_event, wake_event=wake_event,
                           reload_event=reload_event)
            except KeyboardInterrupt:
                return
        else:
            log("Transport: BLE")
            try:
                asyncio.run(run_ble(args.demo, token,
                                    stop_event=stop_event, wake_event=wake_event,
                                    reload_event=reload_event))
            except KeyboardInterrupt:
                return

        if (reload_event is not None and reload_event.is_set()
                and not stop_event.is_set()):
            reload_event.clear()
            log("Transport setting changed — switching now")
            continue
        return


if __name__ == "__main__":
    args = parse_args()
    log("=== Argus daemon ===")
    log(f"Config: {tray_ui.config_path()}")

    if args.demo:
        log("DEMO MODE — sending fake data")
        token = None
    else:
        token = read_token()
        log("Token loaded OK")

    stop_event = threading.Event()
    wake_event = threading.Event()
    reload_event = threading.Event()

    if args.headless:
        # Old behavior: worker on main thread, Ctrl-C to quit.
        try:
            run_worker(args, token, stop_event, wake_event)
        except KeyboardInterrupt:
            log("Stopped")
    else:
        # GUI mode: worker thread, tray on main thread (Qt insists on
        # main-thread event loops).
        last_transport = {"value": tray_ui.load_config().get("transport")}

        def _on_save(new_cfg):
            log(f"Settings saved: transport={new_cfg['transport']} "
                f"brightness={new_cfg['brightness']} "
                f"github={'set' if new_cfg['github_token'] else 'cleared'}")
            # Drop the GitHub/Copilot caches so the immediate push re-polls
            # every source — Save means "show me current data now", not the
            # values cached from the last tick.
            force_refresh()
            # A BLE<->USB switch can't be applied by the running loop; it used
            # to be silently ignored until the daemon was restarted (issue #19,
            # cause 1). Tell run_worker to wind down and re-read the config.
            # The --serial CLI flag still overrides the setting, as documented.
            if new_cfg.get("transport") != last_transport["value"]:
                last_transport["value"] = new_cfg.get("transport")
                if args.serial:
                    log("Transport changed in settings, but the --serial CLI "
                        "override is active — restart the daemon to apply it.")
                else:
                    reload_event.set()
            # Kick the worker out of its inter-poll sleep so the new settings +
            # freshly-pulled data ship to the device on the next tick instead of
            # waiting up to POLL_INTERVAL seconds.
            wake_event.set()

        worker = threading.Thread(
            target=run_worker, args=(args, token, stop_event, wake_event,
                                     reload_event),
            name="argus-worker", daemon=True
        )
        worker.start()

        had_tray = tray_ui.run_tray(on_save=_on_save, stop_event=stop_event)
        if not had_tray:
            log("Tray unavailable — falling back to headless mode.")
            try:
                worker.join()
            except KeyboardInterrupt:
                stop_event.set()
                log("Stopped")
