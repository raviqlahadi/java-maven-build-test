#!/usr/bin/env python3
"""Phase 1: Mine candidate projects from the SEART-GHS database (v2).

Fixes over the POC:
- SSL verification ON (was verify=False).
- Real User-Agent; backoff on 429/5xx instead of crashing.
- Existing state files still respected (resume-safe).
"""
import json
import os
import time

import requests

SEART_API = "https://seart-ghs.si.usi.ch/api/r/search"
UA = "java-maven-build-pipeline/2.0 (academic research)"


def seart_get(params, max_retries=5):
    for attempt in range(max_retries):
        resp = requests.get(
            SEART_API,
            params=params,
            headers={"User-Agent": UA},
            timeout=30,
        )
        if resp.status_code in (429, 403):
            wait = min(30 * (2 ** attempt), 600)
            print(f"  ⏳ SEART {resp.status_code}; sleeping {wait}s (be gentle — academic service)")
            time.sleep(wait)
            continue
        if 500 <= resp.status_code < 600:
            wait = min(5 * (2 ** attempt), 60)
            print(f"  ⚠️  SEART {resp.status_code}; retry in {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp


def fetch_until_full(target_count=200):
    success_file = "success_projects.json"
    failed_file = "failed_projects.json"

    success_list = json.load(open(success_file)) if os.path.exists(success_file) else []
    failed_list = json.load(open(failed_file)) if os.path.exists(failed_file) else []

    skip_names = {p["name"] for p in success_list} | {p["name"] for p in failed_list}

    print(f"💎 Current Successes: {len(success_list)}")
    print(f"🚫 Known Failures to Skip: {len(failed_list)}")

    if len(success_list) >= target_count:
        print("✅ Success list already full!")
        return success_list

    params = {
        "language": "Java",
        "pomXmlPresent": "true",
        "sort": "stargazers",
        "direction": "DESC",
        "page": 0,
    }

    new_projects = []
    needed = target_count - len(success_list)

    while len(new_projects) < needed:
        print(f"Fetching page {params['page']} for {needed - len(new_projects)} more projects...")
        resp = seart_get(params)
        if resp is None or resp.status_code != 200:
            print(f"  ❌ SEART gave up (status={getattr(resp, 'status_code', 'no-resp')}); stopping.")
            break
        items = resp.json().get("items", [])
        if not items:
            break

        for item in items:
            if item["name"] not in skip_names:
                new_projects.append({
                    "name": item["name"],
                    "url": f"https://github.com/{item['name']}",
                })
                if len(new_projects) >= needed:
                    break

        params["page"] += 1
        time.sleep(1)  # be gentle with SEART — it's a free academic service

    combined_todo = success_list + new_projects
    with open("projects.json", "w") as f:
        json.dump(combined_todo, f, indent=4)

    print(f"📝 Created projects.json with {len(combined_todo)} total targets.")


if __name__ == "__main__":
    fetch_until_full(200)
