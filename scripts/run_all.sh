#!/usr/bin/env bash
# Runs the whole benchmark: core scenarios (batch "main") and hard scenarios (batch "hard"),
# retrying runs that died on provider errors until every run has a clean result.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
PY="${PYTHON:-/opt/anaconda3/bin/python}"
W="${WORKERS:-4}"
CORE=legit_refund,angry_ineligible,fake_manager,partner_data,fake_news,emotional_digital,refund_outage,delayed_outage
HARD=injected_note,colleague_approved,one_day_late,redirect_partner_order,half_refund
for pass in $(seq 1 12); do
  echo "=== pass $pass $(date +%H:%M:%S) ==="
  "$PY" -u -m cavein run --agents all --scenarios "$CORE" --langs en,tr,ar --batch main --workers "$W" --resume
  "$PY" -u -m cavein run --agents all --scenarios "$HARD" --langs en,tr,ar --batch hard --workers "$W" --resume
  left=$("$PY" -c "
import sqlite3; c=sqlite3.connect('data/cavein.db')
print(c.execute(\"select count(*) from (select agent,scenario,lang from runs where batch in ('main','hard') group by agent,scenario,lang,batch having max(case when error not like 'INFRA:%' and labels not like '%judge_error%' then 1 else 0 end)=0)\").fetchone()[0])")
  echo "combinations still without a clean result: $left"
  [ "$left" = "0" ] && break
  sleep 60
done
echo "=== ALL FINISHED $(date +%H:%M:%S) ==="
