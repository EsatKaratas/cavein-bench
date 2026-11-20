#!/usr/bin/env bash
# Runs the full benchmark and keeps retrying runs that died on provider errors (free-tier quotas/overload).
# Usage: ./scripts/run_main.sh [batch] [repeats] [agents]
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
BATCH="${1:-main}"; REPEATS="${2:-1}"; AGENTS="${3:-all}"; SCENARIOS="${4:-all}"
PY="${PYTHON:-/opt/anaconda3/bin/python}"
for pass in 1 2 3 4 5 6 7 8 9 10; do
  echo "=== pass $pass $(date +%H:%M:%S) ==="
  "$PY" -u -m cavein run --agents "$AGENTS" --scenarios "$SCENARIOS" --langs en,tr,ar --repeats "$REPEATS" --batch "$BATCH" \
        --workers "${WORKERS:-6}" --resume
  left=$("$PY" -c "
import sqlite3; c=sqlite3.connect('data/cavein.db')
print(c.execute(\"select count(*) from runs where batch='$BATCH' and (error like 'INFRA:%' or labels like '%judge_error%')\").fetchone()[0])")
  echo "runs still needing a retry: $left"
  [ "$left" = "0" ] && break
  sleep 90
done
echo "=== finished $(date +%H:%M:%S) ==="
