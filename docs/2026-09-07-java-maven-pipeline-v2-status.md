---
title: Java-Maven Build Pipeline v2 — Current Status
tags: [project, java, maven, seart, docker, benchmark, status]
created: 2026-09-07
updated: 2026-09-07
---

# Java-Maven Build Pipeline v2 — Current Status

**Repo:** `github.com/raviqlahadi/java-maven-build-test` (branch `v2`, pushed 2026-09-07, 5 commits ahead of the Sep 5 state)
**Machine:** ragna (home PC, WSL2) · **Research context:** [[2026-09-07-third-party-vuln-benchmark-topic]]

## What the pipeline is
Automated large-scale evaluation of Java Maven projects: mine SEART-GHS → download snapshots → isolated Docker builds → classify failures → report. Feeds the benchmarking study (Eclipse Steady + VESTA/VulEUT etc. need building projects with known CVEs).

## Components (all on `v2`)
| Component | File | State |
|---|---|---|
| Fetcher | `fetch-project.py` | ✅ SEART-native: bulk CSV export (26.3MB gz cached), TLS fixed via LE AIA CA bundle, paginated fallback (size=100, sort=stargazers,desc) |
| Downloader | `download-snapshots.py` | ✅ codeload ZIPs, defaultBranch from SEART (0 GitHub API calls), 1.5s polite spacing, retries |
| Build orchestrator | `build-orchestrator.py` | ✅ docker-py, mem 2g / 2 CPU caps, 20-min hard kill, JDK auto-select from pom (8/11/17), failure taxonomy, signal-line extraction, `final_build_report.json` |
| Verifier | `verify-docker.py` | ✅ PASS — healthy fixture builds, ghost-dep fixture classified `dependency` |
| Anti-ban layer | (across all) | ✅ held in e2e: 1 SEART request + codeload = zero GitHub API calls |

## Verified end-to-end (2026-09-07, Q194)
10 top-starred Java repos: fetch (1 request, cached) → clone **10/10** (2.3G) → build → report.

**Result: 0/10 builds — and that is honest data.** Taxonomy:
- `no-pom` ×7 — Gradle migration wave (elasticsearch, ghidra, spring-boot-main, Stirling-PDF) + docs repos (cs-notes, advanced-java, hello-algo, leetcodeanimation)
- `compile` ×1 — java-design-patterns requires **JDK 21** (map only has 8/11/17)
- `timeout` ×1 — mall (mega-project, 20-min cap, cold dependency cache)
- `other` ×1 — spring-boot nested pom (plugin descriptor edge case)

## Findings → improvements backlog
1. **Top-stars sampling is maven-hostile** — Gradle migration wave + docs repos. SEART's `pomXmlPresent` reflects index-time state, not the release tag. Filter should re-check pom at the release tag.
2. **JDK map needs 21** (and probably 22+ soon) — add `maven:3.9-eclipse-temurin-21`.
3. **Mega-projects**: bigger timeout / memory cap, warm `maven_cache` (second mall run would go much further).
4. `auto_pilot.sh` calls `make iterate` — target doesn't exist in Makefile. Fix or remove.
5. Candidate sampling strategy per brief: SEART bulk dump offers 116,955 Java+pom repos — filter harder (pom present AND maven wrapper OR known-buildable heuristics) before wasting build cycles.

## Infrastructure notes
- Docker Desktop data disk relocated to `D:\DockerData\DockerDesktopWSL\` (C: freed). Fresh `docker_data.vhdx` recreated 2026-09-07 after crash-era corruption was diagnosed (maven:3.9.6 extracted broken across all re-pulls; fix = delete vhdx, let Docker recreate). See [[2026-09-05-docker-desktop-crash-c-full]].
- `maven_cache` warm for: verify fixtures, java-design-patterns, spring-boot deps, mall (partial).
- PDF→MD ingestion tool for research papers: `~/tools/pdf2md` (docling, resume-safe, HF cache off C:) — see quest Q193.1.

## Next steps (mapped to research milestones)
- [ ] Scale to **50+ building projects** (brief Milestone 1) — needs findings 1–3 addressed first
- [ ] Prepare Eclipse Steady + Project KB (brief steps 5–6)
- [ ] VESTA / VulEUT runnable (Milestone 2)
- [ ] Push `v2` → `main` decision (PR available at `.../pull/new/v2`)
- [ ] Q164 photo backlog verdict (unrelated, but the wolf keeps count)

## Related
- [[2026-09-05-java-maven-scraper-rebuild]] — the rebuild decision + SEART deep-dive
- [[2026-09-07-third-party-vuln-benchmark-topic]] — the friend's research brief
- [[2026-09-05-docker-desktop-crash-c-full]] — crash forensics
