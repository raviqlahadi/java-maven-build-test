#!/usr/bin/env python3
"""Phase 2: Download project snapshots — rate-limit-safe GitHub access (v2).

Fixes over the POC:
- Optional GITHUB_TOKEN auth (60 -> 5,000 req/hr). This alone likely prevents the bans.
- Resolves each repo's default branch via the API (no more master/main guessing).
- Exponential backoff, honors Retry-After on 403/429, retries on 5xx.
- Persistent manifest (download_manifest.json): records what was fetched.
- Skips repos already in success_projects.json (fixes auto-pilot re-download bug).
- Zipballs fetched from codeload.github.com directly, spaced SLEEP_BETWEEN apart.

Phase A (repo-selection plan): pre-download gate. One CDN request per
candidate fetches the root pom from raw.githubusercontent.com — no API rate
limits, no token. Repos doomed before download are culled (pre-no-pom /
pre-jdk-unsupported / pre-snapshot) at ~zero cost. CDN hiccups fail OPEN:
an unknown candidate always proceeds to download.
"""
import io
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET

import requests

GITHUB_API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
REPOS_DIR = "repos"
MANIFEST_FILE = "download_manifest.json"
SUCCESS_FILE = "success_projects.json"
FAILURES_FILE = "download_failures.json"

MAX_RETRIES = 5
SLEEP_BETWEEN = 1.5          # polite spacing between downloads
SLEEP_CULLED = 0.2           # token gesture after a pre-download cull
RAW_TIMEOUT = 5              # CDN hiccup must never block a candidate
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


# --- Phase A: pre-download gate -------------------------------------------
# One raw-CDN pom check before pulling gigabytes. raw.githubusercontent is
# CDN-served: no GitHub API rate limits, no token, 5s timeout, fail-open.

_ORCH = None


def _orchestrator():
    """Load build-orchestrator.py (dash in filename -> importlib) so the gate
    and the build pre-flight share ONE java-version parser. No drift."""
    global _ORCH
    if _ORCH is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "build-orchestrator.py")
        spec = importlib.util.spec_from_file_location("build_orchestrator", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ORCH = mod
    return _ORCH


def fetch_root_pom(session, full_name, branch):
    """GET the root pom.xml via raw CDN. Returns (status, text) where status
    is 'ok' (200), 'missing' (404 -> no root pom) or 'error' (hiccups).
    Body returned as BYTES — ET.fromstring rejects str carrying an XML
    encoding declaration (nearly every pom has one). Catches broadly: the
    gate must never be the reason a download fails."""
    url = f"{RAW_BASE}/{full_name}/{branch}/pom.xml"
    try:
        resp = session.get(url, timeout=RAW_TIMEOUT)
        if resp.status_code == 200:
            return "ok", resp.content
        if resp.status_code == 404:
            return "missing", None
    except Exception:
        pass
    return "error", None


def _first_child_text(el, name):
    for c in el:
        if c.tag.split('}')[-1] == name:
            return (c.text or '').strip()
    return None


def pom_snapshot_risk(pom_text):
    """True if the root pom's parent version ends in -SNAPSHOT (the
    dbeaver/nacos kill class: unresolvable snapshot parents), or any
    dependency version ends in -SNAPSHOT with a groupId DIFFERENT from the
    project's own (external snapshots; same-group deps are reactor siblings
    that resolve from the source tree)."""
    try:
        root = ET.fromstring(pom_text)
    except ET.ParseError:
        return False
    own_group = None
    for el in root:
        tag = el.tag.split('}')[-1]
        if tag == 'groupId':
            own_group = (el.text or '').strip()
        elif tag == 'parent':
            ver = _first_child_text(el, 'version')
            if ver and ver.endswith('-SNAPSHOT'):
                return True
    for dep in root.iter():
        if dep.tag.split('}')[-1] != 'dependency':
            continue
        ver = _first_child_text(dep, 'version')
        if not (ver and ver.endswith('-SNAPSHOT')):
            continue
        gid = _first_child_text(dep, 'groupId')
        if gid and own_group and gid != own_group:
            return True
    return False


def pre_download_gate(session, full_name, branch):
    """Judge before downloading. Returns a cull reason ('pre-no-pom',
    'pre-jdk-unsupported', 'pre-snapshot') or None to proceed.
    Fails OPEN — an unknown candidate always reaches the build phase."""
    status, pom_text = fetch_root_pom(session, full_name, branch)
    if status == "missing":
        return "pre-no-pom"
    if status != "ok":
        return None                       # unknown -> let the build judge
    try:
        orch = _orchestrator()
        version = orch.parse_java_version_text(pom_text)
        major = orch._major(version) if version else None
        if major is not None and major > orch.MAX_MAPPED_JDK:
            return "pre-jdk-unsupported"
        if pom_snapshot_risk(pom_text):
            return "pre-snapshot"
    except Exception:
        return None                       # parser surprise -> proceed
    return None


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

    stats = {"exists": 0, "skipped_success": 0, "downloaded": 0, "failed": 0,
             "pre-no-pom": 0, "pre-jdk-unsupported": 0, "pre-snapshot": 0}
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

        # SEART gives us defaultBranch in projects.json — skip the GitHub API call
        branch = p.get("default_branch")
        meta = {}
        if branch:
            print(f"[{i}/{len(projects)}] 🌐 {full_name}: branch={branch} (from SEART)")
        else:
            print(f"[{i}/{len(projects)}] 🌐 {full_name}: resolving default branch...", end=" ", flush=True)
            branch, meta = resolve_repo_meta(session, full_name)
            if branch is None:
                print(f"❌ {meta}")
                failures.append({"name": full_name, "reason": meta})
                stats["failed"] += 1
                continue
            print(f"branch={branch}")

        # --- Phase A gate: judge before pulling the ZIP (one CDN request) ---
        gate_reason = pre_download_gate(session, full_name, branch)
        if gate_reason:
            print(f"  ✂️  {gate_reason} — culled before download")
            failures.append({"name": full_name, "reason": gate_reason, "branch": branch})
            stats[gate_reason] += 1
            time.sleep(SLEEP_CULLED)
            continue

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
                "stars": meta.get("stargazers") or p.get("stars"),
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
    pre_culled = stats['pre-no-pom'] + stats['pre-jdk-unsupported'] + stats['pre-snapshot']
    print(f"✂️  pre-download culled: {pre_culled} "
          f"(no-pom={stats['pre-no-pom']}, jdk-unsupported={stats['pre-jdk-unsupported']}, "
          f"snapshot={stats['pre-snapshot']})")


if __name__ == "__main__":
    download_snapshots()
