#!/usr/bin/env python3
"""Phase 1: Mine candidate projects from the SEART-GHS database (v3).

Findings from reading the seart-group/ghs source + live testing (2026-09-05):

1. TLS: the server serves an INCOMPLETE certificate chain (leaf only).
   The POC used verify=False to bypass it — insecure. We instead build a CA
   bundle from the Let's Encrypt YR1 intermediate (AIA: http://yr1.i.lencr.org/)
   and pass it as verify=<bundle>. Verified working (HTTP 200).
2. BULK EXPORT: GET /r/download/{csv,json,xml} streams ALL repos matching the
   filter criteria as a gzipped file — ONE request instead of hundreds of
   paginated searches. Cache it locally and sample offline forever after.
3. PAGINATION: backend is Spring Pageable; max page size is 100
   (spring.data.web.pageable.max-page-size=100). Correct syntax:
       ?page=N&size=100&sort=stargazers,desc
   The POC's `direction: DESC` param was silently IGNORED by Spring — meaning
   it actually fetched ASCENDING stars (least-popular first). Oops.
4. Items/rows include `defaultBranch` — the downstream downloader can use it
   and skip GitHub API metadata calls entirely.
5. The POC's ban came from here (SEART), not GitHub — so: fewest requests
   possible, realistic UA, generous backoff. It's a free academic service.
"""
import base64
import csv
import gzip
import io
import json
import os
import time

import requests

SEART = "https://seart-ghs.si.usi.ch/api/r"
UA = "java-maven-build-pipeline/3.0 (academic research; respectful scraping)"
CA_BUNDLE = "seart-ca-bundle.pem"
LE_YR1_AIA = "http://yr1.i.lencr.org/"          # LE YR1 intermediate (DER)
LE_ROOT_AIA = "http://yr.i.lencr.org/"          # ISRG Root YR (DER)
BULK_CACHE_DIR = "seart_cache"
SUCCESS_FILE = "success_projects.json"
FAILED_FILE = "failed_projects.json"


# ---------------------------------------------------------------- TLS fix ---

def _der_to_pem(der: bytes) -> str:
    b64 = base64.encodebytes(der).decode()
    return f"-----BEGIN CERTIFICATE-----\n{b64}-----END CERTIFICATE-----\n"


def ensure_ca_bundle(path=CA_BUNDLE):
    """Build a CA bundle anchoring SEART's incomplete chain.

    SEART sends only its leaf cert. The chain is:
        leaf -> Let's Encrypt YR1 (intermediate) -> ISRG Root YR (cross-signed
        by ISRG Root X1) -> ISRG Root X1 (self-signed, in certifi).
    NOTE: the AIA-published Root YR is cross-signed, NOT self-signed, and
    Python's OpenSSL requires chains to end at a self-signed trust anchor
    (no partial chains). So the bundle = intermediate + cross-root + the
    full certifi store (provides ISRG Root X1). Returns bundle path, or
    None if it cannot be built (caller then fails loudly — never silently
    disable verification).
    """
    if os.path.exists(path) and os.path.getsize(path) > 100:
        return path
    try:
        intermediate = requests.get(LE_YR1_AIA, timeout=30,
                                    headers={"User-Agent": UA}).content
        root = requests.get(LE_ROOT_AIA, timeout=30,
                            headers={"User-Agent": UA}).content
        import certifi
        with open(path, "wb") as f:
            f.write(_der_to_pem(intermediate).encode())
            f.write(_der_to_pem(root).encode())
            f.write(open(certifi.where(), "rb").read())
        print(f"🔐 Built CA bundle at {path} (LE YR1 + ISRG Root YR + certifi)")
        return path
    except Exception as e:
        print(f"❌ Could not build CA bundle: {e}")
        print("   SEART's chain is incomplete; refusing to continue insecurely.")
        return None


# ------------------------------------------------------------ HTTP plumbing ---

def make_session():
    bundle = ensure_ca_bundle()
    if bundle is None:
        raise SystemExit(1)
    s = requests.Session()
    s.verify = bundle
    s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip"})
    return s


def seart_get(session, url, **kwargs):
    """GET with backoff on 403/429/5xx. SEART banned the POC once — stay gentle."""
    resp = None
    for attempt in range(5):
        resp = session.get(url, timeout=kwargs.pop("timeout", 60), **kwargs)
        if resp.status_code in (403, 429):
            wait = int(resp.headers.get("Retry-After") or min(60 * (2 ** attempt), 1800))
            print(f"  ⏳ SEART {resp.status_code}; sleeping {wait}s")
            time.sleep(wait)
            continue
        if 500 <= resp.status_code < 600:
            wait = min(10 * (2 ** attempt), 120)
            print(f"  ⚠️  SEART {resp.status_code}; retry in {wait}s")
            time.sleep(wait)
            continue
        return resp
    return resp


# ------------------------------------------------------- strategy 1: bulk ---

def fetch_via_bulk(session):
    """Download the FULL filtered dataset in one request (official endpoint).
    Cached locally — later runs re-filter offline with zero API calls."""
    os.makedirs(BULK_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(BULK_CACHE_DIR, "java_pom_all.csv.gz")

    if os.path.exists(cache_file):
        print(f"📦 Using cached bulk dump: {cache_file}")
    else:
        print("🌐 Downloading FULL Java+pom dataset via /r/download/csv (one request)...")
        resp = seart_get(session, f"{SEART}/download/csv",
                         params={"language": "Java", "pomXmlPresent": "true"},
                         timeout=600)
        if resp is None or resp.status_code != 200:
            print(f"  ❌ bulk download failed ({getattr(resp, 'status_code', 'no-resp')})")
            return None
        with open(cache_file, "wb") as f:
            f.write(resp.content)
        print(f"  ✅ cached {len(resp.content) / 1e6:.1f} MB gz")

    repos = []
    with gzip.open(cache_file, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                stars = int(row.get("stargazers") or 0)
            except ValueError:
                stars = 0
            repos.append({
                "name": row["name"],
                "url": f"https://github.com/{row['name']}",
                "default_branch": row.get("defaultBranch") or "master",
                "stars": stars,
            })
    repos.sort(key=lambda r: r["stars"], reverse=True)
    return repos


# -------------------------------------------------- strategy 2: search pages ---

def fetch_via_search(session, needed, skip_names):
    """Paginated search fallback. size=100 (API max); correct Spring sort syntax."""
    repos = []
    page = 0
    while len(repos) < needed:
        print(f"🔍 search page {page} (size=100) for {needed - len(repos)} more...")
        resp = seart_get(session, f"{SEART}/search", params={
            "language": "Java",
            "pomXmlPresent": "true",
            "sort": "stargazers,desc",     # Spring syntax; direction param is a myth
            "page": page,
            "size": 100,                    # server caps at 100 (max-page-size)
        })
        if resp is None or resp.status_code != 200:
            print(f"  ❌ search failed ({getattr(resp, 'status_code', 'no-resp')})")
            break
        # NOTE: SEART serializes Spring Page with the items array under
        # "items" (NOT the standard "content") — verified live 2026-09-05
        items = resp.json().get("items", [])
        if not items:
            break
        for item in items:
            if item["name"] not in skip_names:
                repos.append({
                    "name": item["name"],
                    "url": f"https://github.com/{item['name']}",
                    "default_branch": item.get("defaultBranch") or "master",
                    "stars": item.get("stargazers"),
                })
                if len(repos) >= needed:
                    break
        page += 1
        time.sleep(2)                       # polite — it banned us once
    return repos


# ----------------------------------------------------------------- main ---

def fetch_until_full(target_count=200):
    success_list = json.load(open(SUCCESS_FILE)) if os.path.exists(SUCCESS_FILE) else []
    failed_list = json.load(open(FAILED_FILE)) if os.path.exists(FAILED_FILE) else []
    skip_names = {p["name"] for p in success_list} | {p["name"] for p in failed_list}

    print(f"💎 Current Successes: {len(success_list)} | 🚫 Known Failures: {len(failed_list)}")
    if len(success_list) >= target_count:
        print("✅ Success list already full!")
        return

    needed = target_count - len(success_list)
    session = make_session()

    print(f"🎯 Strategy 1: bulk export (one request, offline sampling afterwards)")
    repos = fetch_via_bulk(session)
    if repos is None:
        print(f"🎯 Falling back to strategy 2: paginated search")
        repos = fetch_via_search(session, needed, skip_names)
    else:
        repos = [r for r in repos if r["name"] not in skip_names]

    repos = repos[:needed]
    combined_todo = success_list + repos
    with open("projects.json", "w") as f:
        json.dump(combined_todo, f, indent=4)
    print(f"📝 projects.json: {len(combined_todo)} total targets "
          f"({len(repos)} new; top-stars first)")


if __name__ == "__main__":
    fetch_until_full(200)
