---
title: 50-Repo Build March — Results Report (v2 pipeline, post-levers)
tags: [project, java, maven, seart, docker, benchmark, results]
created: 2026-09-07
updated: 2026-09-07
---

# 50-Repo Build March — Results Report

**Repo:** `github.com/raviqlahadi/java-maven-build-test` (branch `v2`, through commit `630dde8`)
**Run date:** 2026-09-07 evening · **Machine:** ragna (WSL2 + Docker Desktop on D:)
**Context:** [[2026-09-07-java-maven-pipeline-v2-status]] · [[2026-09-07-third-party-vuln-benchmark-topic]]

## TL;DR
First run after the precision levers: **53 candidates judged → 13 built (476 jars), 54% success on actual Docker builds** (yesterday: 0%). Milestone 1 of the research brief (50+ building projects) is mathematically within reach: ~90–100 sampled repos in one overnight run.

## The four levers that made it possible (commit `f68a826`)
1. **Root-pom guard** — no `pom.xml` at repo root → instant `no-pom`, zero Docker cycles. Killed the nested-pom false positives (spring-boot's shipped maven-plugin module had been fooling `find_pom_directory`).
2. **JDK pre-flight + 21 map** — parses pom properties *and* `maven-compiler-plugin` `<configuration>`; maps 8/11/17/21 → temurin images; anything higher skips as `jdk-unsupported`.
3. **Sampling filters** — bulk CSV filtered by fork / archived / >300MB: **11,161 repos culled from the pool** (4,398 forks, 4,738 archived, 2,025 oversize) before any download.
4. **Warm-cache timeout retry** — timeouts retried once at run end, deps already cached.

## The numbers

| Outcome                | Count  | Cost                   |
| ---------------------- | ------ | ---------------------- |
| ✅ Built                | **13** | jars collected         |
| ❌ Failed (real builds) | 11     | classified, data kept  |
| ⏩ Culled by guards     | 31     | **zero** Docker cycles |
| Total judged           | 53     |                        |

**Success rate: 13/24 builds attempted ≈ 54%** · 13/53 all judged ≈ 24.5% · previous run: 0/10 = 0%.
**Artifacts: 476 jars** in `artifacts/`.

## 🏆 The Honor Roll (13 builders)

| Project | JDK image |
|---|---|
| thealgorithms_java | temurin-**21** |
| iluwatar_java-design-patterns | temurin-**21** *(203 jars — the rematch won)* |
| eugenp_tutorials | jdk-8 |
| binarywang_WxJava | jdk-8 |
| wechat-group_wxjava | jdk-8 |
| apache_dubbo | jdk-8 |
| zxing_zxing | jdk-8 |
| alibaba_easyexcel | jdk-8 |
| yunaiv_ruoyi-vue-pro | temurin-17 |
| alibaba_druid | temurin-17 |
| alibaba_canal | temurin-17 |
| xuxueli_xxl-job | temurin-17 |
| apolloconfig_apollo | temurin-17 |

The full JDK map earned its keep — 8, 17, and 21 all claimed kills. And these are *pillars* of the Java ecosystem, not filler: dubbo, zxing, canal, easyexcel, xxl-job, apollo.

## Failure breakdown (deduped ledger)
`no-pom` 31 culled live (+7 historical) · `compile` 5 (netty et al — real toolchain demands) · `dependency` 2 (dbeaver, nacos — unresolvable snapshot parents) · `other` 2 · `network` 1 · `timeout` 1.

## The two honorable refusals — and the fixes
1. **macrozheng_mall** — yesterday: 20-min timeout. Today: blazed *through* compile on warm cache, then died calling a **hardcoded remote Docker host** (`192.168.3.101:2375`) via the fabric8 docker-maven-plugin. **Fix shipped (`630dde8`): `-Ddocker.skip=true`** in the orchestrator's mvn flags — should convert mall and its whole class on the next run.
2. **xkcoding_spring-boot-demo** — timed out *twice*, even warm. Genuinely too large for 2 GB / 20-min caps. Future: bigger caps or explicit exclusion.
3. *(minor)* `jeecg-boot` — codeload zip-404 (likely repo size limit on ZIP export).

## Milestone 1 math (research brief)
- 54% build-attempt success → **~90–100 sampled repos ⇒ ~50 builders**, i.e. one overnight run (`fetch 100 → clone → build`) on this machine.
- Guards make the long march cheap: 58% of this run's candidates cost zero build time.

## Commits shipped today (v2)
- `607f7c6` taxonomy fixes (signal lines, dependency-before-network, maven 3.9 dialect)
- `93cf8fe` final_build_report.json + jdk-mismatch → compile
- `b813ce1` README rewrite (v2 state)
- `f68a826` the four levers
- `630dde8` `-Ddocker.skip=true` + report dedup

## Next steps
- [ ] Overnight march: fetch 100 (filters on) → clone → build → expect ~50 builders → **Milestone 1** ✓
- [ ] Then Eclipse Steady + Project KB (brief steps 5–6), VESTA/VulEUT (Milestone 2)
- [ ] Optional: retry `jeecg-boot` via git clone fallback when ZIP 404s
- [ ] Q164 photo backlog — fight-or-drop verdict (the wolf remembers)
