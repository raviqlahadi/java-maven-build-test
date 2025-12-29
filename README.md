# Automated Java Maven Build Pipeline

This project automates the large-scale evaluation of Java Maven projects. It mines data from the **SEART-GHS** database, downloads project snapshots, and orchestrates isolated builds using Docker to measure build success rates across different environments.

---

## 🚀 Features

- **Stochastic Mining**
    
    Fetches real-world Java projects with specific star and activity thresholds.
    
- **Snapshot Optimization**
    
    Uses ZIP snapshots instead of full Git clones to save ~80% disk space.
    
- **Isolated Environments**
    
    Uses `docker-py` to run builds in clean containers, preventing host-system pollution.
    
- **Smart JDK Selection**
    
    Automatically detects the required Java version (8, 11, or 17) by parsing `pom.xml`.
    
- **Permission Mapping**
    
    Maps WSL User IDs to Docker containers to avoid root-owned file locks.
    

---

## 🛠 Prerequisites

- WSL2 (Ubuntu/Debian recommended)
- Docker Desktop (with WSL2 integration enabled)
- Python 3.12+
- Make utility

For a new WSL installation, ensure the following steps are completed:

1. **System Tools**: Install essential Linux utilities:Bash
    
    `sudo apt update && sudo apt install -y python3-pip python3-venv make git curl`
    
2. **Docker Integration**:
    - Install **Docker Desktop** on Windows.
    - Go to `Settings > Resources > WSL Integration` and enable it for your distro.
    - **Permission Fix**: Run `sudo usermod -aG docker $USER` and restart your terminal.
3. **Storage**: If your C: drive is low on space, migrate your WSL distro to another drive (e.g., D:) using `wsl --export` and `wsl --import`.

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

### 2. Execution Pipeline

You can run the entire pipeline or step-by-step using the provided Makefile:

| Command | Description |
| --- | --- |
| `make fetch` | Queries SEART-GHS API and saves metadata to `projects.json`. |
| `make clone` | Downloads ZIP snapshots of the projects into the `repos/` folder. |
| `make build` | Runs the Smart Build Orchestrator via Docker. |
| `make clean` | Wipes the `repos/` and reports to start fresh. |

---

## 📊 Build Strategy

To achieve high success rates, the orchestrator employs several **Resilience Flags**:

```bash
mvn clean package -DskipTests
```

- `DskipTests` — Skips tests to avoid environment-specific failures (DBs, ports).
- `Dcheckstyle.skip -Drat.skip` — Bypasses non-compilation checks like code formatting and license headers.
- `Dmaven.repo.local=/cache` — Forces a unified dependency cache for speed and consistency.

---

## 📈 Results

- **Current Success Rate:** 39% (evaluation in progress)
- **Build Duration:** ~4–6 hours for 200 projects
- **Storage Used:** ~15 GB (on D: Drive via WSL migration)

---

## 📁 Project Structure

```
.
├── Makefile                # Command orchestrator
├── fetch_projects.py       # Phase 1: API Mining
├── download_snapshots.py   # Phase 2: Lightweight Downloader
├── build-orchestrator.py   # Phase 3/4: Docker Build Engine
├── maven_cache/            # Shared .m2 dependency repository
├── repos/                  # Downloaded project source code
└── final_build_report.json # Build results and error logs

```
