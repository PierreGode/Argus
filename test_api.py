import httpx, json, os
from pathlib import Path

cred = Path.home() / ".claude" / ".credentials.json"
token = json.loads(cred.read_text())["accessToken"]
print(f"Key: {token[:12]}...{token[-4:]}")

# Test both auth styles
for style, hdrs in [
    ("x-api-key", {"x-api-key": token}),
    ("Bearer+UA", {"Authorization": f"Bearer {token}", "User-Agent": "claude-code/2.1.5"}),
]:
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={**hdrs, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1,
              "messages": [{"role": "user", "content": "h"}]},
        timeout=30)
    print(f"\n=== {style} === Status: {r.status_code}")
    for k, v in r.headers.items():
        if "ratelimit" in k.lower():
            print(f"  {k}: {v}")
