#!/bin/bash
# Full local morning pipeline the scheduler calls: fetch -> score -> find contacts
# (Hunter) -> render your template -> validate -> send (if autosend on) / queue.
set -euo pipefail

PROJECT_DIR="/Users/nithisha/Documents/Jobs/Fetch jobs"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/routine-$(date +%Y-%m-%d).log"

PY="$PROJECT_DIR/.venv/bin/python"

{
  echo "=== routine start $(date) ==="
  "$PY" -m jobhunter.cli routine        # ingest + score + prepare (Hunter contacts + drafts) + report
  echo "--- commit-drafts (send/queue) ---"
  "$PY" -m jobhunter.cli commit-drafts  # validate + send (if autosend) or queue
  echo "=== routine end   $(date) ==="
} >> "$LOG" 2>&1
