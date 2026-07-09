#!/bin/bash
# Wrapper the scheduler calls each morning. Logs stdout/stderr with a timestamp.
set -euo pipefail

PROJECT_DIR="/Users/nithisha/Documents/Jobs/Fetch jobs"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y-%m-%d)"

echo "=== routine start $(date) ===" >> "$LOG_DIR/routine-$STAMP.log"
"$PROJECT_DIR/.venv/bin/python" -m jobhunter.cli routine \
    >> "$LOG_DIR/routine-$STAMP.log" 2>&1
echo "=== routine end   $(date) ===" >> "$LOG_DIR/routine-$STAMP.log"
