"""Fetch GitHub Actions (CI/CD) status for the desk display.

GitHub has no global "all my workflow runs" endpoint, so we look at the user's
most-recently-pushed repos — the ones whose CI you actually care about right
now — and read the latest run of each. The result is one headline (status +
repo/branch/workflow) plus failing/waiting counts for the device, and a per-repo
run list the daemon uses to detect transitions for auto-focus.

Token: a classic or fine-grained PAT with "Actions: read" (plus repo metadata
read for private repos). The same token used for issues/PRs works if it carries
Actions read.

Status codes we emit (short, for the BLE payload):
    ok    latest run succeeded
    fail  latest run failed / timed out
    run   queued or in progress
    wait  waiting on a deployment approval / environment protection rule
    none  no runs found
"""

from __future__ import annotations

import httpx

API = "https://api.github.com"


class CIError(RuntimeError):
    pass


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _classify(run: dict) -> str:
    """Map a workflow run's status/conclusion to one of our short codes."""
    status = (run.get("status") or "").lower()
    concl = (run.get("conclusion") or "").lower()
    if status == "waiting" or concl == "action_required":
        return "wait"
    if status in ("queued", "in_progress", "pending", "requested"):
        return "run"
    if concl in ("failure", "timed_out", "startup_failure"):
        return "fail"
    if concl == "success":
        return "ok"
    return "none"


def fetch(token: str, max_repos: int = 8) -> dict:
    """Return aggregated CI status for the user's most-recently-pushed repos."""
    if not token:
        raise CIError("no token")

    try:
        r = httpx.get(
            f"{API}/user/repos",
            headers=_headers(token),
            params={"sort": "pushed", "per_page": max_repos,
                    "affiliation": "owner,collaborator,organization_member"},
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise CIError(f"network: {e}") from e
    if r.status_code == 401:
        raise CIError("token rejected (401)")
    if r.status_code == 403:
        raise CIError(f"forbidden (403): {r.json().get('message', '')}")
    if r.status_code >= 400:
        raise CIError(f"http {r.status_code}: {r.text[:120]}")

    runs: list[dict] = []
    for repo in r.json():
        full = repo.get("full_name")
        if not full:
            continue
        try:
            rr = httpx.get(
                f"{API}/repos/{full}/actions/runs",
                headers=_headers(token),
                params={"per_page": 1},
                timeout=15,
            )
        except httpx.HTTPError:
            continue  # one flaky repo shouldn't sink the whole poll
        if rr.status_code != 200:
            continue
        wr = (rr.json().get("workflow_runs") or [])
        if not wr:
            continue
        run = wr[0]
        runs.append({
            "repo": full,
            "name": repo.get("name") or full.split("/")[-1],
            "run_id": run.get("id"),
            "branch": run.get("head_branch") or "",
            "workflow": run.get("name") or "",
            "updated_at": run.get("updated_at") or "",
            "code": _classify(run),
        })

    if not runs:
        return {"ok": True, "status": "none", "repo": "", "branch": "",
                "workflow": "", "failing": 0, "waiting": 0, "runs": []}

    failing = sum(1 for x in runs if x["code"] == "fail")
    waiting = sum(1 for x in runs if x["code"] == "wait")

    # Headline = the run most worth glancing at. Prefer surfacing a problem:
    # something waiting on approval first, then a failure, else the newest run.
    # (updated_at is ISO-8601, so lexical max == most recent.)
    waits = [x for x in runs if x["code"] == "wait"]
    fails = [x for x in runs if x["code"] == "fail"]
    if waits:
        headline = max(waits, key=lambda x: x["updated_at"])
    elif fails:
        headline = max(fails, key=lambda x: x["updated_at"])
    else:
        headline = max(runs, key=lambda x: x["updated_at"])

    return {
        "ok": True,
        "status": headline["code"],
        "repo": headline["name"],
        "branch": headline["branch"],
        "workflow": headline["workflow"],
        "failing": failing,
        "waiting": waiting,
        "runs": runs,
    }


if __name__ == "__main__":
    # CLI smoke test: GH_TOKEN=ghp_xxx python ci_stats.py
    import os, json, sys
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        print("set GH_TOKEN to test", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(fetch(tok), indent=2))
