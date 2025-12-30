import json
import os
import requests
import time

def fetch_until_full(target_count=200):
    success_file = 'success_projects.json'
    failed_file = 'failed_projects.json'
    
    # Load existing data
    success_list = json.load(open(success_file)) if os.path.exists(success_file) else []
    failed_list = json.load(open(failed_file)) if os.path.exists(failed_file) else []
    
    # Known names to skip
    skip_names = {p['name'] for p in success_list} | {p['name'] for p in failed_list}
    
    print(f"💎 Current Successes: {len(success_list)}")
    print(f"🚫 Known Failures to Skip: {len(failed_list)}")

    if len(success_list) >= target_count:
        print("✅ Success list already full!")
        return success_list

    url = "https://seart-ghs.si.usi.ch/api/r/search"
    params = {
        "language": "Java",
        "pomXmlPresent": "true",
        "sort": "stargazers",
        "direction": "DESC",
        "page": 0
    }

    new_projects = []
    needed = target_count - len(success_list)

    while len(new_projects) < needed:
        print(f"Fetching page {params['page']} for {needed - len(new_projects)} more projects...")
        response = requests.get(url, params=params, verify=False)
        items = response.json().get('items', [])
        
        if not items: break

        for item in items:
            if item['name'] not in skip_names:
                new_projects.append({
                    "name": item['name'],
                    "url": f"https://github.com/{item['name']}",
                })
                if len(new_projects) >= needed: break
        
        params['page'] += 1
        time.sleep(1)

    # Combine existing successes with new candidates for the NEXT build run
    combined_todo = success_list + new_projects
    with open('projects.json', 'w') as f:
        json.dump(combined_todo, f, indent=4)
    
    print(f"📝 Created projects.json with {len(combined_todo)} total targets.")

if __name__ == "__main__":
    fetch_until_full(200)