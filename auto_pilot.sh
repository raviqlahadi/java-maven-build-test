#!/bin/bash

TARGET=160  # 80% of 200
SUCCESS_FILE="success_projects.json"

echo "🚀 Starting Auto-Pilot mode..."

while true; do
    # 1. Count current successes
    if [ -f "$SUCCESS_FILE" ]; then
        CURRENT_COUNT=$(grep -c "name" "$SUCCESS_FILE")
    else
        CURRENT_COUNT=0
    fi

    echo "📊 Progress: $CURRENT_COUNT / $TARGET"

    # 2. Check if we hit the goal
    if [ "$CURRENT_COUNT" -ge "$TARGET" ]; then
        echo "🎯 Goal Achieved! 80% success rate reached."
        break
    fi

    echo "🔄 Starting a new cycle to find more projects..."

    # 3. Execute the pipeline
    # We use 'make fetch' to get new candidates
    # 'make clone' to download them
    # 'make build' to compile and collect JARs
    make iterate

    # 4. Cleanup repos to save space for the next batch
    # We only keep the .jar files in /artifacts
    rm -rf repos/*
    
    echo "😴 Cycle finished. Resting for 10 seconds before checking again..."
    sleep 10
done