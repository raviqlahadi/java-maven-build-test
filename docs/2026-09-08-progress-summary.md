---
title: "Java-Maven Build Pipeline v2 — Progress Summary (Sep 5–8, 2026)"
tags: [project, java, maven, seart, docker, benchmark, progress]
created: 2026-09-08
updated: 2026-09-08
---

# Java-Maven Build Pipeline v2 — Progress Summary

**Repo:** `github.com/raviqlahadi/java-maven-build-test` (branch `v2`)
**Machines:** ragna (home PC, WSL2 + Docker Desktop) · research context: [[2026-09-05-third-party-vuln-testing-benchmarking]]
**Status as of 2026-09-08:** sampling/gating/download overhaul **complete** (Q198 ✅). Overnight march for research Milestone 1 is the next action.

---

## 1. What this project is

Automated large-scale evaluation of real-world Java Maven projects:

**mine (SEART-GHS) → sample → release-tag pick → pre-download gate → download → isolated Docker builds → classified failure ledger**

It feeds a benchmarking study of third-party vulnerability testing tools (Eclipse Steady, VESTA, VulEUT, …) which requires **50+ building Java projects** with known CVEs — research [[2026-09-05-third-party-vuln-testing-benchmarking]], Milestone 1.

## 2. The journey (evidence-driven, four days)

| Date | Event | Build-attempt success |
|---|---|---|
| Sep 5 | POC autopsy + SEART deep-dive. Root cause of API bans found (SEART edge, not GitHub). Anti-ban layer rebuilt: bulk CSV export + codeload ZIPs = **zero GitHub API calls** in happy path. | — |
| Sep 7 | v2 verified e2e. First honest run: 10 top-starred repos → **0/10** (Gradle wave + docs repos). | 0% |
| Sep 7 | Four levers: root-pom guard, JDK pre-flight + temurin-21 map, sampling filters, warm-cache retry. 50-repo march: 13/24 builds, 476 jars. | **54%** |
| Sep 8 | **Q198 complete** — three-phase repo-selection overhaul (below). | projected **~85%+** |

## 3. What was built (Q198, Sep 8)

### Phase A — Pre-download gate (`1873ea9`)
One 5s request per candidate to `raw.githubusercontent.com/{repo}/{ref}/pom.xml` (CDN — no API limits, no token) reads the root pom before any download:
- `pre-no-pom` — 404 kills the Gradle-migration class for free
- `pre-jdk-unsupported` — pom demands JDK > 21
- `pre-snapshot` — SNAPSHOT parent or external-group SNAPSHOT dep (the dbeaver/nacos kill class)

**Fails open**: CDN hiccups and parser surprises always proceed — the gate is an optimization, never a blocker. Shares ONE pom parser with the build pre-flight (`parse_java_version_text` in build-orchestrator.py) — zero drift.

### Phase B — CSV-level stratified sampling (`93a4c14`)
All offline on the cached 26MB SEART dump, zero API cost:
- **Topic blocklist**: `android` / `kotlin` / `awesome` / `documentation`, exact or hyphen-prefix match (`kotlin-coroutines` caught, `spring-boot` safe) — SEART topics are `;`-separated
- **`awesome`-in-name** fallback for topic-less awesome-lists
- **codeLines band 10k–400k** — real projects that fit the 20-min cap; unknown values fail open
- **Stratified star bands 500★–20k★** — five buckets, per-band stars-desc, deterministic round-robin to exactly N. Replaces top-stars bias (the giants are famous *because* they migrated to Gradle).

**Yield:** 116,944 indexed repos → **2,774 in-window candidates** (bands: 1191 / 730 / 551 / 185 / 57). Known success/failed repos are filtered *before* banding so they never consume band slots.

### Phase C — Release-tag downloads (`3d2b3ec`)
The research brief's own advice: *"check out the latest release — more likely to build."*
- One authed `GET /repos/{name}/releases/latest` per candidate (5,000 req/hr with `GITHUB_TOKEN`) → `codeload` tag ZIP
- Without a token the lookup is skipped entirely — the 60/hr anonymous budget stays reserved for branch resolution
- Missing tag ZIP / no releases / API trouble → silent fallback to default branch
- Manifest records `ref` + `ref_kind` (`release-tag` | `branch`) for research provenance

**Live-verified:** `alibaba/druid` → release `1.2.28`, tag pom gate-clean, tag ZIP 200 (21.4 MB).

## 4. Verification armor

| Suite | Cases | Coverage |
|---|---|---|
| `test-gate.py` | 13 | JDK parsing (properties, compiler-plugin, 1.8 legacy, the old substring trap, XML-encoding-decl bytes issue), SNAPSHOT rules (reactor-sibling exemption), fail-open paths |
| `test-sampling.py` | 44 | topic/star-band/codeLines filters, stratified selection (even coverage, determinism, thin bands), real-CSV dry run on cache hit |
| `test-release-tag.py` | 12 | mocked sessions: no-token = zero HTTP, 404/5xx fail-open, tag→branch fallback paths |
| `verify-docker.py` | 2 fixtures | build-engine regression rite (PASSED after the parser refactor) |

All suites run offline, exit nonzero on failure, and are committed to the repo.

## 5. Honest limitations

- The gate sees only the **root pom** — module-level snapshot issues (e.g. nacos) pass through and become honest build-phase data (by design).
- `releases/latest` returns the latest *published* release; repos releasing from old branches get that branch's code.
- Phase B's window (500★–20k★, 10k–400k lines) intentionally excludes >20k★ giants — prior marches already sampled them; the window targets the buildable mid-star depth.
- The codeLines filter is the harshest cut (71k of 117k culled) — a deliberate trade for 20-min-cap compatibility.

## 6. Next: the overnight march (Milestone 1)

Math: ~85% build-attempt success × ~65–100 sampled repos ⇒ **50+ builders in one run**.

Pre-flight checklist (ragna):
- [ ] **D: at 90% (25G free)** — downloads + vhdx growth need 10–15G; free more if possible
- [ ] Close VS Code + opencode overnight (frees ~3G RAM for the Docker VM)
- [ ] `GITHUB_TOKEN` exported ✅ (verified 5,000/hr)
- [ ] `auto_pilot.sh` calls `make iterate` — target missing from Makefile: fix (~5 lines) or chain the three scripts manually
- [ ] maven_cache is a **host bind mount** `./maven_cache` (3.2G warm — NOT a docker volume)

After Milestone 1: Eclipse Steady + Project KB (brief steps 5–6) → VESTA/VulEUT runnable (Milestone 2).

## 7. Related

- [[2026-09-07-java-maven-repo-selection-plan]] — the plan this work executed (all phases marked done)
- [[2026-09-07-java-maven-pipeline-50repo-march]] — the 54% evidence run
- [[2026-09-07-java-maven-pipeline-v2-status]] — v2 architecture state
- [[2026-09-05-java-maven-scraper-rebuild]] — POC autopsy + SEART deep-dive
- [[2026-09-05-third-party-vuln-testing-benchmarking]] — the research brief this serves
- [[2026-09-05-docker-desktop-crash-c-full]] — crash forensics (Docker data since relocated to D:)
