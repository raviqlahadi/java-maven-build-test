import docker
import os
import json

# --- CONFIG ---
REPOS_DIR = 'repos'
M2_CACHE = os.path.abspath("maven_cache")
REPORT_FILE = 'final_build_report.json'
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

def run_maven_build(client, pom_dir):
    # Added -Duser.home=/tmp to bypass the /root permission error
    maven_cmd = "mvn clean package -DskipTests -B -fae -Dcheckstyle.skip -Drat.skip -Duser.home=/tmp"
    image = get_jdk_image(os.path.join(pom_dir, 'pom.xml'))
    
    container = None
    try:
        # We manually manage removal to ensure logs are available
        container = client.containers.run(
            image=image,
            command=maven_cmd,
            user=f"{CUR_UID}:{CUR_GID}",
            volumes={
                pom_dir: {'bind': '/app', 'mode': 'rw'},
                M2_CACHE: {'bind': '/tmp/.m2', 'mode': 'rw'} # Use /tmp for writable cache mapping
            },
            working_dir='/app',
            environment={"MAVEN_CONFIG": "/tmp/.m2"}, # Point config away from /root
            detach=True # Run in background so we can wait and then get logs
        )
        
        result = container.wait()
        log_output = container.logs().decode('utf-8', errors='ignore')
        
        if result['StatusCode'] == 0:
            return True, "SUCCESS", image
        else:
            error_lines = [l for l in log_output.split('\n') if "[ERROR]" in l]
            reason = " | ".join(error_lines[-2:]) if error_lines else "Build Failed (Check pom.xml)"
            return False, reason, image

    except Exception as e:
        return False, str(e), image
    finally:
        if container:
            try:
                container.remove() # Manually cleanup
            except:
                pass

def main():
    try:
        client = docker.from_env()
    except Exception as e:
        print(f"❌ Docker connection failed: {e}")
        return

    repo_folders = [d for d in os.listdir(REPOS_DIR) if os.path.isdir(os.path.join(REPOS_DIR, d))]
    results = []

    print(f"🚀 Starting build for {len(repo_folders)} projects...")

    for i, folder in enumerate(repo_folders, 1):
        repo_path = os.path.abspath(os.path.join(REPOS_DIR, folder))
        pom_dir = find_pom_directory(repo_path)
        
        if not pom_dir:
            print(f"[{i}/{len(repo_folders)}] ⏩ {folder}: No pom.xml found.")
            continue

        print(f"[{i}/{len(repo_folders)}] 📦 Building {folder}...", end=" ", flush=True)
        success, reason, image = run_maven_build(client, pom_dir)
        
        print(f"{'✅' if success else '❌'} {reason}")
        results.append({"project": folder, "success": success, "reason": reason})

    if results:
        wins = len([r for r in results if r['success']])
        print(f"\n🏁 Success Rate: {(wins/len(results))*100:.2f}%")
        with open(REPORT_FILE, 'w') as f:
            json.dump(results, f, indent=4)

if __name__ == "__main__":
    main()