---
title: Docker Desktop Crash on ragna — C: Drive Full (Forensics & Mitigation)
tags: [troubleshooting, docker, wsl, disk, infrastructure]
created: 2026-09-05
updated: 2026-09-05
---

# Docker Desktop Crash on ragna — C: Drive Full (Forensics & Mitigation)

**Date:** 2026-09-05 · **Machine:** ragna (home PC, WSL2 + Docker Desktop)
**Context:** discovered while live-testing the build engine of [[2026-09-05-java-maven-scraper-rebuild]] (Q191)

## Symptoms (in order)

1. `docker version` OK, `docker pull maven:...` appeared OK
2. Container creation failed: `502 Bad Gateway` then `500 Internal Server Error` (docker-py + CLI alike)
3. `_ping` route → 500, then unix socket dead entirely (HTTP 000)
4. Engine did NOT self-heal (probed ~60s)

## Forensic chain (WSL kernel log, `dmesg`)

| Evidence | Meaning |
|---|---|
| `WSL: Capturing crash for pid 19, executable: initd, signal: 7` | WSL init died with **SIGBUS** — classic "backing store vanished under memory-mapped I/O" |
| `I/O error, dev sdf, sector 0 op 0x1:(WRITE)` ×49 | Docker's virtual data disk went **unwritable** |
| `lsblk`: sdf = 1TB ext4, **no mountpoint** | sdf = `docker_data.vhdx`'s device — unmounted after engine death |
| `df -h /mnt/c` → **100% used, 473MB free** | **Root cause** |

**Root cause:** `C:\` (119GB) completely full. Docker Desktop's data vault
(`C:\Users\Administrator\AppData\Local\Docker\wsl\disk\docker_data.vhdx`, sparse-growing)
could not expand. Image pull consumed the last free bytes → container create needed more →
VHDX growth failed → block device write errors → SIGBUS → engine death.

**Timeline note:** fixture mtimes (21:14) vs kernel crash timestamp (~20:18 by uptime math)
suggest a possible earlier partial crash + recovery; final failure mode was userspace HTTP
errors from a half-dead backend. Wall-clock mapping aside, disk-full causality is unambiguous.

## Why it recurred / will recur

- Anything that writes to C: (Docker pulls, WSL vhdx growth, Windows Update) re-triggers it
- The POC's own README warned about this machine: "migrate your WSL distro to D:" — the same disease, prophesied

## Mitigation (done / to-do)

### Immediate (done 2026-09-05)
- [x] Identified root cause; halted Docker testing before retrying blindly

### Step 1 — sweep Windows Temp (~2.1GB)
- [ ] `C:\Users\Administrator\AppData\Local\Temp` = 2.1GB — safe to clear, skip locked files
- Other C: fat found: npm-cache 168MB, Downloads 400MB (user files — leave)

### Step 2 — move Docker's disk to D: (THE durable fix)
- [ ] Docker Desktop → Settings → Resources → Advanced → **Disk image location** → `D:\docker-data`
- Desktop migrates `docker_data.vhdx` (3.1GB) itself and restarts the engine
- Effect: 3.1GB freed on C: **and** all future image/build growth lands on D: (45GB free)

### Step 3 — verify build engine
- [ ] `./venv/bin/python verify-docker.py` in `~/projects/java-maven-build-test`
  (committed on v2 branch, `0f7e23d`: builds healthy + ghost-dependency fixtures, checks taxonomy)

### Optional deeper C: cleanup (Windows-side)
- Disk Cleanup (`cleanmgr`): Windows Update leftovers, Delivery Optimization, hiberfil
- `wsl --export/--import` migration for any remaining C:-resident distros

## End state target
- C: ≈ 7GB+ free (survivable headroom), Docker permanently off C:, D: does the heavy lifting

## Lessons
1. Sparse VHDX + 100%-full host volume = SIGBUS-looking engine death; check `df` FIRST on any Docker "engine unreachable" event
2. `dmesg` in WSL names the failing block device — map it with `lsblk` before guessing
3. Docker Desktop's own logs (`AppData\Local\Docker\log\host\`) were quiet here — the kernel told the real story
