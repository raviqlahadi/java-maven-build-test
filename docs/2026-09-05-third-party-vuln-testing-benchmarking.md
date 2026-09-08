---
title: "Third-Party Vulnerability Testing — Benchmarking Study Idea"
tags: [research, security, java, vulnerability-testing, sca, llm]
created: 2026-09-05
updated: 2026-09-05
---

# Third-Party Vulnerability Testing — Benchmarking Study Idea

## Core Idea

Benchmarking study comparing existing third-party vulnerability testing/exploitation approaches. Run a SCA tool (Eclipse Steady) on a sampled set of Java projects, then invoke tools like Siege, Transfer, Vesta, VulEUT, and baseline LLMs to see:
- How many vulnerabilities are found
- In how many cases vulnerabilities are *confirmed* (test actually triggers the 3rd-party vulnerability)

**Goal:** Identify limitations of existing testing tools and propose improvements.

---

## Papers & Tools

### Core Papers
- **Empirical study on third-party vulnerabilities in Java projects** — https://ieeexplore.ieee.org/document/10172868 *(also see references)*
- **Empirical study with Eclipse Steady** — https://doi.org/10.1007/s10664-020-09830-x

### Tools Being Compared
| Tool | Link |
|------|------|
| Eclipse Steady (SCA) | https://github.com/eclipse-steady/steady |
| SIEGE | https://ieeexplore.ieee.org/document/9462983 |
| Transfer | https://dl.acm.org/doi/abs/10.1145/3533767.3534398 |
| VESTA | https://dl.acm.org/doi/10.1145/3597503.3639583 |
| VulEUT | https://arxiv.org/abs/2409.16701 |

> ⚠️ For all papers above: read related work and references for additional tools/baselines.

---

## Steps

1. **Select Java projects** from https://seart-ghs.si.usi.ch/ → target hundreds
2. **Check out latest release** (more likely to build successfully)
3. **Auto-build** Maven projects only — consider Docker container for closed-environment dependency packing (`docker-py`: https://github.com/docker/docker-py)
4. **Ensure 50+ projects build successfully**
5. **Prepare Eclipse Steady** — feed backend with whole Project KB (consider updating KB later)
6. **Run Eclipse Steady** to find affected vulnerabilities
7. **Extract data needed for testing tools** — vulnerable constructs from official fixing commits → may need program analysis tools for exact method names (e.g., `javalang`)
8. **Run VESTA + VulEUT first** (priority) on releases where Steady found ≥1 targetable CVE
   - VulEUT uses ChatGPT → consider swapping to open HuggingFace LLM (HPC needed)
9. *(Optional)* **Re-run Steady in dynamic mode** after new tests generated → should show diff in results
10. **Collect results + compute statistics**
11. *(Optional)* **Manual inspection** of some results

---

## Milestones

| # | Target |
|---|--------|
| M1 | Projects built + Eclipse Steady run |
| M2 | 2 tools runnable (VESTA + VulEUT priority) |
| M3 | Some generated test cases exist |
| M4 | Initial results analyzed |

**Duration:** ~4 months (excluding warm-up phase and holidays)

---

## Notes / Considerations

- JUnit testing experience recommended
- VulEUT → ChatGPT dependency is a risk; open LLM alternative preferred (needs HPC)
- Dynamic mode re-run (step 9) is a bonus, not mandatory
- Manual inspection (step 11) is a bonus, not mandatory
- Project KB for Eclipse Steady may need freshening — factor into timeline

---

## Follow-Up
- [ ] Read related work in all listed papers for more baseline tools
- [ ] Investigate HPC options for open LLM replacement in VulEUT
- [ ] Scope the project sampling strategy (random? stratified by stars/age?)
- [ ] Assess Docker-py feasibility for automated Maven builds
