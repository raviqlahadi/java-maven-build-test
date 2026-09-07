# Automated Java Maven Build Pipeline

This project automates the large-scale evaluation of Java Maven projects. It mines data from the **SEART-GHS** database, downloads project snapshots, and orchestrates isolated builds using Docker to measure build success rates across different environments.

> **Status (2026-09-07):** v2 architecture complete and verified end-to-end. Engine verification passes, anti-ban layer proven live, first 10-repo e2e run executed (results below). Next milestone: 50+ building projects. Branch `v2` is the active line of development.

---

## 🚀 Features

- **Stochastic Mining**

    Fetches real-world Java projects with specific star and activity thresholds.

- **SEART-Native Fetching (anti-ban by design)**

    One bulk CSV export (`/r/download/csv`, ~26 MB gz, cached locally) covers the whole Java+pom dataset — no scraping loop. Default branches come from SEART metadata, so **zero GitHub API calls** are needed for branch resolution.

- **Snapshot Optimization**

    Uses codeload ZIP snapshots instead of full Git clones to save ~80% disk space.

- **Isolated Environments**

    Uses `docker-py` to run builds in clean containers, preventing host-system pollution.

- **Smart JDK Selection**

    Automatically detects the required Java version by parsing `pom.xml`
    (properties *and* `maven-compiler-plugin` configuration).
    Maps **8, 11, 17, 21** → `maven:3.9.x-eclipse-temurin-*` images; projects
    demanding anything higher are skipped cheaply as `jdk-unsupported`.

- **Root-pom Guard**

    A repo without `pom.xml` at its **root** is skipped before any Docker
    cycle is spent — nested poms belong to shipped sub-modules (this is how
    Gradle-migrated repos used to slip through and waste a build).

- **Timeout Retry on Warm Cache**

    Timed-out builds are retried once at the end of the run — their
    dependencies are already in `maven_cache`, so the retry is cheap and
    frequently converts.

- **Permission Mapping**

    Maps WSL User IDs to Docker containers to avoid root-owned file locks.

- **Failure Taxonomy**

    Every failed build is classified — see [Failure Taxonomy](#🏷-failure-taxonomy).

- **Engine Verification**

    `verify-docker.py` proves the build engine end-to-end with synthetic fixtures before any real run: a healthy project must build, a ghost-dependency project must fail and classify as `dependency`.

---

## 🛠 Prerequisites

- WSL2 (Ubuntu/Debian recommended)
- Docker Desktop (with WSL2 integration enabled)
- Python 3.12+
- Make utility

For a new WSL installation, ensure the following steps are completed:

1. **System Tools**: Install essential Linux utilities:

    `sudo apt update && sudo apt install -y python3-pip python3-venv make git curl`

2. **Docker Integration**:
    - Install **Docker Desktop** on Windows.
    - Go to `Settings > Resources > WSL Integration` and enable it for your distro.
    - **Permission Fix**: Run `sudo usermod -aG docker $USER` and restart your terminal.
3. **Storage**: If your C: drive is low on space, migrate your WSL distro to another drive (e.g., D:) using `wsl --export` and `wsl --import`. Docker Desktop's data disk should likewise live on D: (`Settings > Resources > Advanced > Disk image location`).

---

## 📋 Getting Started

### 1. Installation

Clone this repository and set up the virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
mkdir -p maven_cache
chmod -R 777 maven_cache
make setup
```

### 2. Verify the build engine (recommended first)

```bash
./venv/bin/python verify-docker.py
```

Runs two synthetic builds through the real orchestrator code path and prints `✅ BUILD ENGINE VERIFIED` on success. Requires the `maven:3.9.6-eclipse-temurin-17` image (pulled automatically).

### 3. Execution Pipeline

You can run the entire pipeline or step-by-step using the provided Makefile:

| Command | Description |
| --- | --- |
| `make fetch` | Pulls the SEART bulk CSV (cached after first run) and samples `projects.json` (default target: 200). |
| `make clone` | Downloads ZIP snapshots of the projects into the `repos/` folder. |
| `make build` | Runs the Smart Build Orchestrator via Docker, writes `final_build_report.json`. |
| `make clean` | Wipes the `repos/` and reports to start fresh. |

For a small trial, invoke the fetcher with a custom target:

```bash
./venv/bin/python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('fp', 'fetch-project.py')
fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
fp.fetch_until_full(10)
"
```

---

## 📊 Build Strategy

To achieve high success rates, the orchestrator employs several **Resilience Flags**:

```bash
mvn clean package -DskipTests -B -fae -Dcheckstyle.skip -Drat.skip -Duser.home=/tmp -Dmaven.repo.local=/cache
```

- `DskipTests` — Skips tests to avoid environment-specific failures (DBs, ports).
- `Dcheckstyle.skip -Drat.skip` — Bypasses non-compilation checks like code formatting and license headers.
- `Dmaven.repo.local=/cache` — Forces a unified dependency cache for speed and consistency.
- `-B -fae` — Batch mode, fail-at-end so all modules report.
- Resource caps per build: **2 GB RAM, 2 CPUs, 20-min hard kill**.

---

## 🏷 Failure Taxonomy

Each failure is categorized by `classify_failure()` (signal `[ERROR]` lines are preferred over Maven's footer boilerplate; Maven ≥3.9 phrasings like *"was not found in \<repo\>"* / *"(absent)"* are understood):

| Category | Trigger examples |
| --- | --- |
| `no-pom` | no `pom.xml` at repo root (Gradle-migrated or docs repos) |
| `jdk-unsupported` | pom demands a Java version beyond the image map (>21) |
| `dependency` | `could not find artifact`, `(absent)`, `was not found in <repo>` |
| `network` | `could not resolve` / `connection` / `network` errors |
| `compile` | `compilation error`, `cannot find symbol`, `invalid target release: N` |
| `timeout` | build exceeded the 20-min hard kill |
| `other` | anything else |

> Note: dependency is checked *before* network — ghost-dependency failures contain both phrases, and the artifact-level signal wins.

---

## 📈 Results

### E2E trial — 2026-09-07 (10 top-starred Java repos)

- **Fetch:** 1 SEART request (bulk CSV cached) · **Clone:** 10/10 via codeload, 0 GitHub API calls — **anti-ban layer held**
- **Builds:** 0/10 success, honestly classified: `no-pom` ×7 (Gradle migrations: elasticsearch, ghidra, spring-boot, Stirling-PDF; docs repos), `compile` ×1 (requires JDK 21), `timeout` ×1 (mega-project on cold cache), `other` ×1

**Findings shaping the next iteration:**

1. **Top-stars sampling is maven-hostile** — the Gradle migration wave and docs repos dominate. SEART's `pomXmlPresent` reflects index-time state, not the release tag; re-verify pom presence at the checked-out ref.
2. **JDK map needs 21+** — several modern projects demand it.
3. **Mega-projects need bigger caps** — timeout/memory headroom, plus a warm `maven_cache` (second runs of the same project are dramatically faster).

*Historical v1 evaluation: 39% success rate over 200 projects (superseded by v2 architecture).*

---

## 🚧 Known Limitations

- JDK image map covers 8/11/17/21 — projects needing newer JDKs (22+) are skipped as `jdk-unsupported` until the map grows.
- Versions declared only in a *parent* pom (not the project's own) can't be pre-detected — they surface as `compile` failures at build time.
- 20-min hard kill is tight for mega-projects on a cold dependency cache (mitigated by the warm-cache retry pass).
- `pomXmlPresent` sampling filter can admit repos whose *release tag* no longer ships Maven (mitigated by the root-pom guard).
- `auto_pilot.sh` references a `make iterate` target that does not exist in the Makefile (experimental script, not part of the core pipeline).

---

## 📁 Project Structure

```
.
├── Makefile                  # Command orchestrator (fetch / clone / build / clean)
├── fetch-project.py          # Phase 1: SEART bulk export + sampling (anti-ban)
├── download-snapshots.py     # Phase 2: codeload ZIP downloader
├── build-orchestrator.py     # Phase 3/4: Docker build engine + taxonomy + report
├── verify-docker.py          # Engine self-test (synthetic healthy + ghost-dep fixtures)
├── auto_pilot.sh             # Experimental loop wrapper (known issue: make iterate)
├── seart-ca-bundle.pem       # TLS chain for SEART (built from LE AIA, auto-generated)
├── seart_cache/              # Cached bulk CSV export
├── maven_cache/              # Shared .m2 dependency repository
├── repos/                    # Downloaded project source code
├── projects.json             # Sampled targets
├── success_projects.json     # Build successes (never re-downloaded/re-built)
├── failed_projects.json      # Build failures + reasons + categories
└── final_build_report.json   # Aggregate report (totals, by-category, details)
```
