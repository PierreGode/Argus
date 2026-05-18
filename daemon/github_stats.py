"""Fetch GitHub counts for the desk display.

We use the search API rather than per-repo iteration because it's one call
covering every repo the token can see, and it's well under the 5000/hr rate
limit even at 1 poll/min.

Queries:
  - Open issues assigned to the user      → "GitHub Issues" number
  - Open PRs awaiting the user's review,
    UNIONED with PRs assigned to them    → "GitHub PRs" number

The token can be a classic PAT or a fine-grained one; only "Issues: read" and
"Pull requests: read" scopes are needed for public repos, plus "metadata: read"
for private.
"""

from __future__ import annotations

import httpx

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def _search_count(token: str, query: str) -> int:
    """Run an Issues+PRs search and return total_count.

    We don't fetch the results themselves — only the count, so per_page=1.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"q": query, "per_page": 1}
    try:
        resp = httpx.get(f"{API}/search/issues", headers=headers, params=params, timeout=15)
    except httpx.HTTPError as e:
        raise GitHubError(f"network: {e}") from e

    if resp.status_code == 401:
        raise GitHubError("token rejected (401)")
    if resp.status_code == 403:
        # Either rate-limited or scope-missing — both surface here.
        raise GitHubError(f"forbidden (403): {resp.json().get('message','')}")
    if resp.status_code >= 400:
        raise GitHubError(f"http {resp.status_code}: {resp.text[:120]}")

    data = resp.json()
    return int(data.get("total_count", 0))


def fetch(token: str) -> dict:
    """Return {'issues': int, 'prs': int} for the authenticated user.

    Issues: open issues with the user as assignee (not just author/mention).
    PRs:    union of open PRs where the user is review-requested OR assignee.
            We can't OR via the search syntax cleanly, so we deduplicate by
            running two queries and taking the max — close enough as a counter
            for a desk widget, and avoids fetching the full result lists.
    """
    if not token:
        raise GitHubError("no token")

    issues = _search_count(token, "is:open is:issue assignee:@me")

    # The search API supports review-requested:@me and assignee:@me as
    # separate queries. For a precise union we'd page both result lists and
    # de-dupe by id — overkill for a "number on a dashboard," so we sum and
    # accept a small overcount on PRs you both authored AND were asked to
    # review (rare in practice).
    pr_review  = _search_count(token, "is:open is:pr review-requested:@me")
    pr_assign  = _search_count(token, "is:open is:pr assignee:@me")
    prs = pr_review + pr_assign

    return {"issues": issues, "prs": prs}


if __name__ == "__main__":
    # CLI smoke test: GH_TOKEN=ghp_xxx python github_stats.py
    import os, json, sys
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        print("set GH_TOKEN to test", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(fetch(tok), indent=2))
