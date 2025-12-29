import requests
import json
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_seart_projects(limit=200):
    url = "https://seart-ghs.si.usi.ch/api/r/search"
    
    params = {
        "nameEquals": "false",
        "language": "Java",
        "pomXmlPresent": "true",       # CRITICAL: Ensures it's a Maven project
        "isFork": "false",             # Avoid forks; original repos are more stable
        "starsMin": 50,                # Minimum quality bar
        "committedMin": "2025-06-01",  # Active in the last year
        "sort": "stargazers",
        "direction": "DESC",
        "page": 0
    }
    
    projects = []
    
    while len(projects) < limit:
        print(f"Fetching page {params['page']}...")
        response = requests.get(url, params=params, verify=False)
        
        if response.status_code != 200:
            print(f"Error {response.status_code}: {response.text}")
            break
            
        data = response.json()
        items = data.get('items', [])
        
        if not items:
            break
            
        for item in items:
            if len(projects) >= limit:
                break
            
            # Additional check: Some repos have pom.xml but are primarily something else
            # We want Java to be the main language
            if item.get('mainLanguage') == 'Java':
                projects.append({
                    "name": item['name'],
                    "url": f"https://github.com/{item['name']}",
                    "stars": item.get('stargazers', 0),
                    "last_commit": item.get('lastCommit', ""),
                    "default_branch": item.get('defaultBranch', 'master')
                })
            
        params['page'] += 1
        time.sleep(0.5)

    return projects

if __name__ == "__main__":
    results = fetch_seart_projects(limit=220) # Fetch a few extra for buffer
    with open('projects.json', 'w') as f:
        json.dump(results, f, indent=4)
    print(f"Done! Saved {len(results)} verified Maven projects to projects.json")