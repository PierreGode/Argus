#!/usr/bin/env python3
"""
Fetch personal GitHub Copilot premium request usage for Pierre-Gode.

Requires a PAT (classic or fine-grained) created from the Pierre-Gode
account, set as GITHUB_TOKEN.
  - Classic PAT: needs `manage_billing:copilot` scope
  - Fine-grained PAT: needs "Plan" user permission = Read

Returns 404 if Enhanced Billing isn't enabled on the account yet.
"""

import os
import sys
import requests
from collections import defaultdict

USERNAME = "Pierre-Gode"

# Set this to the plan's monthly premium request allowance:
#   Copilot Free  = 50
#   Copilot Pro   = 300
#   Copilot Pro+  = 1500
ALLOWANCE = 300


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("error: set GITHUB_TOKEN (PAT from the Pierre-Gode account)")

    url = f"https://api.github.com/users/{USERNAME}/settings/billing/premium_request/usage"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    resp = requests.get(url, headers=headers, timeout=30)

    if resp.status_code == 404:
        sys.exit("404 — Enhanced Billing not enabled on this account, "
                 "or token doesn't belong to Pierre-Gode.")
    if resp.status_code != 200:
        sys.exit(f"HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    period = data["timePeriod"]
    items = data.get("usageItems", [])
    premium = [i for i in items if i["sku"] == "Copilot Premium Request"]

    by_model = defaultdict(float)
    for i in premium:
        by_model[i["model"]] += i["grossQuantity"]

    gross_total = sum(i["grossQuantity"] for i in premium)
    net_total   = sum(i["netQuantity"]   for i in premium)
    net_cost    = sum(i["netAmount"]     for i in premium)
    pct         = (gross_total / ALLOWANCE) * 100 if ALLOWANCE else 0

    print(f"User:        {data.get('user', USERNAME)}")
    print(f"Period:      {period['year']}-{period['month']:02d}")
    print(f"Premium req: {gross_total:.1f} / {ALLOWANCE}  ({pct:.1f}%)")
    print(f"Overage:     {net_total:.1f} req  (${net_cost:.2f})")
    if by_model:
        print("\nBy model:")
        for model, qty in sorted(by_model.items(), key=lambda x: -x[1]):
            print(f"  {qty:>7.1f}  {model}")
    else:
        print("\n(no premium request usage this period)")


if __name__ == "__main__":
    main()