---
title: Repo-Selection Improvement Plan — Raising Build Success Rate
tags: [project, java, maven, seart, sampling, plan]
created: 2026-09-07
updated: 2026-09-07
---

# Repo-Selection Improvement Plan

**Goal:** raise build-attempt success from **54%** toward **85%+**, so that **~65–100 sampled repos ⇒ 50+ builders** (research Milestone 1) in a single run.
**Evidence base:** [[2026-09-07-java-maven-pipeline-50repo-march]] — 53 judged: 13 built, 11 failed, 31 culled (58% of candidates were doomed *and* already downloaded).
**Status 2026-09-08 (ragna):** Phase A ✅ done (`1873ea9`). Phase B ✅ done (`93a4c14`). Phase C pending. ragna pre-flight checks before the overnight march: D: only 25G free (90% full — WSL vhdx grows on D:); maven_cache is a HOST BIND MOUNT (`./maven_cache`, 3.2G warm) not a docker volume.

## Core insight
Almost every failure was knowable from two tiny files we never read before pulling gigabytes: the repo's root `pom.xml` (via CDN) and the SEART CSV metadata we already cache.

## Phase A — Pre-Download Gate (do first; ~30 lines, zero API cost) — ✅ DONE 2026-09-08 (v2 commit `1873ea9`)
Implemented as planned + regression suite `test-gate.py` (13 offline cases). `verify-docker.py` rite PASS after the parser refactor. Live CDN smoke: zxing/guava proceed · ghidra → `pre-no-pom` · dbeaver → `pre-snapshot` (kill class culled for free). Known limit (by design): module-level snapshot issues (nacos) are invisible to a root-pom gate — they proceed and become honest build-phase data. Gate shares ONE pom parser with the build pre-flight (`parse_java_version_text` in build-orchestrator.py) — no drift.
Before downloading any ZIP, one small request per candidate:

```
GET https://raw.githubusercontent.com/{name}/{branch}/pom.xml
```

`raw` is CDN-served — no GitHub API rate limits, no token needed. Then decide:
| Response / pom content | Action | Category |
|---|---|---|
| 404 (no root pom) | skip, no download | `no-pom` |
| pom demands JDK > 21 | skip, no download | `jdk-unsupported` |
| parent/dep versions end in `-SNAPSHOT` | skip (or flag) | `snapshot-risk` |
| clean | proceed to download | — |

**Fixes:** the 58% blind-download waste; the dbeaver/nacos SNAPSHOT-parent class; jdk-unsupported moves pre-download.
**Where:** `download-snapshots.py` (new gate function before ZIP fetch); categories recorded same as build-phase ledger.

## Phase B — Sharper sampling from CSV fields (already cached, offline) — ✅ DONE 2026-09-08 (v2 commit `93a4c14`)
- **Topics/languages exclusion:** skip topics matching `android`, `kotlin`, `awesome`, `documentation` — the Gradle wave's calling card (glide, conductor, androidutilcode, cs-notes, all awesome-lists). ✅ Implemented: exact or hyphen-prefix match on `;`-separated topics (kotlin-coroutines caught, spring-boot safe) + `awesome`-in-name fallback for topic-less repos.
- **`codeLines` band ~10k–400k:** real projects that still fit the 20-min cap (dodges spring-boot-demo-class giants). ✅ Implemented, fail-open on unknown values. Note: this is the single harshest filter — 71k of 117k repos culled here.
- **Stratified star bands (500★–20k★) instead of pure top-stars:** the famous giants are famous *because* they're Gradle-ecosystem migrations. The winners were mid-star libraries: druid, easyexcel, zxing, wxjava, dubbo. ✅ Implemented: five bands (500/1k/2k/5k/10k edges, 20k top-inclusive), per-band stars-desc, deterministic round-robin to exactly N. skip_names filtered BEFORE banding.
- Real-CSV dry run: **2,774 in-window candidates** from 116,944 (bands: 1191/730/551/185/57). Suites: `test-gate.py` (13) + `test-sampling.py` (44), all offline.

## Phase C — Release-tag downloads (the research brief's own advice) — ✅ DONE 2026-09-08 (v2 commit `3d2b3ec`)
Brief: *"check out the latest release — more likely to build."* Current pipeline grabs the default branch.
- With `GITHUB_TOKEN` (5,000 req/hr authed): `GET /repos/{name}/releases/latest` → download `codeload.github.com/{name}/zip/refs/tags/{tag}`. ✅ Implemented — one authed call per candidate; WITHOUT a token the lookup is skipped entirely (60/hr anonymous budget reserved for branch resolution); 404 (no releases) and API trouble degrade silently to branch.
- Tagged code is stable, post-CI code — mid-refactor breakage vanishes. Fallback to branch when no release exists. ✅ `fetch_zipball` with tag→branch fallback; the GATE reads the ref we actually build (tag pom when a release exists).
- Anti-ban note: 1 authed call/candidate is far inside limits; keep the existing SEART-native path untouched. ✅
- Manifest records `ref` + `ref_kind` (release-tag | branch) for research provenance. Live smoke: alibaba/druid → release 1.2.28, tag pom gate-clean, tag ZIP 200. Suite: `test-release-tag.py` (12 mocked checks).

## PROJECT COMPLETE — 2026-09-08
All three phases shipped. Pipeline v2 full chain: **stratified sampling (B) → release-tag pick (C) → pre-download gate (A) → download → isolated build**. Projected build-attempt success ~85%+, download waste <5%. NEXT: the overnight march (~65–100 samples ⇒ 50+ builders ⇒ research Milestone 1) — pre-flight: D: 25G free, close VS Code/opencode, token exported on ragna.

## Projected impact
| Stage                        | Build-attempt success | Download waste |
| ---------------------------- | --------------------- | -------------- |
| No levers (Sep 6)            | 0%                    | 100%           |
| Post-download guards (Sep 7) | 54%                   | ~58%           |
| + Phase A                    | ~75–80%               | <10%           |
| + Phases B & C               | **~85%+**             | <5%            |

At 80%+: ~65 samples ⇒ 50 builders ⇒ **Milestone 1 in one evening**.

## Implementation notes
- Phase A gate runs in `download-snapshots.py` before `resolve_repo_meta`/ZIP fetch; keep raw-check failures in `download_failures.json` with distinct reasons (`pre-no-pom`, `pre-jdk-unsupported`, `pre-snapshot`).
- Raw-check timeout must be short (5s) and failures treated as "unknown → proceed" (never block a candidate on CDN hiccups).
- Phase C needs `GITHUB_TOKEN` exported (ragna currently has none — token optional, feature degrades gracefully to branch downloads).
- Re-run `verify-docker.py` after orchestrator-adjacent changes (regression rite).

## Related
- [[2026-09-07-java-maven-pipeline-50repo-march]] — the evidence run
- [[2026-09-07-java-maven-pipeline-v2-status]] — pipeline state
- [[2026-09-07-third-party-vuln-benchmark-topic]] — the research brief this serves
