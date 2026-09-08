#!/usr/bin/env python3
"""Offline sampling tests for fetch-project.py Phase B filters.

Run from repo root: ./venv/bin/python test-sampling.py
Exit 0 = all pass. No network needed (the bulk CSV check uses the cache).
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fp = _load("fetch_project", os.path.join(HERE, "fetch-project.py"))

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    if not ok:
        FAILS += 1
    print(f"{'✅' if ok else '❌'} {label}: got {got!r} (want {want!r})")


# --- topic blocklist -------------------------------------------------------
check("topic 'android' blocked", fp._topic_blocked("ant-task;android;java"), True)
check("topic hyphen-prefix 'kotlin-coroutines' blocked",
      fp._topic_blocked("jvm;kotlin-coroutines"), True)
check("topic 'awesome' blocked", fp._topic_blocked("awesome;java"), True)
check("topic 'documentation' blocked", fp._topic_blocked("docs;documentation"), True)
check("topic 'spring-boot' kept", fp._topic_blocked("java;spring-boot;web"), False)
check("topic empty kept", fp._topic_blocked(""), False)
check("topic None kept", fp._topic_blocked(None), False)
# 'awesome' substring inside an unrelated topic must NOT cull
check("topic 'handsome-thing' kept (no substring folly)",
      fp._topic_blocked("handsome-thing"), False)

# --- star bands ------------------------------------------------------------
CASES = [(499, None), (500, 0), (999, 0), (1000, 1), (1999, 1), (2000, 2),
         (4999, 2), (5000, 3), (9999, 3), (10000, 4), (19999, 4),
         (20000, 4), (20001, None), (0, None), (None, None)]
for stars, want in CASES:
    check(f"star band {stars}", fp._star_band(stars), want)

# --- codeLines band (fail-open on unknown) ---------------------------------
check("codeLines 9k culled", fp._code_lines_ok("9000"), (False, 9000))
check("codeLines 10k kept", fp._code_lines_ok("10000"), (True, 10000))
check("codeLines 400k kept", fp._code_lines_ok("400000"), (True, 400000))
check("codeLines 401k culled", fp._code_lines_ok("401000"), (False, 401000))
check("codeLines garbage fail-open", fp._code_lines_ok("many")[0], True)
check("codeLines empty fail-open", fp._code_lines_ok("")[0], True)
check("codeLines zero fail-open", fp._code_lines_ok("0")[0], True)
check("codeLines None fail-open", fp._code_lines_ok(None)[0], True)

# --- stratified selection --------------------------------------------------
def repo(name, stars):
    return {"name": name, "stars": stars, "url": "", "default_branch": "master",
            "code_lines": 50_000}

# 4 repos per band, 5 bands
synthetic = []
for band_lo, band_hi in zip(fp.STAR_BANDS, fp.STAR_BANDS[1:]):
    for k in range(4):
        stars = min(band_lo + k * max(1, (band_hi - band_lo) // 4), band_hi)
        synthetic.append(repo(f"band-{band_lo}-{k}", stars))

picked, counts = fp.select_stratified(list(synthetic), 10)
check("stratified: picked 10 of 20", len(picked), 10)
per_band = {}
for r in picked:
    per_band[fp._star_band(r["stars"])] = per_band.get(fp._star_band(r["stars"]), 0) + 1
check("stratified: even 2-per-band coverage", sorted(per_band.values()), [2, 2, 2, 2, 2])
check("stratified: all 5 bands represented", len(per_band), 5)

again, _ = fp.select_stratified(list(synthetic), 10)
check("stratified: deterministic", [r["name"] for r in again],
      [r["name"] for r in picked])

picked_all, _ = fp.select_stratified(list(synthetic), 100)
check("stratified: over-ask returns all", len(picked_all), 20)

# thin band doesn't stall selection: 1 repo in band 0, plenty elsewhere
thin = [repo("lonely", 600)] + [repo(f"mid-{i}", 3000 + i) for i in range(10)]
got, _ = fp.select_stratified(list(thin), 5)
check("stratified: thin band handled", len(got), 5)
check("stratified: thin band's repo included",
      any(r["name"] == "lonely" for r in got), True)

# --- bulk path against the REAL cached CSV (zero API) ----------------------
if os.path.exists(os.path.join(HERE, fp.BULK_CACHE_DIR, "java_pom_all.csv.gz")):
    class FakeSession:            # cache hit → session never used
        def get(self, *a, **kw):
            raise AssertionError("network call attempted on cache hit")

    picked = fp.fetch_via_bulk(FakeSession(), needed=60,
                               skip_names={"jeresig/processing-js"})
    check("bulk: returns exactly needed", len(picked), 60)
    names = {r["name"] for r in picked}
    check("bulk: skip_names respected", "jeresig/processing-js" in names, False)
    check("bulk: all inside star window",
          all(fp.MIN_STARS <= r["stars"] <= fp.MAX_STARS for r in picked), True)
    check("bulk: picked is band-interleaved (NOT old top-stars order)",
          [r["stars"] for r in picked] !=
          sorted([r["stars"] for r in picked], reverse=True), True)
    band_spread = {fp._star_band(r["stars"]) for r in picked}
    check("bulk: picked spans >=4 bands", len(band_spread) >= 4, True)
else:
    print("⏭️  (no cached CSV — bulk path not exercised)")

print(f"\n{'🎉 ALL PASS' if FAILS == 0 else f'💥 {FAILS} FAILURES'}")
sys.exit(1 if FAILS else 0)
