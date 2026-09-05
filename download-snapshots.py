#!/usr/bin/env python3
"""Phase 2: Download project snapshots — rate-limit-safe GitHub access (v2).

Fixes over the POC:
- Optional GITHUB_TOKEN auth (60 -> 5,000 req/hr). This alone likely prevents the bans.
- Resolves each repo's default branch via the API (no more master/main guessing).
- Exponential backoff, honors Retry-After on 403/429, retries on 5xx.
- Persistent manifest (download_manifest.json): records what was fetched.
- Skips repos already in success_projects.json (fixes auto-pilot re-download bug).
- Zipballs fetched from codeload.github.com directly, spaced SLEEP_BETWEEN apart.
"""
import io
import json
import os
import time
import zipfile

import requests

GITHUB_API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
REPOS_DIR = "repos"
MANIFEST_FILE = "download_manifest.json"
SUCCESS_FILE = "success_projects.json"
FAILURES_FILE = "download_failures.json"

MAX_RETRIES = 5
SLEEP_BETWEEN = 1.5          # polite spacing between downloads
UA = "java-maven-build-pipeline/2.0 (academic research)"


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def make_session():
    s = requests.Session()
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
        print("🔑 GITHUB_TOKEN found — authenticated: 5,000 req/hr core limit.")
    else:
        print("⚠️  No GITHUB_TOKEN set! Unauthenticated limit is 60 req/hr per IP —")
        print("    bulk downloading WILL get throttled/banned. export GITHUB_TOKEN=...")
    s.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })
    return s


def github_request(session, url, stream=False, max_retries=MAX_RETRIES):
    """GET with rate-limit awareness: Retry-After first, then exp backoff w/ cap."""
    resp = None
    for attempt in range(max_retries):
        resp = session.get(url, stream=stream, timeout=60)
        if resp.status_code in (403, 429):
            retry_after = resp.headers.get("Retry-After")
            wait = int(retry_after) if retry_after else min(30 * (2 ** attempt), 900)
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            print(f"  ⏳ {resp.status_code} limited (remaining={remaining}); sleeping {wait}s")
            time.sleep(wait)
            continue
        if 500 <= resp.status_code < 600:
            wait = min(5 * (2 ** attempt), 60)
            print(f"  ⚠️  Server {resp.status_code}; retry in {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp  # exhausted retries — caller treats as failure


def resolve_repo_meta(session, full_name):
    """Returns (default_branch, meta_dict) or (None, error_reason)."""
    resp = github_request(session, f"{GITHUB_API}/repos/{full_name}")
    if resp is None:
        return None, "retries-exhausted"
    if resp.status_code == 404:
        return None, "repo-not-found"
    if resp.status_code != 200:
        return None, f"api-{resp.status_code}"
    meta = resp.json()
    return meta.get("default_branch", "master"), meta


def extract_zipball(content, target_path):
    tmp = target_path + ".tmp"
    os.makedirs(tmp, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            z.extractall(tmp)
        entries = os.listdir(tmp)
        if len(entries) != 1:
            raise RuntimeError(f"unexpected zip layout: {entries}")
        os.rename(os.path.join(tmp, entries[0]), target_path)
    finally:
        if os.path.exists(tmp):
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


def download_snapshots(json_file="projects.json"):
    projects = load_json(json_file, [])
    success_names = {p["name"] for p in load_json(SUCCESS_FILE, [])}
    manifest = load_json(MANIFEST_FILE, {})

    os.makedirs(REPOS_DIR, exist_ok=True)
    session = make_session()

    stats = {"exists": 0, "skipped_success": 0, "downloaded": 0, "failed": 0}
    failures = []

    for i, p in enumerate(projects, 1):
        full_name = p["name"]                      # e.g. "google/guava"
        repo_name = full_name.replace("/", "_")
        target_path = os.path.join(REPOS_DIR, repo_name)

        if os.path.exists(target_path):
            print(f"[{i}/{len(projects)}] ⏩ {full_name}: already on disk")
            stats["exists"] += 1
            continue

        if full_name in success_names:
            print(f"[{i}/{len(projects)}] 🏆 {full_name}: prior success — never re-download")
            stats["skipped_success"] += 1
            continue

        print(f"[{i}/{len(projects)}] 🌐 {full_name}: resolving default branch...", end=" ", flush=True)
        branch, meta = resolve_repo_meta(session, full_name)
        if branch is None:
            print(f"❌ {meta}")
            failures.append({"name": full_name, "reason": meta})
            stats["failed"] += 1
            continue
        print(f"branch={branch}")

        zip_url = f"{CODELOAD}/{full_name}/zip/refs/heads/{branch}"
        resp = github_request(session, zip_url)
        if resp is None or resp.status_code != 200:
            reason = f"zip-{resp.status_code if resp is not None else 'no-resp'}"
            print(f"  ❌ {reason}")
            failures.append({"name": full_name, "reason": reason, "branch": branch})
            stats["failed"] += 1
            continue

        try:
            extract_zipball(resp.content, target_path)
            manifest[full_name] = {
                "branch": branch,
                "stars": meta.get("stargazers_count"),
                "size_kb": meta.get("size"),
                "pushed_at": meta.get("pushed_at"),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            with open(MANIFEST_FILE, "w") as f:
                json.dump(manifest, f, indent=2)
            print(f"  ✅ extracted to {target_path}")
            stats["downloaded"] += 1
        except Exception as e:
            print(f"  ❌ {e}")
            failures.append({"name": full_name, "reason": str(e)})
            stats["failed"] += 1

        time.sleep(SLEEP_BETWEEN)

    with open(FAILURES_FILE, "w") as f:
        json.dump(failures, f, indent=2)

    print(f"\n🏁 downloaded={stats['downloaded']}  exists={stats['exists']}  "
          f"skipped(success)={stats['skipped_success']}  failed={stats['failed']}")


if __name__ == "__main__":
    download_snapshots()
