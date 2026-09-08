---
title: Java-Maven Scraper Pipeline — Rebuild Plan & Open Questions
tags: [projects, scraping, java, maven, research, pipeline]
created: 2026-09-05
updated: 2026-09-05
---

# Java-Maven Scraper Pipeline — Rebuild Plan & Open Questions

**Repo:** `~/projects/java-maven-build-test` (cloned from github.com/raviqlahadi/java-maven-build-test)
**Quest:** Q191 · **Machine:** ragna (home PC) · **Goal:** revisit POC, fix API-ban problem, align with friend's research docs, portfolio-grade the result.

## What the POC does today

Pipeline: **mine → download → build → count**

1. `fetch-project.py` — queries SEART-GHS API (`seart-ghs.si.usi.ch`) for Java repos with `pom.xml`, sorted by stars, fills up to 200 targets.
2. `download-snapshots.py` — downloads GitHub zipballs (`/zipball/master`), extracts to `repos/`.
3. `build-orchestrator.py` — Docker-isolated `mvn clean package -DskipTests`, naive JDK selection (8/11/17), collects JARs to `artifacts/`.
4. `auto_pilot.sh` — loops fetch→clone→build until 160 successes, wipes `repos/` each cycle.

**Last run results:** 39% success rate, ~4–6h for 200 projects, ~15 GB. **Killed by API bans.**

## POC review — what's broken

### Ban sources (the critical layer)
- **Unauthenticated GitHub zipball downloads** — 60 req/hr per IP; 200+ downloads = guaranteed ban. No token support anywhere.
- `master` hardcoded; `main` fallback is a TODO comment, never implemented → all `main`-default repos fail.
- SEART fetch: `verify=False` (SSL disabled), no retry, no backoff, no `Retry-After` handling.
- **`auto_pilot.sh` re-downloads successful repos every cycle** (wipes `repos/`, fetch rebuilds `projects.json` from success list) — wasted bandwidth + accelerated bans.
- Default `python-requests` User-Agent, no rotation.
- No local cache — every crash/restart re-hits the APIs.

### Correctness bugs
- `get_jdk_image()`: substring matching (`'11' in content`) — any `11` anywhere in pom.xml (e.g. version `11.0.2` of a dep) selects JDK 11. Also `1.8` check runs first so mixed poms misfire.
- `find_pom_directory()` returns the *first* pom.xml from `os.walk` — multi-module projects may pick a submodule pom instead of root.
- No build timeout: `container.wait()` hangs forever on a stuck Maven download.
- No container memory/CPU limits; builds are sequential (no parallelism).
- `requirement.txt` missing `docker` package; GitPython listed but never used.
- Sample selection = top-starred only → **biased dataset** (research validity issue).

### What's actually good (keep)
- Docker isolation + UID/GID mapping (no root-owned files).
- Shared maven cache volume (`-Dmaven.repo.local=/cache`).
- Resilience flags (`-DskipTests -Dcheckstyle.skip -Drat.skip`).
- Resume-state pattern via `success_projects.json` / `failed_projects.json`.
- ZIP snapshots instead of full clones (disk-smart).

## Questions to answer (before writing code — bring friend's research docs)

1. **What is the actual research question?** Build success rates across JDK versions? Dependency-resolution failures? Error taxonomy? This decides everything downstream.
2. **Sampling strategy** — is top-stars acceptable for the research, or do they need random/stratified sampling (by stars, size, activity)? Stars-only = popularity bias.
3. **Sample size** — 200? 500? 1000? Determines rate-limit budget and runtime.
4. **Definition of "success"** — compile only? package? with tests? Which of our resilience flags are legitimate for the study vs. hiding what's measured?
5. **Data source constraints** — must it be SEART-GHS, or are GitHub search API / Software Heritage / Maven Central acceptable alternatives?
6. **Snapshot vs real clone** — does the research need git history/tags, or is HEAD-snapshot enough? (SEART items carry branch metadata we currently ignore.)
7. **JDK/Maven matrix** — which environments must be covered? (POC only does 8/11/17, one Maven version.)
8. **Budget for scraping** — does the friend have GitHub accounts/PATs available? How many? Any proxy budget?
9. **Metrics & output format** — success rate, build duration, error categories? Paper tables, charts, raw JSON/SQLite?
10. **Reproducibility requirements** — deterministic sampling seed, pinned tool versions, environment documentation?
11. **Deadline** — when does the college project need to deliver?
12. **Attribution/ethics** — SEART terms of use, GitHub ToS, academic fair-use framing. (SEART is an academic service — be gentle, cite it.)

## Improvement plan

## SEART-GHS API specifics (verified 2026-09-05 — source + live tests)

**The ban came from SEART, not GitHub** (user correction). Findings from reading `seart-group/ghs` source and live testing:

1. **TLS quirk** — server serves leaf cert only (chain of 1, Let's Encrypt YR1 signs it). Chain: leaf → YR1 → ISRG Root YR (cross-signed by X1) → ISRG Root X1. Python's OpenSSL needs a self-signed anchor, so bundle = YR1 intermediate + cross-signed Root YR + certifi store. Both certs fetched from LE AIA URLs (`yr1.i.lencr.org`, `yr.i.lencr.org`). `verify=False` removed — never needed.
2. **Official bulk export** — `GET /api/r/download/{csv,json,xml}` streams ALL repos matching filters as gzip (`results.csv.gz`). ONE request. Verified: 17 repos ≥50k★ returned in a single call.
3. **Pagination** — Spring Pageable; **max page size = 100** (`spring.data.web.pageable.max-page-size=100`). Sort syntax: `sort=stargazers,desc`. **The POC's `direction: DESC` param was silently ignored → it was fetching ASCENDING stars.** HA.
4. **Response shape** — items array lives under `"items"` key (SEART custom Page serialization, not Spring's standard `content`).
5. **Item/row fields** — include `defaultBranch`, `stargazers`, `commits`, `contributors`, `codeLines`, `createdAt`, `pushedAt`, etc. `defaultBranch` lets the downloader skip GitHub API metadata calls entirely.
6. **Dataset size** — 116,955 Java+pom repos total (live count).
7. **Sanctioned full-dataset path** — README offers complete SQL dumps (5 iterations) on Dropbox: can import locally and query offline with zero rate limits — ideal for research reproducibility.
8. **No in-app rate limiting** in their code (no bucket4j; nginx is static-only; SecurityConfig permits all) — bans come from their hosting edge. Still: fewest requests possible, realistic UA, generous backoff. It's a free academic service — cite it (CITATION.bib in repo).

### A. Anti-ban scraping layer (top priority) — FIRST PASS DONE 2026-09-05 (v2 branch, commits 3767f20 + 731cba4)
- [x] **GitHub PAT auth** support in downloader — now OPTIONAL: SEART's `defaultBranch` + codeload zipballs = zero GitHub API calls in happy path
- [x] **SEART bulk export first, search pagination fallback** — 1 request for full dataset (cached) or `size=100` pages with correct sort syntax
- [x] **TLS fix via CA bundle** — no more `verify=False`
- [ ] Token pool rotation (only needed if we abandon SEART branch + codeload path)
- [x] Respect `Retry-After` + exponential backoff with jitter on 403/429 (jitter still to add)
- [x] Persistent download cache (`download_manifest.json` w/ metadata: branch, stars, size, pushed_at)
- [x] Fix `auto_pilot` re-download bug (downloader skips `success_projects.json` entries)
- [x] Correct default-branch handling: resolves via GitHub API per repo
- [ ] Adaptive rate limiter (token bucket) — currently fixed 1.5s spacing
- [x] Realistic User-Agent (single UA, rotation optional later)
- [ ] Fallback download strategies: zipball → `git clone --depth 1` → Software Heritage

### B. Pipeline robustness — FIRST PASS DONE 2026-09-05 (same commit)
- [x] Proper JDK detection: XML parse of `maven.compiler.release/target/source`, `java.version` (tested on 5 synthetic poms incl. substring-bug trap)
- [x] Root-pom detection: shallowest pom.xml wins (tested multi-module case)
- [x] Per-build timeout (20 min hard kill → marked `TIMEOUT`)
- [x] Container resource limits (2GB mem / 2 cores)
- [ ] Bounded parallel builds (2–3 concurrent workers)
- [ ] One retry for transient failures (network) before marking failed
- [x] Error taxonomy classifier: timeout / network / no-pom / dependency / compile / other (light version; full pending research docs)
- [ ] Config file (TOML/YAML) instead of hardcoded constants
- [ ] Structured results in SQLite (queryable) + JSONL export
- [x] Fixed `requirements.txt` (added docker, dropped unused GitPython)
- [ ] Real logging (module-level, file + console)

### C. Research quality / portfolio polish
- [ ] Stratified sampling option (stars/size/activity buckets).
- [ ] Full metadata capture per project (stars, size, pushed_at, default branch, JDK detected).
- [ ] Deterministic seed for reproducibility.
- [ ] Methodology writeup in README + architecture diagram.
- [ ] CI badge + example report artifacts in repo.

### Tooling note
- `~/projects/obscura` (stealth headless browser, Rust) is **overkill for this** — the bans are plain API rate-limiting, solved by PATs + caching + backoff, not browser stealth. Keep obscura in reserve only if we pivot to HTML scraping of a site without an API.

## Next steps
- [ ] Get friend's research docs → answer Questions 1–12 above
- [ ] Decide: patch POC vs restructure as proper package (lean: evolve, keep build engine core)
- [x] Implement anti-ban layer (section A) — DONE, live-tested (see SEART section above)
- [ ] Live-verify build engine: `./venv/bin/python verify-docker.py` — **blocked by C:-full crash**, see [[2026-09-05-docker-desktop-crash-c-full]] for forensics + fix steps
- [ ] Then pipeline robustness (B), then research polish (C)

## Discovered during
- Q191 — Revisit Java-Maven scraper pipeline (session 2026-09-05, ragna)
