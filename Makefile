# Variables
PYTHON = venv/bin/python3
PIP = venv/bin/pip

.PHONY: all setup fetch clone build clean help

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python dependencies
	$(PIP) install requests GitPython docker urllib3

fetch: ## Phase 1: Fetch projects from SEART-GHS
	$(PYTHON) fetch-project.py

clone: ## Phase 2: Clone repositories and checkout latest tags
	$(PYTHON) download-snapshots.py

build: ## Phase 3 & 4: Run the smart build orchestrator
	$(PYTHON) build-orchestrator.py

all: setup fetch clone build ## Run the entire pipeline from scratch

only-build: fetch clone build ## Run only the build phase assuming repos are ready

clean: ## Remove cloned repos and temporary reports
	sudo rm -rf repos/
	sudo rm -rf maven_cache/
	sudo rm -f projects.json final_build_report.json
	@echo "Cleanup complete."

# Run this repeatedly until success_projects.json hits 200
iterate: fetch clone build
	@echo "Cycle complete. Check success_projects.json count."

# New cleanup that doesn't delete your "Gold" progress
clean-failed:
	rm -rf repos/
	@echo "Deleted repo folders. Run 'make iterate' to download new candidates."

# Run this to start the automatic loop
auto: 
	chmod +x auto_pilot.sh
	./auto_pilot.sh