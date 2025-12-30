import docker
import os
import json
import shutil
import glob

# --- CONFIG ---
REPOS_DIR = 'repos'
ARTIFACTS_DIR = os.path.abspath("artifacts")
M2_CACHE = os.path.abspath("maven_cache")
SUCCESS_FILE = 'success_projects.json'
FAILED_FILE = 'failed_projects.json'

# Ensure directories exist
os.makedirs(ARTIFACTS_DIR, exist_ok=True)
if not os.path.exists(M2_CACHE): os.makedirs(M2_CACHE)

CUR_UID = os.getuid()
CUR_GID = os.getgid()

def find_pom_directory(root_path):
    for root, dirs, files in os.walk(root_path):
        if 'pom.xml' in files:
            return os.path.abspath(root)
    return None

def get_jdk_image(pom_path):
    try:
        with open(pom_path, 'r', errors='ignore') as f:
            content = f.read()
            if any(v in content for v in ['1.8', '8</java.version>', '1.7']):
                return "maven:3.8.6-jdk-8"
            if '11' in content: return "maven:3.9.6-eclipse-temurin-11"
    except: pass
    return "maven:3.9.6-eclipse-temurin-17"

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
        "-Dmaven.repo.local=/cache"
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
            detach=True
        )
        
        result = container.wait()
        log_output = container.logs().decode('utf-8', errors='ignore')
        
        if result['StatusCode'] == 0:
            return True, "SUCCESS", image
        else:
            error_lines = [l for l in log_output.split('\n') if "[ERROR]" in l]
            reason = " | ".join(error_lines[-2:]) if error_lines else "Build Failed"
            return False, reason, image

    except Exception as e:
        return False, str(e), image
    finally:
        if container:
            try: container.remove()
            except: pass

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
        pom_dir = find_pom_directory(repo_path)
        
        if not pom_dir:
            print(f"[{i}/{len(repo_folders)}] ⏩ {folder}: No pom.xml found.")
            failures.append({"name": folder, "reason": "No pom.xml"})
            continue

        print(f"[{i}/{len(repo_folders)}] 📦 Building {folder}...", end=" ", flush=True)
        success, reason, image = run_maven_build(client, pom_dir)
        
        if success:
            jar_count = collect_jars(repo_path, folder)
            print(f"✅ SUCCESS ({jar_count} jars collected)")
            successes.append({"name": folder, "image": image})
        else:
            print(f"❌ {reason}")
            # Add to temporary failures for this session, but check if already in global failures
            failures.append({"name": folder, "reason": reason})

    # Save final states
    with open(SUCCESS_FILE, 'w') as f:
        json.dump(successes, f, indent=4)
    
    # Clean up failures list to remove duplicates before saving
    unique_failures = {f['name']: f for f in failures}.values()
    with open(FAILED_FILE, 'w') as f:
        json.dump(list(unique_failures), f, indent=4)

    print(f"\n🏁 Total Successes: {len(successes)}")
    print(f"📦 Artifacts stored in: {ARTIFACTS_DIR}")

if __name__ == "__main__":
    main()