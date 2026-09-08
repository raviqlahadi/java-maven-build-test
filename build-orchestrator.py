import docker
import os
import re
import json
import shutil
import glob
import time
from collections import Counter
import xml.etree.ElementTree as ET

# --- CONFIG ---
REPOS_DIR = 'repos'
ARTIFACTS_DIR = os.path.abspath("artifacts")
M2_CACHE = os.path.abspath("maven_cache")
SUCCESS_FILE = 'success_projects.json'
REPORT_FILE = 'final_build_report.json'
FAILED_FILE = 'failed_projects.json'
BUILD_TIMEOUT = 1200          # hard kill after 20 min (fixes infinite hangs)
MEM_LIMIT = "2g"              # per-container memory cap
NANO_CPUS = 2_000_000_000     # 2 CPU cores per container

# Ensure directories exist
os.makedirs(ARTIFACTS_DIR, exist_ok=True)
if not os.path.exists(M2_CACHE): os.makedirs(M2_CACHE)

CUR_UID = os.getuid()
CUR_GID = os.getgid()

def find_pom_directory(root_path):
    """Return the shallowest pom.xml's dir. First-walk-hit can pick a
    submodule pom in multi-module projects; shallowest = root candidate."""
    candidates = []
    for dirpath, dirnames, files in os.walk(root_path):
        if 'pom.xml' in files:
            depth = dirpath[len(root_path):].count(os.sep)
            candidates.append((depth, os.path.abspath(dirpath)))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _local(tag):
    return tag.split('}')[-1]


def parse_java_version_text(pom_text):
    """Parse java version from pom XML *text* (bytes or str). Checks
    maven.compiler.release/target/source and java.version properties.
    Text core shared with download-snapshots.py's pre-download gate —
    one parser, no drift between cull and build pre-flight."""
    try:
        root = ET.fromstring(pom_text)
    except ET.ParseError:
        return None
    props = {}
    for el in root:
        if _local(el.tag) == 'properties':
            for p in el:
                props[_local(p.tag)] = (p.text or '').strip()
    for key in ('maven.compiler.release', 'maven.compiler.target',
                'maven.compiler.source', 'java.version'):
        if props.get(key):
            return props[key]
    # maven-compiler-plugin <configuration><release|target|source> —
    # projects like java-design-patterns declare 21 only there
    for plugin in root.iter():
        if _local(plugin.tag) != 'plugin':
            continue
        artifact_id = None
        conf = None
        for child in plugin:
            tag = _local(child.tag)
            if tag == 'artifactId':
                artifact_id = (child.text or '').strip()
            elif tag == 'configuration':
                conf = child
        if artifact_id != 'maven-compiler-plugin' or conf is None:
            continue
        for key in ('release', 'target', 'source'):
            for child in conf:
                if _local(child.tag) == key and (child.text or '').strip():
                    text = child.text.strip()
                    if text.replace('.', '').isdigit():
                        return text
    return None


def parse_java_version(pom_path):
    """Path variant (build-phase use). Same logic as parse_java_version_text."""
    try:
        with open(pom_path, 'rb') as f:
            return parse_java_version_text(f.read())
    except OSError:
        return None


def _major(version):
    """'1.8' -> 8, '21' -> 21, garbage -> None."""
    try:
        return int(version.split('.')[1]) if version.startswith('1.') \
            else int(version.split('.')[0])
    except (ValueError, IndexError, AttributeError):
        return None


MAX_MAPPED_JDK = 21  # keep in sync with get_jdk_image


def get_jdk_image(pom_path):
    version = parse_java_version(pom_path)
    if not version:
        # POC default; research docs may change this — recorded for review
        return "maven:3.9.6-eclipse-temurin-17"
    major = _major(version)
    if major is None:
        return "maven:3.9.6-eclipse-temurin-17"
    if major <= 8:
        return "maven:3.8.6-jdk-8"
    if major <= 11:
        return "maven:3.9.6-eclipse-temurin-11"
    if major <= 17:
        return "maven:3.9.6-eclipse-temurin-17"
    return "maven:3.9.6-eclipse-temurin-21"

def collect_jars(repo_path, folder_name):
    """Finds .jar files in target folder and copies them to artifacts/"""
    # Look for jars specifically in 'target' folders created by the build
    jar_pattern = os.path.join(repo_path, "**/target/*.jar")
    jars = glob.glob(jar_pattern, recursive=True)
    count = 0
    for jar in jars:
        # Skip common non-runnable jars
        if any(x in jar.lower() for x in ["sources", "javadoc", "original", "tests"]):
            continue
        
        dest_name = f"{folder_name}_{os.path.basename(jar)}"
        shutil.copy2(jar, os.path.join(ARTIFACTS_DIR, dest_name))
        count += 1
    return count

def run_maven_build(client, pom_dir):
    # -Dmaven.repo.local=/cache is the most stable way to handle shared volumes
    maven_cmd = (
        "mvn clean package -DskipTests -B -fae "
        "-Dcheckstyle.skip -Drat.skip -Duser.home=/tmp "
        "-Dmaven.repo.local=/cache -Ddocker.skip=true"
    )
    image = get_jdk_image(os.path.join(pom_dir, 'pom.xml'))

    container = None
    try:
        container = client.containers.run(
            image=image,
            command=maven_cmd,
            user=f"{CUR_UID}:{CUR_GID}",
            volumes={
                pom_dir: {'bind': '/app', 'mode': 'rw'},
                M2_CACHE: {'bind': '/cache', 'mode': 'rw'}
            },
            working_dir='/app',
            mem_limit=MEM_LIMIT,
            nano_cpus=NANO_CPUS,
            detach=True
        )

        # Poll with a deadline — container.wait() alone can hang forever
        deadline = time.time() + BUILD_TIMEOUT
        timed_out = False
        while True:
            container.reload()
            if container.status in ('exited', 'dead'):
                break
            if time.time() > deadline:
                timed_out = True
                try: container.kill()
                except Exception: pass
                container.reload()
                break
            time.sleep(5)

        exit_code = container.attrs.get('State', {}).get('ExitCode', 1)
        log_output = container.logs().decode('utf-8', errors='ignore')

        if timed_out:
            return False, "TIMEOUT: build exceeded hard kill limit", image
        if exit_code == 0:
            return True, "SUCCESS", image
        else:
            error_lines = [l.strip() for l in log_output.split('\n') if "[ERROR]" in l]
            # Prefer signal lines over maven's footer boilerplate ("For more information...", "[Help 1]")
            signal_re = re.compile(
                r"could not (?:find artifact|resolve|transfer)|was not found in|\(absent\)|"
                r"compilation error|cannot find symbol|no pom|failed to execute goal",
                re.I,
            )
            signal_lines = [l for l in error_lines if signal_re.search(l)]
            picked = signal_lines[-2:] if signal_lines else error_lines[-2:]
            reason = " | ".join(picked) if picked else "Build Failed"
            return False, reason, image

    except Exception as e:
        return False, str(e), image
    finally:
        if container:
            try: container.remove()
            except: pass


def classify_failure(reason):
    """Light error taxonomy — full categories pending research docs."""
    r = reason.lower()
    if "timeout" in r: return "timeout"
    # dependency check BEFORE network: ghost-dep lines contain BOTH
    # "could not resolve dependencies" and artifact-not-found markers —
    # the more specific artifact-level signal wins.
    # maven >=3.9 phrasing: "artifact ... was not found in <repo> ... (absent)"
    # maven older phrasing: "could not find artifact X in <repo>"
    if ("could not find artifact" in r or "(absent)" in r
            or "was not found in" in r or "missing" in r):
        return "dependency"
    if "could not resolve" in r or "connection" in r or "network" in r: return "network"
    if "no pom.xml" in r: return "no-pom"
    if "cannot find symbol" in r or "compilation" in r or "incompatible" in r: return "compile"
    # maven dialect: "Fatal error compiling: error: invalid target release: 21"
    if "invalid target release" in r or "fatal error compiling" in r: return "compile"
    if "jdk-unsupported" in r or "no image mapped" in r: return "jdk-unsupported"
    return "other"

def main():
    try:
        client = docker.from_env()
    except Exception as e:
        print(f"❌ Docker connection failed: {e}")
        return

    # Load existing state to avoid re-building successes
    successes = json.load(open(SUCCESS_FILE)) if os.path.exists(SUCCESS_FILE) else []
    failures = json.load(open(FAILED_FILE)) if os.path.exists(FAILED_FILE) else []
    success_names = {p['name'] for p in successes}

    repo_folders = [d for d in os.listdir(REPOS_DIR) if os.path.isdir(os.path.join(REPOS_DIR, d))]
    
    current_results = []
    print(f"🚀 Processing {len(repo_folders)} folders...")

    for i, folder in enumerate(repo_folders, 1):
        if folder in success_names:
            print(f"[{i}/{len(repo_folders)}] ⏩ {folder}: Already in success list. Skipping.")
            continue

        repo_path = os.path.abspath(os.path.join(REPOS_DIR, folder))

        # Root-pom guard: no pom at repo ROOT means the project is not Maven
        # at this ref (Gradle migration, docs repo). Nested poms belong to
        # shipped sub-modules — don't build them. Saves a docker cycle.
        if not os.path.exists(os.path.join(repo_path, 'pom.xml')):
            print(f"[{i}/{len(repo_folders)}] ⏩ {folder}: No pom.xml at repo root.")
            failures.append({"name": folder, "reason": "No pom.xml at repo root",
                             "category": "no-pom"})
            continue

        # JDK pre-flight: don't spend a docker run on an unmappable compiler target
        pom_path = os.path.join(repo_path, 'pom.xml')
        required = _major(parse_java_version(pom_path) or '')
        if required and required > MAX_MAPPED_JDK:
            print(f"[{i}/{len(repo_folders)}] ⏩ {folder}: needs Java {required} "
                  f"(map tops at {MAX_MAPPED_JDK}).")
            failures.append({"name": folder,
                             "reason": f"pom requires Java {required}, no image mapped (jdk-unsupported)",
                             "category": "jdk-unsupported"})
            continue

        print(f"[{i}/{len(repo_folders)}] 📦 Building {folder}...", end=" ", flush=True)
        success, reason, image = run_maven_build(client, repo_path)

        if success:
            jar_count = collect_jars(repo_path, folder)
            print(f"✅ SUCCESS ({jar_count} jars collected)")
            successes.append({"name": folder, "image": image})
        else:
            print(f"❌ {reason}")
            # Add to temporary failures for this session, but check if already in global failures
            failures.append({"name": folder, "reason": reason,
                             "category": classify_failure(reason)})

    # Timeout retry pass — deps of a timed-out build are now warm in maven_cache,
    # so a single retry is cheap and frequently converts.
    timeout_names = [f['name'] for f in failures if f.get('category') == 'timeout'
                     and f['name'] not in success_names]
    if timeout_names:
        print(f"\n🔁 Retrying {len(timeout_names)} timeout(s) on warm cache...")
        for folder in timeout_names:
            repo_path = os.path.abspath(os.path.join(REPOS_DIR, folder))
            print(f"🔁 {folder}...", end=" ", flush=True)
            success, reason, image = run_maven_build(client, repo_path)
            if success:
                jar_count = collect_jars(repo_path, folder)
                print(f"✅ SUCCESS on retry ({jar_count} jars)")
                successes.append({"name": folder, "image": image})
                failures = [f for f in failures if f['name'] != folder]
            else:
                print(f"❌ still failing: {reason}")
                failures = [f for f in failures if f['name'] != folder]
                failures.append({"name": folder, "reason": reason,
                                 "category": classify_failure(reason)})

    # Save final states
    with open(SUCCESS_FILE, 'w') as f:
        json.dump(successes, f, indent=4)
    
    # Clean up failures list to remove duplicates before saving
    unique_failures = {f['name']: f for f in failures}.values()
    with open(FAILED_FILE, 'w') as f:
        json.dump(list(unique_failures), f, indent=4)

    print(f"\n🏁 Total Successes: {len(successes)}")
    print(f"📦 Artifacts stored in: {ARTIFACTS_DIR}")

    # Final report — Makefile contract: final_build_report.json
    deduped_failures = list({f['name']: f for f in failures}.values())
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "totals": {
            "processed": len(repo_folders),
            "success": len(successes),
            "failed": len(deduped_failures),
            "by_category": dict(Counter(f.get("category", "other") for f in deduped_failures)),
        },
        "successes": successes,
        "failures": deduped_failures,
    }
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)
    print(f"📄 Report written: {REPORT_FILE}")

if __name__ == "__main__":
    main()