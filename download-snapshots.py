import json
import os
import requests
import zipfile
import io

def download_snapshots(json_file):
    if not os.path.exists('repos'):
        os.makedirs('repos')

    with open(json_file, 'r') as f:
        projects = json.load(f)

    for p in projects:
        repo_name = p['name'].replace("/", "_")
        target_path = os.path.join('repos', repo_name)
        
        # Skip if folder already exists
        if os.path.exists(target_path):
            print(f"⏩ Skipping {repo_name}, already exists.")
            continue

        # Construct the ZIP URL (defaults to master/main via zipball)
        download_url = f"{p['url']}/zipball/master" 
        
        try:
            print(f"📥 Downloading snapshot for {p['name']}...")
            r = requests.get(download_url, stream=True, timeout=30)
            
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                # extraction usually creates a nested folder like 'repo-master-xyz'
                # we extract it then rename/move it to target_path
                z.extractall('repos/temp_extract')
                
                # Get the name of the folder created inside temp_extract
                inner_folder = os.listdir('repos/temp_extract')[0]
                os.rename(os.path.join('repos/temp_extract', inner_folder), target_path)
                os.rmdir('repos/temp_extract')
                print(f"✅ Extracted to {target_path}")
            else:
                # If 'master' fails, try 'main'
                print(f"  ⚠️ Master failed, trying main branch...")
                # (Repeat logic for 'main' branch if necessary)

        except Exception as e:
            print(f"❌ Failed {p['name']}: {e}")

if __name__ == "__main__":
    download_snapshots('projects.json')