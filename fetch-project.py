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

MAX_SIZE_KB = 300_000          # skip monsters (> ~300MB) — they eat build timeouts

# --- Phase B: CSV-level sampling (repo-selection plan) -----------------------
# Evidence (50-repo march): 58% of candidates were doomed before download;
# the survivors were mid-star LIBRARIES while top-stars skew Gradle/docs.
MIN_STARS = 500                # below this lies toy country
MAX_STARS = 20_000             # above it live the famous gradle migrations
STAR_BANDS = (500, 1_000, 2_000, 5_000, 10_000, 20_000)   # last band incl. top
MIN_CODE_LINES = 10_000        # real projects that still fit the 20-min cap
MAX_CODE_LINES = 400_000
BLOCKED_TOPICS = {"android", "kotlin", "awesome", "documentation"}


def _csv_true(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def _topic_blocked(topics_field):
    """SEART topics are ';'-separated. Blocked on exact match or hyphen
    prefix (kotlin-coroutines, android-lib, ...) — plain substring would
    wrongly catch e.g. 'spring-boot' for 'awesome-...' style terms."""
    for t in (topics_field or '').split(';'):
        t = t.strip().lower()
        for blocked in BLOCKED_TOPICS:
            if t == blocked or t.startswith(blocked + '-'):
                return True
    return False


def _star_band(stars):
    """Index into STAR_BANDS for stars inside the sampling window
    [500, 20000] (top edge inclusive); None if outside."""
    if stars is None or stars < STAR_BANDS[0] or stars > STAR_BANDS[-1]:
        return None
    for i in range(len(STAR_BANDS) - 2):
        if stars < STAR_BANDS[i + 1]:
            return i
    return len(STAR_BANDS) - 2


def _code_lines_ok(raw):
    """(ok, parsed) for the 10k-400k band. Unknown/garbage/0 -> (True, None):
    fail-open — metadata gaps never exclude a candidate."""
    try:
        cl = int(raw or 0)
    except (TypeError, ValueError):
        return True, None
    if cl <= 0:
        return True, None
    return MIN_CODE_LINES <= cl <= MAX_CODE_LINES, cl


def select_stratified(repos, needed):
    """Even coverage across STAR_BANDS: per band, stars-desc; round-robin
    passes take one repo per band until `needed` is filled. Deterministic
    (no RNG) and bias-free across the 500-20k window. Returns (picked, counts)."""
    bands = [[] for _ in range(len(STAR_BANDS) - 1)]
    for r in repos:
        b = _star_band(r.get("stars"))
        if b is not None:
            bands[b].append(r)
    for band in bands:
        band.sort(key=lambda r: (-r.get("stars") or 0, r["name"]))
    picked = []
    while len(picked) < needed:
        took = False
        for band in bands:
            if band and len(picked) < needed:
                picked.append(band.pop(0))
                took = True
        if not took:
            break
    counts = [len(b) for b in bands]
    return picked, counts


def fetch_via_bulk(session, needed, skip_names=frozenset()):
    """Download the FULL filtered dataset in one request (official endpoint).
    Cached locally — later runs re-filter offline with zero API calls.
    Applies Phase B sampling: topic/name/codeLines/star-band filters, then
    stratified selection to exactly `needed` candidates."""
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
    skipped = {"fork": 0, "archived": 0, "size": 0, "topic": 0, "name": 0,
               "codeLines": 0, "star-band": 0, "known": 0}
    with gzip.open(cache_file, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["name"] in skip_names:
                skipped["known"] += 1
                continue
            if _csv_true(row.get("isFork")):
                skipped["fork"] += 1
                continue
            if _csv_true(row.get("isArchived")):
                skipped["archived"] += 1
                continue
            try:
                size_kb = int(row.get("size") or 0)
            except ValueError:
                size_kb = 0
            if size_kb > MAX_SIZE_KB:
                skipped["size"] += 1
                continue
            # --- Phase B filters: the doomed never reach download ---
            if _topic_blocked(row.get("topics")):
                skipped["topic"] += 1
                continue
            if "awesome" in row["name"].lower():   # topic-less awesome-lists
                skipped["name"] += 1
                continue
            ok, cl = _code_lines_ok(row.get("codeLines"))
            if not ok:
                skipped["codeLines"] += 1
                continue
            try:
                stars = int(row.get("stargazers") or 0)
            except ValueError:
                stars = 0
            if _star_band(stars) is None:
                skipped["star-band"] += 1
                continue
            repos.append({
                "name": row["name"],
                "url": f"https://github.com/{row['name']}",
                "default_branch": row.get("defaultBranch") or "master",
                "stars": stars,
                "code_lines": cl,
            })
    if any(skipped.values()):
        print(f"  🧹 filtered out: {skipped['fork']} forks, "
              f"{skipped['archived']} archived, {skipped['size']} oversize (>{MAX_SIZE_KB//1000}MB), "
              f"{skipped['topic']} blocked-topic, {skipped['name']} awesome-name, "
              f"{skipped['codeLines']} codeLines-band, {skipped['star-band']} outside-star-bands, "
              f"{skipped['known']} already-known")
    picked, band_counts = select_stratified(repos, needed)
    labels = [f"{STAR_BANDS[i]}-{STAR_BANDS[i+1]}" for i in range(len(STAR_BANDS) - 1)]
    dist = ", ".join(f"{l}: {n}" for l, n in zip(labels, band_counts))
    print(f"  🎲 stratified pick: {len(picked)}/{len(repos)} in-window candidates "
          f"(band sizes left: {dist})")
    return picked


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
            if item["name"] in skip_names:
                continue
            # Phase B heuristics client-side where the fields exist (fail-open)
            if _topic_blocked(item.get("topics")):
                continue
            stars = item.get("stargazers")
            if _star_band(stars) is None:
                continue
            ok, cl = _code_lines_ok(item.get("codeLines"))
            if not ok:
                continue
            repos.append({
                "name": item["name"],
                "url": f"https://github.com/{item['name']}",
                "default_branch": item.get("defaultBranch") or "master",
                "stars": stars,
                "code_lines": cl,
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
    repos = fetch_via_bulk(session, needed, skip_names)
    if repos is None:
        print(f"🎯 Falling back to strategy 2: paginated search")
        repos = fetch_via_search(session, needed, skip_names)

    combined_todo = success_list + repos
    with open("projects.json", "w") as f:
        json.dump(combined_todo, f, indent=4)
    print(f"📝 projects.json: {len(combined_todo)} total targets "
          f"({len(repos)} new; stratified star bands 500-20k)")


if __name__ == "__main__":
    fetch_until_full(200)
